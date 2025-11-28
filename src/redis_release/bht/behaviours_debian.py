from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction
from redis_release.bht.state import PackageMeta, ReleaseMeta
from redis_release.models import RedisVersion

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
        self.release_version: Optional[RedisVersion] = None

        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return
        if self.release_version is not None:
            return

        if self.release_meta.tag == "unstable":
            return
        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return
        pass

    def update(self) -> Status:
        result: Status = Status.FAILURE
        if self.release_meta.tag is None:
            self.feedback_message = "Release tag is not set"
            result = Status.FAILURE
        if self.release_meta.tag == "unstable":
            self.feedback_message = "Skip unstable release for debian"
            result = Status.FAILURE

        if self.release_version is not None:
            self.feedback_message = "Need to release debian"
            result = Status.SUCCESS

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            self.logger.info(self.feedback_message)
        return result
