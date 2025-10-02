import time
from typing import Optional

from py_trees.decorators import Decorator, behaviour, common
from pydantic import BaseModel


class TimeoutWithFlag(Decorator):
    """
    Executes a child/subtree with a timeout.

    A decorator that applies a timeout pattern to an existing behaviour.
    If the timeout is reached, the encapsulated behaviour's
    :meth:`~py_trees.behaviour.Behaviour.stop` method is called with
    status :data:`~py_trees.common.Status.INVALID` and specified field in
    container is set to True, otherwise it will
    simply directly tick and return with the same status
    as that of it's encapsulated behaviour.
    """

    def __init__(
        self,
        name: str,
        child: behaviour.Behaviour,
        duration: float = 5.0,
        container: Optional[BaseModel] = None,
        field: str = "",
    ):
        """
        Init with the decorated child and a timeout duration.

        Args:
            child: the child behaviour or subtree
            name: the decorator name
            duration: timeout length in seconds
        """
        super(TimeoutWithFlag, self).__init__(name=name, child=child)
        self.duration = duration
        self.finish_time = 0.0
        self.container = container
        self.field = field

    def initialise(self) -> None:
        """Reset the feedback message and finish time on behaviour entry."""
        self.finish_time = time.monotonic() + self.duration
        self.feedback_message = ""

    def update(self) -> common.Status:
        """
        Fail on timeout, or block / reflect the child's result accordingly.

        Terminate the child and return
        :data:`~py_trees.common.Status.FAILURE`
        if the timeout is exceeded.

        Returns:
            the behaviour's new status :class:`~py_trees.common.Status`
        """
        current_time = time.monotonic()
        if (
            self.decorated.status == common.Status.RUNNING
            and current_time > self.finish_time
        ):
            self.feedback_message = "timed out"
            if self.container is not None:
                setattr(self.container, self.field, True)
            self.logger.debug(
                "{}.update() {}".format(self.__class__.__name__, self.feedback_message)
            )
            # invalidate the decorated (i.e. cancel it), could also put this logic in a terminate() method
            self.decorated.stop(common.Status.INVALID)
            return common.Status.FAILURE
        if self.decorated.status == common.Status.RUNNING:
            self.feedback_message = "time still ticking ... [remaining: {}s]".format(
                self.finish_time - current_time
            )
        else:
            self.feedback_message = "child finished before timeout triggered"
        return self.decorated.status
