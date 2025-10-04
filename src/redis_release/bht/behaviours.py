"""
Actions and Conditions for the Release Tree

The guiding principles are:

* Actions should be atomic and represent a single task.
* Actions should unconditionally perform their job. This simplifies reuse, as any conditions can be applied separately.
* Conditions should not have side effects (e.g., modifying state).
"""

import asyncio
import logging
import uuid
from datetime import datetime
from token import OP
from typing import Any, Dict, Optional

import py_trees
from pydantic import BaseModel

from ..github_client_async import GitHubClientAsync
from ..models import WorkflowConclusion, WorkflowRun, WorkflowStatus
from .logging_wrapper import PyTreesLoggerWrapper
from .state import PackageMeta, ReleaseMeta, Workflow

logger = logging.getLogger(__name__)


class LoggingAction(py_trees.behaviour.Behaviour):
    logger: PyTreesLoggerWrapper

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.logger = PyTreesLoggerWrapper(logging.getLogger(self.name))

    def log_exception_and_return_failure(self, e: Exception) -> py_trees.common.Status:
        self.logger.error(f"[red]failed with exception:[/red] {type(e).__name__}: {e}")
        # use the underlying logger to get the full traceback
        self.logger._logger.error(f"[red]Full traceback:[/red]", exc_info=True)
        return py_trees.common.Status.FAILURE


class ReleaseAction(LoggingAction):
    task: Optional[asyncio.Task[Any]] = None

    def __init__(self, name: str) -> None:
        super().__init__(name=name)

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
    ) -> None:
        self.package_meta = package_meta
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        # For now, just set a hardcoded ref
        self.package_meta.ref = "release/8.2"
        self.logger.info(
            f"[green]Target ref identified:[/green] {self.package_meta.ref}"
        )
        self.feedback_message = f"Target ref set to {self.package_meta.ref}"
        return py_trees.common.Status.SUCCESS


class TriggerWorkflow(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.task: Optional[asyncio.Task[bool]] = None
        super().__init__(name=name)

    def initialise(self) -> None:
        self.workflow.uuid = str(uuid.uuid4())
        self.workflow.inputs["workflow_uuid"] = self.workflow.uuid
        if self.release_meta.tag is None:
            self.logger.error(
                "[red]Release tag is None - cannot trigger workflow[/red]"
            )
            self.workflow.ephemeral.trigger_failed = True
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

    def update(self) -> py_trees.common.Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return py_trees.common.Status.RUNNING

            self.task.result()
            self.workflow.triggered_at = datetime.now()
            logger.info(
                f"[green]Workflow triggered successfully:[/green] {self.workflow.uuid}"
            )
            self.feedback_message = "workflow triggered"
            return py_trees.common.Status.SUCCESS
        except Exception as e:
            self.workflow.ephemeral.trigger_failed = True
            self.feedback_message = "failed to trigger workflow"
            return self.log_exception_and_return_failure(e)

    def terminate(self, new_status: py_trees.common.Status) -> None:
        # TODO: Cancel task
        pass


class IdentifyWorkflowByUUID(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
        package_meta: PackageMeta,
    ) -> None:

        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        super().__init__(name=name)

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

    def update(self) -> py_trees.common.Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return py_trees.common.Status.RUNNING

            result = self.task.result()
            if result is None:
                self.logger.error("[red]Workflow not found[/red]")
                return py_trees.common.Status.FAILURE

            self.workflow.run_id = result.run_id
            self.logger.info(
                f"[green]Workflow found successfully:[/green] uuid: {self.workflow.uuid}, run_id: {self.workflow.run_id}"
            )
            self.feedback_message = (
                f"Workflow identified, run_id: {self.workflow.run_id}"
            )
            return py_trees.common.Status.SUCCESS
        except Exception as e:
            return self.log_exception_and_return_failure(e)


class UpdateWorkflowStatus(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
        package_meta: PackageMeta,
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.package_meta = package_meta
        super().__init__(name=name)

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

    def update(self) -> py_trees.common.Status:
        try:
            assert self.task is not None

            if not self.task.done():
                return py_trees.common.Status.RUNNING

            result = self.task.result()
            self.workflow.status = result.status
            self.workflow.conclusion = result.conclusion
            self.feedback_message = (
                f" {self.workflow.status}, {self.workflow.conclusion}"
            )
            return py_trees.common.Status.SUCCESS
        except Exception as e:
            return self.log_exception_and_return_failure(e)


class Sleep(py_trees.behaviour.Behaviour):

    task: Optional[asyncio.Task[None]] = None

    def __init__(self, name: str, sleep_time: float) -> None:
        self.sleep_time = sleep_time
        super().__init__(name=name)

    def initialise(self) -> None:
        self.task = asyncio.create_task(asyncio.sleep(self.sleep_time))

    def update(self) -> py_trees.common.Status:
        if self.task is None:
            logger.error("[red]Task is None - behaviour was not initialized[/red]")
            return py_trees.common.Status.FAILURE

        if not self.task.done():
            return py_trees.common.Status.RUNNING

        return py_trees.common.Status.SUCCESS


class SetFlag(LoggingAction):
    def __init__(
        self, name: str, container: BaseModel, flag: str, value: bool = True
    ) -> None:
        self.container = container
        self.flag = flag
        self.flag_value = value
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        setattr(self.container, self.flag, self.flag_value)
        self.logger.info(f"Set flag {self.flag} to {self.flag_value}")
        self.feedback_message = f"flag {self.flag} set to {self.flag_value}"
        return py_trees.common.Status.SUCCESS


### Conditions ###


class IsTargetRefIdentified(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, package_meta: PackageMeta) -> None:
        self.package_meta = package_meta
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.package_meta.ref is not None:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowTriggerFailed(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.workflow.ephemeral.trigger_failed:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowTriggered(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        logger.debug(f"IsWorkflowTriggered: {self.workflow}")
        if self.workflow.triggered_at is not None:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowIdentified(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        self.logger.debug(f"{self.workflow}")
        if self.workflow.run_id is not None:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowCompleted(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.workflow.status == WorkflowStatus.COMPLETED:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowSuccessful(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.workflow.conclusion == WorkflowConclusion.SUCCESS:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowTimedOut(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.workflow.ephemeral.timed_out:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class IsWorkflowIdentifyFailed(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.workflow.ephemeral.identify_failed:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE
