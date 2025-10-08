import logging
from typing import Iterator, List, Optional

from py_trees.decorators import Decorator, behaviour, common
from pydantic import BaseModel

from redis_release.bht.logging_wrapper import PyTreesLoggerWrapper


class DecoratorWithLogging(Decorator):
    logger: PyTreesLoggerWrapper

    def __init__(
        self, name: str, child: behaviour.Behaviour, log_prefix: str = ""
    ) -> None:
        super().__init__(name=name, child=child)
        if log_prefix != "":
            log_prefix = f"{log_prefix}."
        self.logger = PyTreesLoggerWrapper(
            logging.getLogger(f"{log_prefix}{self.name}")
        )


class FlagGuard(DecoratorWithLogging):
    """
    A decorator that guards behaviour execution based on a flag value.

    If the flag in the container matches the expected flag_value, the guard
    returns guard_status immediately without executing the decorated behaviour.

    If the decorated behaviour executes and its status is in the raise_on list,
    the flag is set to flag_value.

    Args:
        name: the decorator name
        child: the child behaviour or subtree
        container: the BaseModel instance containing the flag
        flag: the name of the flag field in the container
        flag_value: the value to check/set for the flag (default: True)
        guard_status: the status to return when the guard is triggered (default: FAILURE)
        raise_on: list of statuses that should trigger setting the flag (default: [FAILURE])
        when raise_on is set to None, the flag is never raised (expected to be raised by other means)
    """

    def __init__(
        self,
        name: Optional[str],
        child: behaviour.Behaviour,
        container: BaseModel,
        flag: str,
        flag_value: bool = True,
        guard_status: common.Status = common.Status.FAILURE,
        raise_on: Optional[List[common.Status]] = None,
        log_prefix: str = "",
    ):
        if not hasattr(container, flag):
            raise ValueError(
                f"Field '{flag}' does not exist on {container.__class__.__name__}"
            )

        current_value = getattr(container, flag)
        if current_value is not None and type(current_value) != type(flag_value):
            raise TypeError(
                f"Field '{flag}' type mismatch: expected {type(flag_value)}, got {type(current_value)}"
            )

        self.container = container
        self.flag = flag
        self.flag_value = flag_value
        self.guard_status = guard_status
        self.raise_on = raise_on if raise_on is not None else [common.Status.FAILURE]
        if name is None:
            if self.flag_value is True:
                name = f"Unless {flag}"
            else:
                name = f"If {flag}"
        super(FlagGuard, self).__init__(name=name, child=child, log_prefix=log_prefix)

    def _is_flag_active(self) -> bool:
        current_flag_value = getattr(self.container, self.flag, None)
        return current_flag_value == self.flag_value

    def update(self) -> common.Status:
        current_flag_value = getattr(self.container, self.flag, None)
        if current_flag_value == self.flag_value:
            self.logger.debug(f"Returning guard status: {self.guard_status}")
            return self.guard_status

        return self.decorated.status

    def tick(self) -> Iterator[behaviour.Behaviour]:
        """
        Tick the child or bounce back with the original status if already completed.

        Yields:
            a reference to itself or a behaviour in it's child subtree
        """
        if self._is_flag_active():
            # ignore the child
            for node in behaviour.Behaviour.tick(self):
                yield node
        else:
            # tick the child
            for node in Decorator.tick(self):
                yield node

    def terminate(self, new_status: common.Status) -> None:
        if self._is_flag_active():
            return

        if new_status in self.raise_on:
            setattr(self.container, self.flag, self.flag_value)
            self.feedback_message = f"{self.flag} set to {self.flag_value}"
            self.logger.debug(
                f"Terminating with status {new_status}, setting {self.flag} to {self.flag_value}"
            )
        else:
            self.logger.debug(f"Terminating with status {new_status}, no flag change")
