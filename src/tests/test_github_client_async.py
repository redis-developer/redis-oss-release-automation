"""Tests for GitHub API client functionality."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from redis_release.github_client_async import GitHubClientAsync


class TestGitHubClientAsync(AioHTTPTestCase):
    """Test cases for github_request_paginated method."""

    async def get_application(self) -> web.Application:
        """Create a test application with mock endpoints."""
        app = web.Application()
        app.router.add_get("/no-pagination", self.handle_no_pagination)
        app.router.add_get("/array-pagination", self.handle_array_pagination)
        app.router.add_get("/dict-pagination", self.handle_dict_pagination)
        return app

    async def handle_no_pagination(self, request: web.Request) -> web.Response:
        """Handle request without pagination (no Link header)."""
        return web.json_response(
            [{"id": 1, "name": "item1"}, {"id": 2, "name": "item2"}]
        )

    async def handle_array_pagination(self, request: web.Request) -> web.Response:
        """Handle request with array response and pagination."""
        page = int(request.query.get("page", 1))

        # Simulate 3 pages of data
        if page == 1:
            data = [{"id": 1, "name": "item1"}, {"id": 2, "name": "item2"}]
            headers = {"Link": '<http://example.com/page2>; rel="next"'}
            return web.json_response(data, headers=headers)
        elif page == 2:
            data = [{"id": 3, "name": "item3"}, {"id": 4, "name": "item4"}]
            headers = {"Link": '<http://example.com/page3>; rel="next"'}
            return web.json_response(data, headers=headers)
        elif page == 3:
            data = [{"id": 5, "name": "item5"}]
            # No Link header on last page
            return web.json_response(data)
        else:
            return web.json_response([])

    async def handle_dict_pagination(self, request: web.Request) -> web.Response:
        """Handle request with dict response and pagination."""
        page = int(request.query.get("page", 1))

        # Simulate 2 pages of data with dict response
        if page == 1:
            data = {
                "total_count": 5,
                "artifacts": [
                    {"id": 1, "name": "artifact1"},
                    {"id": 2, "name": "artifact2"},
                    {"id": 3, "name": "artifact3"},
                ],
            }
            headers = {"Link": '<http://example.com/page2>; rel="next"'}
            return web.json_response(data, headers=headers)
        elif page == 2:
            data = {
                "total_count": 5,
                "artifacts": [
                    {"id": 4, "name": "artifact4"},
                    {"id": 5, "name": "artifact5"},
                ],
            }
            # No Link header on last page
            return web.json_response(data)
        else:
            return web.json_response({"total_count": 5, "artifacts": []})

    async def test_no_link_header(self) -> None:
        """Test pagination with no Link header (single page response)."""
        client = GitHubClientAsync(token="test-token")
        url = self.server.make_url("/no-pagination")
        headers = {"Authorization": "Bearer test-token"}

        result = await client.github_request_paginated(
            url=str(url),
            headers=headers,
            params={},
            timeout=30,
            per_page=30,
            max_pages=None,
        )

        # Should return the single page of results
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["name"] == "item1"
        assert result[1]["id"] == 2
        assert result[1]["name"] == "item2"

    async def test_array_pagination(self) -> None:
        """Test pagination with array response across multiple pages."""
        client = GitHubClientAsync(token="test-token")
        url = self.server.make_url("/array-pagination")
        headers = {"Authorization": "Bearer test-token"}

        result = await client.github_request_paginated(
            url=str(url),
            headers=headers,
            params={},
            timeout=30,
            per_page=30,
            max_pages=None,
        )

        # Should merge all pages into a single array
        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert result[2]["id"] == 3
        assert result[3]["id"] == 4
        assert result[4]["id"] == 5

    async def test_dict_pagination_with_merge_key(self) -> None:
        """Test pagination with dict response and merge_key."""
        client = GitHubClientAsync(token="test-token")
        url = self.server.make_url("/dict-pagination")
        headers = {"Authorization": "Bearer test-token"}

        result = await client.github_request_paginated(
            url=str(url),
            headers=headers,
            params={},
            timeout=30,
            merge_key="artifacts",
            per_page=30,
            max_pages=None,
        )

        # Should merge artifacts from all pages
        assert isinstance(result, dict)
        assert "total_count" in result
        assert result["total_count"] == 5  # Should have the value from the last page
        assert "artifacts" in result
        assert len(result["artifacts"]) == 5
        assert result["artifacts"][0]["id"] == 1
        assert result["artifacts"][1]["id"] == 2
        assert result["artifacts"][2]["id"] == 3
        assert result["artifacts"][3]["id"] == 4
        assert result["artifacts"][4]["id"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
