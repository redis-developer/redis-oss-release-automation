"""
Actions and Conditions for the Release Tree

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
from token import OP
from typing import Any, Dict, Optional

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter, Repeat, Retry, Timeout
from pydantic import BaseModel

from ..github_client_async import GitHubClientAsync
from ..models import WorkflowConclusion, WorkflowRun, WorkflowStatus
from .decorators import FlagGuard
from .logging_wrapper import PyTreesLoggerWrapper
from .state import PackageMeta, ReleaseMeta, Workflow

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
        self, name: str, package_meta: PackageMeta, log_prefix: str = ""
    ) -> None:
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.ref is not None:
            return Status.SUCCESS
        # For now, just set a hardcoded ref
        self.package_meta.ref = "release/8.2"
        self.logger.info(
            f"[green]Target ref identified:[/green] {self.package_meta.ref}"
        )
        self.feedback_message = f"Target ref set to {self.package_meta.ref}"
        return Status.SUCCESS


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
            logger.info(
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
            self.logger.info(
                f"[green]Workflow found successfully:[/green] uuid: {self.workflow.uuid}, run_id: {self.workflow.run_id}"
            )
            self.feedback_message = (
                f"Workflow identified, run_id: {self.workflow.run_id}"
            )
            return Status.SUCCESS
        except Exception as e:
            return self.log_exception_and_return_failure(e)


class UpdateWorkflowStatus(ReleaseAction):
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
        if self.workflow.run_id is None:
            self.logger.error(
                "[red]Workflow run_id is None - cannot check completion[/red]"
            )
            return

        self.task = asyncio.create_task(
            self.github_client.get_workflow_run(
                self.package_meta.repo, self.workflow.run_id
            )
        )

    def update(self) -> Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            result = self.task.result()
            self.workflow.status = result.status
            self.workflow.conclusion = result.conclusion
            self.feedback_message = (
                f" {self.workflow.status}, {self.workflow.conclusion}"
            )
            return Status.SUCCESS
        except Exception as e:
            return self.log_exception_and_return_failure(e)


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
            self.logger.info(
                f"[green]Downloaded artifacts list:[/green] {len(result)} {result} artifacts"
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


### Conditions ###


class IsTargetRefIdentified(LoggingAction):
    def __init__(
        self, name: str, package_meta: PackageMeta, log_prefix: str = ""
    ) -> None:
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.ref is not None:
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowTriggered(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.debug(f"IsWorkflowTriggered: {self.workflow}")
        if self.workflow.triggered_at is not None:
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowIdentified(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.debug(f"{self.workflow}")
        if self.workflow.run_id is not None:
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowCompleted(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.status == WorkflowStatus.COMPLETED:
            return Status.SUCCESS
        return Status.FAILURE


class IsWorkflowSuccessful(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.conclusion == WorkflowConclusion.SUCCESS:
            return Status.SUCCESS
        return Status.FAILURE


class HasWorkflowArtifacts(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.artifacts is not None:
            return Status.SUCCESS
        return Status.FAILURE


class HasWorkflowResult(LoggingAction):
    def __init__(self, name: str, workflow: Workflow, log_prefix: str = "") -> None:
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.workflow.result is not None:
            return Status.SUCCESS
        return Status.FAILURE


class NeedToPublish(LoggingAction):
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
        # Check if this is an internal release by matching the pattern -int\d*$ in the tag
        if self.release_meta.tag and re.search(r"-int\d*$", self.release_meta.tag):
            self.logger.debug(f"Asssuming internal release: {self.release_meta.tag}")
            if self.package_meta.publish_internal_release:
                self.logger.debug(
                    f"Publishing internal release: {self.release_meta.tag}"
                )
                return Status.SUCCESS
            self.logger.debug(
                f"Skip publishing internal release: {self.release_meta.tag}"
            )
            return Status.FAILURE

        self.logger.debug(f"Public release: {self.release_meta.tag}")
        return Status.SUCCESS


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
