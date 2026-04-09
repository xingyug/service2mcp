"""Integration tests for AsyncAPI compilation pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.extractors.asyncapi import AsyncAPIExtractor
from libs.extractors.base import SourceConfig, TypeDetector
from libs.ir.models import EventTransport, ServiceIR
from libs.validator import PreDeployValidator

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "asyncapi_specs"


@pytest.fixture()
def extractor() -> AsyncAPIExtractor:
    return AsyncAPIExtractor()


@pytest.fixture()
def v2_source() -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / "simple_v2.yaml"))


@pytest.fixture()
def v3_source() -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / "simple_v3.yaml"))


class TestAsyncAPICompilation:
    def test_asyncapi_detect_and_extract_v2(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        confidence = extractor.detect(v2_source)
        assert confidence >= 0.88

        ir = extractor.extract(v2_source)
        assert isinstance(ir, ServiceIR)
        assert ir.protocol == "asyncapi"
        assert ir.service_name == "user-events"
        assert len(ir.operations) >= 3
        assert len(ir.event_descriptors) == 2
        for ed in ir.event_descriptors:
            assert ed.transport == EventTransport.kafka

    def test_asyncapi_detect_and_extract_v3(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        confidence = extractor.detect(v3_source)
        assert confidence >= 0.88

        ir = extractor.extract(v3_source)
        assert isinstance(ir, ServiceIR)
        assert ir.protocol == "asyncapi"
        assert ir.service_name == "order-events"
        assert len(ir.operations) == 3
        assert len(ir.event_descriptors) == 2
        for ed in ir.event_descriptors:
            assert ed.transport == EventTransport.amqp

    @pytest.mark.asyncio()
    async def test_asyncapi_ir_validates(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        validator = PreDeployValidator()
        report = await validator.validate(ir)
        schema_result = report.get_result("schema")
        assert schema_result.passed, f"Schema validation failed: {schema_result.details}"

    def test_asyncapi_type_detection(self, v2_source: SourceConfig) -> None:
        detector = TypeDetector()
        detector.register(AsyncAPIExtractor())
        result = detector.detect(v2_source)
        assert result.protocol_name == "asyncapi"
        assert result.confidence >= 0.88

    def test_asyncapi_type_detection_v3(self, v3_source: SourceConfig) -> None:
        detector = TypeDetector()
        detector.register(AsyncAPIExtractor())
        result = detector.detect(v3_source)
        assert result.protocol_name == "asyncapi"
        assert result.confidence >= 0.88

    def test_asyncapi_metadata(self, extractor: AsyncAPIExtractor, v2_source: SourceConfig) -> None:
        ir = extractor.extract(v2_source)
        assert ir.metadata["asyncapi_version"] == "2.6.0"
        assert ir.metadata["broker_protocol"] == "kafka"
        assert ir.metadata["channel_count"] == 2
        assert ir.metadata["operation_count"] == 3
