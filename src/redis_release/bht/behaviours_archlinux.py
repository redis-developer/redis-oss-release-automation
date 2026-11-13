import asyncio
import re
from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction, ReleaseAction
from redis_release.bht.state import ArchlinuxMeta, ReleaseMeta, Workflow
from redis_release.github_client_async import GitHubClientAsync
from redis_release.models import RedisVersion, ReleaseType


class DetectReleaseTypeArchlinux(LoggingAction):
    """Detect release type for the archlinux package.

    A release type forced in state wins. Otherwise a GA tag releases as public
    to the stable [redis] repo and every other version tag as internal to
    [redis-testing]. ``unstable`` and non-version tags leave the release type
    unset, ClassifyArchlinuxVersion skips them.
    """

    def __init__(
        self,
        name: str,
        package_meta: ArchlinuxMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return Status.FAILURE

        if self.package_meta.release_type is not None:
            self.feedback_message = (
                f"Release type for archlinux (from state): "
                f"{self.package_meta.release_type}"
            )
        elif self.release_meta.tag == "unstable":
            self.feedback_message = "No release type for archlinux unstable tag"
        else:
            try:
                release_version = RedisVersion.parse(self.release_meta.tag)
            except ValueError:
                self.feedback_message = (
                    f"No release type for archlinux non-version tag: "
                    f"{self.release_meta.tag}"
                )
            else:
                self.package_meta.release_type = (
                    ReleaseType.PUBLIC
                    if release_version.is_ga
                    else ReleaseType.INTERNAL
                )
                self.feedback_message = (
                    f"Detected release type for archlinux: "
                    f"{self.package_meta.release_type}"
                )

        if self.log_once(
            "release_type_detected", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(f"[green]{self.feedback_message}[/green]")
        return Status.SUCCESS


class ArchlinuxWorkflowInputs(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: ArchlinuxMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=f"{name} - archlinux", log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is not None:
            self.workflow.inputs["release_type"] = self.package_meta.release_type.value
        if self.release_meta.tag is not None:
            self.workflow.inputs["release_tag"] = self.release_meta.tag
        if self.release_meta.ephemeral.slack_channel_id is not None:
            self.workflow.inputs["slack_channel_id"] = (
                self.release_meta.ephemeral.slack_channel_id
            )
        if self.release_meta.ephemeral.slack_thread_ts is not None:
            self.workflow.inputs["slack_thread_ts"] = (
                self.release_meta.ephemeral.slack_thread_ts
            )
        return Status.SUCCESS


class ClassifyArchlinuxVersion(ReleaseAction):
    """Guard the stable [redis] repo against downgrades.

    A public release is acceptable if its version is >= the ``pkgver`` in the
    ``.SRCINFO`` file on ``repo@ref``, read back as the tag it was made from
    (``8.12rc2`` is ``8.12-rc2``). An internal release goes to
    [redis-testing], which takes any version, so it is always acceptable and
    the file is not downloaded. ``unstable`` and non-version tags are skipped.

    Reads ``release_type`` from state, so DetectReleaseTypeArchlinux must run
    first.
    """

    def __init__(
        self,
        name: str,
        package_meta: ArchlinuxMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.github_client = github_client
        self.task: Optional[asyncio.Task] = None
        self.release_version: Optional[RedisVersion] = None
        self.remote_version: Optional[RedisVersion] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return

        self.feedback_message = ""

        if not self.package_meta.repo:
            self.logger.error("Package repository is not set")
            return

        if not self.package_meta.ref:
            self.logger.error("Package ref is not set")
            return

        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return

        if self.release_meta.tag == "unstable":
            self.package_meta.ephemeral.is_version_acceptable = False
            self.feedback_message = "Skip unstable release for archlinux"
            self.logger.info(self.feedback_message)
            # remote_version must be set as it is a sign of successful classify step
            self.package_meta.remote_version = "unstable"
            return

        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
        except ValueError:
            # Non-version ref (e.g. custom build) must not move the released version
            self.logger.info(
                f"Release tag is not a version, skipping archlinux release: "
                f"{self.release_meta.tag}"
            )
            self.package_meta.ephemeral.is_version_acceptable = False
            self.package_meta.remote_version = "custom"
            return

        if self.package_meta.release_type is None:
            self.logger.error("Package release type is not set")
            return

        if self.package_meta.release_type == ReleaseType.INTERNAL:
            self.package_meta.ephemeral.is_version_acceptable = True
            self.package_meta.remote_version = "internal"
            self.logger.info(
                f"[green]Version acceptable: internal release "
                f"{self.release_version}, downgrade guard not applied[/]"
            )
            return

        version_file = ".SRCINFO"
        self.logger.debug(
            f"Downloading {version_file} from "
            f"{self.package_meta.repo}@{self.package_meta.ref}"
        )
        self.task = asyncio.create_task(
            self.github_client.download_file(
                self.package_meta.repo,
                version_file,
                self.package_meta.ref,
            )
        )

    def update(self) -> Status:
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return Status.SUCCESS

        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            srcinfo = self.task.result()
            if srcinfo is None:
                self.logger.error("Failed to download .SRCINFO file")
                return Status.FAILURE

            version_str: Optional[str] = None
            for line in srcinfo.splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() == "pkgver":
                    version_str = value.strip()
                    break
            if version_str is None:
                self.logger.error(
                    f"No pkgver line in .SRCINFO on "
                    f"{self.package_meta.repo}@{self.package_meta.ref}"
                )
                return Status.FAILURE

            # pkgver is the release tag without its dash (8.12-rc2 -> 8.12rc2)
            pkgver_match = re.fullmatch(
                r"(\d+\.\d+(?:\.\d+)?)([a-z]+\d+)?", version_str
            )
            if pkgver_match is None:
                self.logger.error(f"Unsupported pkgver in .SRCINFO: '{version_str}'")
                return Status.FAILURE
            numeric, suffix = pkgver_match.groups()
            version_str = f"{numeric}-{suffix}" if suffix else numeric

            try:
                self.remote_version = RedisVersion.parse(version_str)
                self.logger.info(
                    f"Remote version: {self.remote_version}, "
                    f"Release version: {self.release_version}"
                )
            except ValueError as e:
                self.logger.error(
                    f"Failed to parse remote version '{version_str}': {e}"
                )
                return Status.FAILURE

            assert self.release_version is not None
            self.package_meta.remote_version = str(self.remote_version)
            log_prepend = ""
            prepend_color = "green"
            if self.release_version >= self.remote_version:
                self.package_meta.ephemeral.is_version_acceptable = True
                self.feedback_message = (
                    f"release {self.release_version} >= remote {self.remote_version}"
                )
                log_prepend = "Version acceptable: "
            else:
                self.package_meta.ephemeral.is_version_acceptable = False
                log_prepend = "Version NOT acceptable: "
                prepend_color = "yellow"
                self.feedback_message = (
                    f"release {self.release_version} < remote {self.remote_version}"
                )
                self.package_meta.ephemeral.skip_message = self.feedback_message
            if self.log_once(
                "archlinux_version_classified",
                self.package_meta.ephemeral.log_once_flags,
            ):
                self.logger.info(
                    f"[{prepend_color}]{log_prepend}{self.feedback_message}[/]"
                )
            return Status.SUCCESS

        except Exception as e:
            return self.log_exception_and_return_failure(e)


class NeedToReleaseArchlinux(LoggingAction):
    """Release archlinux only if ClassifyArchlinuxVersion accepted the version."""

    def __init__(
        self,
        name: str,
        package_meta: ArchlinuxMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.ephemeral.is_version_acceptable is True:
            return Status.SUCCESS
        return Status.FAILURE
