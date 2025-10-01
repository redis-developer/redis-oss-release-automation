from time import sleep

from py_trees.composites import Selector, Sequence
from py_trees.decorators import Retry

from ..github_client_async import GitHubClientAsync
from .behaviours import IdentifyWorkflowByUUID, IsWorkflowTriggered, Sleep
from .state import Workflow


class FindWorkflowByUUID(Sequence):
    max_retries: int = 3

    def __init__(
        self, name: str, workflow: Workflow, github_client: GitHubClientAsync
    ) -> None:
        is_workflow_triggered = IsWorkflowTriggered("Is Workflow Triggered?", workflow)
        identify_workflow = IdentifyWorkflowByUUID(
            "Identify Workflow by UUID", workflow, github_client
        )
        sleep = Sleep("Sleep", 5)
        sleep_then_identify = Sequence(
            "Sleep then Identify", memory=True, children=[sleep, identify_workflow]
        )
        identify_loop = Retry(
            f"Retry {self.max_retries} times", sleep_then_identify, self.max_retries
        )

        super().__init__(
            name=name, memory=False, children=[is_workflow_triggered, identify_loop]
        )
