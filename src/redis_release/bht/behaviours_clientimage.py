"""Behaviours specific to client image packages."""

from py_trees.common import Status

from ..models import ReleaseType
from .behaviours import LoggingAction
from .state import ClientImageMeta, PackageMeta, ReleaseMeta, Workflow


class DetectReleaseTypeClientImage(LoggingAction):
    """Detect release type for client image packages.

    Client image packages are always INTERNAL releases.
    """

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.release_meta = release_meta
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        # Client image packages are always internal releases
        if self.package_meta.release_type is not None:
            return Status.SUCCESS

        self.package_meta.release_type = ReleaseType.INTERNAL
        self.feedback_message = "release type is INTERNAL"

        return Status.SUCCESS


class NeedToReleaseClientImage(LoggingAction):
    """Check if client image package needs to be released.

    Client image packages always need to be released.
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
        feedback_message = "Need to release client image"

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            self.logger.info(feedback_message)

        return Status.SUCCESS


class AwaitDockerImage(LoggingAction):
    def __init__(
        self,
        name: str,
        package_meta: ClientImageMeta,
        release_meta: ReleaseMeta,
        docker_package_meta: PackageMeta,
        docker_build_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.docker_package_meta = docker_package_meta
        self.docker_build_workflow = docker_build_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.docker_package_meta.ephemeral.root_node_status == Status.RUNNING:
            return Status.RUNNING

        if self.docker_build_workflow.result is not None:
            return Status.SUCCESS

        self.feedback_message = "Docker workflow result not available"
        self.package_meta.ephemeral.validate_docker_image = Status.FAILURE
        self.package_meta.ephemeral.validate_docker_image_message = (
            self.feedback_message
        )
        return Status.FAILURE


class LocateDockerImage(LoggingAction):
    """Locate a Docker image with specific distro and arch from the docker build result.

    Examines the docker_images_metadata array in the docker build workflow result
    and locates an image with distro=self.expected_distro and arch=self.expected_arch.
    If found, parses the image URL and sets base_image, base_image_tag, and output_image_tag
    on the package_meta.
    """

    expected_distro = "debian"
    expected_arch = "amd64"

    def __init__(
        self,
        name: str,
        package_meta: ClientImageMeta,
        docker_build_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.docker_build_workflow = docker_build_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        try:
            if self.docker_build_workflow.result is None:
                raise ValueError("Docker build workflow result is not available")

            docker_images_metadata = self.docker_build_workflow.result.get(
                "docker_images_metadata"
            )
            if not docker_images_metadata:
                raise ValueError("docker_images_metadata not found in result")

            image_url = next(
                (
                    img.get("url")
                    for img in docker_images_metadata
                    if img.get("distro") == self.expected_distro
                    and img.get("arch") == self.expected_arch
                ),
                None,
            )
            if not image_url:
                raise ValueError(
                    f"No Docker image found with distro={self.expected_distro} and arch={self.expected_arch}"
                )

            # Parse the image URL into image and tag
            image, tag = image_url.rsplit(":", 1)
            if not image or not tag:
                raise ValueError(f"Invalid image URL format: {image_url}")

            self.package_meta.base_image = image
            self.package_meta.base_image_tag = tag
            self.package_meta.output_image_tag = tag

            self.feedback_message = f"Located Docker image: {image}:{tag}"
            if self.log_once(
                "docker_image_located", self.package_meta.ephemeral.log_once_flags
            ):
                self.logger.info(self.feedback_message)
            return_status = Status.SUCCESS

        except ValueError as e:
            self.feedback_message = f"Image url err: {e}"
            return_status = self.log_exception_and_return_failure(e)

        if return_status == Status.FAILURE:
            self.package_meta.ephemeral.validate_docker_image_message = (
                self.feedback_message
            )
        self.package_meta.ephemeral.validate_docker_image = return_status
        return return_status


class ClientImageWorkflowInputs(LoggingAction):
    """Set workflow inputs for client image build.

    Reads base_image, base_image_tag, and output_image_tag from the meta and sets
    them as workflow inputs.
    """

    def __init__(
        self,
        name: str,
        package_meta: ClientImageMeta,
        workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        base_image = self.package_meta.base_image
        base_image_tag = self.package_meta.base_image_tag
        output_image_tag = self.package_meta.output_image_tag

        if not base_image or not base_image_tag or not output_image_tag:
            self.feedback_message = (
                "base_image, base_image_tag, or output_image_tag is not set"
            )
            self.logger.error(self.feedback_message)
            # This will prevent triggering the workflow in a restart loop
            self.workflow.ephemeral.trigger_workflow = Status.FAILURE
            return Status.FAILURE

        # cae-client-testing expects specific image name
        self.workflow.inputs["base_image"] = "docker-library-redis"
        self.workflow.inputs["base_image_tag"] = base_image_tag
        self.workflow.inputs["output_image_tag"] = output_image_tag

        self.feedback_message = (
            f"Set inputs: base_image={base_image}, base_image_tag={base_image_tag}, "
            f"output_image_tag={output_image_tag}"
        )
        if self.log_once(
            "workflow_inputs_set", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(self.feedback_message)

        return Status.SUCCESS
