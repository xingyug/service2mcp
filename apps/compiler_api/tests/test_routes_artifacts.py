"""Unit tests for apps/compiler_api/routes/artifacts.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.compiler_api.repository import MalformedArtifactDiffError
from apps.compiler_api.routes.artifacts import (
    _not_found,
    activate_artifact_version,
    create_artifact_version,
    delete_artifact_version,
    diff_artifact_versions,
    get_artifact_version,
    list_artifact_versions,
    update_artifact_version,
)
from libs.registry_client.models import (
    ArtifactDiffResponse,
    ArtifactVersionCreate,
    ArtifactVersionListResponse,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)


def _caller(subject: str = "operator") -> TokenPrincipalResponse:
    return TokenPrincipalResponse(
        subject=subject,
        username=None,
        token_type="jwt",
        claims={"sub": subject},
    )


@pytest.fixture(autouse=True)
def _mock_audit_log():
    """Patch AuditLogService so tests don't need a real DB for audit entries."""
    with patch("apps.compiler_api.routes.artifacts.AuditLogService") as mock_cls:
        mock_cls.return_value = AsyncMock()
        yield mock_cls


class TestNotFound:
    def test_creates_404_exception(self) -> None:
        exc = _not_found("test-service", 1)
        assert exc.status_code == 404
        assert "test-service:1" in exc.detail
        assert "not found" in exc.detail


class TestCreateArtifactVersion:
    async def test_successful_creation(self, _mock_audit_log) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionCreate)
        mock_payload.service_id = "test-service"
        mock_payload.version_number = 1
        mock_response = MagicMock(spec=ArtifactVersionResponse)
        caller = _caller()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_version.return_value = mock_response

            result = await create_artifact_version(mock_payload, mock_session, caller)

            assert result == mock_response
            mock_repo_class.assert_called_once_with(mock_session)
            mock_repo.create_version.assert_called_once_with(mock_payload, commit=False)
            _mock_audit_log.return_value.append_entry.assert_called_once()
            assert _mock_audit_log.return_value.append_entry.call_args.kwargs["commit"] is False

    async def test_integrity_error_raises_conflict(self) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionCreate)
        mock_payload.service_id = "test-service"
        mock_payload.version_number = 1
        caller = _caller()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_version.side_effect = IntegrityError("statement", "params", "orig")

            with pytest.raises(HTTPException) as exc_info:
                await create_artifact_version(mock_payload, mock_session, caller)

            assert exc_info.value.status_code == 409
            assert "test-service:1" in exc_info.value.detail
            assert "already exists" in exc_info.value.detail
            mock_session.rollback.assert_called_once()


class TestListArtifactVersions:
    async def test_list_with_filters(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ArtifactVersionListResponse)

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_versions.return_value = mock_response

            result = await list_artifact_versions(
                "test-service",
                tenant="test-tenant",
                environment="prod",
                session=mock_session,
            )

            assert result == mock_response
            mock_repo.list_versions.assert_called_once_with(
                "test-service", tenant="test-tenant", environment="prod"
            )


class TestGetArtifactVersion:
    async def test_successful_get(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ArtifactVersionResponse)

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = mock_response

            result = await get_artifact_version(
                "test-service",
                1,
                tenant="test-tenant",
                environment="prod",
                session=mock_session,
            )

            assert result == mock_response
            mock_repo.get_version.assert_called_once_with(
                "test-service", 1, tenant="test-tenant", environment="prod"
            )

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await get_artifact_version("test-service", 1, session=mock_session)

            assert exc_info.value.status_code == 404


class TestUpdateArtifactVersion:
    async def test_successful_update(self, _mock_audit_log) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionUpdate)
        mock_response = MagicMock(spec=ArtifactVersionResponse)
        caller = _caller()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.update_version.return_value = mock_response

            result = await update_artifact_version(
                "test-service",
                1,
                mock_payload,
                session=mock_session,
                caller=caller,
            )

            assert result == mock_response
            mock_repo.update_version.assert_called_once_with(
                "test-service",
                1,
                mock_payload,
                tenant=None,
                environment=None,
                commit=False,
            )
            _mock_audit_log.return_value.append_entry.assert_called_once()
            assert _mock_audit_log.return_value.append_entry.call_args.kwargs["commit"] is False

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionUpdate)
        caller = _caller()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.update_version.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await update_artifact_version(
                    "test-service",
                    1,
                    mock_payload,
                    session=mock_session,
                    caller=caller,
                )

            assert exc_info.value.status_code == 404


