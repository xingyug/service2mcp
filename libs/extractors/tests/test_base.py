"""Unit tests for libs/extractors/base.py — SourceConfig + TypeDetector."""

from __future__ import annotations

import pytest

from libs.extractors.base import DetectionResult, SourceConfig, TypeDetector
from libs.ir.models import ServiceIR


class FakeExtractor:
    """Minimal extractor implementing ExtractorProtocol."""

    def __init__(self, name: str, confidence: float) -> None:
        self.protocol_name = name
        self._confidence = confidence

    def detect(self, source: SourceConfig) -> float:
        return self._confidence

    def extract(self, source: SourceConfig) -> ServiceIR:
        raise NotImplementedError


class FailingExtractor:
    """Extractor whose detect() raises."""

    protocol_name = "failing"

    def detect(self, source: SourceConfig) -> float:
        raise RuntimeError("detection boom")

    def extract(self, source: SourceConfig) -> ServiceIR:
        raise NotImplementedError


# --- SourceConfig ---


class TestSourceConfig:
    def test_url_only(self) -> None:
        sc = SourceConfig(url="https://example.com/api.yaml")
        assert sc.url == "https://example.com/api.yaml"

    def test_file_path_only(self) -> None:
        sc = SourceConfig(file_path="/tmp/spec.json")
        assert sc.file_path == "/tmp/spec.json"

    def test_file_content_only(self) -> None:
        sc = SourceConfig(file_content='{"openapi": "3.0.0"}')
        assert sc.file_content is not None

    def test_no_source_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            SourceConfig()

    def test_hints_default_empty(self) -> None:
        sc = SourceConfig(url="https://example.com")
        assert sc.hints == {}

    def test_auth_fields(self) -> None:
        sc = SourceConfig(
            url="https://example.com",
            auth_header="Authorization",
            auth_token="Bearer abc",
        )
        assert sc.auth_header == "Authorization"
        assert sc.auth_token == "Bearer abc"


# --- DetectionResult ---


class TestDetectionResult:
    def test_attributes(self) -> None:
        ext = FakeExtractor("openapi", 0.9)
        dr = DetectionResult(extractor=ext, confidence=0.9, protocol_name="openapi")
        assert dr.confidence == 0.9
        assert dr.protocol_name == "openapi"
        assert dr.extractor is ext


# --- TypeDetector ---


class TestTypeDetectorDetect:
    def test_selects_highest_confidence(self) -> None:
        td = TypeDetector(
            [
                FakeExtractor("a", 0.3),
                FakeExtractor("b", 0.9),
                FakeExtractor("c", 0.5),
            ]
        )
        result = td.detect(SourceConfig(url="https://example.com"))
        assert result.protocol_name == "b"
        assert result.confidence == 0.9

    def test_no_extractors_raises(self) -> None:
        td = TypeDetector([])
        with pytest.raises(ValueError, match="No extractors registered"):
            td.detect(SourceConfig(url="https://example.com"))

    def test_all_zero_confidence_raises(self) -> None:
        td = TypeDetector([FakeExtractor("a", 0.0)])
        with pytest.raises(ValueError, match="No extractor could handle"):
            td.detect(SourceConfig(url="https://example.com"))

    def test_clamps_confidence_above_one(self) -> None:
        td = TypeDetector([FakeExtractor("a", 1.5)])
        result = td.detect(SourceConfig(url="https://example.com"))
        assert result.confidence == 1.0

    def test_clamps_negative_confidence(self) -> None:
        td = TypeDetector([FakeExtractor("a", -0.5), FakeExtractor("b", 0.5)])
        result = td.detect(SourceConfig(url="https://example.com"))
        assert result.protocol_name == "b"

    def test_failing_extractor_skipped(self) -> None:
        td = TypeDetector([FailingExtractor(), FakeExtractor("b", 0.7)])
        result = td.detect(SourceConfig(url="https://example.com"))
        assert result.protocol_name == "b"

    def test_all_failing_raises(self) -> None:
        td = TypeDetector([FailingExtractor()])
        with pytest.raises(ValueError, match="No extractor could handle"):
            td.detect(SourceConfig(url="https://example.com"))

    def test_register_adds_extractor(self) -> None:
        td = TypeDetector()
        td.register(FakeExtractor("added", 0.8))
        result = td.detect(SourceConfig(url="https://example.com"))
        assert result.protocol_name == "added"


class TestTypeDetectorDetectAll:
    def test_returns_sorted_by_confidence(self) -> None:
        td = TypeDetector(
            [
                FakeExtractor("a", 0.3),
                FakeExtractor("b", 0.9),
                FakeExtractor("c", 0.5),
            ]
        )
        results = td.detect_all(SourceConfig(url="https://example.com"))
        assert [r.protocol_name for r in results] == ["b", "c", "a"]

    def test_excludes_zero_confidence(self) -> None:
        td = TypeDetector(
            [
                FakeExtractor("a", 0.0),
                FakeExtractor("b", 0.5),
            ]
        )
        results = td.detect_all(SourceConfig(url="https://example.com"))
        assert len(results) == 1
        assert results[0].protocol_name == "b"

    def test_empty_when_none_match(self) -> None:
        td = TypeDetector([FakeExtractor("a", 0.0)])
        results = td.detect_all(SourceConfig(url="https://example.com"))
        assert results == []

    def test_failing_extractor_skipped(self) -> None:
        td = TypeDetector([FailingExtractor(), FakeExtractor("b", 0.5)])
        results = td.detect_all(SourceConfig(url="https://example.com"))
        assert len(results) == 1
