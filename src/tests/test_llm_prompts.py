"""Tests for LLM prompts.

These tests are used to validate and iterate on LLM prompts.
They only run when OPENAI_API_KEY is set in the environment.

Usage:
    OPENAI_API_KEY=sk-xxx uv run pytest src/tests/test_llm_prompts.py -v

To see LLM responses, set LLM_DEBUG=1:
    LLM_DEBUG=1 OPENAI_API_KEY=sk-xxx uv run pytest src/tests/test_llm_prompts.py -v -s
"""

import logging
import os
from pprint import pformat
from typing import Any, List, Optional

import pytest
from openai import OpenAI
from py_trees.common import Status


@pytest.fixture(autouse=True)
def set_log_level_info(caplog: pytest.LogCaptureFixture) -> None:
    """Set log level to INFO for all tests in this module."""
    caplog.set_level(logging.INFO)


from redis_release.bht.conversation_llm import LLMActionHandler
from redis_release.bht.conversation_state import ConversationState
from redis_release.config import Config, PackageConfig
from redis_release.conversation_models import (
    BotReply,
    Command,
    ConversationCockpit,
    InboxMessage,
    UserIntent,
)
from redis_release.models import PackageType

# Skip all tests in this module if OPENAI_API_KEY is not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY environment variable not set",
)


def is_llm_debug() -> bool:
    """Check if LLM_DEBUG is enabled (checked at runtime)."""
    return os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes")


def print_llm_response(label: str, data: Any) -> None:
    """Print LLM response data if LLM_DEBUG is enabled."""
    if is_llm_debug():
        print(f"\n{'='*60}")
        print(f"LLM Response: {label}")
        print(f"{'='*60}")
        print(pformat(data))
        print(f"{'='*60}\n")


def print_action_result(label: str, result: Status, state: ConversationState) -> None:
    """Print action handler result if LLM_DEBUG is enabled."""
    print_llm_response(
        label,
        {
            "status": result,
            "command": state.command,
            "user_release_args": (
                state.user_release_args.model_dump()
                if state.user_release_args
                else None
            ),
            "replies": [
                r.text if isinstance(r, BotReply) else str(r) for r in state.replies
            ],
        },
    )


def print_intent_result(label: str, result: Status, state: ConversationState) -> None:
    """Print intent detector result if LLM_DEBUG is enabled."""
    print_llm_response(
        label,
        {
            "status": result,
            "detected_intent": state.user_intent,
        },
    )


class ConversationBuilder:
    """Builder for constructing conversation history for testing."""

    def __init__(self) -> None:
        self._context: List[InboxMessage] = []
        self._current_message: Optional[InboxMessage] = None

    def user(self, message: str, is_mention: bool = False) -> "ConversationBuilder":
        """Add a user message to the conversation context."""
        self._context.append(
            InboxMessage(
                message=message,
                user="U12345",
                is_from_bot=False,
                is_mention=is_mention,
            )
        )
        return self

    def bot(self, message: str) -> "ConversationBuilder":
        """Add a bot message to the conversation context."""
        self._context.append(
            InboxMessage(
                message=message,
                is_from_bot=True,
            )
        )
        return self

    def current(self, message: str, is_mention: bool = False) -> "ConversationBuilder":
        """Set the current user message (the message being processed)."""
        self._current_message = InboxMessage(
            message=message,
            user="U12345",
            is_from_bot=False,
            is_mention=is_mention,
        )
        return self

    def build_state(
        self,
        llm_available: bool = True,
        llm_confirmation_required: bool = True,
    ) -> ConversationState:
        """Build the ConversationState from the conversation history."""
        if self._current_message is None:
            raise ValueError("Current message must be set using .current()")

        return ConversationState(
            llm_available=llm_available,
            llm_confirmation_required=llm_confirmation_required,
            message=self._current_message,
            context=self._context if self._context else None,
            emojis=["thumbsup", "thumbsdown", "rocket", "thinking"],
        )


@pytest.fixture
def config() -> Config:
    """Provide a minimal test config."""
    return Config(
        version=1,
        packages={
            "docker": PackageConfig(
                repo="redis/docker-redis",
                package_type=PackageType.DOCKER,
                build_workflow="build.yml",
                publish_workflow="publish.yml",
                allow_custom_build=True,
            ),
            "debian": PackageConfig(
                repo="redis/debian",
                package_type=PackageType.DEBIAN,
                build_workflow="build.yml",
                publish_workflow="publish.yml",
            ),
        },
    )


@pytest.fixture
def cockpit() -> ConversationCockpit:
    """Provide a ConversationCockpit with LLM initialized."""
    api_key = os.environ.get("OPENAI_API_KEY")
    cp = ConversationCockpit()
    cp.llm = OpenAI(api_key=api_key)
    return cp


