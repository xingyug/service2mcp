"""Tests for LLM-driven seed mutation endpoint discovery."""

from __future__ import annotations

import json

from libs.extractors.llm_seed_mutation import (
    _parse_seed_response,
    generate_seed_candidates,
)


class MockSeedLLMClient:
    """Mock LLM client for seed mutation tests."""

    def __init__(self, response: str | None = None, fail: bool = False) -> None:
        self._response = response
        self._fail = fail
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 4096) -> object:
        self.calls.append(prompt)
        if self._fail:
            raise RuntimeError("LLM API error")

        class _Response:
            content = self._response or "[]"

        return _Response()


class TestSeedCandidateParsing:
    def test_parse_valid_json(self) -> None:
        content = json.dumps([
            {
                "path": "/api/users/{id}/profile",
                "methods": ["GET"],
                "rationale": "Common user sub-resource",
                "confidence": 0.8,
            }
        ])
        candidates = _parse_seed_response(content)
        assert len(candidates) == 1
        assert candidates[0].path == "/api/users/{id}/profile"
        assert candidates[0].methods == ("GET",)
        assert candidates[0].confidence == 0.8

    def test_parse_markdown_fenced_json(self) -> None:
        content = "```json\n" + json.dumps([
            {"path": "/api/test", "methods": ["GET"], "rationale": "test", "confidence": 0.7}
        ]) + "\n```"
        candidates = _parse_seed_response(content)
        assert len(candidates) == 1

    def test_parse_empty_response(self) -> None:
        assert _parse_seed_response("[]") == []

    def test_parse_invalid_json(self) -> None:
        assert _parse_seed_response("not json") == []

    def test_parse_non_array(self) -> None:
        assert _parse_seed_response('{"key": "value"}') == []

    def test_parse_missing_path(self) -> None:
        content = json.dumps([{"methods": ["GET"], "confidence": 0.5}])
        candidates = _parse_seed_response(content)
        assert len(candidates) == 0

    def test_default_methods(self) -> None:
        content = json.dumps([{"path": "/api/test", "confidence": 0.6}])
        candidates = _parse_seed_response(content)
        assert len(candidates) == 1
        assert candidates[0].methods == ("GET",)


class TestGenerateSeedCandidates:
    def test_generates_candidates(self) -> None:
        mock_response = json.dumps([
            {
                "path": "/api/users/{id}/profile", "methods": ["GET"],
                "rationale": "profile", "confidence": 0.8,
            },
            {
                "path": "/api/users/{id}/avatar", "methods": ["GET", "PUT"],
                "rationale": "avatar", "confidence": 0.7,
            },
        ])
        client = MockSeedLLMClient(response=mock_response)
        discovered = [{"path": "/api/users", "methods": ["GET", "POST"]}]

        candidates = generate_seed_candidates(
            llm_client=client,
            base_url="https://example.com",
            discovered_paths=discovered,
        )

        assert len(candidates) == 2
        assert candidates[0].confidence >= candidates[1].confidence
        assert len(client.calls) == 1

    def test_filters_already_discovered(self) -> None:
        mock_response = json.dumps([
            {"path": "/api/users", "methods": ["GET"], "rationale": "exists", "confidence": 0.9},
            {"path": "/api/new", "methods": ["GET"], "rationale": "new", "confidence": 0.8},
        ])
        client = MockSeedLLMClient(response=mock_response)
        discovered = [{"path": "/api/users", "methods": ["GET"]}]

        candidates = generate_seed_candidates(
            llm_client=client,
            base_url="https://example.com",
            discovered_paths=discovered,
        )

        assert len(candidates) == 1
        assert candidates[0].path == "/api/new"

    def test_empty_discovered_returns_empty(self) -> None:
        client = MockSeedLLMClient()
        candidates = generate_seed_candidates(
            llm_client=client,
            base_url="https://example.com",
            discovered_paths=[],
        )
        assert candidates == []
        assert len(client.calls) == 0

    def test_llm_failure_returns_empty(self) -> None:
        client = MockSeedLLMClient(fail=True)
        candidates = generate_seed_candidates(
            llm_client=client,
            base_url="https://example.com",
            discovered_paths=[{"path": "/api/test", "methods": ["GET"]}],
        )
        assert candidates == []

    def test_respects_max_candidates(self) -> None:
        many_candidates = [
            {
                "path": f"/api/endpoint_{i}", "methods": ["GET"],
                "rationale": f"test {i}", "confidence": 0.5,
            }
            for i in range(50)
        ]
        client = MockSeedLLMClient(response=json.dumps(many_candidates))
        discovered = [{"path": "/api/base", "methods": ["GET"]}]

        candidates = generate_seed_candidates(
            llm_client=client,
            base_url="https://example.com",
            discovered_paths=discovered,
            max_candidates=10,
        )

        assert len(candidates) == 10
