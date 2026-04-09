"""Unit tests for apps/compiler_api/routes/services.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.compiler_api.models import (
    DashboardSummaryResponse,
    ServiceListResponse,
    ServiceSummaryResponse,
)
from apps.compiler_api.repository import MalformedServiceVersionError
from apps.compiler_api.routes.services import (
    delete_service,
    get_dashboard_summary,
    get_service,
    list_services,
)


def _caller(subject: str = "operator", **extra_claims: object) -> TokenPrincipalResponse:
    claims: dict[str, object] = {"sub": subject, **extra_claims}
    return TokenPrincipalResponse(
        subject=subject,
        username=None,
        token_type="jwt",
        claims=claims,
    )


class TestListServices:
    async def test_returns_repository_response(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ServiceListResponse)
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_services.return_value = mock_response

            result = await list_services(
                tenant="team-a",
                environment="prod",
                session=mock_session,
                caller=caller,
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
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_service.return_value = mock_service

            result = await get_service(
                "billing-api",
                tenant="team-a",
                environment="prod",
                session=mock_session,
                caller=caller,
            )

            assert result == mock_service
            mock_repo.get_service.assert_called_once_with(
                "billing-api",
                tenant="team-a",
                environment="prod",
            )

    async def test_raises_404_when_service_missing(self) -> None:
        mock_session = AsyncMock()
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_service.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await get_service("missing-service", session=mock_session, caller=caller)

            assert exc_info.value.status_code == 404
            assert "missing-service" in exc_info.value.detail

    async def test_raises_409_when_service_record_is_malformed(self) -> None:
        mock_session = AsyncMock()
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_service.side_effect = MalformedServiceVersionError(
                service_id="billing-api",
                version_number=3,
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_service("billing-api", session=mock_session, caller=caller)

            assert exc_info.value.status_code == 409
            assert "billing-api v3" in exc_info.value.detail


class TestScopeAccessServices:
    """Verify tenant/environment authorization on service routes."""

    async def test_admin_can_access_any_tenant(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(roles=["admin"])

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_services.return_value = MagicMock(spec=ServiceListResponse)

            result = await list_services(
                tenant="other-tenant",
                session=mock_session,
                caller=caller,
            )
            assert result is not None

    async def test_matching_tenant_claim_allowed(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(tenant="team-a")

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_services.return_value = MagicMock(spec=ServiceListResponse)

            result = await list_services(
                tenant="team-a",
                session=mock_session,
                caller=caller,
            )
            assert result is not None

    async def test_mismatched_tenant_claim_raises_403(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(tenant="team-a")

        with pytest.raises(HTTPException) as exc_info:
            await list_services(
                tenant="team-b",
                session=mock_session,
                caller=caller,
            )

        assert exc_info.value.status_code == 403
        assert "team-b" in exc_info.value.detail

    async def test_no_scope_claim_no_tenant_requested(self) -> None:
        mock_session = AsyncMock()
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_services.return_value = MagicMock(spec=ServiceListResponse)

            result = await list_services(session=mock_session, caller=caller)
            assert result is not None

    async def test_mismatched_environment_claim_raises_403(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(environment="staging")

        with pytest.raises(HTTPException) as exc_info:
            await get_service(
                "svc",
                environment="prod",
                session=mock_session,
                caller=caller,
            )

        assert exc_info.value.status_code == 403
        assert "prod" in exc_info.value.detail

    async def test_tenants_list_claim_allows_matching_tenant(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(tenants=["team-a", "team-b"])

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_services.return_value = MagicMock(spec=ServiceListResponse)

            result = await list_services(
                tenant="team-a",
                session=mock_session,
                caller=caller,
            )
            assert result is not None

    async def test_tenants_list_claim_rejects_unknown_tenant(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(tenants=["team-a", "team-b"])

        with pytest.raises(HTTPException) as exc_info:
            await list_services(
                tenant="team-c",
                session=mock_session,
                caller=caller,
            )

        assert exc_info.value.status_code == 403


class TestGetDashboardSummary:
    async def test_returns_dashboard_response(self) -> None:
        mock_session = AsyncMock()
        mock_response = DashboardSummaryResponse(
            total_services=3,
            total_tools=25,
            protocol_distribution={"openapi": 2, "grpc": 1},
            recent_compilations=[],
        )
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_dashboard_summary = AsyncMock(return_value=mock_response)
            result = await get_dashboard_summary(
                session=mock_session,
                caller=caller,
            )
            assert result.total_services == 3
            assert result.total_tools == 25
            assert result.protocol_distribution["openapi"] == 2

    async def test_dashboard_passes_tenant_filter(self) -> None:
        mock_session = AsyncMock()
        mock_response = DashboardSummaryResponse(
            total_services=1,
            total_tools=5,
            protocol_distribution={"rest": 1},
            recent_compilations=[],
        )
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_dashboard_summary = AsyncMock(return_value=mock_response)
            await get_dashboard_summary(
                tenant="team-a",
                session=mock_session,
                caller=caller,
            )
            mock_repo.get_dashboard_summary.assert_called_once_with(
                tenant="team-a",
                environment=None,
                recent_limit=10,
            )


class TestDeleteService:
    async def test_deletes_service_returns_none(self) -> None:
        mock_session = AsyncMock()
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.delete_service.return_value = 3

            result = await delete_service(
                "billing-api",
                tenant="team-a",
                environment="prod",
                session=mock_session,
                caller=caller,
            )
            assert result is None
            mock_repo.delete_service.assert_called_once_with(
                "billing-api",
                tenant="team-a",
                environment="prod",
            )

    async def test_raises_404_when_service_not_found(self) -> None:
        mock_session = AsyncMock()
        caller = _caller()

        with patch("apps.compiler_api.routes.services.ServiceCatalogRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.delete_service.return_value = 0

            with pytest.raises(HTTPException) as exc_info:
                await delete_service("missing-svc", session=mock_session, caller=caller)

            assert exc_info.value.status_code == 404
            assert "missing-svc" in exc_info.value.detail

    async def test_scope_mismatch_raises_403(self) -> None:
        mock_session = AsyncMock()
        caller = _caller(tenant="team-a")

        with pytest.raises(HTTPException) as exc_info:
            await delete_service(
                "billing-api",
                tenant="team-b",
                session=mock_session,
                caller=caller,
            )

        assert exc_info.value.status_code == 403
