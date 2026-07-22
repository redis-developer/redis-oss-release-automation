from py_trees.behaviour import Behaviour

from .behaviours_formula import DetectReleaseTypeFormula, FormulaWorkflowInputs
from .state import PackageMeta, ReleaseMeta, Workflow
from .tree_factory_generic import GenericPackageFactory


class FormulaFactory(GenericPackageFactory):
    """Factory for the homebrew-formula test package.

    Test-only (no publish); reuses the generic build/trigger/result machinery.
    The build workflow uploads a `result` artifact, same contract as client tests.
    """

    build_result_artifact_name = "result"

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return FormulaWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_detect_release_type_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return DetectReleaseTypeFormula(
            name, package_meta, release_meta, log_prefix=log_prefix
        )
