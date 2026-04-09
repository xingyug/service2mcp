"""Tests for generic collection discovery strategies in rest_probing."""

from __future__ import annotations

from typing import Any

import pytest

from libs.extractors.rest_probing import (
    _apply_collection_strategy,
    _match_collection_strategy,
)

# ---- Fixtures ---------------------------------------------------------------


@pytest.fixture
def directus_payload() -> dict[str, Any]:
    return {
        "data": [
            {"collection": "posts", "meta": {}},
            {"collection": "authors", "meta": {}},
            {"collection": "directus_users", "meta": {}},
        ]
    }


@pytest.fixture
def pocketbase_payload() -> dict[str, Any]:
    return {
        "items": [
            {"name": "tasks", "type": "base"},
            {"name": "users", "type": "view"},
            {"name": "_superusers", "type": "base"},
            {"name": "logs", "type": "auth"},
        ]
    }


# ---- Strategy matching tests ------------------------------------------------


class TestMatchCollectionStrategy:
    def test_directus_pattern_detected(self) -> None:
        url = "http://localhost:8055/collections"
        strategy = _match_collection_strategy(url)
        assert strategy is not None
        assert strategy.path_suffix == "/collections"

    def test_pocketbase_pattern_detected(self) -> None:
        url = "http://localhost:8090/api/collections"
        strategy = _match_collection_strategy(url)
        assert strategy is not None
        assert strategy.path_suffix == "/api/collections"

    def test_unknown_pattern_returns_none(self) -> None:
        url = "http://localhost:9999/api/items"
        strategy = _match_collection_strategy(url)
        assert strategy is None


# ---- Strategy application tests ---------------------------------------------


class TestApplyCollectionStrategy:
    def test_generic_strategy_filters_excludes(self, directus_payload: dict[str, Any]) -> None:
        url = "http://localhost:8055/collections"
        strategy = _match_collection_strategy(url)
        assert strategy is not None
        candidates = _apply_collection_strategy(strategy, url, directus_payload)
        names = [c.collection_url for c in candidates]
        assert not any("directus_users" in n for n in names)
        assert any("posts" in n for n in names)
        assert any("authors" in n for n in names)

    def test_generic_strategy_respects_type_filter(
        self, pocketbase_payload: dict[str, Any]
    ) -> None:
        url = "http://localhost:8090/api/collections"
        strategy = _match_collection_strategy(url)
        assert strategy is not None
        candidates = _apply_collection_strategy(strategy, url, pocketbase_payload)
        names = [c.collection_url for c in candidates]
        # "tasks" (base) and "users" (view) should be included
        assert any("tasks" in n for n in names)
        assert any("users" in n for n in names)
        # "_superusers" excluded by prefix, "logs" excluded by type "auth"
        assert not any("_superusers" in n for n in names)
        assert not any("logs" in n for n in names)

    def test_strategy_constructs_correct_urls_directus(
        self, directus_payload: dict[str, Any]
    ) -> None:
        url = "http://localhost:8055/collections"
        strategy = _match_collection_strategy(url)
        assert strategy is not None
        candidates = _apply_collection_strategy(strategy, url, directus_payload)
        urls = {c.collection_url for c in candidates}
        assert "http://localhost:8055/items/posts" in urls
        assert "http://localhost:8055/items/authors" in urls

    def test_strategy_constructs_correct_urls_pocketbase(
        self, pocketbase_payload: dict[str, Any]
    ) -> None:
        url = "http://localhost:8090/api/collections"
        strategy = _match_collection_strategy(url)
        assert strategy is not None
        candidates = _apply_collection_strategy(strategy, url, pocketbase_payload)
        urls = {c.collection_url for c in candidates}
        assert "http://localhost:8090/api/collections/tasks/records" in urls
        assert "http://localhost:8090/api/collections/users/records" in urls
