import asyncio
import os
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from io import IOBase
from typing import Optional

import janus
from aws_sso_lib.sso import get_boto3_session, get_credentials, login
from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector, Sequence
from py_trees.trees import BehaviourTree
from pydantic import BaseModel

from redis_release.bht.composites import ParallelBarrier

from .behaviours import LoggingAction


class AwsSSODefaults(BaseModel):
    start_url: str = "https://d-9a672d5d56.awsapps.com/start/#"
    sso_region: str = "us-east-2"
    account_id: str = "620187402834"
    role_name: str = "PowerUserAccess"
    region: str = "eu-west-1"


class AwsState(BaseModel):
    """AWS credentials state."""

    model_config = {"arbitrary_types_allowed": True}  # Allow non-serializable types

    aws_sso_defaults: AwsSSODefaults = AwsSSODefaults()
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_session_token: Optional[str] = None
    dialog: Optional[str] = None
    auth_choice: Optional[str] = None
    aws_authenticated: bool = False
    sso_failed: bool = False
    credentials: Optional[dict] = None

    def __init__(self, ui_to_tree: janus.SyncQueue, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._ui_to_tree = ui_to_tree

    def notify_about_update(self) -> None:
        self._ui_to_tree.put("state_updated")


def create_aws_root_node(
    aws_state: AwsState,
    tree_to_ui: janus.SyncQueue,
    ui_to_tree: janus.AsyncQueue,
) -> Behaviour:
    validate_aws_credentials = ValidateAwsCredentials(
        "Validate AWS Credentials", aws_state
    )
    validate_aws_sso = ValidateAwsSSO("Validate AWS SSO", aws_state)

    aws_validators = Selector(
        "AWS Validators",
        memory=False,
        children=[validate_aws_credentials, validate_aws_sso],
    )

    aws_validators_success = OnAwsSuccess("Shutdown UI", tree_to_ui, aws_state)

    aws_validators_process = Sequence(
        "Validate AWS Credentials",
        memory=False,
        children=[aws_validators, aws_validators_success],
    )

    show_choose_auth_dialog = ShowChooseAuthDialog(
        "Show Choose Auth Type Dialog",
        aws_state,
        tree_to_ui,
    )

    sso_login_process = Sequence(
        "AWS SSO UI Flow",
        memory=False,
        children=[
            IsSSOChosen("Is SSO Chosen", aws_state),
            LoginToSSO("Login to SSO", ui_to_tree, tree_to_ui, aws_state),
        ],
    )

    sso_process = Selector(
        "AWS Credentials UI Flow",
        memory=False,
        children=[sso_login_process, show_choose_auth_dialog],
    )

    ui_queue_listener = UiQueueListener(
        "UI Queue Listener",
        ui_to_tree,
        tree_to_ui,
        aws_state,
    )

    ui_process = ParallelBarrier(
        "Run the UI /PARALLEL/",
        memory=False,
        children=[ui_queue_listener, sso_process],
    )

    aws_process = Selector(
        "AWS Get Credentials Goal",
        memory=False,
        children=[aws_validators_process, ui_process],
    )

    return aws_process


def create_aws_tree(
    aws_state: AwsState,
    tree_to_ui: janus.SyncQueue,
    ui_to_tree: janus.AsyncQueue,
) -> BehaviourTree:
    root = create_aws_root_node(aws_state, tree_to_ui, ui_to_tree)
    tree = BehaviourTree(root)
    return tree


class ValidateAwsCredentials(LoggingAction):
    def __init__(self, name: str, aws_state: AwsState, log_prefix: str = "") -> None:
        self.aws_state = aws_state
        self.aws_access_key_id: Optional[str] = None
        self.aws_secret_access_key: Optional[str] = None
        self.aws_session_token: Optional[str] = None
        self.last_result: Optional[Status] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        # Check environment for AWS credentials
        env_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        env_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        env_session_token = os.getenv("AWS_SESSION_TOKEN")

        # Save to AwsState if they exist
        if env_access_key:
            self.aws_state.aws_access_key_id = env_access_key
        if env_secret_key:
            self.aws_state.aws_secret_access_key = env_secret_key
        if env_session_token:
            self.aws_state.aws_session_token = env_session_token

    def update(self) -> Status:
        # Check if credentials are the same as last time
        if (
            self.aws_access_key_id == self.aws_state.aws_access_key_id
            and self.aws_secret_access_key == self.aws_state.aws_secret_access_key
            and self.aws_session_token == self.aws_state.aws_session_token
            and self.last_result is not None
        ):
            return self.last_result

        self.logger.debug(
            f"Checking AWS credentials: {self.aws_state.aws_access_key_id}, {self.aws_state.aws_secret_access_key}, {self.aws_state.aws_session_token}"
        )

        if (
            self.aws_state.aws_access_key_id is None
            or self.aws_state.aws_secret_access_key is None
            or self.aws_state.aws_session_token is None
        ):
            self.logger.info("AWS credentials not found in environment")
            self.last_result = Status.FAILURE
            return Status.FAILURE

        # Update self fields from AwsState
        self.aws_access_key_id = self.aws_state.aws_access_key_id
        self.aws_secret_access_key = self.aws_state.aws_secret_access_key
        self.aws_session_token = self.aws_state.aws_session_token

        # Validate credentials using STS
        try:
            import boto3

            sts_client = boto3.client(
                "sts",
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                aws_session_token=self.aws_session_token,
            )
            sts_client.get_caller_identity()

            self.logger.info("[green]AWS credentials are valid[/green]")
            self.last_result = Status.SUCCESS
            return Status.SUCCESS

        except Exception as e:
            self.logger.error(f"[red]AWS credentials validation failed:[/red] {e}")
            self.last_result = Status.FAILURE
            return Status.FAILURE


class ValidateAwsSSO(LoggingAction):
    def __init__(self, name: str, aws_state: AwsState, log_prefix: str = "") -> None:
        self.aws_state = aws_state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        # If SSO already failed, return failure immediately
        if self.aws_state.sso_failed:
            return Status.FAILURE

        try:
            session = get_boto3_session(
                start_url=self.aws_state.aws_sso_defaults.start_url,
                sso_region=self.aws_state.aws_sso_defaults.sso_region,
                account_id=self.aws_state.aws_sso_defaults.account_id,
                role_name=self.aws_state.aws_sso_defaults.role_name,
                region=self.aws_state.aws_sso_defaults.region,
                login=False,
            )

            sts = session.client("sts")
            sts.get_caller_identity()

            self.logger.info("[green]AWS SSO credentials are valid[/green]")
            self.aws_state.aws_authenticated = True
            self.aws_state.sso_failed = False
            # Get actual AWS credentials using the token
            credentials = get_credentials(
                session=session,
                start_url=self.aws_state.aws_sso_defaults.start_url,
                sso_region=self.aws_state.aws_sso_defaults.sso_region,
                account_id=self.aws_state.aws_sso_defaults.account_id,
                role_name=self.aws_state.aws_sso_defaults.role_name,
            )
            self.aws_state.credentials = credentials

            return Status.SUCCESS

        except Exception as e:
            self.logger.error(f"[red]AWS SSO validation failed:[/red] {e}")
            self.aws_state.sso_failed = True
            return Status.FAILURE


class IsSSOChosen(LoggingAction):
    def __init__(self, name: str, aws_state: AwsState, log_prefix: str = "") -> None:
        self.aws_state = aws_state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if (
            self.aws_state.auth_choice is not None
            and self.aws_state.auth_choice == "sso"
        ):
            return Status.SUCCESS
        return Status.FAILURE


class LoginToSSO(LoggingAction):
    def __init__(
        self,
        name: str,
        ui_to_tree: janus.AsyncQueue,
        tree_to_ui: janus.SyncQueue,
        aws_state: AwsState,
        log_prefix: str = "",
    ) -> None:
        self.aws_state = aws_state
        self.ui_to_tree = ui_to_tree
        self.tree_to_ui = tree_to_ui
        super().__init__(name=name, log_prefix=log_prefix)

    def _run_sso_login_threaded(self) -> None:
        """Run SSO login in a thread with stdout/stderr capture."""
        try:

            class StupidStreamRedirector(IOBase):
                def __init__(self, tree_to_ui: janus.SyncQueue):
                    self.tree_to_ui = tree_to_ui

                def write(self, s):
                    self.tree_to_ui.put(["sso_output_chunk", s])

            to_ui_queue = StupidStreamRedirector(self.tree_to_ui)

            self.logger.info("Starting SSO login process...")
            login(
                start_url=self.aws_state.aws_sso_defaults.start_url,
                sso_region=self.aws_state.aws_sso_defaults.sso_region,
                outfile=to_ui_queue,
            )
            self.logger.info("Completed SSO login process...")
            self.aws_state.sso_failed = False
            self.aws_state.notify_about_update()

        except Exception as e:
            self.tree_to_ui.put(
                ["sso_output_chunk", f"❌ SSO authentication failed: {e}\n"]
            )
            self.aws_state.sso_failed = True

    def initialise(self) -> None:
        self.sso_thread = threading.Thread(
            target=self._run_sso_login_threaded, daemon=True
        )
        self.sso_thread.start()

    def update(self) -> Status:
        # Start the SSO process if not already started
        if self.sso_thread is None:
            self.logger.error("SSO thread is None - behaviour was not initialized")
            return Status.FAILURE

        # Check if the thread is still running
        if self.sso_thread.is_alive():
            return Status.RUNNING

        if self.aws_state.sso_failed:
            return Status.FAILURE
        return Status.SUCCESS


class OnAwsSuccess(LoggingAction):
    def __init__(
        self,
        name: str,
        tree_to_ui: janus.SyncQueue,
        aws_state: AwsState,
        log_prefix: str = "",
    ) -> None:
        self.aws_state = aws_state
        self.tree_to_ui = tree_to_ui
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.tree_to_ui.put("shutdown")
        return Status.SUCCESS


class ShowChooseAuthDialog(LoggingAction):
    def __init__(
        self,
        name: str,
        aws_state: AwsState,
        tree_to_ui: janus.SyncQueue,
        log_prefix: str = "",
    ) -> None:
        self.aws_state = aws_state
        self.tree_to_ui = tree_to_ui
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.aws_state.dialog == "choose_auth":
            return Status.SUCCESS
        self.aws_state.dialog = "choose_auth"

        if self.tree_to_ui:
            try:
                self.tree_to_ui.put(["set", "show_choose_auth", True])
                self.logger.info("[green]Auth dialog shown[/green]")
            except Exception as e:
                self.logger.error(f"[red]Failed to send queue message:[/red] {e}")

        return Status.SUCCESS


class UiQueueListener(LoggingAction):
    def __init__(
        self,
        name: str,
        ui_to_tree: janus.AsyncQueue,
        tree_to_ui: janus.SyncQueue,
        aws_state: AwsState,
        log_prefix: str = "",
    ) -> None:
        self.ui_to_tree = ui_to_tree
        self.tree_to_ui = tree_to_ui
        self.aws_state = aws_state
        self.task: Optional[asyncio.Task] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        self.task = asyncio.create_task(self.ui_to_tree.get())
        self.tree_to_ui.put_nowait("start")

    def update(self) -> Status:
        if not self.ui_to_tree:
            self.logger.error("[red]ui_to_tree is None[/red]")
            return Status.FAILURE

        if self.task is None:
            self.logger.error("[red]Task is None - behaviour was not initialized[/red]")
            return Status.FAILURE

        if not self.task.done():
            return Status.RUNNING

        result = self.task.result()
        messages = [result]
        try:
            while True:
                message = self.ui_to_tree.get_nowait()
                messages.append(message)
        except:
            pass  # Queue is empty

        if messages:
            for message in messages:
                self.logger.info(f"[green]Received UI message:[/green] {message}")
                if type(message) == str and message == "shutdown":
                    self.logger.debug("[green]Received shutdown signal[/green]")
                    if self.aws_state.aws_authenticated:
                        return Status.SUCCESS
                    return Status.FAILURE
                elif type(message) == str and message == "state_updated":
                    self.logger.debug("[green]Received state update signal[/green]")
                elif isinstance(message, list) and len(message) == 3:
                    action, field_name, value = message
                    self.logger.debug(
                        f"[green]Received state set command:[/green] {field_name} = {value}"
                    )
                    if action == "set" and hasattr(self.aws_state, field_name):
                        setattr(self.aws_state, field_name, value)

            # Setup new task to wait for next messages
            if self.task:
                self.task.cancel()
            self.task = asyncio.create_task(self.ui_to_tree.get())

        return Status.RUNNING

    def terminate(self, new_status: Status) -> None:
        if self.task:
            self.task.cancel()
        self.tree_to_ui.put("shutdown")
        super().terminate(new_status)


def print_credentials_as_env_vars(credentials: dict) -> None:
    print(f"export AWS_ACCESS_KEY_ID={credentials['access_key']}")
    print(f"export AWS_SECRET_ACCESS_KEY={credentials['secret_key']}")
    print(f"export AWS_SESSION_TOKEN={credentials['token']}")
