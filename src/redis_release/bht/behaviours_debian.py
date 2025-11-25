from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction
from redis_release.bht.state import PackageMeta, ReleaseMeta

# Conditions


class NeedToReleaseDebian(LoggingAction):
    """Check if Debian package needs to be released."""

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
        # Debian packages are always released
        return Status.SUCCESS
