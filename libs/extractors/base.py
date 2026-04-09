"""Base protocol for all extractors and the TypeDetector that selects the right one."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from libs.ir.models import ServiceIR

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """Configuration for an extraction source."""

    url: str | None = None
    file_path: str | None = None
    file_content: str | None = None
    auth_header: str | None = None
    auth_token: str | None = None
    hints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url and not self.file_path and not self.file_content:
            raise ValueError("SourceConfig requires at least one of: url, file_path, file_content")


@runtime_checkable
class ExtractorProtocol(Protocol):
    """Interface that all protocol extractors must implement."""

    protocol_name: str

    def detect(self, source: SourceConfig) -> float:
        """Return confidence 0.0-1.0 that this extractor handles the source."""
        ...

    def extract(self, source: SourceConfig) -> ServiceIR:
        """Extract API schema and produce a raw IR."""
        ...


@dataclass
class DetectionResult:
    """Result of type detection — the chosen extractor and its confidence."""

    extractor: ExtractorProtocol
    confidence: float
    protocol_name: str


class TypeDetector:
    """Probes a source and selects the highest-confidence extractor."""

    def __init__(self, extractors: list[ExtractorProtocol] | None = None) -> None:
        self._extractors: list[ExtractorProtocol] = extractors or []

    def register(self, extractor: ExtractorProtocol) -> None:
        self._extractors.append(extractor)

    def detect(self, source: SourceConfig) -> DetectionResult:
        """Run all extractors' detect methods and return the highest confidence match.

        Raises ValueError if no extractor has confidence > 0.
        """
        if not self._extractors:
            raise ValueError("No extractors registered")

        results: list[DetectionResult] = []
        for ext in self._extractors:
            try:
                confidence = ext.detect(source)
                confidence = max(0.0, min(1.0, confidence))  # clamp
                if confidence > 0:
                    results.append(
                        DetectionResult(
                            extractor=ext,
                            confidence=confidence,
                            protocol_name=ext.protocol_name,
                        )
                    )
            except Exception:  # broad-except: polymorphic extractor dispatch
                logger.warning("Extractor %s.detect() failed", ext.protocol_name, exc_info=True)

        if not results:
            raise ValueError(
                f"No extractor could handle the source (tried: "
                f"{[e.protocol_name for e in self._extractors]})"
            )

        results.sort(key=lambda r: r.confidence, reverse=True)
        best = results[0]
        logger.info(
            "Type detection: selected %s (confidence=%.2f) from %d candidates",
            best.protocol_name,
            best.confidence,
            len(results),
        )
        return best

    def detect_all(self, source: SourceConfig) -> list[DetectionResult]:
        """Return all extractors with confidence > 0, sorted by confidence descending."""
        results: list[DetectionResult] = []
        for ext in self._extractors:
            try:
                confidence = ext.detect(source)
                if confidence > 0:
                    results.append(
                        DetectionResult(
                            extractor=ext,
                            confidence=max(0.0, min(1.0, confidence)),
                            protocol_name=ext.protocol_name,
                        )
                    )
            except Exception:  # broad-except: polymorphic extractor dispatch
                logger.warning("Extractor %s.detect() failed", ext.protocol_name, exc_info=True)
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results
