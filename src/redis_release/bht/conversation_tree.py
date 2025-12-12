from typing import Optional, Tuple

from openai import OpenAI
from py_trees.behaviour import Behaviour
from py_trees.trees import BehaviourTree
from py_trees.visitors import SnapshotVisitor

from ..config import Config, load_config
from ..conversation_models import ConversationArgs
from ..models import SlackArgs
from .backchain import create_PPA, latch_chain_to_chain
from .conversation_behaviours import (
    HasReleaseArgs,
    IsCommandStarted,
    LLMCommandClassifier,
    RunCommand,
    SimpleCommandClassifier,
)
from .conversation_state import ConversationState, InboxMessage
from .tree import log_tree_state_with_markup


def create_conversation_root_node(
    input: InboxMessage,
    config: Config,
    llm: Optional[OpenAI] = None,
    slack_args: Optional[SlackArgs] = None,
) -> Tuple[Behaviour, ConversationState]:
    state = ConversationState(
        llm_available=llm is not None,
        message=input,
        slack_args=slack_args,
    )
    state.message = input

    # Use LLM classifier if available, otherwise use simple classifier
    if llm is not None:
        command_detector = create_PPA(
            "LLM Command Detector",
            LLMCommandClassifier("LLM Command Detector", llm, state),
            HasReleaseArgs("Has Release Args", state),
        )
    else:
        command_detector = create_PPA(
            "Simple Command Detector",
            SimpleCommandClassifier("Simple Command Classifier", state),
            HasReleaseArgs("Has Release Args", state),
        )

    run_command = create_PPA(
        "Run",
        RunCommand("Run Command", state, config),
        IsCommandStarted("Is Command Started", state),
    )

    latch_chain_to_chain(run_command, command_detector)
    root = run_command

    return root, state


def initialize_conversation_tree(
    args: ConversationArgs,
) -> Tuple[BehaviourTree, ConversationState]:

    # Load config
    config = load_config(args.config_path)

    llm: Optional[OpenAI] = None
    if args.openai_api_key:
        llm = OpenAI(api_key=args.openai_api_key)

    root, state = create_conversation_root_node(
        InboxMessage(message=args.message, context=args.context or []),
        config=config,
        llm=llm,
        slack_args=args.slack_args,
    )
    tree = BehaviourTree(root)
    snapshot_visitor = SnapshotVisitor()
    tree.visitors.append(snapshot_visitor)
    tree.add_post_tick_handler(log_tree_state_with_markup)
    return tree, state


def run_conversation_tree(tree: BehaviourTree) -> None:
    """Abstacting away tree run
    Currently it's just a single tick, but it may change in future
    """
    tree.tick()
