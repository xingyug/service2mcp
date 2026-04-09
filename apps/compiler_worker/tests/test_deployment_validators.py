"""Tests for deployment metadata validators."""

from __future__ import annotations

from apps.compiler_worker.deployment_validators import (
    validate_deployment_revision,
    validate_route_config,
    validate_storage_path,
)


class TestValidateDeploymentRevision:
    def test_valid_revision(self) -> None:
        assert validate_deployment_revision("abc123") == []

    def test_valid_revision_with_dashes(self) -> None:
        assert validate_deployment_revision("20260401-abc123-r1") == []

    def test_blank_revision(self) -> None:
        errors = validate_deployment_revision("   ")
        assert len(errors) > 0

    def test_empty_revision(self) -> None:
        errors = validate_deployment_revision("")
        assert len(errors) > 0


class TestValidateStoragePath:
    def test_valid_path(self) -> None:
        assert validate_storage_path("/data/manifests/service-abc") == []

    def test_traversal_rejected(self) -> None:
        errors = validate_storage_path("/data/../etc/passwd")
        assert len(errors) > 0
        assert any("traversal" in e for e in errors)

    def test_blank_path(self) -> None:
        errors = validate_storage_path("   ")
        assert len(errors) > 0

    def test_empty_path(self) -> None:
        errors = validate_storage_path("")
        assert len(errors) > 0


class TestValidateRouteConfig:
    def test_valid_config(self) -> None:
        config = {
            "default_route": {"route_id": "svc-abc/v1"},
            "service_name": "my-service",
        }
        assert validate_route_config(config) == []

    def test_empty_config(self) -> None:
        assert validate_route_config({}) == []

    def test_missing_route_id(self) -> None:
        config = {"default_route": {"other_field": "value"}}
        errors = validate_route_config(config)
        assert len(errors) > 0

    def test_blank_route_id(self) -> None:
        config = {"default_route": {"route_id": "  "}}
        errors = validate_route_config(config)
        assert len(errors) > 0

    def test_invalid_route_id_characters(self) -> None:
        config = {"default_route": {"route_id": "svc abc!@#"}}
        errors = validate_route_config(config)
        assert len(errors) > 0

    def test_valid_route_id_with_slashes(self) -> None:
        config = {"default_route": {"route_id": "ns/svc-name/v1"}}
        assert validate_route_config(config) == []

    def test_valid_route_id_with_dots(self) -> None:
        config = {"default_route": {"route_id": "com.example.service"}}
        assert validate_route_config(config) == []
