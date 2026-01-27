"""Slack display utilities for release state."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from click import Option
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from redis_release.models import SlackFormat
from redis_release.state_display import (
    DisplayModelGeneric,
    Section,
    Step,
    StepStatus,
    get_display_model,
)

from .bht.state import Package, ReleaseState, Workflow

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

        # Add release type if available
        if package.meta.release_type:
            release_type_str = package.meta.release_type.value
            formatted = f"{formatted} ({release_type_str})"

        return formatted

    def _blocks_append(
        self,
        blocks: List[Dict[str, Any]],
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

    def _make_header_blocks(self, state: ReleaseState) -> List[Dict[str, Any]]:
        """Create header blocks for Slack message.

        Args:
            state: The ReleaseState to display

        Returns:
            List of header block dictionaries
        """
        blocks: List[Dict[str, Any]] = []

        # Header - use "Custom Build" if is_custom_build, otherwise "Release"
        header_prefix = "Custom Build" if state.meta.is_custom_build else "Release"
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{header_prefix} {state.meta.tag or 'N/A'} — Status",
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
        self._blocks_append(blocks, [state_name_block])

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
        self._blocks_append(blocks, [dates_block])

        return blocks

    def _make_custom_build_blocks(
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
        blocks: List[Dict[str, Any]] = []

        display_model = DisplayModelGeneric()
        custom_versions = display_model.get_custom_versions(state)

        if not custom_versions:
            return blocks

        # Format custom versions as a list
        version_lines = [
            f"• *{name}:* {version}" for name, version in custom_versions.items()
        ]
        versions_text = "\n".join(version_lines)

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Custom Versions*\n{versions_text}",
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
        blocks = self._make_blocks(state)
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

    def _make_blocks(self, state: ReleaseState) -> List[Dict[str, Any]]:
        """Create Slack blocks for the release state.

        Args:
            state: The ReleaseState to display

        Returns:
            List of Slack block dictionaries
        """
        blocks: List[Dict[str, Any]] = self._make_header_blocks(state)

        # Add custom build info if applicable
        self._blocks_append(blocks, self._make_custom_build_blocks(state))

        blocks.append({"type": "divider"})

        # Process each package
        for package_name, package in sorted(state.packages.items()):
            # Format package name with capital letter and release type
            formatted_name = self.format_package_name(package_name, package)

            # Get workflow statuses
            build_status, build_status_emoji = self._get_status_emoji(
                package, package.build
            )
            publish_status = StepStatus.NOT_STARTED
            publish_status_emoji = ""
            if package.publish is not None:
                publish_status, publish_status_emoji = self._get_status_emoji(
                    package, package.publish
                )

            # skip if both build and publish are not started
            if (
                build_status == StepStatus.NOT_STARTED
                and publish_status == StepStatus.NOT_STARTED
            ):
                continue

            # Package section
            build_with_emoji = f"*Build:* {build_status_emoji}"
            publish_with_emoji = ""
            if package.publish is not None:
                publish_with_emoji = f"*Publish:* {publish_status_emoji}"
            header_with_emojis = "  |  ".join(
                filter(bool, [build_with_emoji, publish_with_emoji])
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{formatted_name}*\n{header_with_emojis}",
                    },
                }
            )

            # Workflow details in context
            build_link = get_workflow_link(package.meta.repo, package.build.run_id)
            build_details = self._collect_workflow_details_slack(
                package, package.build, build_link
            )
            publish_details = ""
            if package.publish is not None:
                publish_link = get_workflow_link(
                    package.meta.repo, package.publish.run_id
                )
                publish_details = self._collect_workflow_details_slack(
                    package, package.publish, publish_link
                )

            if build_details or publish_details:
                elements = []
                if build_details:
                    elements.append({"type": "mrkdwn", "text": build_details})
                if package.publish is not None and publish_details:
                    elements.append({"type": "mrkdwn", "text": publish_details})
                blocks.append({"type": "context", "elements": elements})

            blocks.append({"type": "divider"})

        # Add results section
        result_blocks = self._make_result_blocks(state)
        if result_blocks:
            blocks.extend(result_blocks)

        return blocks

    def _make_result_blocks(self, state: ReleaseState) -> List[Dict[str, Any]]:
        """Create Slack blocks for results section.

        Args:
            state: The ReleaseState to display

        Returns:
            List of Slack block dictionaries for results
        """
        blocks: List[Dict[str, Any]] = []

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
                                "text": f"*Client Image*\n```\n{client_test_image}\n```",
                            },
                        }
                    )

        return blocks

    def make_clientimage_result_blocks(
        self, state: ReleaseState
    ) -> Optional[List[Dict[str, Any]]]:
        """Create Slack blocks for client image build result.

        Args:
            state: The ReleaseState to display

        Returns:
            List of Slack block dictionaries, or None if no clientimage result
        """
        clientimage_package = state.packages.get("clientimage")
        if clientimage_package is None:
            return None

        result = clientimage_package.build.result
        if result is None:
            return None

        client_test_image = result.get("client_test_image")
        if not client_test_image:
            return None

        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🧪 Client Image Published",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```\n{client_test_image}\n```",
                },
            },
        ]

        return blocks

    def _get_status_emoji(
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
        return (workflow_status[0], self._get_step_status_emoji(workflow_status[0]))

    def _get_step_status_emoji(self, status: StepStatus) -> str:
        """Convert step status to emoji string.

        Args:
            status: The step status

        Returns:
            Emoji status string
        """
        if status == StepStatus.SUCCEEDED:
            return "✅ Success"
        elif status == StepStatus.RUNNING:
            return "⏳ In progress"
        elif status == StepStatus.NOT_STARTED:
            return "⚪ Not started"
        elif status == StepStatus.INCORRECT:
            return "⚠️ Invalid state"
        else:  # FAILED
            return "❌ Failed"

    def _collect_workflow_details_slack(
        self, package: Package, workflow: Workflow, workflow_link: Optional[str]
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
            details.extend(
                self._format_steps_for_slack(workflow_status[1], workflow_link)
            )

        if self.slack_format == SlackFormat.ONE_STEP:
            details = details[-1:]

        return "\n".join(details)

    def _format_steps_for_slack(
        self, steps: List[Union[Step, Section]], workflow_link: Optional[str]
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
                    details.append(f"<{workflow_link}|*{item.name}*>")
                else:
                    details.append(f"*{item.name}*")
            elif isinstance(item, Step):
                if item.status == StepStatus.SUCCEEDED:
                    details.append(f"• ✅ {item.name}")
                elif item.status == StepStatus.RUNNING:
                    details.append(f"• ⏳ {item.name}")
                elif item.status == StepStatus.NOT_STARTED:
                    details.append(f"• ⚪ {item.name}")
                else:  # FAILED or INCORRECT
                    msg = f" ({item.message})" if item.message else ""
                    details.append(f"• ❌ {item.name}{msg}")
                    break

        return details
