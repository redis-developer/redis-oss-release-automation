"""
Higher level composites for the Release Tree

These composites are built from the atomic actions and conditions defined in `behaviours.py`.
Here we make flag and state aware tree behaviors, implement retry and repeat patterns.

The guiding principle for the composites defined here is the same as in behaviours.py
in a sense that we aim to make a more or less direct action without complex conditions
(except for the flags)

More complex behaviors, including pre- and post- conditions are defined in `ppas.py`.
"""

from typing import Iterator, List, Optional
from typing import Sequence as TypingSequence

from py_trees.behaviour import Behaviour
from py_trees.common import OneShotPolicy, Status
from py_trees.composites import Composite, Selector, Sequence
from py_trees.decorators import Repeat, Retry, SuccessIsRunning, Timeout

from ..github_client_async import GitHubClientAsync
from .behaviours import (
    ExtractArtifactResult,
    GetWorkflowArtifactsList,
    HasWorkflowArtifacts,
    HasWorkflowResult,
    IdentifyTargetRef,
    IdentifyWorkflowByUUID,
    IsTargetRefIdentified,
    IsWorkflowCompleted,
    IsWorkflowIdentified,
    IsWorkflowSuccessful,
    IsWorkflowTriggered,
    ResetPackageState,
    ResetWorkflowState,
    Sleep,
)
from .behaviours import TriggerWorkflow as TriggerWorkflow
from .behaviours import UpdateWorkflowStatus
from .decorators import FlagGuard
from .state import Package, PackageMeta, ReleaseMeta, Workflow


class ParallelBarrier(Composite):
    """
    A simplified parallel composite that runs all children until convergence.

    This parallel composite:
    - Ticks all children on each tick
    - Skips children that have already converged (SUCCESS or FAILURE) in synchronized mode
    - Returns FAILURE if any child returns FAILURE
    - Returns SUCCESS if all children return SUCCESS
    - Returns RUNNING if any child is still RUNNING

    Unlike py_trees.Parallel, this composite:
    - Has no policy configuration (always waits for all children)
    - Always operates in synchronized mode (skips converged children)
    - Has simpler logic focused on the all-must-succeed pattern

    Args:
        name: the composite behaviour name
        children: list of children to add
    """

    def __init__(
        self,
        name: str,
        memory: bool = True,
        children: Optional[TypingSequence[Behaviour]] = None,
    ):
        self.memory = memory
        super().__init__(name, children)

    def tick(self) -> Iterator[Behaviour]:
        """
        Tick all children until they converge, then determine status.
        """
        # Initialise if first time
        if self.status != Status.RUNNING:
            # subclass (user) handling
            self.initialise()

        # Handle empty children case
        if not self.children:
            self.current_child = None
            self.stop(Status.SUCCESS)
            yield self
            return

        # Tick all children, skipping those that have already converged
        for child in self.children:
            # Skip children that have already converged (synchronized mode)
            if self.memory and child.status in [Status.SUCCESS, Status.FAILURE]:
                continue
            # Tick the child
            for node in child.tick():
                yield node

        # Determine new status based on children's statuses
        self.current_child = self.children[-1]

        new_status = Status.INVALID
        has_running = any(child.status == Status.RUNNING for child in self.children)
        if has_running:
            new_status = Status.RUNNING
        else:
            has_failed = any(child.status == Status.FAILURE for child in self.children)
            if has_failed:
                new_status = Status.FAILURE
            else:
                new_status = Status.SUCCESS

        # If we've reached a final status, stop and terminate running children
        if new_status != Status.RUNNING:
            self.stop(new_status)

        self.status = new_status
        yield self


class FindWorkflowByUUID(FlagGuard):
    max_retries: int = 3
    poll_interval: int = 5

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        identify_workflow = IdentifyWorkflowByUUID(
            "Identify Workflow by UUID",
            workflow,
            github_client,
            package_meta,
            log_prefix=log_prefix,
        )
        sleep = Sleep("Sleep", self.poll_interval, log_prefix=log_prefix)

        sleep_then_identify = Sequence(
            "Sleep then Identify",
            memory=True,
            children=[sleep, identify_workflow],
        )
        identify_loop = Retry(
            f"Retry {self.max_retries} times",
            sleep_then_identify,
            self.max_retries,
        )
        super().__init__(
            None if name == "" else name,
            identify_loop,
            workflow.ephemeral,
            "identify_failed",
            log_prefix=log_prefix,
        )


class WaitForWorkflowCompletion(FlagGuard):
    poll_interval: int
    timeout_seconds: int

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
        poll_interval: int = 10,
    ) -> None:
        self.poll_interval = poll_interval
        self.timeout_seconds = workflow.timeout_minutes * 60

        update_workflow_status = UpdateWorkflowStatus(
            "Update Workflow Status",
            workflow,
            github_client,
            package_meta,
            log_prefix=log_prefix,
        )
        update_workflow_status_with_pause = Sequence(
            "Update Workflow Status with Pause",
            memory=True,
            children=[
                Sleep("Sleep", self.poll_interval, log_prefix=log_prefix),
                update_workflow_status,
            ],
        )

        super().__init__(
            None,
            Timeout(
                f"Timeout {workflow.timeout_minutes}m",
                Repeat("Repeat", update_workflow_status_with_pause, -1),
                self.timeout_seconds,
            ),
            workflow.ephemeral,
            "timed_out",
            log_prefix=log_prefix,
        )


