from typing import Optional, Tuple

from openai import OpenAI
from py_trees.behaviour import Behaviour
from py_trees.composites import Selector
from py_trees.trees import BehaviourTree
from py_trees.visitors import SnapshotVisitor

from redis_release.bht.conversation_behaviours import (
    LLMCommandClassifier,
    SimpleCommandClassifier,
)

from ..conversation_models import ConversationArgs
from .conversation_state import ConversationState, InboxMessage
from .tree import log_tree_state_with_markup


def create_conversation_root_node(
    input: InboxMessage, llm: Optional[OpenAI] = None
) -> Tuple[Behaviour, ConversationState]:
    state = ConversationState(llm_available=llm is not None, message=input)

    # Use LLM classifier if available, otherwise use simple classifier
    if llm is not None:
        command_detector = LLMCommandClassifier("LLM Command Detector", llm, state)
    else:
        command_detector = SimpleCommandClassifier("Simple Command Classifier", state)

    root = Selector(
        "Conversation Root",
        memory=False,
        children=[command_detector],
    )
    return root, state


def initialize_conversation_tree(args: ConversationArgs) -> BehaviourTree:

    llm: Optional[OpenAI] = None
    if args.openai_api_key:
        llm = OpenAI(api_key=args.openai_api_key)

    root, state = create_conversation_root_node(
        InboxMessage(message=args.message, context=[]), llm=llm
    )
    tree = BehaviourTree(root)
    snapshot_visitor = SnapshotVisitor()
    tree.visitors.append(snapshot_visitor)
    tree.add_post_tick_handler(log_tree_state_with_markup)
    return tree
