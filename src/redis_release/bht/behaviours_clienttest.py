"""Behaviours specific to client test packages."""

from py_trees.common import Status

from ..models import ReleaseType
from .behaviours import LoggingAction
from .state import ClientTestMeta, PackageMeta, ReleaseMeta, Workflow


class DetectReleaseTypeClientTest(LoggingAction):
    """Detect release type for client test packages.

    Client test packages are always INTERNAL releases.
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
        # Client test packages are always internal releases
        if self.package_meta.release_type is not None:
            return Status.SUCCESS

        self.package_meta.release_type = ReleaseType.INTERNAL
        self.feedback_message = "release type is INTERNAL"

        return Status.SUCCESS


class NeedToReleaseClientTest(LoggingAction):
    """Check if client test package needs to be released.

    Client test packages always need to be released.
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
        feedback_message = "Need to release client test"

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            self.logger.info(feedback_message)

        return Status.SUCCESS


class AwaitClientImage(LoggingAction):
    """Wait for clientimage package to complete."""

    def __init__(
        self,
        name: str,
        package_meta: ClientTestMeta,
        release_meta: ReleaseMeta,
        clientimage_package_meta: PackageMeta,
        clientimage_build_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.clientimage_package_meta = clientimage_package_meta
        self.clientimage_build_workflow = clientimage_build_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.clientimage_package_meta.ephemeral.root_node_status == Status.RUNNING:
            self.package_meta.ephemeral.await_client_image = Status.RUNNING
            return Status.RUNNING

        if self.clientimage_build_workflow.result is not None:
            self.package_meta.ephemeral.await_client_image = Status.SUCCESS
            return Status.SUCCESS

        self.feedback_message = "Client image workflow result not available"
        self.package_meta.ephemeral.validate_client_image = Status.FAILURE
        self.package_meta.ephemeral.validate_client_image_message = (
            self.feedback_message
        )
        self.package_meta.ephemeral.await_client_image = Status.FAILURE
        return Status.FAILURE


class LocateClientImage(LoggingAction):
    """Locate the client test image tag from the clientimage build result.

    Reads the output_image_tag from the clientimage build workflow result
    and sets it as client_test_image on the package_meta.
    """

    def __init__(
        self,
        name: str,
        package_meta: ClientTestMeta,
        clientimage_build_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.clientimage_build_workflow = clientimage_build_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        try:
            if self.clientimage_build_workflow.result is None:
                raise ValueError("Client image build workflow result is not available")

            client_test_image = self.clientimage_build_workflow.result.get(
                "client_test_image"
            )
            if not client_test_image:
                raise ValueError("client_test_image not found in result")

            # Extract tag from full image string (e.g., "redislabs/client-libs-test:custom-21334138669-debian-amd64")
            # We want just the part after the colon: "custom-21334138669-debian-amd64"
            if ":" in client_test_image:
                image_tag = client_test_image.split(":", 1)[1]
            else:
                raise ValueError(
                    f"client_test_image does not contain ':' separator: {client_test_image}"
                )

            self.package_meta.client_test_image = image_tag

            self.feedback_message = (
                f"Located client test image tag: {image_tag} (from {client_test_image})"
            )
            if self.log_once(
                "client_image_located", self.package_meta.ephemeral.log_once_flags
            ):
                self.logger.info(self.feedback_message)
            return_status = Status.SUCCESS

        except ValueError as e:
            self.feedback_message = f"Image tag error: {e}"
            return_status = self.log_exception_and_return_failure(e)

        if return_status == Status.FAILURE:
            self.package_meta.ephemeral.validate_client_image_message = (
                self.feedback_message
            )
        self.package_meta.ephemeral.validate_client_image = return_status
        return return_status


class ClientTestWorkflowInputs(LoggingAction):
    """Set workflow inputs for client test workflow.

    Reads client_test_image from the meta and sets it as workflow input.
    """

    def __init__(
        self,
        name: str,
        package_meta: ClientTestMeta,
        workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.workflow = workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        client_test_image = self.package_meta.client_test_image

        if not client_test_image:
            self.feedback_message = "client_test_image is not set"
            self.logger.error(self.feedback_message)
            # This will prevent triggering the workflow in a restart loop
            self.workflow.ephemeral.trigger_workflow = Status.FAILURE
            return Status.FAILURE

        self.workflow.inputs["client_test_image"] = client_test_image

        self.feedback_message = f"Set inputs: client_test_image={client_test_image}"
        if self.log_once(
            "workflow_inputs_set", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(self.feedback_message)

        return Status.SUCCESS