class TriggerWorkflowGuarded(FlagGuard):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        trigger_workflow = TriggerWorkflow(
            "Trigger Workflow",
            workflow,
            package_meta,
            release_meta,
            github_client,
            log_prefix=log_prefix,
        )
        super().__init__(
            None if name == "" else name,
            trigger_workflow,
            workflow.ephemeral,
            "trigger_failed",
            log_prefix=log_prefix,
        )


class IdentifyTargetRefGuarded(FlagGuard):
    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        super().__init__(
            None if name == "" else name,
            IdentifyTargetRef(
                "Identify Target Ref",
                package_meta,
                release_meta,
                github_client,
                log_prefix=log_prefix,
            ),
            package_meta.ephemeral,
            "identify_ref_failed",
            log_prefix=log_prefix,
        )


class DownloadArtifactsListGuarded(FlagGuard):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        super().__init__(
            None if name == "" else name,
            GetWorkflowArtifactsList(
                "Get Workflow Artifacts List",
                workflow,
                package_meta,
                github_client,
                log_prefix=log_prefix,
            ),
            workflow.ephemeral,
            "artifacts_download_failed",
            log_prefix=log_prefix,
        )


class ExtractArtifactResultGuarded(FlagGuard):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        artifact_name: str,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        super().__init__(
            None if name == "" else name,
            ExtractArtifactResult(
                "Extract Artifact Result",
                workflow,
                artifact_name,
                github_client,
                package_meta,
                log_prefix=log_prefix,
            ),
            workflow.ephemeral,
            "extract_result_failed",
            log_prefix=log_prefix,
        )


class ResetPackageStateGuarded(FlagGuard):
    """
    Reset package once if force_rebuild is True.
    Always returns SUCCESS.
    """

    def __init__(
        self,
        name: str,
        package: Package,
        default_package: Package,
        log_prefix: str = "",
    ) -> None:
        super().__init__(
            None if name == "" else name,
            ResetPackageState(
                "Reset Package State",
                package,
                default_package,
                log_prefix=log_prefix,
            ),
            package.meta.ephemeral,
            "force_rebuild",
            flag_value=False,
            raise_on=[Status.SUCCESS, Status.FAILURE],
            guard_status=Status.SUCCESS,
            log_prefix=log_prefix,
        )


class RestartPackageGuarded(FlagGuard):
    """
    Reset package if we didn't trigger the workflow in current run
    This is intended to be used for build workflow since if build has failed
    we have to reset not only build but also publish which effectively means
    we have to reset the entire package and restart from scratch.

    When reset is made we return RUNNING to give the tree opportunity to run the workflow again.
    """

    def __init__(
        self,
        name: str,
        package: Package,
        workflow: Workflow,
        default_package: Package,
        log_prefix: str = "",
    ) -> None:
        reset_package_state = ResetPackageState(
            "Reset Package State",
            package,
            default_package,
            log_prefix=log_prefix,
        )
        reset_package_state_running = SuccessIsRunning(
            "Success is Running", reset_package_state
        )
        reset_package_state_guarded = FlagGuard(
            None if name == "" else name,
            reset_package_state_running,
            package.meta.ephemeral,
            "identify_ref_failed",
            flag_value=True,
            raise_on=[],
            guard_status=Status.FAILURE,
            log_prefix=log_prefix,
        )
        super().__init__(
            None if name == "" else name,
            reset_package_state_guarded,
            workflow.ephemeral,
            "trigger_attempted",
            flag_value=True,
            raise_on=[],
            guard_status=Status.FAILURE,
            log_prefix=log_prefix,
        )


class RestartWorkflowGuarded(FlagGuard):
    """
    Reset workflow if we didn't trigger the workflow in current run and if there was no identify target ref error

    This will only reset the workflow state

    When reset is made we return RUNNING to give the tree opportunity to run the workflow again.
    """

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        default_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        reset_workflow_state = ResetWorkflowState(
            "Reset Workflow State",
            workflow,
            default_workflow,
            log_prefix=log_prefix,
        )
        reset_workflow_state_running = SuccessIsRunning(
            "Success is Running", reset_workflow_state
        )
        reset_workflow_state_guarded = FlagGuard(
            None if name == "" else name,
            reset_workflow_state_running,
            package_meta.ephemeral,
            "identify_ref_failed",
            flag_value=True,
            raise_on=[],
            guard_status=Status.FAILURE,
            log_prefix=log_prefix,
        )
        super().__init__(
            None if name == "" else name,
            reset_workflow_state_guarded,
            workflow.ephemeral,
            "trigger_attempted",
            flag_value=True,
            raise_on=[],
            guard_status=Status.FAILURE,
            log_prefix=log_prefix,
        )
