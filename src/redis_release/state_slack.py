"""Slack display utilities for release state."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .bht.state import Package, ReleaseState, Workflow, WorkflowConclusion
from .models import PackageType, ReleaseType, SlackFormat
from .state_display import (
    DisplayModelGeneric,
    Section,
    Step,
    StepStatus,
    get_display_model,
)

logger = logging.getLogger(__name__)


def get_workflow_link(repo: str, run_id: Optional[int]) -> Optional[str]:
    """Generate GitHub workflow URL from repo and run_id.

    Args:
        repo: Repository in format "owner/repo"
        run_id: GitHub workflow run ID

    Returns:
        GitHub workflow URL or None if run_id is not available
    """
    if not run_id or not repo:
        return None
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def init_slack_printer(
    slack_token: Optional[str],
    slack_channel_id: Optional[str],
    thread_ts: Optional[str] = None,
    reply_broadcast: bool = False,
    slack_format: SlackFormat = SlackFormat.DEFAULT,
    state: Optional[ReleaseState] = None,
    state_name: Optional[str] = None,
) -> "SlackStatePrinter":
    """Initialize SlackStatePrinter with validation.

    Args:
        slack_token: Slack bot token (if None, uses SLACK_BOT_TOKEN env var)
        slack_channel_id: Slack channel ID to post to
        thread_ts: Optional thread timestamp to post messages in a thread
        reply_broadcast: If True and thread_ts is set, also show in main channel
        slack_format: Slack message format (default or one-step)
        state: Optional release state to post initial message to create the thread

    Warning: if state is provided, and thread_ts is not provided, the state will
    be modified in-place by setting the thread_ts and channel_id. Initial message
    will be posted to the channel to create the thread.

    Returns:
        SlackStatePrinter instance

    Raises:
        ValueError: If channel_id is not provided or token is not available
    """
    if not slack_channel_id:
        raise ValueError("Slack channel ID is required")

    # Get token from argument or environment variable
    token = slack_token or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError(
            "Slack token not provided. Use slack_token argument or set SLACK_BOT_TOKEN environment variable"
        )

    slack_printer = SlackStatePrinter(
        token, slack_channel_id, thread_ts, reply_broadcast, slack_format, state_name
    )

    # If thread_ts is not provided, post initial message to create the thread
    # and save thread_ts to the state to make all subsequent posts by the
    # workflows to be in the same thread
    if state and thread_ts is None:
        logger.info(
            "Posting initial slack message to create a thread and save thread_ts to the release state"
        )
        slack_printer.update_message(state)
        if state.meta.ephemeral.slack_channel_id is None:
            state.meta.ephemeral.slack_channel_id = slack_channel_id
        if slack_printer.message_ts is not None:
            state.meta.ephemeral.slack_thread_ts = slack_printer.message_ts

    return slack_printer


class SlackStatePrinter:
    """Handles posting and updating release state to Slack channel."""

    def __init__(
        self,
        slack_token: str,
        slack_channel_id: str,
        thread_ts: Optional[str] = None,
        reply_broadcast: bool = False,
        slack_format: SlackFormat = SlackFormat.DEFAULT,
        state_name: Optional[str] = None,
    ):
        """Initialize the Slack printer.

        Args:
            slack_token: Slack bot token
            slack_channel_id: Slack channel ID to post messages to
            thread_ts: Optional thread timestamp to post messages in a thread
            reply_broadcast: If True and thread_ts is set, also show in main channel
            slack_format: Slack message format (default or one-step)
        """
        self.client = WebClient(token=slack_token)
        self.channel_id: str = slack_channel_id
        self.thread_ts = thread_ts
        self.reply_broadcast = reply_broadcast
        self.slack_format = slack_format
        self.message_ts: Optional[str] = None
        self.last_blocks_json: Optional[str] = None
        self.started_at = datetime.now(timezone.utc)
        self.state_name = state_name

    def format_package_name(self, package_name: str, package: Package) -> str:
        """Format package name with capital letter and release type.

        Args:
            package_name: The raw package name
            package: The Package to get release type from

        Returns:
            Formatted package name with capital letter and release type in parentheses
        """

        # Capitalize first letter of package name
        formatted = package_name.capitalize()

        if package.meta.package_display_name:
            formatted = package.meta.package_display_name

        # Add release type if available
        if package.meta.release_type == ReleaseType.PUBLIC:
            release_type_str = f" - public release"
            formatted = f"*{formatted}* {release_type_str}"
        else:
            formatted = f"*{formatted}*"

        return formatted

    def blocks_append(
        self,
        blocks: List[Union[Dict[str, Any], None]],
        block: Optional[List[Union[Dict[str, Any], None]]],
    ) -> None:
        """Append block to blocks list if block is not None.

        Args:
            blocks: The list to append to
            block: The block to append (if not None)
        """
        if block is not None:
            for b in block:
                if isinstance(b, dict):
                    blocks.append(b)

    def blocks_prepend(
        self,
        blocks: List[Union[Dict[str, Any], None]],
        block: Optional[List[Union[Dict[str, Any], None]]],
    ) -> None:
        """Append block to blocks list if block is not None.

        Args:
            blocks: The list to append to
            block: The block to append (if not None)
        """
        if block is not None:
            for b in reversed(block):
                if isinstance(b, dict):
                    blocks.insert(0, b)

    def make_header_blocks(
        self, state: ReleaseState, all_workflow_statuses: Set[StepStatus]
    ) -> List[Union[Dict[str, Any], None]]:
        """Create header blocks for Slack message.

        Args:
            state: The ReleaseState to display

        Returns:
            List of header block dictionaries
        """
        blocks: List[Union[Dict[str, Any], None]] = []

        # Header - use "Custom Build" if is_custom_build, otherwise "Release"
        header_prefix = "Custom Build" if state.meta.is_custom_build else "Release"

        aggregated_status = self.aggregate_status(all_workflow_statuses)
        status_emoji = self.get_step_status_emoji(aggregated_status)
        if (
            state.meta.ephemeral.last_ended_at is not None
            and aggregated_status == StepStatus.RUNNING
        ):
            status_emoji = f"{self.get_status_icon(StepStatus.INCORRECT)} Aborted?"

        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{header_prefix} {state.meta.tag or 'N/A'} — {status_emoji}",
                },
            }
        )

        # State name (if provided)
        state_name_block = None
        if self.state_name:
            state_name_block = {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"state-name: {self.state_name}"}
                ],
            }
        self.blocks_append(blocks, [state_name_block])

        # Dates
        started_str = ""
        ended_str = ""
        if state.meta.ephemeral.last_started_at:
            started_str = "*Started:* " + state.meta.ephemeral.last_started_at.strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        if state.meta.ephemeral.last_ended_at:
            ended_str = "*Ended:* " + state.meta.ephemeral.last_ended_at.strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        dates_str = " | ".join(x for x in [started_str, ended_str] if x)

        dates_block = None
        if dates_str:
            dates_block = {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": dates_str}],
            }
        self.blocks_append(blocks, [dates_block])

        return blocks

    def make_custom_build_blocks(
        self, state: ReleaseState
    ) -> List[Union[Dict[str, Any], None]]:
        """Create custom build info blocks for Slack message.

        Uses display model to get custom build info and creates a block
        if there are custom versions to display.

        Args:
            state: The ReleaseState to display

        Returns:
            List of custom build block dictionaries (empty if not a custom build)
        """
        blocks: List[Union[Dict[str, Any], None]] = []

        display_model = DisplayModelGeneric()
        custom_versions = display_model.get_custom_versions(state)

        if not custom_versions:
            return blocks

        # Format custom versions as a list
        version_lines = [
            f"- {name}: {version}" for name, version in custom_versions.items()
        ]
        versions_text = "\n".join(version_lines)

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Redis and modules versions:\n{versions_text}",
                },
            }
        )

        return blocks

    def update_message(self, state: ReleaseState) -> bool:
        """Post or update Slack message with release state.

        Only updates if the blocks have changed since last update.

        Args:
            state: The ReleaseState to display

        Returns:
            True if message was posted/updated, False if no change
        """
        blocks = self.make_blocks(state)
        blocks_json = json.dumps(blocks, sort_keys=True)

        # Check if blocks have changed
        if blocks_json == self.last_blocks_json:
            logger.debug("Slack message unchanged, skipping update")
            return False

        text = f"Release {state.meta.tag or 'N/A'} — Status"

        try:
            if self.message_ts is None:
                # Post new message
                kwargs: Dict[str, Any] = {
                    "channel": self.channel_id,
                    "text": text,
                    "blocks": blocks,
                }

                # Add thread parameters if thread_ts is set
                if self.thread_ts:
                    kwargs["thread_ts"] = self.thread_ts
                    if self.reply_broadcast:
                        kwargs["reply_broadcast"] = True

                response = self.client.chat_postMessage(**kwargs)
                self.message_ts = response["ts"]
                # Update channel_id from response (authoritative)
                channel = response.get("channel")
                if isinstance(channel, str):
                    self.channel_id = channel
                logger.info(
                    f"Posted Slack message ts={self.message_ts}"
                    + (f" in thread {self.thread_ts}" if self.thread_ts else "")
                )
            else:
                # Update existing message
                self.client.chat_update(
                    channel=self.channel_id,
                    ts=self.message_ts,
                    text=text,
                    blocks=blocks,
                )
                logger.debug(f"Updated Slack message ts={self.message_ts}")

            self.last_blocks_json = blocks_json
            return True

        except SlackApiError as e:
            error_msg = getattr(e.response, "get", lambda x: "Unknown error")("error") if hasattr(e, "response") else str(e)  # type: ignore
            logger.error(f"Slack API error: {error_msg}")
            raise

    def make_blocks(self, state: ReleaseState) -> List[Dict[str, Any]]:
        """Create Slack blocks for the release state.

        Args:
            state: The ReleaseState to display

        Returns:
            List of Slack block dictionaries
        """
        blocks: List[Union[Dict[str, Any], None]] = []

        # Add custom build info if applicable
        self.blocks_append(blocks, self.make_custom_build_blocks(state))

        blocks.append({"type": "divider"})

        # Overall release status
        all_workflow_statuses: Set[StepStatus] = set()

        # Process each package
        for package_name, package in sorted(state.packages.items()):
            # Format package name with capital letter and release type
            formatted_name = self.format_package_name(package_name, package)

            # Get workflow statuses
            build_status, build_status_emoji = self.get_status_emoji(
                package, package.build
            )
            publish_status = StepStatus.NOT_STARTED
            publish_status_emoji = ""
            if package.publish is not None:
                publish_status, publish_status_emoji = self.get_status_emoji(
                    package, package.publish
                )

            all_workflow_statuses.update({build_status, publish_status})

            # skip if both build and publish are not started
            if (
                build_status == StepStatus.NOT_STARTED
                and publish_status == StepStatus.NOT_STARTED
            ):
                continue

            # Package section
            # Use "Test" label for clienttest package types instead of "Build"
            build_label = (
                "Test"
                if package.meta.package_type == PackageType.CLIENTTEST
                else "Build"
            )
            build_with_emoji = f"*{build_label}:* {build_status_emoji}"
            publish_with_emoji = ""
            if package.publish is not None:
                publish_with_emoji = f"*Publish:* {publish_status_emoji}"
            header_with_emojis = "  |  ".join(
                filter(bool, [build_with_emoji, publish_with_emoji])
            )
            package_status = self.aggregate_status({build_status, publish_status})
            package_status_emoji = self.get_status_icon(package_status)
            if self.slack_format == SlackFormat.DEFAULT:
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{package_status_emoji} {formatted_name}\n{header_with_emojis}",
                        },
                    }
                )

            is_sucess = {build_status, publish_status}.issubset(
                {StepStatus.SUCCEEDED, StepStatus.NOT_STARTED}
            )

            # Workflow details in context
            build_link = get_workflow_link(package.meta.repo, package.build.run_id)
            build_details = self.collect_workflow_details_slack(
                package, package.build, build_link, show_only_workflow_link=is_sucess
            )
            publish_details = ""
            if package.publish is not None:
                publish_link = get_workflow_link(
                    package.meta.repo, package.publish.run_id
                )
                publish_details = self.collect_workflow_details_slack(
                    package,
                    package.publish,
                    publish_link,
                    show_only_workflow_link=is_sucess,
                )

            if build_details or publish_details:
                elements = []
                if build_details:
                    elements.append({"type": "mrkdwn", "text": build_details})
                if package.publish is not None and publish_details:
                    elements.append({"type": "mrkdwn", "text": publish_details})
                blocks.append({"type": "context", "elements": elements})

            # Add package result blocks if package is successful
            if is_sucess:
                self.blocks_append(
                    blocks, self.make_package_result_blocks(package_name, state)
                )

            blocks.append({"type": "divider"})

        self.blocks_prepend(
            blocks, self.make_header_blocks(state, all_workflow_statuses)
        )

        return blocks

    def make_package_result_blocks(
        self, package_name: str, state: ReleaseState
    ) -> Optional[List[Union[Dict[str, Any], None]]]:
        """Create Slack blocks for package result.

        Args:
            package_name: The package name
            state: The ReleaseState to display

        Returns:
            List of Slack block dictionaries, or None if no result
        """
        blocks: List[Union[Dict[str, Any], None]] = []
        if package_name == "clientimage":
            self.blocks_append(blocks, self.make_clientimage_result_blocks(state))
        elif package_name == "redis-py":
            self.blocks_append(blocks, self.make_redispy_result_blocks(state))
        return blocks

    def make_clientimage_result_blocks(
        self, state: ReleaseState
    ) -> Optional[List[Union[Dict[str, Any], None]]]:

        blocks: List[Union[Dict[str, Any], None]] = []

        # Client image result
        clientimage_package = state.packages.get("clientimage")
        if clientimage_package is not None:
            result = clientimage_package.build.result
            if result is not None:
                client_test_image = result.get("client_test_image")
                if client_test_image:
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"```\n{client_test_image}\n```",
                            },
                        }
                    )

        return blocks

    def make_redispy_result_blocks(
        self, state: ReleaseState
    ) -> Optional[List[Union[Dict[str, Any], None]]]:
        blocks: List[Union[Dict[str, Any], None]] = []

        # Redis-py test results
        redispy_package = state.packages.get("redis-py")
        if redispy_package is not None:
            result = redispy_package.build.result
            workflow = redispy_package.build

            # Show result if workflow succeeded and we have results
            if result is not None:
                status = result.get("status", "unknown")
                redis_version = result.get("redis_version", "N/A")
                image_tag = result.get("client_test_image_tag", "N/A")
                python_version = result.get("python_version", "N/A")
                parser = result.get("parser_backend", "N/A")

                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"```\nRedis Version: {redis_version}\nImage Tag: {image_tag}\nPython: {python_version}\nParser: {parser}\n```",
                        },
                    }
                )
            # Show error message if workflow failed
            elif (
                workflow.conclusion == WorkflowConclusion.FAILURE
                and workflow.run_id is not None
            ):
                workflow_url = get_workflow_link(
                    redispy_package.meta.repo, workflow.run_id
                )
                if workflow_url:
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Redis-py Tests*\n:x: Test failed - <{workflow_url}|view logs>",
                            },
                        }
                    )

        return blocks

    def get_status_emoji(
        self, package: Package, workflow: Workflow
    ) -> Tuple[StepStatus, str]:
        """Get emoji status for a workflow.

        For build workflow of Homebrew/Snap packages, checks validation status first.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Emoji status string
        """
        display_model = get_display_model(package.meta)

        # Check workflow status
        workflow_status = display_model.get_workflow_status(package, workflow)
        return (workflow_status[0], self.get_step_status_emoji(workflow_status[0]))

    def get_status_icon(self, status: StepStatus) -> str:
        if status == StepStatus.SUCCEEDED:
            return "✅"
        elif status == StepStatus.RUNNING:
            return "⏳"
        elif status == StepStatus.FAILED:
            return "❌"
        elif status == StepStatus.NOT_STARTED:
            return "⚪"
        else:
            return "⚠️"

    def get_step_status_emoji(self, status: StepStatus) -> str:
        """Convert step status to emoji string.

        Args:
            status: The step status

        Returns:
            Emoji status string
        """
        if status == StepStatus.SUCCEEDED:
            return f"{self.get_status_icon(status)} Success"
        elif status == StepStatus.RUNNING:
            return f"{self.get_status_icon(status)} In progress"
        elif status == StepStatus.NOT_STARTED:
            return f"{self.get_status_icon(status)} Not started"
        elif status == StepStatus.INCORRECT:
            return f"️{self.get_status_icon(status)} Invalid state"
        else:  # FAILED
            return f"{self.get_status_icon(status)} Failed"

    def collect_workflow_details_slack(
        self,
        package: Package,
        workflow: Workflow,
        workflow_link: Optional[str],
        show_only_workflow_link: bool = False,
    ) -> str:
        """Collect workflow step details for Slack display.

        For build workflow of Homebrew/Snap packages, includes validation details.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check
            workflow_link: Optional link to the workflow run

        Returns:
            Formatted string of workflow steps
        """
        details: List[str] = []
        display_model = get_display_model(package.meta)

        workflow_status = display_model.get_workflow_status(package, workflow)
        # Add workflow details
        if workflow_status[0] != StepStatus.NOT_STARTED:
            if self.slack_format == SlackFormat.ONE_STEP:
                details.extend(
                    self.format_steps_one_step_format(
                        workflow_status[1], workflow_link, show_only_workflow_link
                    )
                )
            else:
                details.extend(
                    self.format_steps_for_slack(
                        workflow_status[1], workflow_link, show_only_workflow_link
                    )
                )

        return "\n".join(details)

    def format_steps_for_slack(
        self,
        steps: List[Union[Step, Section]],
        workflow_link: Optional[str],
        show_only_workflow_link: bool = False,
    ) -> List[str]:
        """Format step details for Slack display.

        The first item in the steps list should be a Section, which will be used as the header.

        Args:
            steps: List of Step and Section objects (first item should be Section)
            workflow_link: Optional link to the workflow run

        Returns:
            List of formatted step strings
        """
        details: List[str] = []

        for item in steps:
            if isinstance(item, Section):
                if item.is_workflow and workflow_link:
                    if show_only_workflow_link:
                        return [f"<{workflow_link}|*{item.name}*>"]
                    details.append(f"<{workflow_link}|*{item.name}*>")
                else:
                    details.append(f"*{item.name}*")
            elif isinstance(item, Step):
                if item.status == StepStatus.SUCCEEDED:
                    details.append(f"{self.get_status_icon(item.status)} {item.name}")
                elif item.status == StepStatus.RUNNING:
                    details.append(f"{self.get_status_icon(item.status)} {item.name}")
                elif item.status == StepStatus.NOT_STARTED:
                    details.append(f"{self.get_status_icon(item.status)} {item.name}")
                else:  # FAILED or INCORRECT
                    msg = f" ({item.message})" if item.message else ""
                    details.append(
                        f"{self.get_status_icon(item.status)} {item.name}{msg}"
                    )
                    break

        return details

    def format_steps_one_step_format(
        self,
        steps: List[Union[Step, Section]],
        workflow_link: Optional[str],
        show_only_workflow_link: bool = False,
    ) -> List[str]:
        """Format step details for Slack display in one-step format."""
        details: List[str] = []

        current_section = ""
        current_step = ""

        for item in steps:
            if isinstance(item, Section):
                if item.is_workflow and workflow_link:
                    current_section = f"<{workflow_link}|*{item.name}*>"
                else:
                    current_section = f"*{item.name}*"
            elif isinstance(item, Step):
                if item.status == StepStatus.SUCCEEDED:
                    pass
                else:
                    current_step = f"{self.get_status_icon(item.status)} {item.name}"
                    break

        details.append(f"{current_section} {current_step}")

        return details

    def aggregate_status(self, statuses: Set[StepStatus]) -> StepStatus:
        if StepStatus.RUNNING in statuses:
            return StepStatus.RUNNING
        elif StepStatus.FAILED in statuses:
            return StepStatus.FAILED
        elif StepStatus.SUCCEEDED in statuses:
            return StepStatus.SUCCEEDED
        return StepStatus.NOT_STARTED
