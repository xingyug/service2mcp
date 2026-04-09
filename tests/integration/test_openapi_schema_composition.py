"""Tests for allOf / oneOf / anyOf composition in OpenAPI schema extraction."""

from __future__ import annotations

import json
from typing import Any

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor


def _make_spec(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal OpenAPI 3.0 spec with one POST endpoint using *schema*."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": schema},
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


def _extract_params(spec: dict[str, Any]) -> list[Any]:
    source = SourceConfig(file_content=json.dumps(spec), hints={"protocol": "openapi"})
    extractor = OpenAPIExtractor()
    ir = extractor.extract(source)
    op = next(o for o in ir.operations if o.id == "createItem")
    return op.params


# ── allOf ──────────────────────────────────────────────────────────────────


class TestAllOf:
    def test_allof_merges_properties(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"name": {"type": "string"}}},
                {"type": "object", "properties": {"age": {"type": "integer"}}},
            ]
        }
        params = _extract_params(_make_spec(schema))
        names = {p.name for p in params}
        assert "name" in names
        assert "age" in names

    def test_allof_with_required_union(self) -> None:
        schema = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "required": ["a"],
                },
                {
                    "type": "object",
                    "properties": {"b": {"type": "integer"}},
                    "required": ["b"],
                },
            ]
        }
        params = _extract_params(_make_spec(schema))
        by_name = {p.name: p for p in params}
        assert by_name["a"].required is True
        assert by_name["b"].required is True

    def test_allof_with_top_level_properties(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"base": {"type": "string"}}},
            ],
            "properties": {"extra": {"type": "boolean"}},
        }
        params = _extract_params(_make_spec(schema))
        names = {p.name for p in params}
        assert "base" in names
        assert "extra" in names


# ── oneOf ──────────────────────────────────────────────────────────────────


class TestOneOf:
    def test_oneof_merges_properties_not_required(self) -> None:
        schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "required": ["x"],
                },
                {
                    "type": "object",
                    "properties": {"y": {"type": "integer"}},
                    "required": ["y"],
                },
            ]
        }
        params = _extract_params(_make_spec(schema))
        names = {p.name for p in params}
        assert "x" in names
        assert "y" in names
        # None should be required — only one branch applies
        for p in params:
            if p.name in ("x", "y"):
                assert p.required is False
                assert p.confidence == pytest.approx(0.8)


# ── anyOf ──────────────────────────────────────────────────────────────────


class TestAnyOf:
    def test_anyof_merges_properties_not_required(self) -> None:
        schema = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {"m": {"type": "string"}},
                    "required": ["m"],
                },
                {
                    "type": "object",
                    "properties": {"n": {"type": "number"}},
                    "required": ["n"],
                },
            ]
        }
        params = _extract_params(_make_spec(schema))
        names = {p.name for p in params}
        assert "m" in names
        assert "n" in names
        for p in params:
            if p.name in ("m", "n"):
                assert p.required is False
                assert p.confidence == pytest.approx(0.8)


# ── Nested composition ─────────────────────────────────────────────────────


class TestNestedComposition:
    def test_nested_composition(self) -> None:
        """allOf containing a sub-schema with oneOf."""
        schema = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
                {
                    "oneOf": [
                        {"type": "object", "properties": {"email": {"type": "string"}}},
                        {"type": "object", "properties": {"phone": {"type": "string"}}},
                    ]
                },
            ]
        }
        params = _extract_params(_make_spec(schema))
        names = {p.name for p in params}
        # id comes from the allOf base — required
        assert "id" in names
        id_param = next(p for p in params if p.name == "id")
        assert id_param.required is True
        # email/phone come from oneOf — not required, lower confidence
        assert "email" in names or "phone" in names


# ── Full extraction round-trip ─────────────────────────────────────────────


class TestFullExtractionWithAllOf:
    def test_full_extraction_with_allof_request_body(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Composed", "version": "2.0"},
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "allOf": [
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "username": {"type": "string"},
                                                },
                                                "required": ["username"],
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "bio": {"type": "string"},
                                                    "avatar_url": {"type": "string"},
                                                },
                                            },
                                        ]
                                    }
                                }
                            }
                        },
                        "responses": {"201": {"description": "Created"}},
                    }
                }
            },
        }
        source = SourceConfig(file_content=json.dumps(spec), hints={"protocol": "openapi"})
        extractor = OpenAPIExtractor()
        ir = extractor.extract(source)

        op = next(o for o in ir.operations if o.id == "createUser")
        by_name = {p.name: p for p in op.params}

        assert "username" in by_name
        assert by_name["username"].required is True
        assert "bio" in by_name
        assert "avatar_url" in by_name
        assert by_name["bio"].required is False
        assert by_name["avatar_url"].required is False
