import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Tuple, Union

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter
from py_trees.display import unicode_tree
from py_trees.trees import BehaviourTree
from rich.text import Text

from ..config import Config
from ..github_client_async import GitHubClientAsync
from .args import ReleaseArgs
from .backchain import latch_chains
from .behaviours import NeedToPublish
from .ppas import (
    create_attach_release_handle_ppa,
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_identify_target_ref_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
    create_workflow_success_ppa,
)
from .state import (
    Package,
    PackageMeta,
    ReleaseMeta,
    ReleaseState,
    S3StateStorage,
    StateSyncer,
    Workflow,
)

logger = logging.getLogger(__name__)


async def async_tick_tock(tree: BehaviourTree, cutoff: int = 100) -> None:
    """Drive Behaviour tree using async event loop

    The tree is always ticked once.

    Next tick happens while there is at least one task completed
    or the tree is in RUNNING state.

    """
    while True:
        tree.tick()
        if tree.count > cutoff:
            logger.error(f"The Tree has not converged, hit cutoff limit {cutoff}")
            break

        other_tasks = asyncio.all_tasks() - {asyncio.current_task()}
        logger.debug(other_tasks)
        if not other_tasks:
            # Let the tree continue running if it's not converged
            if tree.root.status != Status.RUNNING:
                logger.info(f"The Tree has converged to {tree.root.status}")
                break
        else:
            await asyncio.wait(other_tasks, return_when=asyncio.FIRST_COMPLETED)


@contextmanager
def initialize_tree_and_state(
    config: Config, args: ReleaseArgs
) -> Iterator[Tuple[BehaviourTree, StateSyncer]]:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN"))

    # Create S3 storage backend
    storage = S3StateStorage()

    # Create state syncer with storage backend and acquire lock
    with StateSyncer(
        storage=storage,
        config=config,
        args=args,
    ) as state_syncer:
        root = create_root_node(state_syncer.state, github_client)
        tree = BehaviourTree(root)
        tree.add_post_tick_handler(lambda _: state_syncer.sync())
        tree.add_post_tick_handler(log_tree_state_with_markup)

        yield (tree, state_syncer)


def log_tree_state_with_markup(tree: BehaviourTree) -> None:
    rich_markup = Text.from_ansi(
        unicode_tree(tree.root, show_status=True, show_only_visited=False)
    ).markup
    logger.debug(f"\n{rich_markup}")


def create_root_node(
    state: ReleaseState, github_client: GitHubClientAsync
) -> Behaviour:

    root = create_package_release_tree_branch(
        state.packages["docker"], state.meta, github_client, "docker"
    )

    return root


def create_package_release_tree_branch(
    package: Package,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    package_name: str,
) -> Union[Selector, Sequence]:
    build = create_build_workflow_tree_branch(
        package.build,
        package.meta,
        release_meta,
        github_client,
        package_name,
    )
    publish = create_publish_workflow_tree_branch(
        package.build,
        package.publish,
        package.meta,
        release_meta,
        github_client,
        package_name,
    )
    package_release = Sequence(
        f"Package Release: {package_name}",
        memory=False,
        children=[build, publish],
    )
    return package_release


def create_build_workflow_tree_branch(
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    package_name: str,
) -> Union[Selector, Sequence]:
    return create_workflow_with_result_tree_branch(
        "release_handle",
        workflow,
        package_meta,
        release_meta,
        github_client,
        package_name,
    )


def create_publish_workflow_tree_branch(
    build_workflow: Workflow,
    publish_workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    package_name: str,
) -> Union[Selector, Sequence]:
    workflow_result = create_workflow_with_result_tree_branch(
        "release_info",
        publish_workflow,
        package_meta,
        release_meta,
        github_client,
        package_name,
    )
    attach_release_handle = create_attach_release_handle_ppa(
        build_workflow, publish_workflow, package_name
    )
    latch_chains(workflow_result, attach_release_handle)

    not_need_to_publish = Inverter(
        "Not",
        NeedToPublish(
            "Need To Publish?", package_meta, release_meta, log_prefix=package_name
        ),
    )
    return Selector(
        "Publish", memory=False, children=[not_need_to_publish, workflow_result]
    )


def create_workflow_with_result_tree_branch(
    artifact_name: str,
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    package_name: str,
) -> Union[Selector, Sequence]:
    """
    Creates a workflow process that succedes when the workflow
    is successful and a result artifact is extracted and json decoded.
    """
    workflow_result = create_extract_result_tree_branch(
        artifact_name,
        workflow,
        package_meta,
        github_client,
        package_name,
    )
    workflow_complete = create_workflow_complete_tree_branch(
        workflow,
        package_meta,
        release_meta,
        github_client,
        package_name,
    )

    latch_chains(workflow_result, workflow_complete)

    return workflow_result


def create_workflow_complete_tree_branch(
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    workflow_complete = create_workflow_completion_ppa(
        workflow,
        package_meta,
        github_client,
        log_prefix,
    )
    find_workflow_by_uud = create_find_workflow_by_uuid_ppa(
        workflow,
        package_meta,
        github_client,
        log_prefix,
    )
    trigger_workflow = create_trigger_workflow_ppa(
        workflow,
        package_meta,
        release_meta,
        github_client,
        log_prefix,
    )
    identify_target_ref = create_identify_target_ref_ppa(
        package_meta,
        release_meta,
        log_prefix,
    )
    latch_chains(
        workflow_complete,
        find_workflow_by_uud,
        trigger_workflow,
        identify_target_ref,
    )
    return workflow_complete


def create_extract_result_tree_branch(
    artifact_name: str,
    workflow: Workflow,
    package_meta: PackageMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
    extract_artifact_result = create_extract_artifact_result_ppa(
        artifact_name,
        workflow,
        package_meta,
        github_client,
        log_prefix,
    )
    download_artifacts = create_download_artifacts_ppa(
        workflow,
        package_meta,
        github_client,
        log_prefix,
    )
    latch_chains(extract_artifact_result, download_artifacts)
    return extract_artifact_result
