from ast import Invert
from socket import timeout
from time import sleep

import py_trees
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter, Repeat, Retry

from ..github_client_async import GitHubClientAsync
from .behaviours import IdentifyTargetRef as IdentifyTargetRefAction
from .behaviours import (
    IdentifyWorkflowByUUID,
    IsTargetRefIdentified,
    IsWorkflowCompleted,
    IsWorkflowIdentified,
    IsWorkflowIdentifyFailed,
    IsWorkflowSuccessful,
    IsWorkflowTimedOut,
    IsWorkflowTriggered,
    IsWorkflowTriggerFailed,
    SetFlag,
    Sleep,
)
from .behaviours import TriggerWorkflow as TriggerWorkflow
from .behaviours import UpdateWorkflowStatus
from .decorators import TimeoutWithFlag
from .state import PackageMeta, ReleaseMeta, Workflow


class FindWorkflowByUUID(Sequence):
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
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        is_workflow_triggered = IsWorkflowTriggered(
            f"{log_prefix}Is Workflow Triggered?", workflow
        )
        identify_workflow = IdentifyWorkflowByUUID(
            f"{log_prefix}Identify Workflow by UUID",
            workflow,
            github_client,
            package_meta,
        )
        sleep = Sleep("Sleep", self.poll_interval)

        is_workflow_identify_failed = IsWorkflowIdentifyFailed(
            f"Identify Failed?", workflow
        )
        sleep_then_identify = Sequence(
            f"{log_prefix}Sleep then Identify",
            memory=True,
            children=[sleep, identify_workflow],
        )
        set_identify_failed_flag = SetFlag(
            f"{log_prefix}Set Identify Failed Flag",
            workflow.ephemeral,
            "identify_failed",
            True,
        )
        identify_loop = Retry(
            f"Retry {self.max_retries} times",
            sleep_then_identify,
            self.max_retries,
        )
        identify_if_required = Selector(
            f"{log_prefix}Identify if required",
            False,
            children=[
                IsWorkflowIdentified(f"Is Workflow Identified?", workflow),
                is_workflow_identify_failed,
                identify_loop,
                set_identify_failed_flag,
            ],
        )

        super().__init__(
            name=name,
            memory=False,
            children=[
                is_workflow_triggered,
                identify_if_required,
            ],
        )


class WaitForWorkflowCompletion(Sequence):
    poll_interval: int
    timeout_seconds: int

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
        timeout_seconds: int = 3 * 60,
        poll_interval: int = 10,
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds

        is_workflow_identified = IsWorkflowIdentified(
            f"Is Workflow Identified?", workflow
        )
        is_workflow_completed = IsWorkflowCompleted(f"Is Workflow Completed?", workflow)
        is_worklow_timed_out = IsWorkflowTimedOut(f"Is Workflow Timed Out?", workflow)
        update_workflow_status = UpdateWorkflowStatus(
            f"{log_prefix}Update Workflow Status", workflow, github_client, package_meta
        )
        update_workflow_status_with_pause = Sequence(
            f"{log_prefix}Update Workflow Status with Pause",
            memory=True,
            children=[
                Sleep("Sleep", self.poll_interval),
                update_workflow_status,
            ],
        )

        update_workflow_loop = TimeoutWithFlag(
            "Timeout",
            Repeat("Repeat", update_workflow_status_with_pause, -1),
            self.timeout_seconds,
            workflow,
            "timed_out",
        )

        # Sequence:
        super().__init__(
            name=name,
            memory=False,
            children=[
                is_workflow_identified,
                Selector(
                    f"Wait for completion",
                    False,
                    children=[
                        is_workflow_completed,
                        is_worklow_timed_out,
                        update_workflow_loop,
                    ],
                ),
            ],
        )


class IdentifyTargetRef(Selector):
    """Composite to identify target ref if not already identified."""

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        is_target_ref_identified = IsTargetRefIdentified(
            f"{log_prefix}Is Target Ref Identified?", package_meta
        )
        identify_target_ref = IdentifyTargetRefAction(
            f"{log_prefix}Identify Target Ref", package_meta
        )

        super().__init__(
            name=name,
            memory=False,
            children=[is_target_ref_identified, identify_target_ref],
        )


class TriggerWorkflowGoal(Sequence):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        may_start_workflow = Inverter(
            f"{log_prefix}Not Trigger Failed",
            IsWorkflowTriggerFailed(
                f"{log_prefix}Is Workflow Trigger Failed?", workflow
            ),
        )
        identify_target_ref = IdentifyTargetRef(
            f"{log_prefix}Identify Target Ref", package_meta, release_meta, log_prefix
        )
        trigger_workflow = TriggerWorkflow(
            f"{log_prefix}Trigger Workflow",
            workflow,
            package_meta,
            release_meta,
            github_client,
        )

        super().__init__(
            name=name,
            memory=True,
            children=[may_start_workflow, identify_target_ref, trigger_workflow],
        )
