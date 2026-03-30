"""Unit tests for apps/compiler_api/route_publisher.py uncovered lines."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from apps.compiler_api.route_publisher import (
    AccessControlArtifactRoutePublisher,
    NoopArtifactRoutePublisher,
    UnconfiguredArtifactRoutePublisher,
    _resolve_default_route_publisher,
    dispose_route_publisher,
    get_route_publisher,
)


class TestNoopArtifactRoutePublisher:
    @pytest.mark.asyncio
    async def test_delete_returns_none(self) -> None:
        publisher = NoopArtifactRoutePublisher()
        result = await publisher.delete({"service": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_rollback_returns_none(self) -> None:
        publisher = NoopArtifactRoutePublisher()
        result = await publisher.rollback({"service": "test"}, {"route": {"id": "old"}})
        assert result is None


class TestUnconfiguredArtifactRoutePublisher:
    @pytest.mark.asyncio
    async def test_sync_raises_configuration_error(self) -> None:
        publisher = UnconfiguredArtifactRoutePublisher()

        with pytest.raises(RuntimeError, match="ACCESS_CONTROL_URL is not configured"):
            await publisher.sync({"service": "test"})


class TestAccessControlArtifactRoutePublisher:
    @pytest.mark.asyncio
    async def test_non_json_response_raises_runtime_error(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = ValueError("No JSON")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        publisher = AccessControlArtifactRoutePublisher(
            base_url="http://localhost:8000",
            client=mock_client,
            auth_token="test-token",
        )
        with pytest.raises(RuntimeError, match="non-JSON response"):
            await publisher.sync({"service": "test"})

    @pytest.mark.asyncio
    async def test_non_dict_response_raises_runtime_error(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = ["not", "a", "dict"]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        publisher = AccessControlArtifactRoutePublisher(
            base_url="http://localhost:8000",
            client=mock_client,
            auth_token="test-token",
        )
        with pytest.raises(RuntimeError, match="non-object response"):
            await publisher.sync({"service": "test"})

    @pytest.mark.asyncio
    async def test_owns_client_cleanup_on_success(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("apps.compiler_api.route_publisher.httpx.AsyncClient", return_value=mock_client):
            publisher = AccessControlArtifactRoutePublisher(
                base_url="http://localhost:8000",
                client=None,
                auth_token="test-token",
            )
            result = await publisher.sync({"service": "test"})

        assert result == {"ok": True}
        mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_posts_previous_routes(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        publisher = AccessControlArtifactRoutePublisher(
            base_url="http://localhost:8000",
            client=mock_client,
            auth_token="test-token",
        )

        previous_routes = {"svc-v1": {"route_id": "svc-v1"}}
        result = await publisher.rollback({"service": "test"}, previous_routes)

        assert result == {"ok": True}
        mock_client.post.assert_awaited_once_with(
            "/api/v1/gateway-binding/service-routes/rollback",
            json={
                "route_config": {"service": "test"},
                "previous_routes": previous_routes,
            },
            headers={"Authorization": "Bearer test-token"},
        )

    @pytest.mark.asyncio
    async def test_owns_client_cleanup_on_error(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(),
        ))

        with patch("apps.compiler_api.route_publisher.httpx.AsyncClient", return_value=mock_client):
            publisher = AccessControlArtifactRoutePublisher(
                base_url="http://localhost:8000",
                client=None,
                auth_token="test-token",
            )
            with pytest.raises(httpx.HTTPStatusError):
                await publisher.sync({"service": "test"})

        mock_client.aclose.assert_awaited_once()


class TestGetRoutePublisher:
    def test_default_resolution_and_caching(self) -> None:
        app = FastAPI()
        request = MagicMock()
        request.app = app

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACCESS_CONTROL_URL", None)
            publisher = get_route_publisher(request)

        assert isinstance(publisher, UnconfiguredArtifactRoutePublisher)
        # Second call returns the cached instance
        publisher2 = get_route_publisher(request)
        assert publisher2 is publisher


class TestDisposeRoutePublisher:
    @pytest.mark.asyncio
    async def test_calls_aclose_on_publisher(self) -> None:
        app = FastAPI()
        mock_publisher = AsyncMock()
        mock_publisher.aclose = AsyncMock()
        setattr(app.state, "artifact_route_publisher", mock_publisher)

        await dispose_route_publisher(app)
        mock_publisher.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_publisher_no_error(self) -> None:
        app = FastAPI()
        await dispose_route_publisher(app)


class TestResolveDefaultRoutePublisher:
    def test_returns_unconfigured_when_url_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACCESS_CONTROL_URL", None)
            publisher = _resolve_default_route_publisher()
        assert isinstance(publisher, UnconfiguredArtifactRoutePublisher)

    def test_returns_unconfigured_when_url_empty(self) -> None:
        with patch.dict(os.environ, {"ACCESS_CONTROL_URL": "  "}):
            publisher = _resolve_default_route_publisher()
        assert isinstance(publisher, UnconfiguredArtifactRoutePublisher)

    def test_returns_access_control_publisher_when_url_set(self) -> None:
        with patch.dict(os.environ, {"ACCESS_CONTROL_URL": "http://ac.local"}):
            publisher = _resolve_default_route_publisher()
        assert isinstance(publisher, AccessControlArtifactRoutePublisher)
