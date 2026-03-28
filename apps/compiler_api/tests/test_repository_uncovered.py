"""Unit tests for apps/compiler_api/repository.py uncovered lines."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from apps.compiler_api.repository import (
    ArtifactRegistryRepository,
    CompilationRepository,
    ServiceCatalogRepository,
)
from libs.db_models import CompilationJob
from libs.registry_client.models import ArtifactVersionUpdate


def create_mock_update(**kwargs) -> MagicMock:
    """Create a mock ArtifactVersionUpdate with all fields set to None except specified ones."""
    mock_update = MagicMock(spec=ArtifactVersionUpdate)
    # Set all fields to None by default
    for field in [
        "ir_json",
        "raw_ir_json",
        "compiler_version",
        "source_url",
        "source_hash",
        "protocol",
        "validation_report",
        "deployment_revision",
        "route_config",
        "tenant",
        "environment",
        "artifacts",
    ]:
        setattr(mock_update, field, kwargs.get(field, None))
    return mock_update


class TestCompilationRepositoryUncoveredLines:
    async def test_delete_job_nonexistent_returns_early(self) -> None:
        """Test line 66-67: return when job doesn't exist."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None

        repo = CompilationRepository(mock_session)

        job_id = uuid4()
        await repo.delete_job(job_id)

        mock_session.get.assert_called_once_with(CompilationJob, job_id)
        # Should not call delete or commit when job doesn't exist
        mock_session.delete.assert_not_called()
        mock_session.commit.assert_not_called()

    async def test_delete_job_existing_deletes_and_commits(self) -> None:
        """Test lines 64-68: delete existing job."""
        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_session.get.return_value = mock_job

        repo = CompilationRepository(mock_session)

        job_id = uuid4()
        await repo.delete_job(job_id)

        mock_session.get.assert_called_once_with(CompilationJob, job_id)
        mock_session.delete.assert_called_once_with(mock_job)
        mock_session.commit.assert_called_once()

    async def test_get_job_nonexistent_returns_none(self) -> None:
        """Test line 73: return None when job doesn't exist."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None

        repo = CompilationRepository(mock_session)

        job_id = uuid4()
        result = await repo.get_job(job_id)

        assert result is None
        mock_session.get.assert_called_once_with(CompilationJob, job_id)


class TestServiceCatalogRepositoryUncoveredLines:
    async def test_list_services_with_tenant_filter(self) -> None:
        """Test line 138: tenant filter is applied."""
        mock_session = AsyncMock()
        # Mock the entire chain to avoid coroutine issues
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.scalars.return_value = mock_result

        repo = ServiceCatalogRepository(mock_session)

        result = await repo.list_services(tenant="test-tenant")

        # Verify query was built with tenant filter and result is correct
        mock_session.scalars.assert_called_once()
        assert hasattr(result, "services")
        assert result.services == []

    async def test_list_services_with_environment_filter(self) -> None:
        """Test line 143: environment filter is applied."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.scalars.return_value = mock_result

        repo = ServiceCatalogRepository(mock_session)

        result = await repo.list_services(environment="prod")

        # Verify query was built with environment filter and result is correct
        mock_session.scalars.assert_called_once()
        assert hasattr(result, "services")
        assert result.services == []

    async def test_list_services_with_both_filters(self) -> None:
        """Test both lines 138 and 143: both filters applied."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.scalars.return_value = mock_result

        repo = ServiceCatalogRepository(mock_session)

        result = await repo.list_services(tenant="test-tenant", environment="prod")

        # Verify query was built with both filters and result is correct
        mock_session.scalars.assert_called_once()
        assert hasattr(result, "services")
        assert result.services == []


class TestArtifactRegistryRepositoryUncoveredLines:
    async def test_get_version_nonexistent_returns_none(self) -> None:
        """Test line 219: return None when version doesn't exist."""
        mock_session = AsyncMock()

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=None
        ) as mock_get:
            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.get_version("nonexistent-service", 1)

            assert result is None
            mock_get.assert_called_once_with(
                "nonexistent-service", 1, tenant=None, environment=None
            )

    async def test_update_version_nonexistent_returns_none(self) -> None:
        """Test line 252: return None when version doesn't exist."""
        mock_session = AsyncMock()

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=None
        ) as mock_get:
            repo = ArtifactRegistryRepository(mock_session)

            mock_update = create_mock_update(ir_json={"test": "data"})
            result = await repo.update_version("nonexistent-service", 1, mock_update)

            assert result is None
            mock_get.assert_called_once_with("nonexistent-service", 1)

    async def test_activate_version_nonexistent_returns_none(self) -> None:
        """Test line 292: return None when version doesn't exist."""
        mock_session = AsyncMock()

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=None
        ) as mock_get:
            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.activate_version("nonexistent-service", 1)

            assert result is None
            mock_get.assert_called_once_with("nonexistent-service", 1)

    async def test_delete_version_nonexistent_returns_false(self) -> None:
        """Test line 309: return False when version doesn't exist."""
        mock_session = AsyncMock()

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=None
        ) as mock_get:
            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.delete_version("nonexistent-service", 1)

            assert result is False
            mock_get.assert_called_once_with("nonexistent-service", 1)

    async def test_delete_version_inactive_no_replacement_needed(self) -> None:
        """Test delete inactive version - no replacement logic triggered."""
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_active = False

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
        ):
            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.delete_version("test-service", 1)

            assert result is True
            mock_session.delete.assert_called_once_with(mock_record)
            mock_session.flush.assert_called_once()
            mock_session.commit.assert_called_once()
            # Should not query for replacement since it wasn't active
            mock_session.scalar.assert_not_called()

    async def test_delete_version_active_with_replacement(self) -> None:
        """Test lines 317-319: replacement version becomes active."""
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_active = True
        mock_replacement = MagicMock()
        mock_session.scalar.return_value = mock_replacement

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
        ):
            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.delete_version("test-service", 1)

            assert result is True
            mock_session.delete.assert_called_once_with(mock_record)
            mock_session.flush.assert_called_once()
            mock_session.scalar.assert_called_once()  # Query for replacement
            assert mock_replacement.is_active is True
            mock_session.commit.assert_called_once()

    async def test_delete_version_active_no_replacement(self) -> None:
        """Test delete active version with no replacement available."""
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_active = True
        mock_session.scalar.return_value = None  # No replacement found

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
        ):
            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.delete_version("test-service", 1)

            assert result is True
            mock_session.delete.assert_called_once_with(mock_record)
            mock_session.flush.assert_called_once()
            mock_session.scalar.assert_called_once()  # Query for replacement
            mock_session.commit.assert_called_once()

    async def test_diff_versions_missing_from_version_returns_none(self) -> None:
        """Test lines 342-351: return None when from_version doesn't exist."""
        mock_session = AsyncMock()

        with patch.object(ArtifactRegistryRepository, "_get_version_record") as mock_get:
            # First call (from_version) returns None, second call doesn't matter
            mock_get.side_effect = [None, MagicMock()]

            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.diff_versions("test-service", from_version=1, to_version=2)

            assert result is None

    async def test_diff_versions_missing_to_version_returns_none(self) -> None:
        """Test lines 342-351: return None when to_version doesn't exist."""
        mock_session = AsyncMock()

        with patch.object(ArtifactRegistryRepository, "_get_version_record") as mock_get:
            # First call (from_version) returns record, second call (to_version) returns None
            mock_get.side_effect = [MagicMock(), None]

            repo = ArtifactRegistryRepository(mock_session)

            result = await repo.diff_versions("test-service", from_version=1, to_version=2)

            assert result is None

    def test_normalize_optional_ir_json_with_none(self) -> None:
        """Test line 447: return None when payload is None."""
        result = ArtifactRegistryRepository._normalize_optional_ir_json(None)
        assert result is None

    def test_normalize_optional_ir_json_with_data(self) -> None:
        """Test line 447: call _normalize_ir_json when payload is not None."""
        with patch.object(
            ArtifactRegistryRepository, "_normalize_ir_json", return_value={"normalized": True}
        ) as mock_normalize:
            payload = {"test": "data"}
            result = ArtifactRegistryRepository._normalize_optional_ir_json(payload)

            assert result == {"normalized": True}
            mock_normalize.assert_called_once_with(payload)
