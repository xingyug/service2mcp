"""Integration tests for CLI compilation flow."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from libs.extractors.base import SourceConfig, TypeDetector
from libs.extractors.cli import CLIExtractor
from libs.ir.models import SourceType

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "cli_specs"


@pytest.fixture
def simple_source() -> SourceConfig:
    path = FIXTURES_DIR / "simple_tool.cli.yaml"
    return SourceConfig(file_path=str(path))


@pytest.fixture
def complex_source() -> SourceConfig:
    path = FIXTURES_DIR / "complex_tool.cli.yaml"
    return SourceConfig(file_path=str(path))


class TestCLIDetectAndExtract:
    """Full detect → extract flow."""

    def test_cli_detect_and_extract(self, simple_source: SourceConfig) -> None:
        extractor = CLIExtractor()
        confidence = extractor.detect(simple_source)
        assert confidence == 0.90  # .cli.yaml extension

        ir = extractor.extract(simple_source)
        assert ir.protocol == "cli"
        assert ir.service_name == "simple-tool"
        assert len(ir.operations) == 3

        # Verify operations have expected structure
        op_names = {op.name for op in ir.operations}
        assert op_names == {"list-items", "create-item", "delete-item"}

        for op in ir.operations:
            assert op.source == SourceType.extractor
            assert op.cli is not None
            assert op.risk is not None

    def test_cli_detect_extract_complex(self, complex_source: SourceConfig) -> None:
        extractor = CLIExtractor()
        ir = extractor.extract(complex_source)
        assert ir.service_name == "kubectl-proxy"
        assert len(ir.operations) == 3
        assert ir.metadata["base_command"] == "kubectl"

        get_pods = next(op for op in ir.operations if op.name == "get-pods")
        assert get_pods.cli is not None
        assert get_pods.cli.subcommands == ["get", "pods"]
        assert get_pods.cli.output_format == "json"


class TestCLIIRValidation:
    """Extracted IR passes Pydantic model validation."""

    def test_cli_ir_validates(self, simple_source: SourceConfig) -> None:
        extractor = CLIExtractor()
        ir = extractor.extract(simple_source)

        # ServiceIR model_validate round-trip: if it doesn't raise, IR is valid
        from libs.ir.models import ServiceIR

        roundtripped = ServiceIR.model_validate(ir.model_dump())
        assert roundtripped.service_name == ir.service_name
        assert len(roundtripped.operations) == len(ir.operations)

    def test_cli_ir_operation_ids_unique(self, simple_source: SourceConfig) -> None:
        extractor = CLIExtractor()
        ir = extractor.extract(simple_source)
        ids = [op.id for op in ir.operations]
        assert len(ids) == len(set(ids))

    def test_cli_ir_metadata_present(self, simple_source: SourceConfig) -> None:
        extractor = CLIExtractor()
        ir = extractor.extract(simple_source)
        assert "base_command" in ir.metadata
        assert "service_version" in ir.metadata
        assert "command_count" in ir.metadata
        assert ir.metadata["command_count"] == 3


class TestCLITypeDetection:
    """CLI extractor wins over others for .cli.yaml files."""

    def test_cli_type_detection_with_other_extractors(self) -> None:
        # Create a fake competing extractor
        class FakeOpenAPIExtractor:
            protocol_name = "openapi"

            def detect(self, source: SourceConfig) -> float:
                return 0.1  # low confidence for CLI file

            def extract(self, source: SourceConfig) -> NoReturn:
                raise NotImplementedError

        detector = TypeDetector(extractors=[FakeOpenAPIExtractor(), CLIExtractor()])
        source = SourceConfig(
            file_path=str(FIXTURES_DIR / "simple_tool.cli.yaml"),
            file_content=(FIXTURES_DIR / "simple_tool.cli.yaml").read_text(),
        )
        result = detector.detect(source)
        assert result.protocol_name == "cli"
        assert result.confidence >= 0.85

    def test_cli_not_selected_for_openapi_content(self) -> None:
        class FakeOpenAPIExtractor:
            protocol_name = "openapi"

            def detect(self, source: SourceConfig) -> float:
                return 0.9

            def extract(self, source: SourceConfig) -> NoReturn:
                raise NotImplementedError

        openapi_content = '{"openapi": "3.0.0", "info": {"title": "API"}, "paths": {}}'
        detector = TypeDetector(extractors=[FakeOpenAPIExtractor(), CLIExtractor()])
        source = SourceConfig(file_content=openapi_content)
        result = detector.detect(source)
        assert result.protocol_name == "openapi"
