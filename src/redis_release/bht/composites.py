from ast import Invert
from socket import timeout
from time import sleep

from py_trees.composites import Selector, Sequence
from py_trees.decorators import Repeat, Retry

from ..github_client_async import GitHubClientAsync
from .behaviours import (
    IdentifyWorkflowByUUID,
    IsWorkflowCompleted,
    IsWorkflowIdentified,
    IsWorkflowSuccessful,
    IsWorkflowTimedOut,
    IsWorkflowTriggered,
    Sleep,
    UpdateWorkflowStatus,
)
from .decorators import TimeoutWithFlag
from .state import Workflow


class FindWorkflowByUUID(Sequence):
    max_retries: int = 3
    poll_interval: int = 5

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        is_workflow_triggered = IsWorkflowTriggered(
            f"{log_prefix}Is Workflow Triggered?", workflow
        )
        identify_workflow = IdentifyWorkflowByUUID(
            f"{log_prefix}Identify Workflow by UUID", workflow, github_client
        )
        sleep = Sleep("Sleep", self.poll_interval)

        sleep_then_identify = Sequence(
            f"{log_prefix}Sleep then Identify",
            memory=True,
            children=[sleep, identify_workflow],
        )
        identify_loop = Retry(
            f"{log_prefix}Retry {self.max_retries} times",
            sleep_then_identify,
            self.max_retries,
        )
        identify_if_required = Selector(
            f"{log_prefix}Identify if required",
            False,
            children=[
                IsWorkflowIdentified(f"{log_prefix}Is Workflow Identified?", workflow),
                identify_loop,
            ],
        )

        super().__init__(
            name=name,
            memory=False,
            children=[is_workflow_triggered, identify_if_required],
        )


class WaitForWorkflowCompletion(Sequence):
    poll_interval: int
    timeout_seconds: int

    def __init__(
        self,
        name: str,
        workflow: Workflow,
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
            f"{log_prefix}Update Workflow Status", workflow, github_client
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