class TestDeleteArtifactVersion:
    async def test_successful_delete(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        caller = _caller()
        deleted_version = MagicMock(
            is_active=False,
            route_config={"service_id": "test-service", "version_route": {"route_id": "v1"}},
        )

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = deleted_version
            mock_repo.delete_version.return_value = True

            response = await delete_artifact_version(
                "test-service",
                1,
                session=mock_session,
                route_publisher=route_publisher,
                caller=caller,
            )

            assert response.status_code == 204
            mock_repo.delete_version.assert_called_once_with(
                "test-service",
                1,
                tenant=None,
                environment=None,
                commit=False,
            )
            route_publisher.delete.assert_awaited_once()
            mock_session.commit.assert_awaited_once()

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        caller = _caller()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await delete_artifact_version(
                    "test-service",
                    1,
                    session=mock_session,
                    route_publisher=route_publisher,
                    caller=caller,
                )

            assert exc_info.value.status_code == 404

    async def test_delete_active_version_syncs_replacement_routes(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        caller = _caller()
        deleted_version = MagicMock(
            is_active=True,
            route_config={
                "service_id": "test-service",
                "default_route": {"route_id": "test-service-active"},
                "version_route": {"route_id": "test-service-v1"},
            },
        )
        replacement = MagicMock(
            route_config={
                "service_id": "test-service",
                "default_route": {"route_id": "test-service-active"},
                "version_route": {"route_id": "test-service-v2"},
            }
        )

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = deleted_version
            mock_repo.delete_version.return_value = True
            mock_repo.get_active_version.return_value = replacement

            response = await delete_artifact_version(
                "test-service",
                1,
                session=mock_session,
                route_publisher=route_publisher,
                caller=caller,
            )

            assert response.status_code == 204
            route_publisher.delete.assert_awaited_once()
            route_publisher.sync.assert_awaited_once_with(replacement.route_config)

    async def test_delete_rolls_back_when_route_sync_fails(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        route_publisher.delete.side_effect = RuntimeError("gateway down")
        caller = _caller()
        deleted_version = MagicMock(
            is_active=False,
            route_config={"service_id": "test-service", "version_route": {"route_id": "v1"}},
        )

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = deleted_version
            mock_repo.delete_version.return_value = True

            with pytest.raises(HTTPException) as exc_info:
                await delete_artifact_version(
                    "test-service",
                    1,
                    session=mock_session,
                    route_publisher=route_publisher,
                    caller=caller,
                )

            assert exc_info.value.status_code == 502
            mock_session.rollback.assert_awaited_once()

    async def test_delete_audit_failure_rolls_back_routes(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        route_publisher.delete.return_value = {
            "previous_routes": {"test-service-v1": {"route_id": "test-service-v1"}},
        }
        caller = _caller()
        deleted_version = MagicMock(
            is_active=False,
            route_config={
                "service_id": "test-service",
                "version_route": {"route_id": "test-service-v1"},
            },
        )

        with (
            patch(
                "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
            ) as mock_repo_class,
            patch("apps.compiler_api.routes.artifacts.AuditLogService") as mock_audit_cls,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = deleted_version
            mock_repo.delete_version.return_value = True
            mock_audit = AsyncMock()
            mock_audit.append_entry.side_effect = RuntimeError("audit broke")
            mock_audit_cls.return_value = mock_audit

            with pytest.raises(HTTPException) as exc_info:
                await delete_artifact_version(
                    "test-service",
                    1,
                    session=mock_session,
                    route_publisher=route_publisher,
                    caller=caller,
                )

            assert exc_info.value.status_code == 502
            route_publisher.rollback.assert_awaited_once_with(
                {
                    "service_id": "test-service",
                    "default_route": None,
                    "version_route": {"route_id": "test-service-v1"},
                },
                {"test-service-v1": {"route_id": "test-service-v1"}},
            )


class TestActivateArtifactVersion:
    async def test_successful_activation(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        caller = _caller()
        mock_response = MagicMock(
            spec=ArtifactVersionResponse,
            route_config={"service_id": "test-service", "default_route": {"route_id": "active"}},
        )

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.activate_version.return_value = mock_response

            result = await activate_artifact_version(
                "test-service",
                1,
                session=mock_session,
                route_publisher=route_publisher,
                caller=caller,
            )

            assert result == mock_response
            mock_repo.activate_version.assert_called_once_with(
                "test-service",
                1,
                tenant=None,
                environment=None,
                commit=False,
            )
            route_publisher.sync.assert_awaited_once_with(mock_response.route_config)
            mock_session.commit.assert_awaited_once()

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        caller = _caller()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.activate_version.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await activate_artifact_version(
                    "test-service",
                    1,
                    session=mock_session,
                    route_publisher=route_publisher,
                    caller=caller,
                )

            assert exc_info.value.status_code == 404

    async def test_activation_rolls_back_when_route_sync_fails(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        route_publisher.sync.side_effect = RuntimeError("gateway down")
        caller = _caller()
        mock_response = MagicMock(
            spec=ArtifactVersionResponse,
            route_config={"service_id": "test-service", "default_route": {"route_id": "active"}},
        )

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.activate_version.return_value = mock_response

            with pytest.raises(HTTPException) as exc_info:
                await activate_artifact_version(
                    "test-service",
                    1,
                    session=mock_session,
                    route_publisher=route_publisher,
                    caller=caller,
                )

            assert exc_info.value.status_code == 502
            mock_session.rollback.assert_awaited_once()

    async def test_activation_audit_failure_rolls_back_routes(self) -> None:
        mock_session = AsyncMock()
        route_publisher = AsyncMock()
        route_publisher.sync.return_value = {
            "previous_routes": {"test-service-active": {"route_id": "test-service-active"}},
        }
        caller = _caller()
        mock_response = MagicMock(
            spec=ArtifactVersionResponse,
            route_config={
                "service_id": "test-service",
                "default_route": {"route_id": "test-service-active"},
            },
        )

        with (
            patch(
                "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
            ) as mock_repo_class,
            patch("apps.compiler_api.routes.artifacts.AuditLogService") as mock_audit_cls,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.activate_version.return_value = mock_response
            mock_audit = AsyncMock()
            mock_audit.append_entry.side_effect = RuntimeError("audit broke")
            mock_audit_cls.return_value = mock_audit

            with pytest.raises(HTTPException) as exc_info:
                await activate_artifact_version(
                    "test-service",
                    1,
                    session=mock_session,
                    route_publisher=route_publisher,
                    caller=caller,
                )

            assert exc_info.value.status_code == 502
            route_publisher.rollback.assert_awaited_once_with(
                mock_response.route_config,
                {"test-service-active": {"route_id": "test-service-active"}},
            )


class TestDiffArtifactVersions:
    async def test_successful_diff(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ArtifactDiffResponse)

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.diff_versions.return_value = mock_response

            result = await diff_artifact_versions(
                "test-service",
                from_version=1,
                to_version=2,
                tenant="test-tenant",
                environment="prod",
                session=mock_session,
            )

            assert result == mock_response
            mock_repo.diff_versions.assert_called_once_with(
                "test-service",
                from_version=1,
                to_version=2,
                tenant="test-tenant",
                environment="prod",
            )

    async def test_diff_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.diff_versions.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await diff_artifact_versions(
                    "test-service", from_version=1, to_version=2, session=mock_session
                )

            assert exc_info.value.status_code == 404
            assert "Unable to diff" in exc_info.value.detail

    async def test_diff_malformed_ir_raises_409(self) -> None:
        mock_session = AsyncMock()

        with patch(
            "apps.compiler_api.routes.artifacts.ArtifactRegistryRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.diff_versions.side_effect = MalformedArtifactDiffError(
                service_id="test-service",
                version_number=1,
            )

            with pytest.raises(HTTPException) as exc_info:
                await diff_artifact_versions(
                    "test-service", from_version=1, to_version=2, session=mock_session
                )

            assert exc_info.value.status_code == 409
            assert "malformed" in exc_info.value.detail
