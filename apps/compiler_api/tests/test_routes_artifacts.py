"""Unit tests for apps/compiler_api/routes/artifacts.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

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


class TestNotFound:
    def test_creates_404_exception(self) -> None:
        exc = _not_found("test-service", 1)
        assert exc.status_code == 404
        assert "test-service:1" in exc.detail
        assert "not found" in exc.detail


class TestCreateArtifactVersion:
    async def test_successful_creation(self) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionCreate)
        mock_response = MagicMock(spec=ArtifactVersionResponse)
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_version.return_value = mock_response
            
            result = await create_artifact_version(mock_payload, mock_session)
            
            assert result == mock_response
            mock_repo_class.assert_called_once_with(mock_session)
            mock_repo.create_version.assert_called_once_with(mock_payload)

    async def test_integrity_error_raises_conflict(self) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionCreate)
        mock_payload.service_id = "test-service"
        mock_payload.version_number = 1
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_version.side_effect = IntegrityError("statement", "params", "orig")
            
            with pytest.raises(HTTPException) as exc_info:
                await create_artifact_version(mock_payload, mock_session)
            
            assert exc_info.value.status_code == 409
            assert "test-service:1" in exc_info.value.detail
            assert "already exists" in exc_info.value.detail
            mock_session.rollback.assert_called_once()


class TestListArtifactVersions:
    async def test_list_with_filters(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ArtifactVersionListResponse)
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
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
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
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
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_version.return_value = None
            
            with pytest.raises(HTTPException) as exc_info:
                await get_artifact_version("test-service", 1, session=mock_session)
            
            assert exc_info.value.status_code == 404


class TestUpdateArtifactVersion:
    async def test_successful_update(self) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionUpdate)
        mock_response = MagicMock(spec=ArtifactVersionResponse)
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.update_version.return_value = mock_response
            
            result = await update_artifact_version("test-service", 1, mock_payload, mock_session)
            
            assert result == mock_response
            mock_repo.update_version.assert_called_once_with("test-service", 1, mock_payload)

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        mock_payload = MagicMock(spec=ArtifactVersionUpdate)
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.update_version.return_value = None
            
            with pytest.raises(HTTPException) as exc_info:
                await update_artifact_version("test-service", 1, mock_payload, mock_session)
            
            assert exc_info.value.status_code == 404


class TestDeleteArtifactVersion:
    async def test_successful_delete(self) -> None:
        mock_session = AsyncMock()
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.delete_version.return_value = True
            
            response = await delete_artifact_version("test-service", 1, mock_session)
            
            assert response.status_code == 204
            mock_repo.delete_version.assert_called_once_with("test-service", 1)

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.delete_version.return_value = False
            
            with pytest.raises(HTTPException) as exc_info:
                await delete_artifact_version("test-service", 1, mock_session)
            
            assert exc_info.value.status_code == 404


class TestActivateArtifactVersion:
    async def test_successful_activation(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ArtifactVersionResponse)
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.activate_version.return_value = mock_response
            
            result = await activate_artifact_version("test-service", 1, mock_session)
            
            assert result == mock_response
            mock_repo.activate_version.assert_called_once_with("test-service", 1)

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.activate_version.return_value = None
            
            with pytest.raises(HTTPException) as exc_info:
                await activate_artifact_version("test-service", 1, mock_session)
            
            assert exc_info.value.status_code == 404


class TestDiffArtifactVersions:
    async def test_successful_diff(self) -> None:
        mock_session = AsyncMock()
        mock_response = MagicMock(spec=ArtifactDiffResponse)
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
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
        
        with patch("apps.compiler_api.routes.artifacts.ArtifactRegistryRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.diff_versions.return_value = None
            
            with pytest.raises(HTTPException) as exc_info:
                await diff_artifact_versions(
                    "test-service", from_version=1, to_version=2, session=mock_session
                )
            
            assert exc_info.value.status_code == 404
            assert "Unable to diff" in exc_info.value.detail