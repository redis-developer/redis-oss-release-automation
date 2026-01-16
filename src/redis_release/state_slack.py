"""Slack display utilities for release state."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from redis_release.models import SlackFormat
from redis_release.state_display import Section, Step, StepStatus, get_display_model

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
) -> "SlackStatePrinter":
    """Initialize SlackStatePrinter with validation.

    Args:
        slack_token: Slack bot token (if None, uses SLACK_BOT_TOKEN env var)
        slack_channel_id: Slack channel ID to post to
        thread_ts: Optional thread timestamp to post messages in a thread
        reply_broadcast: If True and thread_ts is set, also show in main channel
        slack_format: Slack message format (default or one-step)

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

    return SlackStatePrinter(
        token, slack_channel_id, thread_ts, reply_broadcast, slack_format
    )


class SlackStatePrinter:
    """Handles posting and updating release state to Slack channel."""

    def __init__(
        self,
        slack_token: str,
        slack_channel_id: str,
        thread_ts: Optional[str] = None,
        reply_broadcast: bool = False,
        slack_format: SlackFormat = SlackFormat.DEFAULT,
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
        blocks: List[Dict[str, Any]] = []

        # Header
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Release {state.meta.tag or 'N/A'} — Status",
                },
            }
        )

        # Show started date from state.meta.last_started_at if available
        if state.meta.last_started_at:
            started_str = state.meta.last_started_at.strftime("%Y-%m-%d %H:%M:%S %Z")
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Started:* {started_str}",
                        }
                    ],
                }
            )

        # Legend with two columns (skip to reduce visual noise)
        if False:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "✅ Success\n❌ Failed",
                        },
                        {
                            "type": "mrkdwn",
                            "text": "⏳ In progress\n⚪ Not started",
                        },
                    ],
                }
            )

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
            build_details = self._collect_workflow_details_slack(package, package.build)
            publish_details = ""
            if package.publish is not None:
                publish_details = self._collect_workflow_details_slack(
                    package, package.publish
                )

            if build_details or publish_details:
                elements = []
                if build_details:
                    # Create link for Build Workflow if run_id exists
                    build_link = get_workflow_link(
                        package.meta.repo, package.build.run_id
                    )
                    build_title = (
                        f"<{build_link}|*Build Workflow*>"
                        if build_link
                        else "*Build Workflow*"
                    )
                    elements.append(
                        {"type": "mrkdwn", "text": f"{build_title}\n{build_details}"}
                    )
                if package.publish is not None and publish_details:
                    # Create link for Publish Workflow if run_id exists
                    publish_link = get_workflow_link(
                        package.meta.repo, package.publish.run_id
                    )
                    publish_title = (
                        f"<{publish_link}|*Publish Workflow*>"
                        if publish_link
                        else "*Publish Workflow*"
                    )
                    elements.append(
                        {
                            "type": "mrkdwn",
                            "text": f"{publish_title}\n{publish_details}",
                        }
                    )
                blocks.append({"type": "context", "elements": elements})

            blocks.append({"type": "divider"})

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
        self, package: Package, workflow: Workflow
    ) -> str:
        """Collect workflow step details for Slack display.

        For build workflow of Homebrew/Snap packages, includes validation details.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Formatted string of workflow steps
        """
        details: List[str] = []
        display_model = get_display_model(package.meta)

        workflow_status = display_model.get_workflow_status(package, workflow)
        # Add workflow details
        if workflow_status[0] != StepStatus.NOT_STARTED:
            details.extend(self._format_steps_for_slack(workflow_status[1]))

        if self.slack_format == SlackFormat.ONE_STEP:
            details = details[-1:]

        return "\n".join(details)

    def _format_steps_for_slack(self, steps: List[Union[Step, Section]]) -> List[str]:
        """Format step details for Slack display.

        The first item in the steps list should be a Section, which will be used as the header.

        Args:
            steps: List of Step and Section objects (first item should be Section)

        Returns:
            List of formatted step strings
        """
        details: List[str] = []

        for item in steps:
            if isinstance(item, Section):
                # Section can be used as header if needed
                # details.append(f"*{item.name}*")
                pass
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
