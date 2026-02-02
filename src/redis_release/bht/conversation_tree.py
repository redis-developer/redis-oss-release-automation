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
from ..conversation_models import (
    Command,
    ConversationArgs,
    ConversationCockpit,
    InboxMessage,
)
from ..models import SlackArgs
from .conversation_behaviours import (
    ExtractArgsFromConfirmation,
    HasConfirmationRequest,
    HasIntent,
    HasReleaseArgs,
    HasUserReleaseArgs,
    IgnoreThread,
    IsAction,
    IsCommand,
    IsCommandStarted,
    IsNoAction,
    IsQuestion,
    NeedConfirmation,
    RunReleaseCommand,
    RunStatusCommand,
    ShowConfirmationMessage,
)
from .conversation_llm import (
    IsLLMAvailable,
    LLMActionHandler,
    LLMHandleConfirmation,
    LLMIntentDetector,
    LLMNoActionHandler,
    LLMQuestionHandler,
)
from .conversation_state import ConversationState
from .tree import log_tree_state_with_markup

logger = logging.getLogger(__name__)


# Use redis-release conversation-print command to visualize the tree
def create_conversation_root_node(
    input: InboxMessage,
    config: Config,
    cockpit: ConversationCockpit,
    context: Optional[List[InboxMessage]] = None,
    slack_args: Optional[SlackArgs] = None,
    authorized_users: Optional[List[str]] = None,
    emojis: Optional[List[str]] = None,
    slack_format_is_available: bool = False,
) -> Tuple[Behaviour, ConversationState]:
    state = ConversationState(
        llm_available=cockpit.llm is not None,
        message=input,
        context=context,
        slack_args=slack_args,
        authorized_users=authorized_users,
        emojis=emojis or [],
        slack_format_is_available=slack_format_is_available,
    )
    state.message = input

    LLMResolve = Selector(
        "LLM Resolve",
        memory=False,
        children=[
            Sequence(
                "Question",
                memory=False,
                children=[
                    IsQuestion("Is Question", state, cockpit),
                    LLMQuestionHandler("Handle Question", state, cockpit, config),
                ],
            ),
            Sequence(
                "Action",
                memory=False,
                children=[
                    IsAction("Is Action", state, cockpit),
                    LLMActionHandler("Handle Action", state, cockpit, config),
                ],
            ),
            Sequence(
                "NoAction",
                memory=False,
                children=[
                    IsNoAction("Is No Action", state, cockpit),
                    LLMNoActionHandler("Handle NoAction", state, cockpit, config),
                ],
            ),
        ],
    )

    LLMIntent = Selector(
        "LLM Intent",
        memory=False,
        children=[
            HasIntent("Has Intent", state, cockpit),
            LLMIntentDetector("Detect Intent", state, cockpit, config),
        ],
    )

    LLMClass = Sequence(
        "LLM Classification", memory=False, children=[LLMIntent, LLMResolve]
    )

    command_detector = Selector(
        "Classify Command",
        memory=False,
        children=[
            HasReleaseArgs("Has Release Args", state, cockpit),
            LLMClass,
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

    run_release = Sequence(
        "Release",
        memory=False,
        children=[
            IsCommand("Is Release Command", state, Command.RELEASE),
            Selector(
                "Run Release",
                memory=False,
                children=[
                    show_confirmation,
                    RunReleaseCommand("Run Release Command", state, cockpit, config),
                ],
            ),
        ],
    )

    run_status = Sequence(
        "Status",
        memory=False,
        children=[
            IsCommand("Is Status Command", state, Command.STATUS),
            RunStatusCommand("Run Status Command", state, cockpit, config),
        ],
    )

    # Handle confirmation flow - check if previous message was a confirmation request
    handle_confirmation = Selector(
        "Handle Confirmation",
        memory=False,
        children=[
            HasUserReleaseArgs("Has User Release Args", state, cockpit),
            Selector(
                "Check Confirmation Request",
                memory=False,
                children=[
                    Inverter(
                        name="Not Confirmation Request",
                        child=HasConfirmationRequest(
                            "Has Confirmation Request", state, cockpit
                        ),
                    ),
                    Sequence(
                        "Process Confirmation",
                        memory=False,
                        children=[
                            ExtractArgsFromConfirmation(
                                "Extract Args From Confirmation", state, cockpit
                            ),
                            LLMHandleConfirmation(
                                "LLM Handle Confirmation", state, cockpit, config
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    ignore_thread = Sequence(
        "Ignore Thread",
        memory=False,
        children=[
            IsCommand("Is Ignore Thread Command", state, Command.IGNORE_THREAD),
            IgnoreThread("Ignore Thread", state, cockpit),
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
                    handle_confirmation,
                    command_detector,
                    Selector(
                        name="Command Router",
                        memory=False,
                        children=[
                            run_status,
                            run_release,
                            ignore_thread,
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
        context=args.context,
        slack_args=args.slack_args,
        authorized_users=args.authorized_users,
        emojis=args.emojis,
        slack_format_is_available=args.slack_format_is_available,
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
    from ..conversation_models import BotReply

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
            reply_queue.put(BotReply(text=f"Error running conversation tree: {str(e)}"))
        except Exception as e:
            logger.error(f"Error putting error reply to queue: {e}", exc_info=True)
    finally:
        logger.debug("Shutting down reply queue")
        reply_queue.shutdown(immediate=False)
