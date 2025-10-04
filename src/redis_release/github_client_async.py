"""Async GitHub API client for workflow operations."""

import logging
import re
from typing import Any, Dict, List, Optional, Union

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

    async def github_request(
        self,
        url: str,
        headers: Dict[str, str],
        method: str = "GET",
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
        error_context: str = "request",
    ) -> Dict[str, Any]:
        """Make a single GitHub API request with error handling.

        Args:
            url: The API URL to fetch
            headers: HTTP headers to include in the request
            method: HTTP method (GET, POST, PATCH, PUT, DELETE)
            json: JSON payload for POST/PATCH/PUT requests
            params: Query parameters
            timeout: Request timeout in seconds
            error_context: Context string for error messages (e.g., "trigger workflow", "get workflow run")

        Returns:
            JSON response as a dictionary

        Raises:
            aiohttp.ClientError: On HTTP errors
        """
        async with aiohttp.ClientSession() as session:
            request_method = getattr(session, method.lower())

            kwargs = {
                "headers": headers,
                "timeout": aiohttp.ClientTimeout(total=timeout),
            }

            if params is not None:
                kwargs["params"] = params

            if json is not None:
                kwargs["json"] = json

            async with request_method(url, **kwargs) as response:
                if response.status >= 400:
                    # Read response body for error details
                    try:
                        error_body = await response.text()
                        logger.error(
                            f"[red]Failed to {error_context}:[/red] HTTP {response.status}"
                        )
                        logger.error(f"[red]Response body:[/red] {error_body}")
                    except Exception:
                        logger.error(
                            f"[red]Failed to {error_context}:[/red] HTTP {response.status}"
                        )
                    response.raise_for_status()

                # For methods that may not return content (like POST to workflow dispatch)
                if response.status == 204 or not response.content_length:
                    return {}

                return await response.json()

    async def github_request_paginated(
        self,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
        merge_key: Optional[str] = None,
        per_page: int = 100,
        max_pages: Optional[int] = None,
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """Get paginated results from a GitHub API URL.

        Follows GitHub's pagination using Link headers as described in:
        https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api

        Args:
            url: The API URL to fetch
            headers: HTTP headers to include in the request
            params: Query parameters (per_page will be added/overridden)
            timeout: Request timeout in seconds
            merge_key: Key to merge results from dict responses (e.g., "artifacts", "workflow_runs")
            per_page: Number of items per page (default: 100)
            max_pages: Maximum number of pages to fetch (None = all pages)

        Returns:
            - If response is a list: merged list of all items from all pages
            - If response is a dict: merged dict with merge_key items combined and other fields from last page

        Raises:
            ValueError: If response is dict but merge_key is not provided or not found in response
            aiohttp.ClientError: On HTTP errors
        """
        if params is None:
            params = {}

        params["per_page"] = per_page
        params["page"] = 1

        all_results: List[Dict[str, Any]] = []
        merged_dict: Optional[Dict[str, Any]] = None
        pages_fetched = 0
        link_header = ""

        async with aiohttp.ClientSession() as session:
            while True:
                if max_pages and pages_fetched >= max_pages:
                    break

                request_method = getattr(session, "get")

                kwargs = {
                    "headers": headers,
                    "params": params,
                    "timeout": aiohttp.ClientTimeout(total=timeout),
                }

                async with request_method(url, **kwargs) as response:
                    if response.status >= 400:
                        try:
                            error_body = await response.text()
                            logger.error(
                                f"[red]Failed to fetch paginated URL:[/red] HTTP {response.status}"
                            )
                            logger.error(f"[red]Response body:[/red] {error_body}")
                        except Exception:
                            logger.error(
                                f"[red]Failed to fetch paginated URL:[/red] HTTP {response.status}"
                            )
                        response.raise_for_status()

                    data = await response.json()
                    pages_fetched += 1

                    # Handle list responses
                    if isinstance(data, list):
                        all_results.extend(data)
                    # Handle dict responses
                    elif isinstance(data, dict):
                        if merge_key is None:
                            raise ValueError(
                                "merge_key is required when API returns a dictionary"
                            )
                        if merge_key not in data:
                            raise ValueError(
                                f"merge_key '{merge_key}' not found in response"
                            )

                        # Initialize merged_dict on first page
                        if merged_dict is None:
                            merged_dict = data.copy()
                        else:
                            # Merge the items from merge_key
                            merged_dict[merge_key].extend(data[merge_key])
                            # Update other fields from the latest page
                            for key, value in data.items():
                                if key != merge_key:
                                    merged_dict[key] = value

                    # Check for Link header to determine if there are more pages
                    link_header = response.headers.get("Link", "")
                    if not link_header or 'rel="next"' not in link_header:
                        break

                    # Increment page number for next request
                    params["page"] = params.get("page", 1) + 1

        # Return appropriate result type
        if isinstance(all_results, list) and len(all_results) > 0:
            return all_results
        elif merged_dict is not None:
            return merged_dict
        else:
            return []

    def _extract_next_url(self, link_header: str) -> Optional[str]:
        """Extract the 'next' URL from a GitHub Link header.

        Args:
            link_header: The Link header value

        Returns:
            The next page URL if found, None otherwise
        """
        # Link header format: <url>; rel="next", <url>; rel="last"
        links = link_header.split(",")
        for link in links:
            if 'rel="next"' in link:
                # Extract URL from <url>
                url_match = re.search(r"<([^>]+)>", link)
                if url_match:
                    return url_match.group(1)
        return None

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
            await self.github_request(
                url=url,
                headers=headers,
                method="POST",
                json=payload,
                timeout=30,
                error_context="trigger workflow",
            )
            logger.info(f"[green]Workflow triggered successfully[/green]")
            return True
        except aiohttp.ClientError as e:
            logger.error(f"[red]Failed to trigger workflow:[/red] {e}")
            raise

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
            data = await self.github_request(
                url=url,
                headers=headers,
                method="GET",
                timeout=30,
                error_context="get workflow run",
            )

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
            data = await self.github_request(
                url=url,
                headers=headers,
                method="GET",
                params=params,
                timeout=30,
                error_context="get workflow runs",
            )

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

    async def get_workflow_artifacts(self, repo: str, run_id: int) -> Dict[str, Dict]:
        """Get artifacts from a completed workflow.

        Args:
            repo: Repository name
            run_id: Workflow run ID

        Returns:
            Dictionary with artifact names as keys and artifact details as values.
            Each artifact dictionary contains: id, archive_download_url, created_at,
            expires_at, updated_at, size_in_bytes, digest
        """
        logger.info(f"[blue]Getting artifacts for workflow {run_id} in {repo}[/blue]")

        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            data = await self.github_request_paginated(
                url=url,
                headers=headers,
                params={},
                timeout=30,
                merge_key="artifacts",
                per_page=100,
                max_pages=None,
            )

            artifacts = {}

            # data is a dict with "artifacts" key containing the merged list
            if not isinstance(data, dict):
                logger.error("[red]Unexpected response type from API[/red]")
                return {}

            for artifact_data in data.get("artifacts", []):
                artifact_name = artifact_data.get("name", "unknown")

                # Extract the required fields from the GitHub API response
                artifact_info = {
                    "id": artifact_data.get("id"),
                    "archive_download_url": artifact_data.get("archive_download_url"),
                    "created_at": artifact_data.get("created_at"),
                    "expires_at": artifact_data.get("expires_at"),
                    "updated_at": artifact_data.get("updated_at"),
                    "size_in_bytes": artifact_data.get("size_in_bytes"),
                    "digest": artifact_data.get("workflow_run", {}).get(
                        "head_sha"
                    ),  # Using head_sha as digest
                }

                artifacts[artifact_name] = artifact_info

            if artifacts:
                logger.info(f"[green]Found {len(artifacts)} artifacts[/green]")
                for artifact_name, artifact_info in artifacts.items():
                    size_mb = round(
                        artifact_info.get("size_in_bytes", 0) / (1024 * 1024), 2
                    )
                    logger.debug(
                        f"   {artifact_name} ({size_mb}MB) - ID: {artifact_info.get('id')}"
                    )
            else:
                logger.warning(
                    "[yellow]No artifacts found for this workflow run[/yellow]"
                )

            return artifacts

        except aiohttp.ClientError as e:
            logger.error(f"[red]Failed to get artifacts: {e}[/red]")
            return {}
        except ValueError as e:
            logger.error(f"[red]Failed to get artifacts: {e}[/red]")
            return {}

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
