from time import sleep

from py_trees.composites import Selector, Sequence
from py_trees.decorators import Retry

from ..github_client_async import GitHubClientAsync
from .behaviours import IdentifyWorkflowByUUID, IsWorkflowTriggered, Sleep
from .state import Workflow


class FindWorkflowByUUID(Sequence):
    max_retries: int = 3

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
        sleep = Sleep("Sleep", 5)
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

        super().__init__(
            name=name, memory=False, children=[is_workflow_triggered, identify_loop]
        )
