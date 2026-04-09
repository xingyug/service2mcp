"""Unit tests for libs/registry_client/models.py."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from libs.ir.models import ServiceIR
from libs.registry_client.models import (
    ArtifactDiffChange,
    ArtifactDiffOperation,
    ArtifactDiffResponse,
    ArtifactRecordPayload,
    ArtifactRecordResponse,
    ArtifactVersionCreate,
    ArtifactVersionListResponse,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)


def _minimal_ir_dict() -> dict[str, Any]:
    """Return a minimal valid ServiceIR dict for use in version payloads."""
    ir = ServiceIR(
        service_id="test-svc",
        service_name="Test Service",
        base_url="https://example.com",
        source_hash="sha256:test",
        protocol="openapi",
        operations=[],
    )
    return ir.model_dump()


def _minimal_route_config() -> dict[str, Any]:
    return {
        "service_id": "svc-1",
        "service_name": "Svc 1",
        "namespace": "default",
        "version_number": 1,
        "default_route": {
            "route_id": "svc-1-active",
            "target_service": {"name": "svc-1-v1", "port": 8000},
        },
        "version_route": {
            "route_id": "svc-1-v1",
            "target_service": {"name": "svc-1-v1", "port": 8000},
        },
    }


class TestArtifactRecordPayload:
    def test_valid(self) -> None:
        payload = ArtifactRecordPayload(
            artifact_type="ir_json",
            content_hash="sha256:abc123",
        )
        assert payload.artifact_type == "ir_json"

    def test_empty_artifact_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="artifact_type"):
            ArtifactRecordPayload(artifact_type="", content_hash="abc")

    def test_empty_content_hash_rejected(self) -> None:
        with pytest.raises(ValidationError, match="content_hash"):
            ArtifactRecordPayload(artifact_type="ir", content_hash="")

    def test_optional_fields(self) -> None:
        payload = ArtifactRecordPayload(
            artifact_type="ir",
            content_hash="abc",
            storage_path="/tmp/artifact.json",
            metadata_json={"key": "value"},
        )
        assert payload.storage_path == "/tmp/artifact.json"
        assert payload.metadata_json == {"key": "value"}


class TestArtifactRecordResponse:
    def test_inherits_payload_fields(self) -> None:
        resp = ArtifactRecordResponse(
            id=uuid4(),
            artifact_type="ir",
            content_hash="abc",
            created_at=datetime.utcnow(),
        )
        assert resp.artifact_type == "ir"


class TestArtifactVersionCreate:
    def test_valid_minimal(self) -> None:
        version = ArtifactVersionCreate(
            service_id="svc-1",
            version_number=1,
            ir_json=_minimal_ir_dict(),
        )
        assert version.service_id == "svc-1"
        assert version.compiler_version == "0.1.0"

    def test_empty_service_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="service_id"):
            ArtifactVersionCreate(
                service_id="",
                version_number=1,
                ir_json=_minimal_ir_dict(),
            )

    def test_version_number_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactVersionCreate(
                service_id="svc-1",
                version_number=0,
                ir_json=_minimal_ir_dict(),
            )

    def test_invalid_ir_json_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactVersionCreate(
                service_id="svc-1",
                version_number=1,
                ir_json={"not": "valid-ir"},
            )

    def test_raw_ir_json_validated(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactVersionCreate(
                service_id="svc-1",
                version_number=1,
                ir_json=_minimal_ir_dict(),
                raw_ir_json={"invalid": "ir"},
            )

    def test_with_artifacts(self) -> None:
        version = ArtifactVersionCreate(
            service_id="svc-1",
            version_number=1,
            ir_json=_minimal_ir_dict(),
            artifacts=[
                ArtifactRecordPayload(artifact_type="manifest", content_hash="abc"),
            ],
        )
        assert len(version.artifacts) == 1

    def test_invalid_route_config_rejected(self) -> None:
        with pytest.raises(ValidationError, match="namespace"):
            ArtifactVersionCreate(
                service_id="svc-1",
                version_number=1,
                ir_json=_minimal_ir_dict(),
                route_config={"service_id": "svc-1", "service_name": "Svc 1"},
            )


class TestArtifactVersionUpdate:
    def test_valid_single_field(self) -> None:
        update = ArtifactVersionUpdate(protocol="graphql")
        assert update.protocol == "graphql"

    def test_empty_update_rejected(self) -> None:
        with pytest.raises(ValidationError, match="At least one field"):
            ArtifactVersionUpdate()

    def test_invalid_ir_json_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactVersionUpdate(ir_json={"bad": "ir"})

    def test_invalid_raw_ir_json_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactVersionUpdate(raw_ir_json={"bad": "ir"})

    def test_valid_ir_json(self) -> None:
        update = ArtifactVersionUpdate(ir_json=_minimal_ir_dict())
        assert update.ir_json is not None

    def test_invalid_route_config_rejected(self) -> None:
        with pytest.raises(ValidationError, match="target_service"):
            ArtifactVersionUpdate(
                route_config={
                    **_minimal_route_config(),
                    "default_route": {"route_id": "svc-1-active"},
                }
            )


class TestArtifactVersionResponse:
    def test_full_construction(self) -> None:
        now = datetime.utcnow()
        resp = ArtifactVersionResponse(
            id=uuid4(),
            service_id="svc-1",
            version_number=1,
            is_active=True,
            ir_json=_minimal_ir_dict(),
            compiler_version="0.1.0",
            created_at=now,
        )
        assert resp.is_active is True
        assert resp.artifacts == []


class TestArtifactVersionListResponse:
    def test_empty(self) -> None:
        resp = ArtifactVersionListResponse(service_id="svc-1", versions=[])
        assert resp.versions == []


class TestArtifactDiffChange:
    def test_construction(self) -> None:
        change = ArtifactDiffChange(
            field_name="description",
            old_value="old",
            new_value="new",
        )
        assert change.param_name is None


class TestArtifactDiffOperation:
    def test_defaults(self) -> None:
        op = ArtifactDiffOperation(
            operation_id="op1",
            operation_name="Get Item",
        )
        assert op.changes == []
        assert op.added_params == []
        assert op.removed_params == []


class TestArtifactDiffResponse:
    def test_construction(self) -> None:
        resp = ArtifactDiffResponse(
            service_id="svc-1",
            from_version=1,
            to_version=2,
            summary="2 operations changed",
            is_empty=False,
            changed_operations=[
                ArtifactDiffOperation(
                    operation_id="op1",
                    operation_name="Get Item",
                    changes=[
                        ArtifactDiffChange(
                            field_name="description",
                            old_value="old",
                            new_value="new",
                        )
                    ],
                )
            ],
        )
        assert not resp.is_empty
        assert len(resp.changed_operations) == 1
