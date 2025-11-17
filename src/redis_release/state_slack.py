"""Slack display utilities for release state."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from redis_release.state_display import DisplayModel, StepStatus

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
    slack_token: Optional[str], slack_channel_id: Optional[str]
) -> "SlackStatePrinter":
    """Initialize SlackStatePrinter with validation.

    Args:
        slack_token: Slack bot token (if None, uses SLACK_BOT_TOKEN env var)
        slack_channel_id: Slack channel ID to post to

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

    return SlackStatePrinter(token, slack_channel_id)


class SlackStatePrinter:
    """Handles posting and updating release state to Slack channel."""

    def __init__(self, slack_token: str, slack_channel_id: str):
        """Initialize the Slack printer.

        Args:
            slack_token: Slack bot token
            slack_channel_id: Slack channel ID to post messages to
        """
        self.client = WebClient(token=slack_token)
        self.channel_id = slack_channel_id
        self.message_ts: Optional[str] = None
        self.last_blocks_json: Optional[str] = None
        self.started_at = datetime.now(timezone.utc)

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
                response = self.client.chat_postMessage(
                    channel=self.channel_id,
                    text=text,
                    blocks=blocks,
                )
                self.message_ts = response["ts"]
                # Update channel_id from response (authoritative)
                self.channel_id = response["channel"]
                logger.info(f"Posted Slack message ts={self.message_ts}")
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

        # Show started date (when SlackStatePrinter was created)
        started_str = self.started_at.strftime("%Y-%m-%d %H:%M:%S %Z")
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

        # Legend with two columns
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
            # Get workflow statuses
            build_status_emoji = self._get_status_emoji(package, package.build)
            publish_status_emoji = self._get_status_emoji(package, package.publish)

            # Package section
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{package_name}*\n*Build:* {build_status_emoji}   |   *Publish:* {publish_status_emoji}",
                    },
                }
            )

            # Workflow details in context
            build_details = self._collect_workflow_details_slack(package, package.build)
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
                if publish_details:
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

    def _get_status_emoji(self, package: Package, workflow: Workflow) -> str:
        """Get emoji status for a workflow.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Emoji status string
        """
        workflow_status = DisplayModel.get_workflow_status(package, workflow)
        status = workflow_status[0]

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

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Formatted string of workflow steps
        """
        workflow_status = DisplayModel.get_workflow_status(package, workflow)
        if workflow_status[0] == StepStatus.NOT_STARTED:
            return ""

        details: List[str] = []

        for step_status, step_name, step_message in workflow_status[1]:
            if step_status == StepStatus.SUCCEEDED:
                details.append(f"• ✅ {step_name}")
            elif step_status == StepStatus.RUNNING:
                details.append(f"• ⏳ {step_name}")
            elif step_status == StepStatus.NOT_STARTED:
                details.append(f"• ⚪ {step_name}")
            else:  # FAILED or INCORRECT
                msg = f" ({step_message})" if step_message else ""
                details.append(f"• ❌ {step_name}{msg}")
                break

        return "\n".join(details)
