"""Behaviours specific to the homebrew-formula test package.

The formula package runs this repo's `homebrew-formula.yml` workflow to test a
homebrew-core redis formula. It is test-only (no publish) and always releases.
"""

from py_trees.common import Status

from ..models import RedisVersion, ReleaseType
from .behaviours import LoggingAction
from .state import PackageMeta, ReleaseMeta, Workflow

# The `make tarball` target the formula workflow relies on exists from 8.10 onward.
MIN_FORMULA_VERSION = (8, 10)


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


class NeedToReleaseFormula(LoggingAction):
    """Only test the formula for `unstable` or releases >= 8.10.

    Earlier releases lack the `make tarball` target the workflow depends on, so
    they are skipped (FAILURE, inverted by the tree into a clean skip).
    """

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
        tag = self.release_meta.tag
        if tag == "unstable":
            self.feedback_message = "Test formula for unstable"
            return Status.SUCCESS

        if not tag:
            self.feedback_message = "Release tag is not set, skipping formula"
            return Status.FAILURE

        try:
            version = RedisVersion.parse(tag)
        except ValueError:
            self.feedback_message = f"Skip formula for non-version release {tag}"
            if self.log_once(
                "formula_need_to_release", self.package_meta.ephemeral.log_once_flags
            ):
                self.logger.info(self.feedback_message)
            return Status.FAILURE

        if (version.major, version.minor) >= MIN_FORMULA_VERSION:
            self.feedback_message = f"Test formula for {tag}"
            return Status.SUCCESS

        self.feedback_message = (
            f"Skip formula for {tag} (< {MIN_FORMULA_VERSION[0]}.{MIN_FORMULA_VERSION[1]})"
        )
        if self.log_once(
            "formula_need_to_release", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(self.feedback_message)
        return Status.FAILURE
