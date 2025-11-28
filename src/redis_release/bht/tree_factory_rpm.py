from py_trees.behaviour import Behaviour

from redis_release.bht.behaviours_rpm import DetectReleaseTypeRPM, NeedToReleaseRPM
from redis_release.bht.state import PackageMeta, ReleaseMeta
from redis_release.bht.tree_factory_generic import GenericPackageFactory


class RPMFactory(GenericPackageFactory):
    def create_need_to_release_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return NeedToReleaseRPM(name, package_meta, release_meta, log_prefix=log_prefix)

    def create_detect_release_type_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return DetectReleaseTypeRPM(
            name, package_meta, release_meta, log_prefix=log_prefix
        )
