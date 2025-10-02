"""Async GitHub API client for workflow operations."""

import asyncio
import logging
import re
from typing import Dict, List, Optional

import aiohttp

from .models import WorkflowConclusion, WorkflowRun, WorkflowStatus

# Get logger for this module
logger = logging.getLogger(__name__)


class GitHubClientAsync:
    """Async GitHub API client for workflow operations."""

    def __init__(self, token: str):
        """Initialize async GitHub client.

        Args:
            token: GitHub API token
        """
        self.token = token

    async def trigger_workflow(
        self, repo: str, workflow_file: str, inputs: Dict[str, str], ref: str = "main"
    ) -> bool:
        """Trigger a workflow in a repository.

        Args:
            repo: Repository name (e.g., "redis/docker-library-redis")
            workflow_file: Workflow file name (e.g., "build.yml")
            inputs: Workflow inputs
            ref: Git reference to run workflow on

        Returns:
            WorkflowRun object with basic information (workflow identification will be done separately)
        """
        logger.info(f"[blue]Triggering workflow[/blue] {workflow_file} in {repo}")
        logger.debug(f"Inputs: {inputs}")
        logger.debug(f"Ref: {ref}")
        logger.debug(f"Workflow UUID: [cyan]{inputs['workflow_uuid']}[/cyan]")

        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Add the workflow UUID to inputs so it appears in the workflow run name
        enhanced_inputs = inputs.copy()

        payload = {"ref": ref, "inputs": enhanced_inputs}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status >= 400:
                        # Read response body for error details
                        try:
                            error_body = await response.text()
                            logger.error(
                                f"[red]Failed to trigger workflow:[/red] HTTP {response.status}"
                            )
                            logger.error(f"[red]Response body:[/red] {error_body}")
                        except Exception:
                            logger.error(
                                f"[red]Failed to trigger workflow:[/red] HTTP {response.status}"
                            )
                        response.raise_for_status()

                    logger.info(f"[green]Workflow triggered successfully[/green]")

                    return True
        except aiohttp.ClientError as e:
            logger.error(f"[red]Failed to trigger workflow:[/red] {e}")
            raise

        return False

    async def identify_workflow(
        self, repo: str, workflow_file: str, workflow_uuid: str
    ) -> WorkflowRun | None:

        logger.debug(
            f"[blue]Searching for workflow run with UUID:[/blue] [cyan]{workflow_uuid}[/cyan]"
        )
        runs = await self.get_recent_workflow_runs(repo, workflow_file, limit=20)

        for run in runs:
            extracted_uuid = self._extract_uuid(run.workflow_id)
            if extracted_uuid and extracted_uuid.lower() == workflow_uuid.lower():
                logger.info(f"[green]Found matching workflow run:[/green] {run.run_id}")
                logger.debug(f"Workflow name: {run.workflow_id}")
                logger.debug(f"Extracted UUID: {extracted_uuid}")
                run.workflow_uuid = workflow_uuid
                return run
        return None

    async def get_workflow_run(self, repo: str, run_id: int) -> WorkflowRun:
        """Get workflow run status.

        Args:
            repo: Repository name
            run_id: Workflow run ID

        Returns:
            Updated WorkflowRun object
        """
        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status >= 400:
                        # Read response body for error details
                        try:
                            error_body = await response.text()
                            logger.error(
                                f"[red]Failed to get workflow run:[/red] HTTP {response.status}"
                            )
                            logger.error(f"[red]Response body:[/red] {error_body}")
                        except Exception:
                            logger.error(
                                f"[red]Failed to get workflow run:[/red] HTTP {response.status}"
                            )
                        response.raise_for_status()

                    data = await response.json()

            # Map GitHub API status to our enum
            github_status = data.get("status", "unknown")
            if github_status == "queued":
                status = WorkflowStatus.QUEUED
            elif github_status == "in_progress":
                status = WorkflowStatus.IN_PROGRESS
            elif github_status == "completed":
                status = WorkflowStatus.COMPLETED
            else:
                status = WorkflowStatus.PENDING

            # Map GitHub API conclusion to our enum
            github_conclusion = data.get("conclusion")
            conclusion = None
            if github_conclusion == "success":
                conclusion = WorkflowConclusion.SUCCESS
            elif github_conclusion == "failure":
                conclusion = WorkflowConclusion.FAILURE

            workflow_name = data.get("name", "unknown")
            workflow_uuid = self._extract_uuid(workflow_name)

            return WorkflowRun(
                repo=repo,
                workflow_id=workflow_name,
                workflow_uuid=workflow_uuid,
                run_id=data.get("id"),
                status=status,
                conclusion=conclusion,
            )

        except aiohttp.ClientError as e:
            logger.error(f"[red]Failed to get workflow run:[/red] {e}")
            raise

    async def get_recent_workflow_runs(
        self, repo: str, workflow_file: str, limit: int = 10
    ) -> List[WorkflowRun]:
        """Get recent workflow runs for a specific workflow.

        Args:
            repo: Repository name
            workflow_file: Workflow file name
            limit: Maximum number of runs to return

        Returns:
            List of WorkflowRun objects, sorted by creation time (newest first)
        """
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/runs"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        params = {"per_page": limit, "page": 1}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status >= 400:
                        # Read response body for error details
                        try:
                            error_body = await response.text()
                            logger.error(
                                f"[red]Failed to get workflow runs:[/red] HTTP {response.status}"
                            )
                            logger.error(f"[red]Response body:[/red] {error_body}")
                        except Exception:
                            logger.error(
                                f"[red]Failed to get workflow runs:[/red] HTTP {response.status}"
                            )
                        response.raise_for_status()

                    data = await response.json()

            runs = []
            for run_data in data.get("workflow_runs", []):
                # Map GitHub API status to our enum
                api_status = run_data.get("status", "unknown").lower()
                if api_status == "queued":
                    status = WorkflowStatus.QUEUED
                elif api_status == "in_progress":
                    status = WorkflowStatus.IN_PROGRESS
                elif api_status == "completed":
                    status = WorkflowStatus.COMPLETED
                else:
                    status = WorkflowStatus.PENDING

                # Map GitHub API conclusion to our enum
                api_conclusion = run_data.get("conclusion")
                conclusion = None
                if api_conclusion == "success":
                    conclusion = WorkflowConclusion.SUCCESS
                elif api_conclusion == "failure":
                    conclusion = WorkflowConclusion.FAILURE

                workflow_name = run_data.get("name", workflow_file)
                workflow_uuid = self._extract_uuid(workflow_name)

                runs.append(
                    WorkflowRun(
                        repo=repo,
                        workflow_id=workflow_name,
                        workflow_uuid=workflow_uuid,
                        run_id=run_data.get("id"),
                        status=status,
                        conclusion=conclusion,
                    )
                )

            return runs

        except aiohttp.ClientError as e:
            logger.error(f"[red]Failed to get workflow runs:[/red] {e}")
            return []

    def _extract_uuid(self, text: str) -> Optional[str]:
        """Extract UUID from a string if present.

        Args:
            text: String to search for UUID pattern

        Returns:
            UUID string if found, None otherwise
        """
        if not text:
            return None

        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        uuid_match = re.search(uuid_pattern, text, re.IGNORECASE)
        return uuid_match.group() if uuid_match else None
