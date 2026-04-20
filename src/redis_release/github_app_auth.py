"""GitHub App authentication utilities."""

import logging
import time
from typing import Optional

import aiohttp
import jwt

logger = logging.getLogger(__name__)


class GitHubAppAuth:
    """GitHub App authentication helper."""

    def __init__(self, app_id: str, private_key: str):
        """Initialize GitHub App authentication.

        Args:
            app_id: GitHub App ID
            private_key: GitHub App private key (PEM format)
        """
        self.app_id = app_id
        self.private_key = private_key

    def generate_jwt(self, expiration_seconds: int = 600) -> str:
        """Generate a JWT for GitHub App authentication.

        Args:
            expiration_seconds: JWT expiration time in seconds (max 600)

        Returns:
            JWT token string
        """
        now = int(time.time())
        payload = {
            "iat": now
            - 60,  # Issued at time (60 seconds in the past to account for clock drift)
            "exp": now + expiration_seconds,  # Expiration time
            "iss": self.app_id,  # Issuer (GitHub App ID)
        }

        # Generate JWT using RS256 algorithm
        token = jwt.encode(payload, self.private_key, algorithm="RS256")
        logger.debug(f"Generated JWT for GitHub App {self.app_id}")
        return str(token)

    async def get_installation_id(self, repo: str, jwt_token: str) -> Optional[int]:
        """Get the installation ID for a repository.

        Args:
            repo: Repository name in format "owner/repo"
            jwt_token: JWT token for authentication

        Returns:
            Installation ID or None if not found
        """
        url = f"https://api.github.com/repos/{repo}/installation"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    installation_id: Optional[int] = data.get("id")
                    logger.info(
                        f"Found installation ID {installation_id} for repo {repo}"
                    )
                    return installation_id
                else:
                    error_text = await response.text()
                    logger.error(
                        f"Failed to get installation ID for {repo}: HTTP {response.status}"
                    )
                    logger.error(f"Response: {error_text}")
                    return None

    async def get_installation_token(
        self, installation_id: int, jwt_token: str
    ) -> Optional[str]:
        """Get an installation access token.

        Args:
            installation_id: GitHub App installation ID
            jwt_token: JWT token for authentication

        Returns:
            Installation access token or None if failed
        """
        result = await self.get_installation_token_with_expiry(
            installation_id, jwt_token
        )
        if result:
            return result[0]
        return None

    async def get_installation_token_with_expiry(
        self, installation_id: int, jwt_token: str
    ) -> Optional[tuple[str, float]]:
        """Get an installation access token with expiration timestamp.

        Args:
            installation_id: GitHub App installation ID
            jwt_token: JWT token for authentication

        Returns:
            Tuple of (token, expires_at_timestamp) or None if failed
        """
        from datetime import datetime

        url = (
            f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        )
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers) as response:
                if response.status == 201:
                    data = await response.json()
                    token: Optional[str] = data.get("token")
                    expires_at_str = data.get("expires_at")

                    if not token or not expires_at_str:
                        return None

                    # Parse ISO 8601 timestamp to Unix timestamp
                    # GitHub returns format like "2024-01-15T12:00:00Z"
                    expires_at_dt = datetime.fromisoformat(
                        expires_at_str.replace("Z", "+00:00")
                    )
                    expires_at = expires_at_dt.timestamp()

                    logger.info(
                        f"Generated installation token (expires at {expires_at_str})"
                    )
                    return (token, expires_at)
                else:
                    error_text = await response.text()
                    logger.error(
                        f"Failed to get installation token: HTTP {response.status}"
                    )
                    logger.error(f"Response: {error_text}")
                    return None

    async def get_token_for_repo(self, repo: str) -> Optional[str]:
        """Get an installation access token for a specific repository.

        This is a convenience method that combines JWT generation, installation ID lookup,
        and installation token generation.

        Args:
            repo: Repository name in format "owner/repo"

        Returns:
            Installation access token or None if failed
        """
        # Generate JWT
        jwt_token = self.generate_jwt()

        # Get installation ID
        installation_id = await self.get_installation_id(repo, jwt_token)
        if not installation_id:
            return None

        # Get installation token
        return await self.get_installation_token(installation_id, jwt_token)
