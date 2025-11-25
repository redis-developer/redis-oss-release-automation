from py_trees.behaviour import Behaviour

from redis_release.bht.behaviours_debian import NeedToReleaseDebian
from redis_release.bht.state import PackageMeta, ReleaseMeta
from redis_release.bht.tree_factory_generic import GenericPackageFactory


class DebianFactory(GenericPackageFactory):
    def create_need_to_release_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return NeedToReleaseDebian(
            name, package_meta, release_meta, log_prefix=log_prefix
        )
