"""
Actions and Conditions for the Release Tree

Here we define only simple atomic actions and conditions.
Next level composites are defined in `composites.py`.

The guiding principles are:

* Actions should be atomic and represent a single task.
* Actions should unconditionally perform their job. This simplifies reuse, as any conditions can be applied separately.
* Conditions should not have side effects (e.g., modifying state).
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from py import log
from py_trees.behaviour import Behaviour
from py_trees.common import Status

from redis_release.bht.state import reset_model_to_defaults

from ..github_client_async import GitHubClientAsync
from ..models import RedisVersion, ReleaseType, WorkflowConclusion, WorkflowStatus
from .logging_wrapper import PyTreesLoggerWrapper
from .state import Package, PackageMeta, ReleaseMeta, Workflow

logger = logging.getLogger(__name__)


class LoggingAction(Behaviour):
    logger: PyTreesLoggerWrapper

    def __init__(self, name: str, log_prefix: str = "") -> None:
        if name == "":
            name = f"{self.__class__.__name__}"
        super().__init__(name=name)
        if log_prefix != "":
            log_prefix = f"{log_prefix}."
        self.logger = PyTreesLoggerWrapper(
            logging.getLogger(f"{log_prefix}{self.name}")
        )

    def log_exception_and_return_failure(self, e: Exception) -> Status:
        self.logger.error(f"[red]failed with exception:[/red] {type(e).__name__}: {e}")
        # use the underlying logger to get the full traceback
        self.logger._logger.error(f"[red]Full traceback:[/red]", exc_info=True)
        return Status.FAILURE

    def log_once(self, key: str, container: Dict[str, bool]) -> bool:
        if key not in container:
            container[key] = True
            return True
        return False


class ReleaseAction(LoggingAction):
    task: Optional[asyncio.Task[Any]] = None

    def __init__(self, name: str, log_prefix: str = "") -> None:
        super().__init__(name=name, log_prefix=log_prefix)

    def check_task_exists(self) -> bool:
        if self.task is None:
            self.logger.error("[red]Task is None - workflow was not initialized[/red]")
            return False
        return True


### Actions ###


class IdentifyTargetRef(ReleaseAction):
    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.github_client = github_client
        self.release_version: Optional["RedisVersion"] = None
        self.branches: List[str] = []
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        """Initialize by parsing release version and listing branches."""
        # If ref is already set, nothing to do
        if self.package_meta.ref is not None:
            return

        # Parse release version from tag
        if not self.release_meta.tag:
            self.logger.error("Release tag is not set")
            return

        try:

            self.release_version = RedisVersion.parse(self.release_meta.tag)
            self.logger.debug(
                f"Parsed release version: {self.release_version.major}.{self.release_version.minor}"
            )
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return

        # List remote branches matching release pattern with major version
        # Pattern: release/MAJOR.\d+$ (e.g., release/8.\d+$ for major version 8)
        pattern = f"^release/{self.release_version.major}\\.\\d+$"
        self.task = asyncio.create_task(
            self.github_client.list_remote_branches(
                self.package_meta.repo, pattern=pattern
            )
        )

    def update(self) -> Status:
        # If ref is already set, we're done
        if self.package_meta.ref is not None:
            self.logger.debug(f"Ref already set: {self.package_meta.ref}")
            return Status.SUCCESS

        try:
            assert self.task is not None

            # Wait for branch listing to complete
            if not self.task.done():
                return Status.RUNNING

            self.branches = self.task.result()
            self.logger.debug(f"Found {len(self.branches)} branches")

            # Sort branches and detect appropriate one
            sorted_branches = self._sort_branches(self.branches)
            detected_branch = self._detect_branch(sorted_branches)

            if detected_branch:
                self.package_meta.ref = detected_branch
                if self.log_once(
                    "target_ref_identified", self.package_meta.ephemeral.log_once_flags
                ):
                    self.logger.info(
                        f"[green]Target ref identified:[/green] {self.package_meta.ref}"
                    )
                self.feedback_message = f"Target ref set to {self.package_meta.ref}"
                return Status.SUCCESS
            else:
                self.logger.error("Failed to detect appropriate branch")
                self.feedback_message = "Failed to detect appropriate branch"
                return Status.FAILURE

        except Exception as e:
            return self.log_exception_and_return_failure(e)

    def _sort_branches(self, branches: List[str]) -> List[str]:
        """Sort branches by version in descending order.

        Args:
            branches: List of branch names (e.g., ["release/8.0", "release/8.4"])

        Returns:
            Sorted list of branch names in descending order by version
            (e.g., ["release/8.4", "release/8.2", "release/8.0"])
        """
        pattern = re.compile(r"^release/(\d+)\.(\d+)$")
        branch_versions = []

        for branch in branches:
            match = pattern.match(branch)
            if match:
                major = int(match.group(1))
                minor = int(match.group(2))
                branch_versions.append((major, minor, branch))

        # Sort by (major, minor) descending
        branch_versions.sort(reverse=True)

        return [branch for _, _, branch in branch_versions]

    def _detect_branch(self, sorted_branches: List[str]) -> Optional[str]:
        """Detect the appropriate branch from sorted list of branches.

        Walks over sorted list of branches (descending order) trying to find first
        branch equal to release/MAJOR.MINOR or lower version.

        Args:
            sorted_branches: Sorted list of branch names in descending order
                           (e.g., ["release/8.4", "release/8.2", "release/8.0"])
                           Can be empty.

        Returns:
            Branch name or None if no suitable branch found
        """
        if not self.release_version:
            return None

        if not sorted_branches:
            self.logger.warning("No release branches found matching pattern")
            return None

        target_major = self.release_version.major
        target_minor = self.release_version.minor

        # Pattern to extract version from branch name
        pattern = re.compile(r"^release/(\d+)\.(\d+)$")

        # Walk through sorted branches (descending order)
        # Find first branch <= target version
        for branch in sorted_branches:
            match = pattern.match(branch)
            if match:
                major = int(match.group(1))
                minor = int(match.group(2))

                if (major, minor) <= (target_major, target_minor):
                    self.logger.debug(
                        f"Found matching branch: {branch} for target {target_major}.{target_minor}"
                    )
                    return branch

        self.logger.warning(
            f"No suitable branch found for version {target_major}.{target_minor}"
        )
        return None


class TriggerWorkflow(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.task: Optional[asyncio.Task[bool]] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        self.workflow.uuid = str(uuid.uuid4())
        self.workflow.inputs["workflow_uuid"] = self.workflow.uuid
        if self.release_meta.tag is None:
            self.logger.error(
                "[red]Release tag is None - cannot trigger workflow[/red]"
            )
            self.feedback_message = "failed to trigger workflow"
            return
        self.workflow.inputs["release_tag"] = self.release_meta.tag
        ref = self.package_meta.ref if self.package_meta.ref is not None else "main"
        if self.log_once(
            "workflow_trigger_start", self.workflow.ephemeral.log_once_flags
        ):
            self.logger.info(
                f"Triggering workflow {self.workflow.workflow_file}, ref: {ref}, uuid: {self.workflow.uuid}"
            )
        self.task = asyncio.create_task(
            self.github_client.trigger_workflow(
                self.package_meta.repo,
                self.workflow.workflow_file,
                self.workflow.inputs,
                ref,
            )
        )

    def update(self) -> Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            self.task.result()
            self.workflow.triggered_at = datetime.now()
            if self.log_once(
                "workflow_triggered", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"[green]Workflow triggered successfully:[/green] {self.workflow.uuid}"
                )
            self.feedback_message = "workflow triggered"
            return Status.SUCCESS
        except Exception as e:
            self.feedback_message = "failed to trigger workflow"
            return self.log_exception_and_return_failure(e)

    def terminate(self, new_status: Status) -> None:
        # TODO: Cancel task
        pass


class IdentifyWorkflowByUUID(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
        package_meta: PackageMeta,
        log_prefix: str = "",
    ) -> None:

        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if self.workflow.uuid is None:
            self.logger.error(
                "[red]Workflow UUID is None - cannot identify workflow[/red]"
            )
            return
        if self.log_once(
            "workflow_identify_start", self.workflow.ephemeral.log_once_flags
        ):
            self.logger.info(
                f"Start identifying workflow {self.workflow.workflow_file}, uuid: {self.workflow.uuid}"
            )
        self.task = asyncio.create_task(
            self.github_client.identify_workflow(
                self.package_meta.repo, self.workflow.workflow_file, self.workflow.uuid
            )
        )

    def update(self) -> Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            result = self.task.result()
            if result is None:
                self.logger.error("[red]Workflow not found[/red]")
                return Status.FAILURE

            self.workflow.run_id = result.run_id
            if self.log_once(
                "workflow_identified", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"[green]Workflow found successfully:[/green] uuid: {self.workflow.uuid}, run_id: {self.workflow.run_id}"
                )
            self.feedback_message = (
                f"Workflow identified, run_id: {self.workflow.run_id}"
            )
            return Status.SUCCESS
        except Exception as e:
            return self.log_exception_and_return_failure(e)


class UpdateWorkflowStatusUntilCompletion(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
        package_meta: PackageMeta,
        log_prefix: str = "",
        timeout_seconds: int = 0,
        cutoff: int = 0,
        poll_interval: int = 3,
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        self.timeout_seconds = timeout_seconds
        self.cutoff = cutoff
        self.interval = poll_interval
        self.start_time: Optional[float] = None
        self.tick_count: int = 0
        self.is_sleeping: bool = False
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        self.logger.debug(
            f"Initialise: timeout: {self.timeout_seconds}, cutoff: {self.cutoff}, interval: {self.interval}"
        )
        self.start_time = asyncio.get_event_loop().time()
        self.is_sleeping = False
        self.tick_count = 0
        self.feedback_message = ""
        self._initialise_status_task()

    def _initialise_status_task(self) -> None:
        if self.workflow.run_id is None:
            self.logger.error(
                "[red]Workflow run_id is None - cannot check completion[/red]"
            )
            return

        if self.log_once(
            "workflow_status_update", self.workflow.ephemeral.log_once_flags
        ):
            self.logger.info(
                f"Start checking workflow {self.workflow.workflow_file}, run_id: {self.workflow.run_id} status"
            )
        self.task = asyncio.create_task(
            self.github_client.get_workflow_run(
                self.package_meta.repo, self.workflow.run_id
            )
        )
        self.is_sleeping = False

    def _initialise_sleep_task(self) -> None:
        self.task = asyncio.create_task(asyncio.sleep(self.interval))
        self.is_sleeping = True

    def update(self) -> Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            # If we just finished sleeping, switch back to status request
            if self.is_sleeping:
                self._initialise_status_task()
                return Status.RUNNING

            # We just finished a status request
            result = self.task.result()
            self.tick_count += 1

            if self.log_once(
                "workflow_status_current", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"Workflow {self.workflow.workflow_file}, run_id: {self.workflow.run_id} current status: {result.status}, {result.conclusion}"
                )
            if self.workflow.status != result.status:
                self.logger.info(
                    f"Workflow {self.workflow.workflow_file}({self.workflow.run_id}) status changed: {self.workflow.status} -> {result.status}"
                )
            self.workflow.status = result.status
            if self.workflow.conclusion != result.conclusion:
                self.logger.info(
                    f"Workflow {self.workflow.workflow_file}({self.workflow.run_id}) conclusion changed: {self.workflow.conclusion} -> {result.conclusion}"
                )
            self.workflow.conclusion = result.conclusion
            self.feedback_message = (
                f" {self.workflow.status}, {self.workflow.conclusion}"
            )

            if self.workflow.conclusion is not None:
                if self.workflow.conclusion == WorkflowConclusion.SUCCESS:
                    return Status.SUCCESS
                self.feedback_message = f"Workflow failed"
                return Status.FAILURE

            # Check cutoff (0 means no limit)
            if self.cutoff > 0 and self.tick_count >= self.cutoff:
                self.logger.debug(f"Cutoff reached: {self.tick_count} ticks")
                self.feedback_message = f"Cutoff reached: {self.tick_count}"
                return Status.FAILURE

            # Check timeout (0 means no limit)
            if self.timeout_seconds > 0 and self.start_time is not None:
                elapsed = asyncio.get_event_loop().time() - self.start_time
                self.feedback_message = (
                    f"{self.feedback_message}, elapsed: {elapsed:.1f}s"
                )
                if elapsed >= self.timeout_seconds:
                    self.logger.debug(f"Timeout reached: {elapsed:.1f}s")
                    self.feedback_message = (
                        f"Timed out: {elapsed:.1f}s of {self.timeout_seconds}s"
                    )
                    self.workflow.ephemeral.wait_for_completion_timed_out = True
                    return Status.FAILURE

            # Switch to sleep task
            self._initialise_sleep_task()
            return Status.RUNNING

        except Exception as e:
            return self.log_exception_and_return_failure(e)

    def terminate(self, new_status: Status) -> None:
        """Cancel the current task if it's running."""
        if self.task is not None and not self.task.done():
            self.task.cancel()
            self.logger.debug(
                f"Cancelled task during terminate with status: {new_status}"
            )


