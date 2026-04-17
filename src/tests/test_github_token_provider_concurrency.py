"""Concurrency tests for GitHubAppTokenProvider."""

import asyncio
import threading
import time
from typing import List

import pytest

from redis_release.github_token_provider import CachedToken, GitHubAppTokenProvider


class TestGitHubAppTokenProvider(GitHubAppTokenProvider):
    """Provider with deterministic fetch behavior for concurrency tests."""

    def __init__(
        self,
        app_id: str,
        private_key: str,
        token_value: str,
        fetch_calls: List[int],
        fetch_calls_lock: threading.Lock,
    ) -> None:
        super().__init__(app_id=app_id, private_key=private_key)
        self._token_value = token_value
        self._fetch_calls = fetch_calls
        self._fetch_calls_lock = fetch_calls_lock

    async def fetch_token(self, repo: str) -> CachedToken:
        with self._fetch_calls_lock:
            self._fetch_calls[0] += 1
        await asyncio.sleep(0.1)
        return CachedToken(token=self._token_value, expires_at=time.time() + 3600)


@pytest.fixture(autouse=True)
def reset_provider_class_state() -> None:
    """Reset shared provider state between tests."""
    with GitHubAppTokenProvider.lock:
        GitHubAppTokenProvider.token_cache.clear()
        GitHubAppTokenProvider.async_locks.clear()


def _run_get_token_in_thread(
    provider: GitHubAppTokenProvider,
    repo: str,
    barrier: threading.Barrier,
    results: List[str],
    errors: List[BaseException],
) -> None:
    """Wait on barrier and run get_token in a dedicated event loop."""
    try:
        barrier.wait(timeout=2)
        token = asyncio.run(provider.get_token(repo))
        results.append(token)
    except BaseException as exc:  # noqa: BLE001
        errors.append(exc)


def test_no_deadlock_multithread_same_provider_instance() -> None:
    """Concurrent calls from multiple threads should finish without deadlock."""
    fetch_calls = [0]
    fetch_calls_lock = threading.Lock()
    provider = TestGitHubAppTokenProvider(
        app_id="1",
        private_key="dummy",
        token_value="thread-safe-token",
        fetch_calls=fetch_calls,
        fetch_calls_lock=fetch_calls_lock,
    )

    barrier = threading.Barrier(2)
    results: List[str] = []
    errors: List[BaseException] = []

    threads = [
        threading.Thread(
            target=_run_get_token_in_thread,
            args=(provider, "owner/repo", barrier, results, errors),
            daemon=True,
        ),
        threading.Thread(
            target=_run_get_token_in_thread,
            args=(provider, "owner/repo", barrier, results, errors),
            daemon=True,
        ),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads), "Thread join timeout"
    assert not errors, f"Unexpected thread errors: {errors}"
    assert results == ["thread-safe-token", "thread-safe-token"]
    assert fetch_calls[0] == 1


def test_no_deadlock_multithread_across_provider_instances() -> None:
    """Concurrent calls from multiple instances/threads should finish."""
    fetch_calls = [0]
    fetch_calls_lock = threading.Lock()
    providers = [
        TestGitHubAppTokenProvider(
            app_id="1",
            private_key="dummy",
            token_value="shared-cache-token",
            fetch_calls=fetch_calls,
            fetch_calls_lock=fetch_calls_lock,
        ),
        TestGitHubAppTokenProvider(
            app_id="1",
            private_key="dummy",
            token_value="shared-cache-token",
            fetch_calls=fetch_calls,
            fetch_calls_lock=fetch_calls_lock,
        ),
    ]

    barrier = threading.Barrier(2)
    results: List[str] = []
    errors: List[BaseException] = []

    threads = [
        threading.Thread(
            target=_run_get_token_in_thread,
            args=(providers[0], "owner/repo", barrier, results, errors),
            daemon=True,
        ),
        threading.Thread(
            target=_run_get_token_in_thread,
            args=(providers[1], "owner/repo", barrier, results, errors),
            daemon=True,
        ),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads), "Thread join timeout"
    assert not errors, f"Unexpected thread errors: {errors}"
    assert results == ["shared-cache-token", "shared-cache-token"]
    assert fetch_calls[0] == 1
