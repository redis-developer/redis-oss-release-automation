"""Tools for creating behavior trees using backchaining.

See Michele Colledanchise and Petter Ögren
Behavior Trees in Robotics and AI
3.5 Creating Deliberative BTs using Backchaining
"""

import logging
from typing import Optional, Union

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence

logger = logging.getLogger(__name__)


def find_chain_anchor_point(
    root: Behaviour,
) -> Sequence:
    for child in root.children:
        if len(child.children) > 1:
            return find_chain_anchor_point(child)
    if isinstance(root, Sequence):
        return root
    else:
        raise Exception("No chain anchor_point found")


def latch_chains(*chains: Union[Selector, Sequence]) -> None:
    assert len(chains) >= 2
    first = chains[0]
    for chain in chains[1:]:
        latch_chain_to_chain(first, chain)
        first = chain


def latch_chain_to_chain(
    first: Behaviour,
    next: Union[Selector, Sequence],
) -> None:
    """Latch two chains together. Both are expected to be formed using PPAs.

    If precondition exists in the anchor point, it is replaced by the next chain.
    Otherwise the next chain is added as a leftmost child to the anchor point.

    If the next chain is a sequence, its children are merged into the anchor point.

    Args:
        ppa: PPA composite to latch to
        link: Link composite to latch
    """
    anchor_point = find_chain_anchor_point(first)
    next_postcondition: Optional[Behaviour] = None
    anchor_precondition: Optional[Behaviour] = None

    logger.debug(f"Latching {next.name} to {anchor_point.name}")

    # Trying to guess from the structure which node may be a postcondition
    # Later we compare it with the anchor point precondition and when they match
    # we assume it is the postcondition that could be removed as a part of backchaining
    if type(next) == Selector and len(next.children) > 0:
        next_postcondition = next.children[0]
    if type(next) == Sequence:
        if len(next.children) == 1:
            # This is a PPA with only one action which may be interpreted as a postcondition
            # Like Sequence --> IsWorkflowSuccessful?
            next_postcondition = next.children[0]
        elif (
            len(next.children) > 1
            and type(next.children[0]) == Selector
            and len(next.children[-1].children) == 0
        ):
            # The same as above but when another chain is already latched to this PPA
            # and therefore it now has leftmost Selector children and rightmost action
            next_postcondition = next.children[-1]

    assert len(anchor_point.children) > 0
    anchor_precondition = anchor_point.children[0]

    logger.debug(
        f"Anchor precondition: {anchor_precondition.name}, Next postcondition: {next_postcondition.name if next_postcondition else 'None'}"
    )

    # If anchor point has both precondition and action, remove anchor_precondition if it matches the next_postcondition
    # very weak check that the anchor_precondition is the same as the next_postcondition:
    if (
        len(anchor_point.children) == 2
        and next_postcondition is not None
        and type(next_postcondition) == type(anchor_precondition)
        and next_postcondition.name == anchor_precondition.name
    ):
        anchor_point.children.pop(0)
        logger.debug(f"Removed precondition from PPA {anchor_precondition.name}")

    if type(next) == Sequence:
        # If next is a sequence, merge next's children into achor_point sequence to the left
        for child in reversed(next.children):
            child.parent = anchor_point
            anchor_point.children.insert(0, child)
            logger.debug(
                f"Merged child {child.name} to anchor point {anchor_point.name}"
            )
    else:
        next.parent = anchor_point
        anchor_point.children.insert(0, next)
        logger.debug(
            f"Added chain {next.name} directly to anchor point {anchor_point.name}"
        )


def create_PPA(
    name: str,
    action: Behaviour,
    postcondition: Optional[Behaviour] = None,
    precondition: Optional[Behaviour] = None,
) -> Union[Sequence, Selector]:
    """Create a PPA (Precondition-Postcondition-Action) composite."""

    sequence = Sequence(
        name=f"{name}",
        memory=False,
        children=[],
    )
    if precondition is not None:
        sequence.add_child(precondition)
    sequence.add_child(action)

    if postcondition is not None:
        selector = Selector(
            name=f"{name} Goal",
            memory=False,
            children=[],
        )
        selector.add_child(postcondition)
        selector.add_child(sequence)
        return selector
    else:
        return sequence
