import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

import py_trees

from ..github_client_async import GitHubClientAsync
from ..models import WorkflowRun
from .logging_wrapper import PyTreesLoggerWrapper
from .state import Workflow

logger = logging.getLogger(__name__)


def log_exception_and_return_failure(
    TaskName: str, e: Exception
) -> py_trees.common.Status:
    logger.error(
        f"[red]{TaskName} failed with exception:[/red] {type(e).__name__}: {e}"
    )
    logger.error(f"[red]Full traceback:[/red]", exc_info=True)
    return py_trees.common.Status.FAILURE


class TriggerWorkflow(py_trees.behaviour.Behaviour):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
    ) -> None:
        self.github_client = github_client
        self.workflow = workflow
        self.task: Optional[asyncio.Task[bool]] = None
        super().__init__(name=name)

    def initialise(self) -> None:
        self.workflow.uuid = str(uuid.uuid4())
        self.workflow.inputs["workflow_uuid"] = self.workflow.uuid
        logger.info("initialise")
        self.task = asyncio.create_task(
            self.github_client.trigger_workflow(
                self.workflow.repo,
                self.workflow.workflow_file,
                self.workflow.inputs,
                self.workflow.ref,
            )
        )

    def update(self) -> py_trees.common.Status:
        if self.task is None:
            logger.error("[red]Task is None - workflow was not initialized[/red]")
            return py_trees.common.Status.FAILURE

        if not self.task.done():
            return py_trees.common.Status.RUNNING

        try:
            result = self.task.result()
            self.workflow.triggered_at = datetime.now()
            logger.info(
                f"[green]Workflow triggered successfully:[/green] {self.workflow.uuid}"
            )
            return py_trees.common.Status.SUCCESS
        except Exception as e:
            self.workflow.trigger_failed = True
            return log_exception_and_return_failure("TriggerWorkflow", e)

    def terminate(self, new_status: py_trees.common.Status) -> None:
        # TODO: Cancel task
        pass


class IdentifyWorkflowByUUID(py_trees.behaviour.Behaviour):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
    ) -> None:

        self.github_client = github_client
        self.workflow = workflow
        self.task: Optional[asyncio.Task[Optional[WorkflowRun]]] = None
        super().__init__(name=name)
        self.logger = PyTreesLoggerWrapper(logging.getLogger(self.name))

    def initialise(self) -> None:
        if self.workflow.uuid is None:
            self.logger.error(
                "[red]Workflow UUID is None - cannot identify workflow[/red]"
            )
            return

        self.task = asyncio.create_task(
            self.github_client.identify_workflow(
                self.workflow.repo, self.workflow.workflow_file, self.workflow.uuid
            )
        )

    def update(self) -> py_trees.common.Status:
        self.logger.debug("IdentifyWorkflowByUUID: update")
        if self.task is None:
            self.logger.error("red]Task is None - behaviour was not initialized[/red]")
            return py_trees.common.Status.FAILURE

        if not self.task.done():
            self.logger.debug(
                f"Task not yet done {self.task.cancelled()} {self.task.done()} {self.task}"
            )
            return py_trees.common.Status.RUNNING

        try:
            self.logger.debug("before result")
            result = self.task.result()
            self.logger.debug("result {result}")
            if result is None:
                self.logger.error("[red]Workflow not found[/red]")
                return py_trees.common.Status.FAILURE

            self.workflow.run_id = result.run_id
            self.logger.info(
                f"[green]Workflow found successfully:[/green] uuid: {self.workflow.uuid}, run_id: {self.workflow.run_id}"
            )
            return py_trees.common.Status.SUCCESS
        except Exception as e:
            return log_exception_and_return_failure(f"{self.name} TriggerWorkflow", e)


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


class IsWorkflowTriggerFailed(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, workflow: Workflow) -> None:
        self.workflow = workflow
        super().__init__(name=name)

    def update(self) -> py_trees.common.Status:
        if self.workflow.trigger_failed:
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
        if self.workflow.run_id is not None:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE
