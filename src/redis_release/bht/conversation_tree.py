import logging
from typing import List, Optional, Tuple

from janus import SyncQueue
from openai import OpenAI
from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter
from py_trees.trees import BehaviourTree
from py_trees.visitors import SnapshotVisitor

from ..config import Config, load_config
from ..conversation_models import ConversationArgs, ConversationCockpit, InboxMessage
from ..models import SlackArgs
from .conversation_behaviours import (
    HasReleaseArgs,
    IsCommandStarted,
    IsLLMAvailable,
    LLMCommandClassifier,
    NeedConfirmation,
    RunReleaseCommand,
    RunStatusCommand,
    ShowConfirmationMessage,
    SimpleCommandClassifier,
)
from .conversation_state import ConversationState
from .tree import log_tree_state_with_markup

logger = logging.getLogger(__name__)


def create_conversation_root_node(
    input: InboxMessage,
    config: Config,
    cockpit: ConversationCockpit,
    slack_args: Optional[SlackArgs] = None,
    authorized_users: Optional[List[str]] = None,
) -> Tuple[Behaviour, ConversationState]:
    state = ConversationState(
        llm_available=cockpit.llm is not None,
        message=input,
        slack_args=slack_args,
        authorized_users=authorized_users,
    )
    state.message = input

    command_detector = Selector(
        "Classify Command",
        memory=False,
        children=[
            HasReleaseArgs("Has Release Args", state, cockpit),
            Sequence(
                "Simple Classification",
                memory=False,
                children=[
                    Inverter("Not", IsLLMAvailable("Is LLM Available", state, cockpit)),
                    SimpleCommandClassifier(
                        "Simple Command Classifier", state, cockpit
                    ),
                ],
            ),
            LLMCommandClassifier("LLM Classification", state, cockpit),
        ],
    )

    show_confirmation = Sequence(
        "Show Confirmation",
        memory=False,
        children=[
            NeedConfirmation("Need Confirmation", state, cockpit),
            ShowConfirmationMessage("Show Confirmation Message", state, cockpit),
        ],
    )

    run_release = Selector(
        "Run Release",
        memory=False,
        children=[
            show_confirmation,
            RunReleaseCommand("Run Release Command", state, cockpit, config),
        ],
    )

    conversation_root = Selector(
        "Conversation",
        memory=False,
        children=[
            IsCommandStarted("Is Command Started", state, cockpit),
            Sequence(
                "Conversation Sequence",
                memory=False,
                children=[
                    command_detector,
                    Selector(
                        name="Command Router",
                        memory=False,
                        children=[
                            RunStatusCommand(
                                "Run Status Command", state, cockpit, config
                            ),
                            run_release,
                        ],
                    ),
                ],
            ),
        ],
    )

    root = conversation_root

    return root, state


def initialize_conversation_tree(
    args: ConversationArgs,
    reply_queue: Optional[SyncQueue] = None,
) -> Tuple[BehaviourTree, ConversationState]:

    # Load config
    config = load_config(args.config_path)

    llm: Optional[OpenAI] = None
    if args.openai_api_key:
        llm = OpenAI(api_key=args.openai_api_key)

    cockpit = ConversationCockpit()
    cockpit.llm = llm
    cockpit.reply_queue = reply_queue

    if not args.inbox:
        raise ValueError("Inbox message is required")

    root, state = create_conversation_root_node(
        args.inbox,
        config=config,
        cockpit=cockpit,
        slack_args=args.slack_args,
        authorized_users=args.authorized_users,
    )
    tree = BehaviourTree(root)
    snapshot_visitor = SnapshotVisitor()
    tree.visitors.append(snapshot_visitor)
    tree.add_post_tick_handler(log_tree_state_with_markup)
    return tree, state


def run_conversation_tree(
    tree: BehaviourTree, state: ConversationState, reply_queue: SyncQueue
) -> None:
    """Abstacting away tree run
    Currently it's just a single tick, but it may change in future
    """
    try:
        tree.tick()
        try:
            # Send all replies from the list
            for reply in state.replies:
                reply_queue.put(reply)
        except Exception as e:
            logger.error(f"Error putting reply to queue: {e}", exc_info=True)
    except Exception as e:
        try:
            reply_queue.put(f"Error running conversation tree: {str(e)}")
        except Exception as e:
            logger.error(f"Error putting error reply to queue: {e}", exc_info=True)
    finally:
        logger.debug("Shutting down reply queue")
        reply_queue.shutdown(immediate=False)
