from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter, Repeat, Retry, Timeout

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
    Sleep,
)
from .behaviours import TriggerWorkflow as TriggerWorkflow
from .behaviours import UpdateWorkflowStatus
from .decorators import FlagGuard
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

        sleep_then_identify = Sequence(
            f"{log_prefix}Sleep then Identify",
            memory=True,
            children=[sleep, identify_workflow],
        )
        identify_loop = Retry(
            f"Retry {self.max_retries} times",
            sleep_then_identify,
            self.max_retries,
        )
        identify_guard = FlagGuard(
            None,
            identify_loop,
            workflow.ephemeral,
            "identify_failed",
        )
        identify_if_required = Selector(
            f"{log_prefix}Identify if required",
            False,
            children=[
                IsWorkflowIdentified(f"Is Workflow Identified?", workflow),
                identify_guard,
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
        poll_interval: int = 10,
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        self.poll_interval = poll_interval
        self.timeout_seconds = workflow.timeout_minutes * 60

        is_workflow_identified = IsWorkflowIdentified(
            f"Is Workflow Identified?", workflow
        )
        is_workflow_completed = IsWorkflowCompleted(f"Is Workflow Completed?", workflow)
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

        update_workflow_loop = FlagGuard(
            None,
            Timeout(
                f"Timeout {workflow.timeout_minutes}m",
                Repeat("Repeat", update_workflow_status_with_pause, -1),
                self.timeout_seconds,
            ),
            workflow.ephemeral,
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
                        update_workflow_loop,
                    ],
                ),
            ],
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

        is_target_ref_identified = IsTargetRefIdentified(
            f"{log_prefix}Is Target Ref Identified?", package_meta
        )
        is_workflow_triggered = IsWorkflowTriggered(
            f"{log_prefix}Is Workflow Triggered?", workflow
        )
        trigger_workflow = TriggerWorkflow(
            f"{log_prefix}Trigger Workflow",
            workflow,
            package_meta,
            release_meta,
            github_client,
        )
        trigger_guard = FlagGuard(
            None,
            trigger_workflow,
            workflow.ephemeral,
            "trigger_failed",
        )
        trigger_workflow_if_req = Selector(
            f"{log_prefix}Trigger Workflow if Required",
            memory=False,
            children=[is_workflow_triggered, trigger_guard],
        )

        super().__init__(
            name=name,
            memory=False,
            children=[is_target_ref_identified, trigger_workflow_if_req],
        )


class IdentifyTargetRefGoal(FlagGuard):
    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        super().__init__(
            None,
            IdentifyTargetRef(
                f"{log_prefix}Identify Target Ref",
                package_meta,
            ),
            package_meta.ephemeral,
            "identify_ref_failed",
        )


class GetResultGoal(Sequence):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        artifact_name: str,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        # Extract Result Goal: Sequence to extract result if artifacts exist
        has_artifacts = HasWorkflowArtifacts(
            f"{log_prefix}Has Workflow Artifacts?", workflow
        )

        # Extract Result If Required: Selector to check if result exists or extract it
        has_result = HasWorkflowResult(f"{log_prefix}Has Workflow Result?", workflow)
        extract_result = ExtractArtifactResult(
            f"{log_prefix}Extract Artifact Result",
            workflow,
            artifact_name,
            github_client,
            package_meta,
        )
        extract_result_guard = FlagGuard(
            None,
            extract_result,
            workflow.ephemeral,
            "extract_result_failed",
        )
        extract_result_if_required = Selector(
            f"{log_prefix}Extract Result If Required",
            memory=False,
            children=[has_result, extract_result_guard],
        )

        extract_result_goal = Sequence(
            f"{log_prefix}Extract Result Goal",
            memory=False,
            children=[has_artifacts, extract_result_if_required],
        )

        # Get Workflow Artifacts (guarded)
        get_artifacts = GetWorkflowArtifactsList(
            f"{log_prefix}Get Workflow Artifacts List",
            workflow,
            github_client,
            package_meta,
        )
        get_artifacts_guard = FlagGuard(
            None,
            get_artifacts,
            workflow.ephemeral,
            "artifacts_download_failed",
        )

        # Get Result Process: Selector to try extracting or downloading artifacts
        get_result_process = Selector(
            f"{log_prefix}Get Result Process",
            memory=False,
            children=[
                extract_result_goal,
                get_artifacts_guard,
            ],
        )

        # Final check: Has Workflow Result?
        has_workflow_result = HasWorkflowResult(
            f"{log_prefix}Has Workflow Result?", workflow
        )

        super().__init__(
            name=name,
            memory=False,
            children=[
                get_result_process,
                has_workflow_result,
            ],
        )


class WorkflowSuccessGoal(Sequence):
    """Composite that ensures a workflow runs successfully.

    This is a Sequence that:
    1. Runs the workflow (via Workflow Run selector)
    2. Checks that the workflow succeeded
    """

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

        # Workflow Run: Selector to get workflow running
        is_workflow_successful = IsWorkflowSuccessful(
            f"{log_prefix}Is Workflow Successful?", workflow
        )
        identify_workflow = FindWorkflowByUUID(
            f"{log_prefix}Identify Workflow Goal",
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        trigger_workflow = TriggerWorkflowGoal(
            f"{log_prefix}Trigger Workflow Goal",
            workflow,
            package_meta,
            release_meta,
            github_client,
            log_prefix,
        )
        identify_target_ref = IdentifyTargetRefGoal(
            f"{log_prefix}Identify Target Ref Goal",
            package_meta,
            release_meta,
            log_prefix,
        )
        wait_for_completion = WaitForWorkflowCompletion(
            f"{log_prefix}Workflow Completion Goal",
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )

        workflow_run = Selector(
            f"{log_prefix}Workflow Run",
            memory=False,
            children=[
                wait_for_completion,
                identify_workflow,
                trigger_workflow,
                identify_target_ref,
            ],
        )

        super().__init__(
            name=name,
            memory=False,
            children=[
                workflow_run,
                is_workflow_successful,
            ],
        )


class ReleasePhaseGoal(Sequence):
    """Composite that manages a complete release phase (build or publish).

    This is a Sequence that:
    1. Ensures the workflow runs successfully (WorkflowSuccessGoal)
    2. Gets the result from the workflow artifacts (GetResultGoal)
    """

    def __init__(
        self,
        phase_name: str,
        workflow: Workflow,
        artifact_name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        if log_prefix != "":
            log_prefix = f"{log_prefix}."

        name = f"{log_prefix}{phase_name.capitalize()} Phase Goal"
        phase_log_prefix = f"{log_prefix}{phase_name.upper()}"

        workflow_success = WorkflowSuccessGoal(
            f"{phase_log_prefix}.Workflow Success Goal",
            workflow,
            package_meta,
            release_meta,
            github_client,
            phase_log_prefix,
        )

        get_result = GetResultGoal(
            f"{phase_log_prefix}.Get Result Goal",
            workflow,
            artifact_name,
            package_meta,
            github_client,
            phase_log_prefix,
        )

        super().__init__(
            name=name,
            memory=False,
            children=[
                workflow_success,
                get_result,
            ],
        )
