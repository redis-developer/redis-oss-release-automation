"""
Here we define PPAs (Postcondition-Precondition-Action) composites to be used in backchaining.

More specific PPAs are defined directly in the tree factory files.

See backchain.py for more details on backchaining.

Chains are formed and latched in `tree_factory.py`

"""

from typing import Union

from py_trees.composites import Selector, Sequence

from ..github_client_async import GitHubClientAsync
from .backchain import create_PPA
from .behaviours import (
    AttachReleaseHandleToPublishWorkflow,
    HasWorkflowArtifacts,
    HasWorkflowResult,
    IsTargetRefIdentified,
    IsWorkflowCompleted,
    IsWorkflowIdentified,
    IsWorkflowSuccessful,
    IsWorkflowTriggered,
)
from .composites import (
    DownloadArtifactsListGuarded,
    ExtractArtifactResultGuarded,
    FindWorkflowByUUID,
    IdentifyTargetRefGuarded,
    TriggerWorkflowGuarded,
    WaitForWorkflowCompletion,
)
from .state import PackageMeta, ReleaseMeta, Workflow


def create_workflow_success_ppa(
    workflow: Workflow,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Workflow Success",
        IsWorkflowSuccessful(
            "Is Workflow Successful?", workflow, log_prefix=log_prefix
        ),
    )


def create_workflow_completion_ppa(
    workflow: Workflow,
    package_meta: PackageMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Wait for Completion",
        WaitForWorkflowCompletion(
            "",
            workflow,
            package_meta,
            github_client,
            log_prefix=log_prefix,
        ),
        IsWorkflowCompleted(f"Is Workflow Completed?", workflow, log_prefix=log_prefix),
        IsWorkflowIdentified(
            f"Is Workflow Identified?", workflow, log_prefix=log_prefix
        ),
    )


def create_find_workflow_by_uuid_ppa(
    workflow: Workflow,
    package_meta: PackageMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Find Workflow",
        FindWorkflowByUUID(
            "",
            workflow,
            package_meta,
            github_client,
            log_prefix=log_prefix,
        ),
        IsWorkflowIdentified(
            "Is Workflow Identified?", workflow, log_prefix=log_prefix
        ),
        IsWorkflowTriggered("Is Workflow Triggered?", workflow, log_prefix=log_prefix),
    )


def create_trigger_workflow_ppa(
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Trigger Workflow",
        TriggerWorkflowGuarded(
            "",
            workflow,
            package_meta,
            release_meta,
            github_client,
            log_prefix=log_prefix,
        ),
        IsWorkflowTriggered("Is Workflow Triggered?", workflow, log_prefix=log_prefix),
        IsTargetRefIdentified(
            "Is Target Ref Identified?", package_meta, log_prefix=log_prefix
        ),
    )


def create_identify_target_ref_ppa(
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Identify Target Ref",
        IdentifyTargetRefGuarded(
            "",
            package_meta,
            release_meta,
            github_client,
            log_prefix=log_prefix,
        ),
        IsTargetRefIdentified(
            "Is Target Ref Identified?", package_meta, log_prefix=log_prefix
        ),
    )


def create_download_artifacts_ppa(
    workflow: Workflow,
    package_meta: PackageMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Download Artifacts",
        DownloadArtifactsListGuarded(
            "",
            workflow,
            package_meta,
            github_client,
            log_prefix=log_prefix,
        ),
        HasWorkflowArtifacts(
            "Has Workflow Artifacts?", workflow, log_prefix=log_prefix
        ),
        IsWorkflowSuccessful(
            "Is Workflow Successful?", workflow, log_prefix=log_prefix
        ),
    )


def create_extract_artifact_result_ppa(
    artifact_name: str,
    workflow: Workflow,
    package_meta: PackageMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Extract Artifact Result",
        ExtractArtifactResultGuarded(
            "",
            workflow,
            artifact_name,
            package_meta,
            github_client,
            log_prefix=log_prefix,
        ),
        HasWorkflowResult("Has Workflow Result?", workflow, log_prefix=log_prefix),
        HasWorkflowArtifacts(
            "Has Workflow Artifacts?", workflow, log_prefix=log_prefix
        ),
    )


def create_attach_release_handle_ppa(
    build_workflow: Workflow,
    publish_workflow: Workflow,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    return create_PPA(
        "Attach Release Handle",
        AttachReleaseHandleToPublishWorkflow(
            "Attach Release Handle",
            build_workflow,
            publish_workflow,
            log_prefix=log_prefix,
        ),
    )
