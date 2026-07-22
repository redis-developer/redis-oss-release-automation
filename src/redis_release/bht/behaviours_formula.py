"""Behaviours specific to the homebrew-formula test package.

The formula package runs this repo's `homebrew-formula.yml` workflow to test a
homebrew-core redis formula. It is test-only (no publish) and always releases.
"""

from py_trees.common import Status

from ..models import ReleaseType
from .behaviours import LoggingAction
from .state import PackageMeta, ReleaseMeta, Workflow


class DetectReleaseTypeFormula(LoggingAction):
    """Formula testing is always an INTERNAL release."""

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is None:
            self.package_meta.release_type = ReleaseType.INTERNAL
        self.feedback_message = f"release type: {self.package_meta.release_type.value}"
        return Status.SUCCESS


class FormulaWorkflowInputs(LoggingAction):
    """Set inputs for homebrew-formula.yml.

    Maps the release tag to `redis_version`; formula_repo/formula_branch/redis_repo
    come from the static build_inputs in config.yaml.
    """

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=f"{name} - formula", log_prefix=log_prefix)

    def update(self) -> Status:
        if self.release_meta.tag is not None:
            self.workflow.inputs["redis_version"] = self.release_meta.tag
        self.feedback_message = f"Set inputs: {self.workflow.inputs}"
        if self.log_once(
            "workflow_inputs_set", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(self.feedback_message)
        return Status.SUCCESS
