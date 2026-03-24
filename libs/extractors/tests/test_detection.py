"""Tests for ExtractorProtocol, SourceConfig, and TypeDetector."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from libs.ir.models import ServiceIR, RiskLevel, RiskMetadata, Operation
from libs.extractors.base import ExtractorProtocol, SourceConfig, TypeDetector


# ── Mock Extractors ────────────────────────────────────────────────────────

@dataclass
class MockExtractor:
    protocol_name: str
    _confidence: float = 0.0
    _should_fail: bool = False

    def detect(self, source: SourceConfig) -> float:
        if self._should_fail:
            raise RuntimeError("Detection failed")
        return self._confidence

    def extract(self, source: SourceConfig) -> ServiceIR:
        return ServiceIR(
            source_hash="mock",
            protocol=self.protocol_name,
            service_name="mock",
            base_url="http://mock",
        )


# ── SourceConfig Tests ─────────────────────────────────────────────────────

class TestSourceConfig:
    def test_url_source(self):
        s = SourceConfig(url="https://example.com/api")
        assert s.url == "https://example.com/api"

    def test_file_source(self):
        s = SourceConfig(file_path="/tmp/spec.yaml")
        assert s.file_path == "/tmp/spec.yaml"

    def test_content_source(self):
        s = SourceConfig(file_content='{"openapi": "3.0.0"}')
        assert s.file_content is not None

    def test_empty_source_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            SourceConfig()

    def test_hints(self):
        s = SourceConfig(url="https://example.com", hints={"protocol": "graphql"})
        assert s.hints["protocol"] == "graphql"


# ── TypeDetector Tests ─────────────────────────────────────────────────────

class TestTypeDetector:
    def test_selects_highest_confidence(self):
        detector = TypeDetector([
            MockExtractor("openapi", _confidence=0.9),
            MockExtractor("graphql", _confidence=0.3),
            MockExtractor("rest", _confidence=0.6),
        ])
        result = detector.detect(SourceConfig(url="https://example.com/api"))
        assert result.protocol_name == "openapi"
        assert result.confidence == 0.9

    def test_no_extractors_raises(self):
        detector = TypeDetector([])
        with pytest.raises(ValueError, match="No extractors registered"):
            detector.detect(SourceConfig(url="https://example.com"))

    def test_all_zero_confidence_raises(self):
        detector = TypeDetector([
            MockExtractor("openapi", _confidence=0.0),
            MockExtractor("graphql", _confidence=0.0),
        ])
        with pytest.raises(ValueError, match="No extractor could handle"):
            detector.detect(SourceConfig(url="https://example.com"))

    def test_failing_extractor_skipped(self):
        detector = TypeDetector([
            MockExtractor("broken", _should_fail=True),
            MockExtractor("openapi", _confidence=0.8),
        ])
        result = detector.detect(SourceConfig(url="https://example.com"))
        assert result.protocol_name == "openapi"

    def test_register(self):
        detector = TypeDetector()
        detector.register(MockExtractor("openapi", _confidence=0.9))
        result = detector.detect(SourceConfig(url="https://example.com"))
        assert result.protocol_name == "openapi"

    def test_detect_all_sorted(self):
        detector = TypeDetector([
            MockExtractor("openapi", _confidence=0.9),
            MockExtractor("graphql", _confidence=0.3),
            MockExtractor("rest", _confidence=0.6),
            MockExtractor("sql", _confidence=0.0),
        ])
        results = detector.detect_all(SourceConfig(url="https://example.com"))
        assert len(results) == 3  # sql excluded (0.0)
        assert results[0].protocol_name == "openapi"
        assert results[1].protocol_name == "rest"
        assert results[2].protocol_name == "graphql"

    def test_confidence_clamped(self):
        detector = TypeDetector([MockExtractor("openapi", _confidence=1.5)])
        result = detector.detect(SourceConfig(url="https://example.com"))
        assert result.confidence == 1.0


class TestExtractorProtocol:
    def test_mock_implements_protocol(self):
        ext = MockExtractor("openapi")
        assert isinstance(ext, ExtractorProtocol)
