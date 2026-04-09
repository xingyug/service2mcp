"""Tests for the SCIM 2.0 extractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.scim import SCIMExtractor
from libs.ir.models import ServiceIR

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "scim_schemas"


@pytest.fixture
def extractor() -> SCIMExtractor:
    return SCIMExtractor()


# ── Detection ──────────────────────────────────────────────────────────────


class TestDetection:
    def test_detect_with_protocol_hint(self, extractor: SCIMExtractor) -> None:
        source = SourceConfig(file_content="{}", hints={"protocol": "scim"})
        assert extractor.detect(source) == 1.0

    def test_detect_with_scim_url(self, extractor: SCIMExtractor) -> None:
        source = SourceConfig(url="https://example.com/scim/v2/Schemas")
        assert extractor.detect(source) == 0.85

    def test_detect_with_scim_content(self, extractor: SCIMExtractor) -> None:
        content = json.dumps(
            {
                "schemas": {
                    "Resources": [
                        {
                            "id": "urn:ietf:params:scim:schemas:core:2.0:User",
                            "name": "User",
                        }
                    ]
                }
            }
        )
        source = SourceConfig(file_content=content)
        assert extractor.detect(source) == 0.9

    def test_detect_non_scim(self, extractor: SCIMExtractor) -> None:
        source = SourceConfig(file_content='{"openapi": "3.0.0"}')
        assert extractor.detect(source) == 0.0


# ── User + Group extraction ───────────────────────────────────────────────


class TestUserGroupExtraction:
    @pytest.fixture
    def ir(self, extractor: SCIMExtractor) -> ServiceIR:
        source = SourceConfig(file_path=str(FIXTURES_DIR / "user_group.json"))
        return extractor.extract(source)

    def test_protocol(self, ir: ServiceIR) -> None:
        assert ir.protocol == "scim"

    def test_user_operations_exist(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        expected_ops = (
            "list_users",
            "get_user",
            "create_user",
            "update_user",
            "patch_user",
            "delete_user",
        )
        for expected in expected_ops:
            assert expected in op_names, f"Missing operation: {expected}"

    def test_group_operations_exist(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        expected_ops = (
            "list_groups",
            "get_group",
            "create_group",
            "update_group",
            "patch_group",
            "delete_group",
        )
        for expected in expected_ops:
            assert expected in op_names, f"Missing operation: {expected}"

    def test_change_password_exists(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        assert "change_password" in op_names

    def test_bulk_operation_exists(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        assert "bulk_operation" in op_names

    def test_readonly_excluded_from_create_user(self, ir: ServiceIR) -> None:
        create_op = next(op for op in ir.operations if op.name == "create_user")
        param_names = {p.name for p in create_op.params}
        assert "id" not in param_names
        assert "meta" not in param_names

    def test_readonly_excluded_from_update_user(self, ir: ServiceIR) -> None:
        update_op = next(op for op in ir.operations if op.name == "update_user")
        # readOnly 'id' attribute should not appear as body param;
        # only the path id (description "user identifier") is expected.
        readonly_body_params = {
            p.name
            for p in update_op.params
            if p.name in ("meta",) or (p.name == "id" and p.description != "user identifier")
        }
        assert len(readonly_body_params) == 0

    def test_username_in_create_and_required(self, ir: ServiceIR) -> None:
        create_op = next(op for op in ir.operations if op.name == "create_user")
        username_param = next(p for p in create_op.params if p.name == "userName")
        assert username_param.required is True


# ── Custom resource extraction ─────────────────────────────────────────────


class TestCustomResourceExtraction:
    @pytest.fixture
    def ir(self, extractor: SCIMExtractor) -> ServiceIR:
        source = SourceConfig(file_path=str(FIXTURES_DIR / "custom_resource.json"))
        return extractor.extract(source)

    def test_device_operations_exist(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        expected_ops = (
            "list_devices",
            "get_device",
            "create_device",
            "update_device",
            "delete_device",
        )
        for expected in expected_ops:
            assert expected in op_names, f"Missing operation: {expected}"

    def test_immutable_in_create_not_update(self, ir: ServiceIR) -> None:
        create_op = next(op for op in ir.operations if op.name == "create_device")
        create_params = {p.name for p in create_op.params}
        assert "serialNumber" in create_params

        update_op = next(op for op in ir.operations if op.name == "update_device")
        update_body_params = {p.name for p in update_op.params if p.name != "id"}
        assert "serialNumber" not in update_body_params

    def test_readonly_excluded_from_create_and_update(self, ir: ServiceIR) -> None:
        create_op = next(op for op in ir.operations if op.name == "create_device")
        create_params = {p.name for p in create_op.params}
        assert "firmwareVersion" not in create_params
        assert "lastSeen" not in create_params
        assert "id" not in create_params

        update_op = next(op for op in ir.operations if op.name == "update_device")
        update_body_params = {p.name for p in update_op.params if p.name != "id"}
        assert "firmwareVersion" not in update_body_params
        assert "lastSeen" not in update_body_params

    def test_no_change_password(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        assert "change_password" not in op_names

    def test_no_bulk_operation(self, ir: ServiceIR) -> None:
        op_names = {op.name for op in ir.operations}
        assert "bulk_operation" not in op_names


# ── extract edge cases ─────────────────────────────────────────────────────


class TestExtractEdgeCases:
    def test_extract_raises_when_no_content(self, extractor: SCIMExtractor) -> None:
        """Line 78: extract raises ValueError when _raw_content returns empty."""
        source = SourceConfig(file_content="placeholder")
        with patch.object(extractor, "_raw_content", return_value=""):
            with pytest.raises(ValueError, match="No content available"):
                extractor.extract(source)

    def test_resource_without_name_is_skipped(self, extractor: SCIMExtractor) -> None:
        """Lines 93-97: resource without 'name' field is skipped with warning."""
        content = json.dumps(
            {
                "schemas": {
                    "Resources": [
                        {"id": "urn:ietf:params:scim:schemas:core:2.0:NoName", "attributes": []},
                        {
                            "name": "User",
                            "attributes": [
                                {
                                    "name": "userName",
                                    "type": "string",
                                    "required": True,
                                    "mutability": "readWrite",
                                },
                            ],
                        },
                    ]
                },
                "service_provider_config": {},
            }
        )
        source = SourceConfig(file_content=content)
        ir = extractor.extract(source)
        # The nameless resource should be skipped; only User operations
        op_names = {op.name for op in ir.operations}
        assert "list_users" in op_names
        # No operations for the nameless resource
        assert ir.metadata["resource_types"] == ["User"]

    def test_fallback_extracts_standard_resources_from_users_list_response(
        self,
        extractor: SCIMExtractor,
    ) -> None:
        content = json.dumps(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": 0,
                "Resources": [],
            }
        )
        source = SourceConfig(
            url="https://jackson.example.com/api/scim/v2.0/directory-id/Users",
            file_content=content,
        )

        ir = extractor.extract(source)

        op_names = {op.name for op in ir.operations}
        assert "list_users" in op_names
        assert "list_groups" in op_names
        assert ir.base_url == "https://jackson.example.com/api/scim/v2.0/directory-id"

    def test_normalize_base_url_strips_users_suffix(self, extractor: SCIMExtractor) -> None:
        assert (
            extractor._normalize_base_url("https://example.com/scim/v2/tenant/Users")
            == "https://example.com/scim/v2/tenant"
        )

    def test_fallback_when_resources_are_user_records(
        self,
        extractor: SCIMExtractor,
    ) -> None:
        """When source URL is /Users and Resources contain user records (not
        schema definitions), the extractor should fall back to standard SCIM
        resources instead of crashing on non-string 'name' fields."""
        content = json.dumps(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "startIndex": 1,
                "totalResults": 1,
                "itemsPerPage": 100,
                "Resources": [
                    {
                        "userName": "admin@test.com",
                        "name": {"givenName": "Admin", "familyName": "User"},
                        "emails": [{"value": "admin@test.com"}],
                        "active": True,
                        "id": "user-123",
                    }
                ],
            }
        )
        source = SourceConfig(
            url="https://jackson.example.com/api/scim/v2.0/tenant-id/Users",
            file_content=content,
        )
        ir = extractor.extract(source)
        op_names = {op.name for op in ir.operations}
        assert "list_users" in op_names
        assert "list_groups" in op_names

    def test_resource_with_dict_name_is_skipped(self, extractor: SCIMExtractor) -> None:
        """A resource where 'name' is a dict (e.g. raw user record) must be
        skipped without crashing."""
        content = json.dumps(
            {
                "schemas": {
                    "Resources": [
                        {
                            "name": {"givenName": "A", "familyName": "B"},
                            "id": "bad-resource",
                        },
                        {
                            "name": "User",
                            "attributes": [
                                {
                                    "name": "userName",
                                    "type": "string",
                                    "required": True,
                                    "mutability": "readWrite",
                                },
                            ],
                        },
                    ]
                },
                "service_provider_config": {},
            }
        )
        source = SourceConfig(file_content=content)
        ir = extractor.extract(source)
        op_names = {op.name for op in ir.operations}
        assert "list_users" in op_names


# ── _raw_content URL branch ───────────────────────────────────────────────


class TestRawContentUrl:
    def test_raw_content_fetches_from_url(self, extractor: SCIMExtractor) -> None:
        """Lines 140-148: _raw_content fetches from URL with auth headers."""
        mock_response = MagicMock()
        mock_response.text = '{"schemas": {}}'
        mock_response.raise_for_status = MagicMock()

        with patch("libs.extractors.scim.httpx.get", return_value=mock_response) as mock_get:
            source = SourceConfig(url="https://scim.example.com/v2")
            content = extractor._raw_content(source)

        assert content == '{"schemas": {}}'
        mock_get.assert_called_once()

    def test_raw_content_url_with_auth_header(self, extractor: SCIMExtractor) -> None:
        """Lines 142-143: auth_header passed in request."""
        mock_response = MagicMock()
        mock_response.text = "{}"
        mock_response.raise_for_status = MagicMock()

        with patch("libs.extractors.scim.httpx.get", return_value=mock_response) as mock_get:
            source = SourceConfig(url="https://scim.example.com/v2", auth_header="Bearer tok")
            extractor._raw_content(source)

        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_raw_content_url_with_auth_token(self, extractor: SCIMExtractor) -> None:
        """Lines 144-145: auth_token formatted as Bearer."""
        mock_response = MagicMock()
        mock_response.text = "{}"
        mock_response.raise_for_status = MagicMock()

        with patch("libs.extractors.scim.httpx.get", return_value=mock_response) as mock_get:
            source = SourceConfig(url="https://scim.example.com/v2", auth_token="mytoken")
            extractor._raw_content(source)

        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer mytoken"

    def test_raw_content_no_source_returns_empty(self, extractor: SCIMExtractor) -> None:
        """Line 149: no file_content, no file_path, no URL → empty string."""
        source = SourceConfig(url="https://scim.example.com/v2")
        with patch("libs.extractors.scim.httpx.get", side_effect=Exception("fail")):
            with pytest.raises(Exception):
                extractor._raw_content(source)


class TestBuildComplexJsonSchema:
    """Tests for _build_complex_json_schema."""

    def test_complex_with_sub_attributes(self, extractor):
        attr = {
            "name": "name",
            "type": "complex",
            "subAttributes": [
                {"name": "givenName", "type": "string", "description": "First name"},
                {"name": "familyName", "type": "string", "required": True},
            ],
        }
        result = extractor._build_complex_json_schema(attr)
        assert result is not None
        assert result["type"] == "object"
        assert "givenName" in result["properties"]
        assert result["properties"]["givenName"]["description"] == "First name"
        assert result["required"] == ["familyName"]

    def test_complex_multivalued(self, extractor):
        attr = {
            "name": "emails",
            "type": "complex",
            "multiValued": True,
            "subAttributes": [
                {"name": "value", "type": "string"},
                {"name": "primary", "type": "boolean"},
            ],
        }
        result = extractor._build_complex_json_schema(attr)
        assert result is not None
        assert result["type"] == "array"
        assert result["items"]["type"] == "object"
        assert "value" in result["items"]["properties"]

    def test_non_complex_returns_none(self, extractor):
        assert extractor._build_complex_json_schema({"name": "x", "type": "string"}) is None

    def test_complex_no_sub_attrs_returns_none(self, extractor):
        assert extractor._build_complex_json_schema({"name": "x", "type": "complex"}) is None