class Sleep(LoggingAction):

    task: Optional[asyncio.Task[None]] = None

    def __init__(self, name: str, sleep_time: float, log_prefix: str = "") -> None:
        self.sleep_time = sleep_time
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        self.task = asyncio.create_task(asyncio.sleep(self.sleep_time))

    def update(self) -> Status:
        if self.task is None:
            logger.error("[red]Task is None - behaviour was not initialized[/red]")
            return Status.FAILURE

        if not self.task.done():
            return Status.RUNNING

        return Status.SUCCESS


class GetWorkflowArtifactsList(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if self.workflow.run_id is None:
            self.logger.error(
                "[red]Workflow run_id is None - cannot get artifacts[/red]"
            )
            return

        self.logger.info(
            f"Start getting artifacts for workflow {self.workflow.workflow_file}, run_id: {self.workflow.run_id}"
        )
        self.task = asyncio.create_task(
            self.github_client.get_workflow_artifacts(
                self.package_meta.repo, self.workflow.run_id
            )
        )

    def update(self) -> Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            result = self.task.result()
            self.workflow.artifacts = result
            if self.log_once(
                "workflow_artifacts_list", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"[green]Downloaded artifacts list:[/green] {len(result)} artifacts"
                )
            self.feedback_message = f"Downloaded {len(result)} artifacts"
            return Status.SUCCESS
        except Exception as e:
            self.feedback_message = "failed to download artifacts list"
            return self.log_exception_and_return_failure(e)


class ExtractArtifactResult(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        artifact_name: str,
        github_client: GitHubClientAsync,
        package_meta: PackageMeta,
        log_prefix: str = "",
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.artifact_name = artifact_name
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if not self.workflow.artifacts:
            self.logger.error(
                "[red]Workflow artifacts is empty - cannot extract result[/red]"
            )
            return

        self.task = asyncio.create_task(
            self.github_client.download_and_extract_json_result(
                self.package_meta.repo,
                self.workflow.artifacts,
                self.artifact_name,
                "result.json",
            )
        )

    def update(self) -> Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            result = self.task.result()
            if result is None:
                self.logger.error(
                    f"[red]Failed to extract result from {self.artifact_name}[/red]"
                )
                self.feedback_message = "failed to extract result"
                return Status.FAILURE

            self.workflow.result = result
            self.logger.info(
                f"[green]Extracted result from {self.artifact_name}[/green]"
            )
            self.feedback_message = f"Extracted result from {self.artifact_name}"
            return Status.SUCCESS
        except Exception as e:
            self.feedback_message = "failed to extract result"
            return self.log_exception_and_return_failure(e)


class AttachReleaseHandleToPublishWorkflow(LoggingAction):
    def __init__(
        self,
        name: str,
        build_workflow: Workflow,
        publish_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.build_workflow = build_workflow
        self.publish_workflow = publish_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if "release_handle" in self.publish_workflow.inputs:
            return Status.SUCCESS

        if self.build_workflow.result is None:
            return Status.FAILURE

        self.publish_workflow.inputs["release_handle"] = json.dumps(
            self.build_workflow.result
        )
        return Status.SUCCESS


class ResetPackageState(ReleaseAction):
    def __init__(
        self,
        name: str,
        package: Package,
        default_package: Package,
        log_prefix: str = "",
    ) -> None:
        self.package = package
        self.default_package = default_package
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        reset_model_to_defaults(self.package, self.default_package)

        self.feedback_message = "Package state reset to default values"
        self.logger.info(f"[green]{self.feedback_message}[/green]")
        return Status.SUCCESS


class ResetWorkflowState(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        default_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.default_workflow = default_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:  # type: ignore
        reset_model_to_defaults(self.workflow, self.default_workflow)

        self.feedback_message = "Workflow state reset to default values"
        self.logger.info(f"[green]{self.feedback_message}[/green]")
        return Status.SUCCESS


class GenericWorkflowInputs(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=f"{name} - debian", log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is not None:
            self.workflow.inputs["release_type"] = self.package_meta.release_type.value
        if self.release_meta.tag is not None:
            self.workflow.inputs["release_tag"] = self.release_meta.tag
        return Status.SUCCESS


### Conditions ###


class IsTargetRefIdentified(LoggingAction):
    def __init__(
        self, name: str, package_meta: PackageMeta, log_prefix: str = ""
    ) -> None:
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.ref is not None:
            if self.log_once(
                "target_ref_identified", self.package_meta.ephemeral.log_once_flags
            ):
                self.logger.info(f"Target ref identified: {self.package_meta.ref}")
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowTriggered(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.debug(f"IsWorkflowTriggered: {self.workflow}")
        if self.workflow.triggered_at is not None:
            if self.log_once(
                "workflow_triggered", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"Workflow is triggered at: {self.workflow.triggered_at}"
                )
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowIdentified(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.debug(f"{self.workflow}")
        if self.workflow.run_id is not None:
            if self.log_once(
                "workflow_identified", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"Workflow is identified, run_id: {self.workflow.run_id}"
                )
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowCompleted(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.status == WorkflowStatus.COMPLETED:
            if self.log_once(
                "workflow_completed", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(f"Workflow is completed")
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowSuccessful(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.conclusion == WorkflowConclusion.SUCCESS:
            if self.log_once(
                "workflow_successful", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(f"Workflow completed with success status")
            return Status.SUCCESS
        elif self.workflow.conclusion == WorkflowConclusion.FAILURE:
            if self.log_once(
                "workflow_unsuccessful", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.error(f"Workflow completed with failure status")
        return Status.FAILURE


class HasWorkflowArtifacts(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.artifacts is not None:
            if self.log_once(
                "workflow_artifacts_list", self.workflow.ephemeral.log_once_flags
            ):
                self.logger.info(f"Workflow has artifacts")
            return Status.SUCCESS
        return Status.FAILURE


class HasWorkflowResult(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.result is not None:
            if self.log_once("workflow_result", self.workflow.ephemeral.log_once_flags):
                self.logger.info(
                    f"Workflow {self.workflow.workflow_file}, run_id: {self.workflow.run_id} is successful and has result"
                )
            return Status.SUCCESS
        return Status.FAILURE


class NeedToPublishRelease(LoggingAction):
    """Check the release type and package configuration to determine if we need to run publish workflow."""

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type == ReleaseType.INTERNAL:
            if self.package_meta.publish_internal_release:
                self.logger.debug(
                    f"Internal release requires publishing: {self.release_meta.tag}"
                )
                return Status.SUCCESS
            else:
                self.logger.debug(
                    f"Skip publishing internal release: {self.release_meta.tag}"
                )
            return Status.FAILURE
        return Status.SUCCESS


class DetectReleaseType(LoggingAction):
    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.release_meta = release_meta
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is not None:
            if self.log_once(
                "release_type_detected", self.package_meta.ephemeral.log_once_flags
            ):
                self.logger.info(
                    f"Detected release type: {self.package_meta.release_type}"
                )
            return Status.SUCCESS
        if self.release_meta.tag and re.search(r"-int\d*$", self.release_meta.tag):
            self.package_meta.release_type = ReleaseType.INTERNAL
        else:
            self.package_meta.release_type = ReleaseType.PUBLIC
        self.log_once(
            "release_type_detected", self.release_meta.ephemeral.log_once_flags
        )
        self.logger.info(f"Detected release type: {self.package_meta.release_type}")
        return Status.SUCCESS


class IsForceRebuild(LoggingAction):
    def __init__(
        self, name: str, package_meta: PackageMeta, log_prefix: str = ""
    ) -> None:
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.ephemeral.force_rebuild:
            return Status.SUCCESS
        return Status.FAILURE
