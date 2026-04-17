"""Concurrency tests for GitHubAppTokenProvider."""

import asyncio
import threading
import time
import types
from typing import List

import pytest

from redis_release.github_token_provider import CachedToken, GitHubAppTokenProvider


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
    provider = GitHubAppTokenProvider(app_id="1", private_key="dummy")
    fetch_calls = 0
    fetch_calls_lock = threading.Lock()

    async def fake_fetch(self: GitHubAppTokenProvider, repo: str) -> CachedToken:
        nonlocal fetch_calls
        with fetch_calls_lock:
            fetch_calls += 1
        await asyncio.sleep(0.1)
        return CachedToken(token="thread-safe-token", expires_at=time.time() + 3600)

    provider.fetch_token = types.MethodType(fake_fetch, provider)

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
    assert fetch_calls == 1


def test_no_deadlock_multithread_across_provider_instances() -> None:
    """Concurrent calls from multiple instances/threads should finish."""
    providers = [
        GitHubAppTokenProvider(app_id="1", private_key="dummy"),
        GitHubAppTokenProvider(app_id="1", private_key="dummy"),
    ]
    fetch_calls = 0
    fetch_calls_lock = threading.Lock()

    async def fake_fetch(self: GitHubAppTokenProvider, repo: str) -> CachedToken:
        nonlocal fetch_calls
        with fetch_calls_lock:
            fetch_calls += 1
        await asyncio.sleep(0.1)
        return CachedToken(token="shared-cache-token", expires_at=time.time() + 3600)

    for provider in providers:
        provider.fetch_token = types.MethodType(fake_fetch, provider)

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
    assert fetch_calls == 1
