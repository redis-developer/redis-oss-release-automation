"""GitHub API client for workflow operations."""

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional
import requests

from rich.console import Console

from .models import WorkflowConclusion, WorkflowRun, WorkflowStatus

console = Console()


class GitHubClient:
    """GitHub API client for workflow operations."""

    def __init__(self, token: str, dry_run: bool = False):
        """Initialize GitHub client.

        Args:
            token: GitHub API token
            dry_run: If True, only simulate operations without making real API calls
        """
        self.token = token
        self.dry_run = dry_run
        self._mock_run_counter = 1000

    def trigger_workflow(
        self, repo: str, workflow_file: str, inputs: Dict[str, str], ref: str = "main"
    ) -> WorkflowRun:
        """Trigger a workflow in a repository.

        Args:
            repo: Repository name (e.g., "redis/docker-library-redis")
            workflow_file: Workflow file name (e.g., "build.yml")
            inputs: Workflow inputs
            ref: Git reference to run workflow on

        Returns:
            WorkflowRun object with run information
        """
        # Generate a unique UUID for this workflow run
        workflow_uuid = str(uuid.uuid4())

        console.print(f"[blue] Triggering workflow {workflow_file} in {repo}[/blue]")
        console.print(f"[dim] Inputs: {inputs}[/dim]")
        console.print(f"[dim] Ref: {ref}[/dim]")
        console.print(f"[dim] Workflow UUID: {workflow_uuid}[/dim]")

        if self.dry_run:
            console.print("[yellow]   (DRY RUN - not actually triggered)[/yellow]")
            # generate mock run_id even in dry-run for consistency
            run_id = self._mock_run_counter
            self._mock_run_counter += 1
            return WorkflowRun(
                repo=repo,
                workflow_id=workflow_file,
                workflow_uuid=workflow_uuid,
                run_id=run_id,
                status=WorkflowStatus.PENDING,
            )

        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Add the workflow UUID to inputs so it appears in the workflow run name
        enhanced_inputs = inputs.copy()
        enhanced_inputs["workflow_uuid"] = workflow_uuid

        payload = {"ref": ref, "inputs": enhanced_inputs}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()

            console.print(f"[green]Workflow triggered successfully[/green]")

            workflow_run = self._identify_workflow(repo, workflow_file, workflow_uuid)
            console.print(f"[dim]   Run ID: {workflow_run.run_id}[/dim]")
            console.print(
                f"[dim]   URL: https://github.com/{repo}/actions/runs/{workflow_run.run_id}[/dim]"
            )
            return workflow_run

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Failed to trigger workflow: {e}[/red]")
            raise

    def get_workflow_run(self, repo: str, run_id: int) -> WorkflowRun:
        """Get workflow run status.

        Args:
            repo: Repository name
            run_id: Workflow run ID

        Returns:
            Updated WorkflowRun object
        """
        if self.dry_run:
            return WorkflowRun(
                repo=repo,
                workflow_id="mock.yml",
                workflow_uuid=None,  # No UUID for mock runs
                run_id=run_id,
                status=WorkflowStatus.COMPLETED,
                conclusion=WorkflowConclusion.SUCCESS,
            )

        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()

            github_status = data.get("status", "unknown")
            if github_status == "queued":
                status = WorkflowStatus.QUEUED
            elif github_status == "in_progress":
                status = WorkflowStatus.IN_PROGRESS
            elif github_status == "completed":
                status = WorkflowStatus.COMPLETED
            else:
                status = WorkflowStatus.PENDING

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

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Failed to get workflow run: {e}[/red]")
            raise

    def wait_for_workflow_completion(
        self, repo: str, run_id: int, timeout_minutes: int = 30, poll_interval: int = 30
    ) -> WorkflowRun:
        """Wait for workflow to complete.

        Args:
            repo: Repository name
            run_id: Workflow run ID
            timeout_minutes: Maximum time to wait
            poll_interval: Seconds between status checks

        Returns:
            Final WorkflowRun object

        Raises:
            TimeoutError: If workflow doesn't complete within timeout
        """
        console.print(
            f"[blue] Waiting for workflow {run_id} in {repo} to complete...[/blue]"
        )

        start_time = time.time()
        timeout_seconds = timeout_minutes * 60

        while True:
            if run_id is None:
                raise ValueError("Cannot wait for workflow completion: run_id is None")

            workflow_run = self.get_workflow_run(repo, run_id)

            status_value = (
                workflow_run.status.value if workflow_run.status else "unknown"
            )
            console.print(f"[dim]   Status: {status_value}[/dim]")

            if workflow_run.status == WorkflowStatus.COMPLETED:
                if workflow_run.conclusion == WorkflowConclusion.SUCCESS:
                    console.print(
                        f"[green] Workflow {run_id} completed successfully[/green]"
                    )
                elif workflow_run.conclusion == WorkflowConclusion.FAILURE:
                    console.print(f"[red] Workflow {run_id} failed[/red]")
                else:
                    conclusion_value = (
                        workflow_run.conclusion.value
                        if workflow_run.conclusion
                        else "cancelled/skipped"
                    )
                    if conclusion_value in ["cancelled", "cancelled/skipped"]:
                        status_color = "yellow"
                    elif conclusion_value in ["skipped"]:
                        status_color = "blue"
                    else:
                        status_color = "red"

                    console.print(
                        f"[dim] Workflow {run_id} completed with status:[/dim] [{status_color}]{conclusion_value}[/{status_color}]"
                    )
                return workflow_run

            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                raise TimeoutError(
                    f"Workflow {run_id} in {repo} did not complete within {timeout_minutes} minutes"
                )

            if not self.dry_run:
                time.sleep(poll_interval)
            else:
                # in dry run, simulate quick completion
                time.sleep(0.1)
                return WorkflowRun(
                    repo=repo,
                    workflow_id="mock.yml",
                    workflow_uuid=None,  # No UUID for mock runs
                    run_id=run_id,
                    status=WorkflowStatus.COMPLETED,
                    conclusion=WorkflowConclusion.SUCCESS,
                )

    def get_workflow_artifacts(self, repo: str, run_id: int) -> Dict[str, Dict]:
        """Get artifacts from a completed workflow.

        Args:
            repo: Repository name
            run_id: Workflow run ID

        Returns:
            Dictionary with artifact names as keys and artifact details as values.
            Each artifact dictionary contains: id, archive_download_url, created_at,
            expires_at, updated_at, size_in_bytes, digest
        """
        console.print(f"[blue]Getting artifacts for workflow {run_id} in {repo}[/blue]")

        if self.dry_run:
            return {
                "release_handle": {
                    "id": 12345,
                    "archive_download_url": f"https://api.github.com/repos/{repo}/actions/artifacts/12345/zip",
                    "created_at": "2023-01-01T00:00:00Z",
                    "expires_at": "2023-01-31T00:00:00Z",
                    "updated_at": "2023-01-01T00:00:00Z",
                    "size_in_bytes": 1048576,
                    "digest": "sha256:mock-digest"
                },
                "release_info": {
                    "id": 67890,
                    "archive_download_url": f"https://api.github.com/repos/{repo}/actions/artifacts/67890/zip",
                    "created_at": "2023-01-01T00:00:00Z",
                    "expires_at": "2023-01-31T00:00:00Z",
                    "updated_at": "2023-01-01T00:00:00Z",
                    "size_in_bytes": 2097152,
                    "digest": "sha256:mock-digest-info"
                },
                "mock-artifact": {
                    "id": 11111,
                    "archive_download_url": f"https://api.github.com/repos/{repo}/actions/artifacts/11111/zip",
                    "created_at": "2023-01-01T00:00:00Z",
                    "expires_at": "2023-01-31T00:00:00Z",
                    "updated_at": "2023-01-01T00:00:00Z",
                    "size_in_bytes": 2048576,
                    "digest": "sha256:mock-digest-2"
                }
            }

        # Real GitHub API call to get artifacts
        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            artifacts = {}

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
                    "digest": artifact_data.get("workflow_run", {}).get("head_sha")  # Using head_sha as digest
                }

                artifacts[artifact_name] = artifact_info

            if artifacts:
                console.print(f"[green]Found {len(artifacts)} artifacts[/green]")
                for artifact_name, artifact_info in artifacts.items():
                    size_mb = round(artifact_info.get("size_in_bytes", 0) / (1024 * 1024), 2)
                    console.print(f"[dim]   {artifact_name} ({size_mb}MB) - ID: {artifact_info.get('id')}[/dim]")
            else:
                console.print(
                    "[yellow]No artifacts found for this workflow run[/yellow]"
                )

            return artifacts

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Failed to get artifacts: {e}[/red]")
            return {}

    def extract_result(self, repo: str, artifacts: Dict[str, Dict], artifact_name: str, json_file_name: str) -> Optional[Dict[str, Any]]:
        """Extract JSON result from artifacts.

        Args:
            repo: Repository name
            artifacts: Dictionary of artifacts from get_workflow_artifacts
            artifact_name: Name of the artifact to extract from
            json_file_name: Name of the JSON file within the artifact

        Returns:
            Parsed JSON content from the specified file, or None if not found
        """
        if artifact_name not in artifacts:
            console.print(f"[yellow]No {artifact_name} artifact found[/yellow]")
            return None

        target_artifact = artifacts[artifact_name]
        artifact_id = target_artifact.get("id")

        if not artifact_id:
            console.print(f"[red]{artifact_name} artifact has no ID[/red]")
            return None

        console.print(f"[blue]Extracting {json_file_name} from artifact {artifact_id}[/blue]")

        if self.dry_run:
            console.print(f"[yellow]   (DRY RUN - returning mock {json_file_name})[/yellow]")
            return {
                "mock": True,
                "version": "1.0.0",
                "build_info": {
                    "timestamp": "2023-01-01T00:00:00Z",
                    "commit": "mock-commit-hash"
                }
            }

        # Download the artifact and extract JSON file
        download_url = target_artifact.get("archive_download_url")
        if not download_url:
            console.print(f"[red]{artifact_name} artifact has no download URL[/red]")
            return None

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            # Download the artifact zip file
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()

            # Extract JSON file from the zip
            import zipfile
            import io

            with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
                if json_file_name in zip_file.namelist():
                    with zip_file.open(json_file_name) as json_file:
                        result_data = json.load(json_file)
                        console.print(f"[green]Successfully extracted {json_file_name}[/green]")
                        return result_data
                else:
                    console.print(f"[red]{json_file_name} not found in artifact[/red]")
                    return None

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Failed to download {artifact_name} artifact: {e}[/red]")
            return None
        except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as e:
            console.print(f"[red]Failed to extract {json_file_name}: {e}[/red]")
            return None

    def extract_release_handle(self, repo: str, artifacts: Dict[str, Dict]) -> Optional[Dict[str, Any]]:
        """Extract release_handle JSON from artifacts.

        This is a backward compatibility wrapper around extract_result.

        Args:
            repo: Repository name
            artifacts: Dictionary of artifacts from get_workflow_artifacts

        Returns:
            Parsed JSON content from release_handle.json file, or None if not found
        """
        return self.extract_result(repo, artifacts, "release_handle", "release_handle.json")

    def _get_recent_workflow_runs(
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
        if self.dry_run:
            return []

        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/runs"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        params = {"per_page": limit, "page": 1}

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            runs = []

            for run_data in data.get("workflow_runs", []):
                github_status = run_data.get("status", "unknown")
                if github_status == "queued":
                    status = WorkflowStatus.QUEUED
                elif github_status == "in_progress":
                    status = WorkflowStatus.IN_PROGRESS
                elif github_status == "completed":
                    status = WorkflowStatus.COMPLETED
                else:
                    status = WorkflowStatus.PENDING

                github_conclusion = run_data.get("conclusion")
                conclusion = None
                if github_conclusion == "success":
                    conclusion = WorkflowConclusion.SUCCESS
                elif github_conclusion == "failure":
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

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Failed to get workflow runs: {e}[/red]")
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

        uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
        uuid_match = re.search(uuid_pattern, text, re.IGNORECASE)
        return uuid_match.group() if uuid_match else None

    def _identify_workflow(
        self, repo: str, workflow_file: str, workflow_uuid: str, max_tries: int = 10
    ) -> WorkflowRun:
        """Identify a specific workflow run by UUID in its name.

        Args:
            repo: Repository name
            workflow_file: Workflow file name
            workflow_uuid: UUID to search for in workflow run names
            max_tries: Maximum number of attempts to find the workflow

        Returns:
            WorkflowRun object with matching UUID

        Raises:
            RuntimeError: If workflow run cannot be found after max_tries
        """
        console.print(f"[blue]Searching for workflow run with UUID: {workflow_uuid}[/blue]")

        for attempt in range(max_tries):
            time.sleep(2)
            if attempt > 0:
                console.print(f"[dim]  Attempt {attempt + 1}/{max_tries}[/dim]")

            runs = self._get_recent_workflow_runs(repo, workflow_file, limit=20)

            for run in runs:
                extracted_uuid = self._extract_uuid(run.workflow_id)
                if extracted_uuid and extracted_uuid.lower() == workflow_uuid.lower():
                    console.print(f"[green]Found matching workflow run: {run.run_id}[/green]")
                    console.print(f"[dim]  Workflow name: {run.workflow_id}[/dim]")
                    console.print(f"[dim]  Extracted UUID: {extracted_uuid}[/dim]")
                    run.workflow_uuid = workflow_uuid
                    return run

            console.print("[dim]  No matching workflow found, trying again...[/dim]")


        raise RuntimeError(
            f"Could not find workflow run with UUID {workflow_uuid} after {max_tries} attempts. "
            f"The workflow may have failed to start or there may be a delay in GitHub's API."
        )

    def check_workflow_exists(self, repo: str, workflow_file: str) -> bool:
        """Check if a workflow file exists and is accessible.

        Args:
            repo: Repository name
            workflow_file: Workflow file name

        Returns:
            True if workflow exists and is accessible
        """
        if self.dry_run:
            return True

        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                workflow_data = response.json()
                console.print(f"[green]✓ Workflow '{workflow_file}' found[/green]")
                console.print(
                    f"[dim]  Name: {workflow_data.get('name', 'Unknown')}[/dim]"
                )
                console.print(
                    f"[dim]  State: {workflow_data.get('state', 'Unknown')}[/dim]"
                )
                return True
            elif response.status_code == 404:
                console.print(
                    f"[red]✗ Workflow '{workflow_file}' not found in {repo}[/red]"
                )
                return False
            else:
                console.print(
                    f"[yellow]? Cannot check workflow: HTTP {response.status_code}[/yellow]"
                )
                return False

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error checking workflow: {e}[/red]")
            return False

    def get_tag_commit(self, repo: str, tag: str) -> Optional[str]:
        """Get commit hash for a specific tag.

        Args:
            repo: Repository name (e.g., "redis/redis")
            tag: Tag name (e.g., "8.2.1")

        Returns:
            Commit hash or None if not found
        """
        if self.dry_run:
            return f"mock-commit-{tag}"


        url = f"https://api.github.com/repos/{repo}/tags"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # only add auth for non-public repos or if we're accessing our own repos
        if not repo.startswith("redis/"):
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()

                for tag_data in data:
                    if tag_data.get("name") == tag:
                        commit_sha = tag_data.get("commit", {}).get("sha")
                        if commit_sha:
                            return commit_sha

                console.print(f"[red]Tag '{tag}' not found in {repo}[/red]")
                console.print(
                    f"[dim]Available tags: https://github.com/{repo}/tags[/dim]"
                )
                return None

            elif response.status_code == 404:
                console.print(f"[red]Repository '{repo}' not found[/red]")
                return None
            else:
                console.print(
                    f"[yellow]Could not check tags in {repo}: HTTP {response.status_code}[/yellow]"
                )
                return None

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error getting tag commit: {e}[/red]")
            return None

    def get_branch_latest_commit(self, repo: str, branch: str) -> Optional[str]:
        """Get latest commit hash from a branch.

        Args:
            repo: Repository name
            branch: Branch name

        Returns:
            Commit hash or None if not found
        """
        if self.dry_run:
            return f"mock-commit-{branch}"

        url = f"https://api.github.com/repos/{repo}/git/refs/heads/{branch}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get("object", {}).get("sha")
            else:
                console.print(f"[yellow]Branch '{branch}' not found in {repo}[/yellow]")
                return None

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error getting branch commit: {e}[/red]")
            return None
