import asyncio
import logging
import threading
from typing import List, Optional

import janus
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Static

logger = logging.getLogger(__name__)


class ChooseAuthDialog(ModalScreen):
    """Dialog for choosing authentication method."""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Choose Authentication Method", classes="dialog-title"),
            Button("Credentials", id="credentials"),
            Button("SSO", id="sso"),
            Button("Back", id="back"),
            classes="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "credentials":
            self.app.show_choose_auth = False  # type: ignore
            self.dismiss("credentials")
        elif event.button.id == "sso":
            self.app.ui_to_tree.put(["set", "auth_choice", "sso"])  # type: ignore
            self.app.show_choose_auth = False  # type: ignore
            self.app.show_sso_progress = True  # type: ignore
            self.dismiss("sso")
        elif event.button.id == "back":
            self.app.show_choose_auth = False  # type: ignore
            self.dismiss("back")


class SSOProgressDialog(ModalScreen):
    """Dialog showing SSO login progress with log output."""

    sso_output_chunks: reactive[List[str]] = reactive(
        ["Starting SSO login process..."], recompose=True
    )

    def compose(self) -> ComposeResult:
        sep = ""
        yield Vertical(
            Static(
                f"SSO Login Progress {len(self.sso_output_chunks)}",
                classes="dialog-title",
            ),
            Static(f"{sep.join(self.sso_output_chunks)}", id="sso-log"),
            Horizontal(
                Button("Cancel", id="cancel", variant="error"),
                Button("Close", id="close", variant="primary"),
                classes="dialog-buttons",
            ),
            classes="dialog progress-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss("cancel")
        elif event.button.id == "close":
            self.dismiss("close")


class SSOApp(App):
    status = reactive("SSO Authentication in progress...")
    show_choose_auth = reactive(False)
    show_sso_progress = reactive(False)

    sso_output_chunks: reactive[List[str]] = reactive(
        ["Starting SSO ~login process..."]
    )

    def __init__(self, tree_to_ui: janus.AsyncQueue, ui_to_tree: janus.SyncQueue):
        super().__init__()
        logger.debug("SSOApp initialized")
        self.tree_to_ui = tree_to_ui
        self.ui_to_tree = ui_to_tree

    def compose(self) -> ComposeResult:
        yield Static(f"{self.status}", id="status")

    def watch_show_choose_auth(self, show: bool) -> None:
        """Show/hide the choose auth dialog when reactive value changes."""
        if show:
            self.push_screen(ChooseAuthDialog())

    def watch_show_sso_progress(self, show: bool) -> None:
        """Show/hide the SSO progress dialog when reactive value changes."""
        if show:
            sso_progress_dialog = SSOProgressDialog().data_bind(
                SSOApp.sso_output_chunks
            )
            self.push_screen(sso_progress_dialog)

    # def watch_sso_log_progress(self, log_lines: List[str]) -> None:
    #     """Update the SSO progress dialog with new log lines."""
    #     if hasattr(self, "sso_progress_dialog") and self.sso_progress_dialog:
    #         self.sso_progress_dialog.update_log_display(log_lines)

    async def on_mount(self) -> None:
        self.run_worker(self.handle_tree_to_ui_messages(), exclusive=True)

    async def handle_tree_to_ui_messages(self) -> None:
        """Handle messages from the tree thread."""
        logger.debug("Starting tree_to_ui handler")
        while True:
            try:
                command = await self.tree_to_ui.get()
                if isinstance(command, list) and len(command) == 3:
                    action, field_name, value = command
                    logger.debug(f"Received command: {action} {field_name} = {value}")
                    if action == "set" and hasattr(self, field_name):
                        setattr(self, field_name, value)
                    else:
                        logger.error(f"Unknown command: {command}")
                elif isinstance(command, list) and len(command) == 2:
                    action, value = command
                    if action == "sso_output_chunk":
                        logger.debug(f"Received {action}: {value}")
                        self.sso_output_chunks.append(value)
                        self.mutate_reactive(SSOApp.sso_output_chunks)
                    else:
                        logger.error(f"Unknown command: {command}")
                elif command == "shutdown":
                    logger.debug("Received shutdown command, exiting app")
                    self.exit()
                    break
                else:
                    logger.debug(f"Unknown command: {command}")
            except Exception as e:
                logger.error(f"Error handling tree_to_ui message: {e}")
                break

    async def on_unmount(self) -> None:
        """Send shutdown signal when app is closing."""
        if self.ui_to_tree:
            try:
                self.ui_to_tree.put("shutdown")
                logger.debug("Sent shutdown signal to ui_to_tree")
            except Exception as e:
                logger.error(f"Failed to send shutdown signal: {e}")


def run_sso_ui(tree_to_ui: janus.AsyncQueue, ui_to_tree: janus.SyncQueue) -> None:
    app = SSOApp(tree_to_ui, ui_to_tree)
    app.run(inline=True)