class TestLLMActionHandler:
    """Tests for LLMActionHandler prompt behavior."""

    def test_detect_custom_build_request(
        self, config: Config, cockpit: ConversationCockpit
    ) -> None:
        """Test that LLM detects a custom build request correctly."""
        state = (
            ConversationBuilder()
            .current("Build 8.6-rc1 with redisjson 8.6-rc1", is_mention=True)
            .build_state()
        )

        handler = LLMActionHandler(
            name="Test Action Handler",
            state=state,
            cockpit=cockpit,
            config=config,
        )

        result = handler.update()
        print_action_result("Action Handler Result", result, state)

        assert result == Status.SUCCESS
        assert state.command == Command.RELEASE
        assert state.user_release_args is not None
        assert state.user_release_args.release_tag == "8.6-rc1"
        assert state.user_release_args.custom_build is True
        assert len(state.user_release_args.module_versions) == 1
        assert state.user_release_args.module_versions[0].module_name == "redisjson"
        assert state.user_release_args.module_versions[0].version == "8.6-rc1"

    def test_multi_turn_run_tests_unstable(
        self, config: Config, cockpit: ConversationCockpit
    ) -> None:
        """Test multi-turn conversation: user gradually provides details for running tests.

        Conversation from slack.log 2026-02-04 17:46:31:
        - User: "hey bot, let's run" (mention)
        - Bot: asks for more details
        - User: "run tests"
        - Bot: asks for Redis version, modules, packages
        - User: "unstable"

        Expected: custom build for unstable branch with default modules.
        """
        state = (
            ConversationBuilder()
            .user("<@U0ACB7T9DDE> hey bot, let's run", is_mention=True)
            .bot(
                "It looks like you want to start some action, but I need more "
                "details to proceed.\n\n"
                "Could you please specify which release tag, branch, or package "
                "you'd like to work with? You may also indicate if you want to "
                "check the status of a release, or if you want to use custom "
                "module versions."
            )
            .user("run tests")
            .bot(
                "I see you want to run tests, but I'll need more information to proceed:\n\n"
                "- Which Redis version or branch would you like to test? (e.g., "
                "8.4-m01, unstable, or another branch)\n"
                "- Would you like to specify any particular module versions or just "
                "test with the defaults?\n"
                "- Are there specific packages or environments you'd like to target "
                "(e.g., docker, debian)?\n\n"
                "Please provide these details so I can prepare the correct test run for you."
            )
            .current("unstable")
            .build_state()
        )

        handler = LLMActionHandler(
            name="Test Action Handler",
            state=state,
            cockpit=cockpit,
            config=config,
        )

        result = handler.update()
        print_action_result("Multi-turn Run Tests Unstable", result, state)

        assert result == Status.SUCCESS
        assert state.command == Command.RELEASE
        assert state.user_release_args is not None
        assert state.user_release_args.release_tag == "unstable"
        assert state.user_release_args.custom_build is True
        assert len(state.user_release_args.module_versions) == 0

    def test_approve_from_clients_point_of_view(
        self, config: Config, cockpit: ConversationCockpit
    ) -> None:
        """Test that 'test unstable' is detected as custom build with unstable."""
        state = (
            ConversationBuilder()
            .current(
                "can you approve the 8.4-int3 from clients point of view?",
                is_mention=True,
            )
            .build_state()
        )

        handler = LLMActionHandler(
            name="Test Action Handler",
            state=state,
            cockpit=cockpit,
            config=config,
        )

        result = handler.update()
        print_action_result("Test Approve from Clients Point of View", result, state)

        assert result == Status.SUCCESS
        assert state.command == Command.RELEASE
        assert state.user_release_args is not None
        assert state.user_release_args.release_tag == "8.4-int3"
        assert state.user_release_args.custom_build is True


class TestLLMIntentDetector:
    """Tests for LLMIntentDetector prompt behavior."""

    @pytest.mark.parametrize(
        "message,expected_intent",
        [
            ("huh", UserIntent.NO_ACTION),
            (
                "can you approve the 8.4-int3 from clients point of view?",
                UserIntent.ACTION,
            ),
            ("run tests", UserIntent.ACTION),
            ("test unstable", UserIntent.ACTION),
            ("stop replying", UserIntent.ACTION),
            ("do not reply", UserIntent.ACTION),
            ("ignore messages", UserIntent.ACTION),
            ("ignore this thread", UserIntent.ACTION),
            ("shut up please", UserIntent.ACTION),
        ],
    )
    def test_intent_detection(
        self,
        config: Config,
        cockpit: ConversationCockpit,
        message: str,
        expected_intent: UserIntent,
    ) -> None:
        """Test that LLM detects user intent correctly."""
        from redis_release.bht.conversation_llm import LLMIntentDetector

        state = ConversationBuilder().current(message, is_mention=True).build_state()

        detector = LLMIntentDetector(
            name="Test Intent Detector",
            state=state,
            cockpit=cockpit,
            config=config,
        )

        result = detector.update()
        print_intent_result(f"Intent Detection for '{message}'", result, state)

        assert result == Status.SUCCESS
        assert state.user_intent == expected_intent
