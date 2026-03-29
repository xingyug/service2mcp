"""Unit tests for apps/compiler_api/routes/services.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from apps.compiler_api.models import ServiceListResponse, ServiceSummaryResponse
from apps.compiler_api.routes.services import get_service, list_services


class TestListServices:
    async def test_returns_repository_response(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ServiceListResponse)

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_services.return_value = mock_response

            result = await list_services(
                tenant="team-a",
                environment="prod",
                session=mock_session,
            )

            assert result == mock_response
            mock_repo.list_services.assert_called_once_with(
                tenant="team-a",
                environment="prod",
            )


class TestGetService:
    async def test_returns_service_detail(self) -> None:
        mock_session = AsyncMock()
        mock_service = MagicMock(spec=ServiceSummaryResponse)

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_service.return_value = mock_service

            result = await get_service(
                "billing-api",
                tenant="team-a",
                environment="prod",
                session=mock_session,
            )

            assert result == mock_service
            mock_repo.get_service.assert_called_once_with(
                "billing-api",
                tenant="team-a",
                environment="prod",
            )

    async def test_raises_404_when_service_missing(self) -> None:
        mock_session = AsyncMock()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_service.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await get_service("missing-service", session=mock_session)

            assert exc_info.value.status_code == 404
            assert "missing-service" in exc_info.value.detail
