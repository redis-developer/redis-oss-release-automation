"""
Package-specific tree factories.

This module provides factories for creating package-type-specific behaviors
and tree branches. Each package type gets its own factory class that knows
how to create all the specialized behaviors and tree structures for that type.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional

from py_trees.behaviour import Behaviour

from ..models import PackageType
from .behaviours import GenericWorkflowInputs
from .state import PackageMeta, ReleaseMeta, Workflow


class TreeFactory(ABC):
    """Abstract base class for package-specific tree factories.

    Subclasses can override specific methods to customize behavior for
    different package types. Methods not overridden will use the default
    implementation from GenericFactory.
    """

    @abstractmethod
    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        """Create behavior for preparing build workflow inputs."""
        pass

    @abstractmethod
    def create_publish_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        """Create behavior for preparing publish workflow inputs."""
        pass

    # Add more methods as you need different behaviors or tree branches
    # @abstractmethod
    # def create_artifact_handler(self, ...) -> Behaviour:
    #     """Create behavior for handling package artifacts."""
    #     pass
    #
    # @abstractmethod
    # def create_validation_branch(self, ...) -> Sequence:
    #     """Create a tree branch for package validation."""
    #     pass


class GenericFactory(TreeFactory):
    """Default factory for packages without specific customizations.

    This provides the base implementation that other factories can inherit
    and selectively override.
    """

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return GenericWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_publish_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return GenericWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )


class DebianFactory(GenericFactory):
    """Factory for Debian packages.

    Inherits from GenericFactory and overrides only the methods that need
    Debian-specific behavior.
    """

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        from .behaviours import DebianWorkflowInputs

        return DebianWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_publish_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        from .behaviours import DebianWorkflowInputs

        return DebianWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )


class DockerFactory(GenericFactory):
    """Factory for Docker packages.

    Currently uses generic implementations. Override methods as needed
    when Docker-specific behavior is required.
    """

    pass  # Inherits all methods from GenericFactory


# Factory registry
_FACTORIES: Dict[PackageType, TreeFactory] = {
    PackageType.DEBIAN: DebianFactory(),
    PackageType.DOCKER: DockerFactory(),
}

_DEFAULT_FACTORY = GenericFactory()


def get_factory(package_type: Optional[PackageType]) -> TreeFactory:
    """Get the factory for a given package type.

    Args:
        package_type: The package type to get factory for

    Returns:
        TreeFactory instance for the given type, or default factory if not found
    """
    if package_type is None:
        return _DEFAULT_FACTORY
    return _FACTORIES.get(package_type, _DEFAULT_FACTORY)


def create_build_workflow_inputs_behaviour(
    name: str,
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    log_prefix: str,
) -> Behaviour:
    return get_factory(package_meta.package_type).create_build_workflow_inputs(
        name, workflow, package_meta, release_meta, log_prefix
    )


def create_publish_workflow_inputs_behaviour(
    name: str,
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    log_prefix: str,
) -> Behaviour:
    return get_factory(package_meta.package_type).create_publish_workflow_inputs(
        name, workflow, package_meta, release_meta, log_prefix
    )
