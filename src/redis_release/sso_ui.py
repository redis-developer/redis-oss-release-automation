import asyncio
import logging
import threading
from typing import Optional

import janus
from textual.app import App
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
            self.app.show_choose_auth = False
            self.dismiss("credentials")
        elif event.button.id == "sso":
            self.app.ui_to_tree.put(["set", "auth_choice", "sso"])
            self.app.show_choose_auth = False
            self.dismiss("sso")
        elif event.button.id == "back":
            self.app.show_choose_auth = False
            self.dismiss("back")


class SSOApp(App):
    status = reactive("SSO Authentication in progress...")
    show_choose_auth = reactive(False)

    def __init__(self, tree_to_ui: janus.AsyncQueue, ui_to_tree: janus.SyncQueue):
        super().__init__()
        print(f"SSOApp initialized with queue: {tree_to_ui}")
        logger.debug("SSOApp initialized")
        self.tree_to_ui = tree_to_ui
        self.ui_to_tree = ui_to_tree

    def compose(self):
        yield Static(self.status, id="status")

    def watch_show_choose_auth(self, show: bool) -> None:
        """Show/hide the choose auth dialog when reactive value changes."""
        if show:
            self.push_screen(ChooseAuthDialog())

    async def on_mount(self) -> None:
        self.run_worker(self.handle_tree_to_ui_messages(), exclusive=True)

    async def handle_tree_to_ui_messages(self) -> None:
        """Handle messages from the tree thread."""
        logger.debug("Starting tree_to_ui handler")
        while True:
            try:
                command = await self.tree_to_ui.get()
                logger.debug(f"Received command: {command}")
                if isinstance(command, list) and len(command) == 3:
                    action, field_name, value = command
                    if action == "set" and hasattr(self, field_name):
                        setattr(self, field_name, value)
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
    app.run()
