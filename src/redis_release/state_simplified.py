"""Simplified per-package view of a release state.

Encapsulates the rules used by both the text printer and the export CLI:
- The "overall" package status is the publish status if it has progressed,
  otherwise the build status.
- Packages whose overall status is NOT_STARTED are omitted.
- Per-workflow URL is None when the workflow is NOT_STARTED.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from redis_release.bht.state import ReleaseState, Workflow
from redis_release.state_display import Section, Step, StepStatus, get_display_model
from redis_release.state_slack import get_workflow_link


@dataclass
class SimplifiedWorkflow:
    status: StepStatus
    url: Optional[str]
    steps: List[Union[Step, Section]]


@dataclass
class SimplifiedPackage:
    package_name: str
    status: StepStatus
    build: SimplifiedWorkflow
    publish: Optional[SimplifiedWorkflow]


class StateSimplifier:
    """Builds a simplified per-package view of a ReleaseState."""

    def __init__(self, state: ReleaseState) -> None:
        self.state = state
        self.packages: List[SimplifiedPackage] = self._build()

    def _build(self) -> List[SimplifiedPackage]:
        result: List[SimplifiedPackage] = []
        for package_name, package in self.state.packages.items():
            display_model = get_display_model(package.meta)
            build_status = display_model.get_workflow_status(package, package.build)
            publish_status: Optional[Tuple[StepStatus, List[Union[Step, Section]]]] = (
                None
            )
            if package.publish is not None:
                publish_status = display_model.get_workflow_status(
                    package, package.publish
                )

            overall = (
                publish_status[0]
                if publish_status is not None
                and publish_status[0] != StepStatus.NOT_STARTED
                else build_status[0]
            )
            if overall == StepStatus.NOT_STARTED:
                continue

            repo = package.meta.repo
            result.append(
                SimplifiedPackage(
                    package_name=package_name,
                    status=overall,
                    build=self._simplified_workflow(repo, package.build, build_status),
                    publish=(
                        self._simplified_workflow(repo, package.publish, publish_status)
                        if package.publish is not None and publish_status is not None
                        else None
                    ),
                )
            )
        return result

    @staticmethod
    def _simplified_workflow(
        repo: str,
        workflow: Workflow,
        workflow_status: Tuple[StepStatus, List[Union[Step, Section]]],
    ) -> SimplifiedWorkflow:
        status, steps = workflow_status
        url: Optional[str] = None
        if status != StepStatus.NOT_STARTED:
            url = workflow.url or get_workflow_link(repo, workflow.run_id)
        return SimplifiedWorkflow(status=status, url=url, steps=steps)

    def to_json(self) -> Dict[str, Dict[str, Any]]:
        """Render the simplified state as a dict keyed by package name."""
        return {
            p.package_name: {
                "status": p.status.value,
                "build": {"status": p.build.status.value, "url": p.build.url},
                "publish": (
                    {"status": p.publish.status.value, "url": p.publish.url}
                    if p.publish is not None
                    else None
                ),
            }
            for p in self.packages
        }
