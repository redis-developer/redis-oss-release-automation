"""GitHub token provider implementations.

This module provides a token provider pattern for GitHub authentication,
supporting multiple authentication mechanisms with thread-safe caching.
"""

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict

from .github_app_auth import GitHubAppAuth

logger = logging.getLogger(__name__)


class TokenProviderError(Exception):
    """Exception raised when token cannot be obtained."""

    pass


class GitHubTokenProvider(ABC):
    """Abstract base class for GitHub token providers."""

    @abstractmethod
    async def get_token(self, repo: str) -> str:
        """Get a valid GitHub token for the specified repository.

        Args:
            repo: Repository name in format "owner/repo"

        Returns:
            Valid GitHub API token

        Raises:
            TokenProviderError: If token cannot be obtained
        """
        pass


class GitHubEnvTokenProvider(GitHubTokenProvider):
    """Token provider that reads from environment variable."""

    def __init__(self, env_var: str = "GITHUB_TOKEN"):
        """Initialize environment token provider.

        Args:
            env_var: Name of environment variable containing the token
        """
        self.env_var = env_var

    async def get_token(self, repo: str) -> str:
        """Get token from environment variable.

        Args:
            repo: Repository name (unused, but required by interface)

        Returns:
            Token from environment variable

        Raises:
            TokenProviderError: If environment variable is not set
        """
        token = os.getenv(self.env_var)
        if not token:
            raise TokenProviderError(f"Environment variable {self.env_var} not set")
        return token


class GitHubDummyTokenProvider(GitHubTokenProvider):
    """Token provider that returns a static dummy token for testing."""

    def __init__(self, token: str = "dummy"):
        """Initialize dummy token provider.

        Args:
            token: Static token to return
        """
        self.token = token

    async def get_token(self, repo: str) -> str:
        """Get the dummy token.

        Args:
            repo: Repository name (unused)

        Returns:
            The configured dummy token
        """
        return self.token


@dataclass
class CachedToken:
    """Cached token with expiration time."""

    token: str
    expires_at: float  # Unix timestamp

    def is_valid(self, min_lifetime_seconds: int = 300) -> bool:
        """Check if token has at least min_lifetime_seconds remaining.

        Args:
            min_lifetime_seconds: Minimum remaining lifetime required

        Returns:
            True if token has sufficient remaining lifetime
        """
        return (self.expires_at - time.time()) > min_lifetime_seconds


class GitHubAppTokenProvider(GitHubTokenProvider):
    """Token provider using GitHub App authentication with caching.

    Features:
    - Per-repo token caching
    - Thread-safe synchronization using threading.Lock
    - Automatic renewal when token has less than min_lifetime_seconds remaining
    - Global token cache shared across all instances
    """

    # Class-level cache for global token sharing across all instances
    token_cache: Dict[str, CachedToken] = {}
    lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        app_id: str,
        private_key: str,
        min_lifetime_seconds: int = 300,
    ):
        """Initialize GitHub App token provider.

        Args:
            app_id: GitHub App ID
            private_key: GitHub App private key (PEM format)
            min_lifetime_seconds: Minimum token lifetime before refresh (default: 300s)
        """
        self.app_id = app_id
        self.private_key = private_key
        self.min_lifetime_seconds = min_lifetime_seconds
        self.app_auth = GitHubAppAuth(app_id=app_id, private_key=private_key)

    async def get_token(self, repo: str) -> str:
        """Get a valid token, using cache or refreshing if needed.

        Args:
            repo: Repository name in format "owner/repo"

        Returns:
            Valid GitHub installation token

        Raises:
            TokenProviderError: If token cannot be obtained
        """
        # Check cache without lock first (fast path)
        cached = self.token_cache.get(repo)
        if cached and cached.is_valid(self.min_lifetime_seconds):
            logger.debug(f"Using cached app token for {repo}")
            return cached.token

        # Acquire lock for token refresh (thread-safe)
        with self.lock:
            # Double-check after acquiring lock
            cached = self.token_cache.get(repo)
            if cached and cached.is_valid(self.min_lifetime_seconds):
                logger.debug(f"Using cached app token for {repo}")
                return cached.token

            # Fetch new token
            token_data = await self.fetch_token(repo)
            self.token_cache[repo] = token_data
            logger.info(f"Issued new app token for {repo}")
            return token_data.token

    async def fetch_token(self, repo: str) -> CachedToken:
        """Fetch a new installation token from GitHub.

        Args:
            repo: Repository name in format "owner/repo"

        Returns:
            CachedToken with token and expiration

        Raises:
            TokenProviderError: If token cannot be obtained
        """
        logger.info(f"Issuing new GitHub App token for repo: {repo}")

        # Generate JWT
        jwt_token = self.app_auth.generate_jwt()

        # Get installation ID
        installation_id = await self.app_auth.get_installation_id(repo, jwt_token)
        if not installation_id:
            raise TokenProviderError(f"Failed to get installation ID for {repo}")

        # Get installation token with expiration
        result = await self.app_auth.get_installation_token_with_expiry(
            installation_id, jwt_token
        )

        if not result:
            raise TokenProviderError(f"Failed to get installation token for {repo}")

        token, expires_at = result

        expires_at_str = datetime.fromtimestamp(expires_at).isoformat()
        logger.info(f"GitHub App token issued for {repo}, expires at {expires_at_str}")

        return CachedToken(token=token, expires_at=expires_at)


def create_token_provider_from_env() -> GitHubTokenProvider:
    """Create a token provider based on environment variables.

    Priority:
    1. GITHUB_TOKEN - If set, use GitHubEnvTokenProvider
    2. GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY - If both set, use GitHubAppTokenProvider

    Returns:
        Configured GitHubTokenProvider

    Raises:
        TokenProviderError: If neither authentication method is configured
    """
    # Priority 1: Static token from environment
    if os.getenv("GITHUB_TOKEN"):
        logger.info("Using GITHUB_TOKEN for authentication")
        return GitHubEnvTokenProvider()

    # Priority 2: GitHub App authentication
    app_id = os.getenv("GITHUB_APP_ID")
    private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")

    if app_id and private_key:
        logger.info(f"Using GitHub App authentication (App ID: {app_id})")
        return GitHubAppTokenProvider(
            app_id=app_id,
            private_key=private_key,
        )

    # No authentication configured
    logger.error(
        "No GitHub authentication configured. "
        "Set GITHUB_TOKEN or (GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY)"
    )
    raise TokenProviderError(
        "No GitHub authentication configured. "
        "Set GITHUB_TOKEN or (GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY)"
    )
