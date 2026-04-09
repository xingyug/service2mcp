"""Tests for the CLI extractor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.cli import CLIExtractor, _json_schema_for_param
from libs.ir.models import ErrorSchema, RiskLevel, SourceType


@pytest.fixture
def extractor() -> CLIExtractor:
    return CLIExtractor()


@pytest.fixture
def simple_fixture_path() -> Path:
    return Path(__file__).parents[3] / "tests" / "fixtures" / "cli_specs" / "simple_tool.cli.yaml"


@pytest.fixture
def complex_fixture_path() -> Path:
    return Path(__file__).parents[3] / "tests" / "fixtures" / "cli_specs" / "complex_tool.cli.yaml"


@pytest.fixture
def simple_yaml_content(simple_fixture_path: Path) -> str:
    return simple_fixture_path.read_text(encoding="utf-8")


@pytest.fixture
def complex_yaml_content(complex_fixture_path: Path) -> str:
    return complex_fixture_path.read_text(encoding="utf-8")


# ── Detection tests ──────────────────────────────────────────────────────


class TestCLIDetection:
    def test_detect_with_protocol_hint(self, extractor: CLIExtractor) -> None:
        source = SourceConfig(
            file_content="irrelevant",
            hints={"protocol": "cli"},
        )
        assert extractor.detect(source) == 0.95

    def test_detect_with_cli_yaml_extension(self, extractor: CLIExtractor) -> None:
        source = SourceConfig(
            file_path="/some/path/tool.cli.yaml",
            file_content="name: x",
        )
        assert extractor.detect(source) == 0.90

    def test_detect_with_cli_json_extension(self, extractor: CLIExtractor) -> None:
        source = SourceConfig(
            file_path="/some/path/tool.cli.json",
            file_content="{}",
        )
        assert extractor.detect(source) == 0.90

    def test_detect_with_cli_yml_extension(self, extractor: CLIExtractor) -> None:
        source = SourceConfig(
            file_path="/some/path/tool.cli.yml",
            file_content="name: x",
        )
        assert extractor.detect(source) == 0.90

    def test_detect_with_commands_key_in_content(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        assert extractor.detect(source) == 0.85

    def test_detect_returns_zero_for_openapi_content(self, extractor: CLIExtractor) -> None:
        openapi_content = '{"openapi": "3.0.0", "info": {"title": "API"}, "paths": {}}'
        source = SourceConfig(file_content=openapi_content)
        assert extractor.detect(source) == 0.0

    def test_detect_returns_zero_for_empty_content(self, extractor: CLIExtractor) -> None:
        source = SourceConfig(file_content="   ")  # whitespace-only
        assert extractor.detect(source) == 0.0

    def test_detect_returns_zero_for_no_source(self, extractor: CLIExtractor) -> None:
        source = SourceConfig(url="https://example.com/api")
        assert extractor.detect(source) == 0.0

    def test_detect_with_json_content(self, extractor: CLIExtractor) -> None:
        content = json.dumps(
            {
                "name": "json-tool",
                "commands": [{"name": "run", "subcommands": ["run"]}],
            }
        )
        source = SourceConfig(file_content=content)
        assert extractor.detect(source) > 0


# ── Extraction tests ─────────────────────────────────────────────────────


class TestCLIExtraction:
    def test_extract_simple_tool_operations_count(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        assert len(ir.operations) == 3

    def test_extract_simple_tool_service_name(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        assert ir.service_name == "simple-tool"

    def test_extract_simple_tool_protocol(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        assert ir.protocol == "cli"

    def test_extract_operation_has_cli_config(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        for op in ir.operations:
            assert op.cli is not None, f"Operation {op.name} missing CliOperationConfig"

    def test_extract_operation_params(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        list_op = next(op for op in ir.operations if op.name == "list-items")
        param_names = {p.name for p in list_op.params}
        assert "format" in param_names
        assert "limit" in param_names
        format_param = next(p for p in list_op.params if p.name == "format")
        assert format_param.type == "string"
        assert format_param.required is False
        assert format_param.default == "table"
        limit_param = next(p for p in list_op.params if p.name == "limit")
        assert limit_param.type == "integer"

    def test_extract_risk_safe_operation(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        list_op = next(op for op in ir.operations if op.name == "list-items")
        assert list_op.risk.risk_level == RiskLevel.safe
        assert list_op.risk.writes_state is False
        assert list_op.risk.destructive is False

    def test_extract_risk_cautious_operation(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        create_op = next(op for op in ir.operations if op.name == "create-item")
        assert create_op.risk.risk_level == RiskLevel.cautious
        assert create_op.risk.writes_state is True
        assert create_op.risk.destructive is False

    def test_extract_risk_dangerous_operation(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        delete_op = next(op for op in ir.operations if op.name == "delete-item")
        assert delete_op.risk.risk_level == RiskLevel.dangerous
        assert delete_op.risk.writes_state is True
        assert delete_op.risk.destructive is True

    def test_extract_complex_tool(self, extractor: CLIExtractor, complex_yaml_content: str) -> None:
        source = SourceConfig(file_content=complex_yaml_content)
        ir = extractor.extract(source)
        assert len(ir.operations) == 3
        assert ir.service_name == "kubectl-proxy"

    def test_extract_complex_tool_subcommands(
        self, extractor: CLIExtractor, complex_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=complex_yaml_content)
        ir = extractor.extract(source)
        get_pods = next(op for op in ir.operations if op.name == "get-pods")
        assert get_pods.cli is not None
        assert get_pods.cli.subcommands == ["get", "pods"]

    def test_extract_from_file_path(
        self, extractor: CLIExtractor, simple_fixture_path: Path
    ) -> None:
        source = SourceConfig(file_path=str(simple_fixture_path))
        ir = extractor.extract(source)
        assert len(ir.operations) == 3
        assert ir.service_name == "simple-tool"

    def test_extract_from_file_content(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        assert len(ir.operations) == 3
        assert ir.protocol == "cli"


# ── Edge case tests ──────────────────────────────────────────────────────


class TestCLIEdgeCases:
    def test_extract_minimal_spec(self, extractor: CLIExtractor) -> None:
        minimal = "name: minimal\ncommands:\n  - name: do-thing\n    subcommands: ['run']"
        source = SourceConfig(file_content=minimal)
        ir = extractor.extract(source)
        assert len(ir.operations) == 1
        assert ir.operations[0].name == "do-thing"

    def test_extract_no_commands_raises(self, extractor: CLIExtractor) -> None:
        no_commands = "name: bad-tool\nversion: '1.0'"
        source = SourceConfig(file_content=no_commands)
        with pytest.raises(ValueError, match="commands"):
            extractor.extract(source)

    def test_extract_invalid_yaml_raises(self, extractor: CLIExtractor) -> None:
        garbage = "{{{{not valid yaml or json!!!!}}}}"
        source = SourceConfig(file_content=garbage)
        with pytest.raises(ValueError, match="not valid YAML or JSON"):
            extractor.extract(source)

    def test_operation_ids_are_unique(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        ids = [op.id for op in ir.operations]
        assert len(ids) == len(set(ids))

    def test_source_is_extractor(self, extractor: CLIExtractor, simple_yaml_content: str) -> None:
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        for op in ir.operations:
            assert op.source == SourceType.extractor

    def test_extract_command_without_name_skipped(self, extractor: CLIExtractor) -> None:
        content = (
            "name: t\ncommands:\n  - subcommands: ['run']\n  - name: valid\n    subcommands: ['go']"
        )
        source = SourceConfig(file_content=content)
        ir = extractor.extract(source)
        assert len(ir.operations) == 1
        assert ir.operations[0].name == "valid"


# ── Error schema tests ───────────────────────────────────────────────────


class TestCLIErrorSchema:
    def test_cli_operations_have_error_schema(
        self, extractor: CLIExtractor, simple_yaml_content: str
    ) -> None:
        """CLI operations should have exit-code based error schema."""
        source = SourceConfig(file_content=simple_yaml_content)
        ir = extractor.extract(source)
        assert len(ir.operations) > 0
        for op in ir.operations:
            assert op.error_schema is not None, f"Operation {op.name} missing error_schema"
            assert isinstance(op.error_schema, ErrorSchema)
            assert len(op.error_schema.responses) >= 2
            error_codes = {r.error_code for r in op.error_schema.responses}
            assert "nonzero_exit" in error_codes
            assert "timeout" in error_codes
            assert "not_found" in error_codes
            assert op.error_schema.default_error_schema is not None


class TestCLIJsonSchema:
    """Tests for json_schema emission on CLI params with complex types."""

    def test_json_schema_for_object_arg(self) -> None:
        """Object arg with properties should produce json_schema."""
        arg: dict[str, Any] = {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
            },
            "required_fields": ["host"],
        }
        result = _json_schema_for_param(arg, "object")
        assert result is not None
        assert result["type"] == "object"
        assert "host" in result["properties"]
        assert result["required"] == ["host"]

    def test_json_schema_for_array_arg(self) -> None:
        """Array arg with items should produce json_schema."""
        arg: dict[str, Any] = {
            "type": "array",
            "items": {"type": "integer"},
        }
        result = _json_schema_for_param(arg, "array")
        assert result is not None
        assert result["type"] == "array"
        assert result["items"] == {"type": "integer"}

    def test_json_schema_for_array_without_items(self) -> None:
        """Array arg without items should default to string items."""
        result = _json_schema_for_param({"type": "array"}, "array")
        assert result is not None
        assert result["items"] == {"type": "string"}

    def test_json_schema_for_scalar_returns_none(self) -> None:
        """Scalar types should not produce json_schema."""
        assert _json_schema_for_param({"type": "string"}, "string") is None
        assert _json_schema_for_param({"type": "integer"}, "integer") is None

    def test_json_schema_for_object_without_properties(self) -> None:
        """Object without properties should not produce json_schema."""
        assert _json_schema_for_param({"type": "object"}, "object") is None

    def test_extraction_includes_json_schema_for_complex_args(self) -> None:
        """CLI extraction should propagate json_schema for complex args."""
        spec: dict[str, Any] = {
            "name": "test-tool",
            "description": "A test CLI tool",
            "base_command": "test-cmd",
            "commands": [
                {
                    "name": "deploy",
                    "description": "Deploy resources",
                    "args": [
                        {"name": "target", "type": "string", "required": True},
                        {
                            "name": "config",
                            "type": "object",
                            "properties": {
                                "replicas": {"type": "integer"},
                                "image": {"type": "string"},
                            },
                        },
                        {
                            "name": "tags",
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    ],
                },
            ],
        }
        extractor = CLIExtractor()
        source = SourceConfig(file_content=json.dumps(spec))
        ir = extractor.extract(source)
        assert len(ir.operations) == 1
        by_name = {p.name: p for p in ir.operations[0].params}
        assert by_name["target"].json_schema is None
        assert by_name["config"].json_schema is not None
        assert by_name["config"].json_schema["type"] == "object"
        assert by_name["tags"].json_schema is not None
        assert by_name["tags"].json_schema["type"] == "array"
