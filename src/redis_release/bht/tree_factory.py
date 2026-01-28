"""
Package-specific tree factories and factory functions.
"""

import logging
from typing import Dict, Optional, Union

from redis_release.bht.tree_factory_clientimage import ClientImageFactory
from redis_release.bht.tree_factory_clienttest import ClientTestFactory
from redis_release.bht.tree_factory_debian import DebianFactory
from redis_release.bht.tree_factory_docker import DockerFactory
from redis_release.bht.tree_factory_generic import GenericPackageFactory
from redis_release.bht.tree_factory_homebrew import HomebrewFactory
from redis_release.bht.tree_factory_rpm import RPMFactory
from redis_release.bht.tree_factory_snap import SnapFactory

from ..models import PackageType

logger = logging.getLogger(__name__)


# Factory registry
_FACTORIES: Dict[PackageType, GenericPackageFactory] = {
    PackageType.DOCKER: DockerFactory(),
    PackageType.DEBIAN: DebianFactory(),
    PackageType.RPM: RPMFactory(),
    PackageType.HOMEBREW: HomebrewFactory(),
    PackageType.SNAP: SnapFactory(),
    PackageType.CLIENTIMAGE: ClientImageFactory(),
    PackageType.CLIENTTEST: ClientTestFactory(),
}

_DEFAULT_FACTORY = GenericPackageFactory()


def get_factory(package_type: Optional[PackageType]) -> GenericPackageFactory:
    """Get the factory for a given package type.

    Args:
        package_type: The package type to get factory for

    Returns:
        TreeFactory instance for the given type, or default factory if not found
    """
    if package_type is None:
        return _DEFAULT_FACTORY
    return _FACTORIES.get(package_type, _DEFAULT_FACTORY)
