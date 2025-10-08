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


class IdentifyTargetRefGoal(FlagGuard):
    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        super().__init__(
            None,
            IdentifyTargetRef(
                "Identify Target Ref",
                package_meta,
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
