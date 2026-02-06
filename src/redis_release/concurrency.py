"""Concurrency management for graceful shutdown of async tasks and threads."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Set

import janus

logger = logging.getLogger(__name__)


class ConcurrencyManager:
    """Manages async tasks, threads, and queues for graceful shutdown.

    This class tracks all active concurrent resources and provides
    a unified shutdown mechanism to cleanly stop them all.
    """

    def __init__(self) -> None:
        self._active_tasks: Set[asyncio.Task[None]] = set()
        self._active_threads: Set[threading.Thread] = set()
        self._active_queues: Set[janus.Queue[Any]] = set()
        self._thread_stop_events: Dict[threading.Thread, threading.Event] = {}
        self._shutting_down = False
        self._lock = threading.Lock()

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self._shutting_down

    def register_task(self, task: asyncio.Task[None]) -> None:
        """Register an async task for tracking."""
        with self._lock:
            self._active_tasks.add(task)
        # Auto-remove when task completes
        task.add_done_callback(lambda t: self._unregister_task(t))

    def _unregister_task(self, task: asyncio.Task[None]) -> None:
        """Unregister an async task."""
        with self._lock:
            self._active_tasks.discard(task)

    def register_thread(self, thread: threading.Thread) -> threading.Event:
        """Register a thread for tracking and return a stop event.

        The returned event will be set when shutdown is requested,
        allowing the thread to gracefully stop.

        Args:
            thread: The thread to register

        Returns:
            A threading.Event that will be set on shutdown
        """
        stop_event = threading.Event()
        with self._lock:
            self._active_threads.add(thread)
            self._thread_stop_events[thread] = stop_event
        return stop_event

    def unregister_thread(self, thread: threading.Thread) -> None:
        """Unregister a thread after it completes."""
        with self._lock:
            self._active_threads.discard(thread)
            self._thread_stop_events.pop(thread, None)

    def register_queue(self, queue: janus.Queue[Any]) -> None:
        """Register a janus queue for tracking."""
        with self._lock:
            self._active_queues.add(queue)

    def get_active_threads(self) -> list[threading.Thread]:
        """Get list of currently active (alive) threads."""
        with self._lock:
            return [t for t in self._active_threads if t.is_alive()]

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Gracefully shutdown all tracked resources.

        Args:
            timeout: Maximum time to wait for resources to complete
        """
        if self._shutting_down:
            logger.debug("Shutdown already in progress")
            return

        self._shutting_down = True
        logger.info("Initiating graceful shutdown...")

        # Close all active queues to signal threads to stop
        with self._lock:
            queues = list(self._active_queues)
        for queue in queues:
            try:
                if not queue.closed:
                    queue.close()
            except Exception as e:
                logger.debug(f"Error closing queue: {e}")

        # Cancel all active async tasks
        with self._lock:
            tasks_to_cancel = list(self._active_tasks)
        if tasks_to_cancel:
            logger.info(f"Cancelling {len(tasks_to_cancel)} active tasks...")
            for task in tasks_to_cancel:
                if not task.done():
                    task.cancel()

            # Wait for tasks to complete with timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for tasks to complete")

        # Signal all threads to stop via their stop events
        with self._lock:
            stop_events = list(self._thread_stop_events.values())
        if stop_events:
            logger.info(f"Signalling {len(stop_events)} threads to stop...")
            for event in stop_events:
                event.set()

        # Wait for threads to finish
        threads_to_wait = self.get_active_threads()
        if threads_to_wait:
            logger.info(f"Waiting for {len(threads_to_wait)} threads to finish...")
            per_thread_timeout = timeout / max(len(threads_to_wait), 1)
            for thread in threads_to_wait:
                thread.join(timeout=per_thread_timeout)
                if thread.is_alive():
                    logger.warning(f"Thread {thread.name} did not exit in time")
                else:
                    self.unregister_thread(thread)

        # Wait for queues to fully close
        for queue in queues:
            try:
                await queue.wait_closed()
            except Exception as e:
                logger.debug(f"Error waiting for queue to close: {e}")

        # Clear all tracking sets
        with self._lock:
            self._active_tasks.clear()
            self._active_threads.clear()
            self._active_queues.clear()

        logger.info("Graceful shutdown complete")
