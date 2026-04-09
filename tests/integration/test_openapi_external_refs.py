"""Tests for external $ref resolution in the OpenAPI extractor."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import yaml

from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor


def _make_spec_with_ref(ref: str) -> dict[str, Any]:
    """Build a minimal OpenAPI 3.0 spec whose request body schema is an external $ref."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": ref},
                            },
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


def _extract_params(spec_path: str | Path) -> list[Any]:
    source = SourceConfig(file_path=str(spec_path), hints={"protocol": "openapi"})
    extractor = OpenAPIExtractor()
    ir = extractor.extract(source)
    op = next(o for o in ir.operations if o.id == "createPet")
    return op.params


class TestExternalRefFileResolved:
    """External $ref pointing to a local file → schema properties extracted."""

    def test_external_ref_file_resolved(self, tmp_path: Path) -> None:
        # Write the external schema file
        models_file = tmp_path / "models.yaml"
        models_file.write_text(
            yaml.dump(
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name"],
                }
            )
        )

        # Write the main spec referencing the external file
        spec = _make_spec_with_ref("models.yaml")
        main_file = tmp_path / "openapi.yaml"
        main_file.write_text(yaml.dump(spec))

        params = _extract_params(main_file)
        names = {p.name for p in params}
        assert "name" in names
        assert "age" in names


class TestExternalRefWithPointer:
    """External $ref with JSON pointer (e.g., models.yaml#/components/schemas/Pet)."""

    def test_external_ref_with_pointer(self, tmp_path: Path) -> None:
        # Write external file containing nested schemas
        models_file = tmp_path / "models.yaml"
        models_file.write_text(
            yaml.dump(
                {
                    "components": {
                        "schemas": {
                            "Pet": {
                                "type": "object",
                                "properties": {
                                    "species": {"type": "string"},
                                    "weight": {"type": "number"},
                                },
                            }
                        }
                    }
                }
            )
        )

        spec = _make_spec_with_ref("models.yaml#/components/schemas/Pet")
        main_file = tmp_path / "openapi.yaml"
        main_file.write_text(yaml.dump(spec))

        params = _extract_params(main_file)
        names = {p.name for p in params}
        assert "species" in names
        assert "weight" in names


class TestExternalRefCached:
    """Same external ref used twice → document only fetched once."""

    def test_external_ref_cached(self, tmp_path: Path) -> None:
        models_file = tmp_path / "models.yaml"
        models_file.write_text(
            yaml.dump(
                {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                    },
                }
            )
        )

        # Spec with two endpoints referencing the same external file
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/pets": {
                    "post": {
                        "operationId": "createPet",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "models.yaml"},
                                },
                            },
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/pets/{id}": {
                    "put": {
                        "operationId": "updatePet",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "models.yaml"},
                                },
                            },
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

        main_file = tmp_path / "openapi.yaml"
        main_file.write_text(yaml.dump(spec))

        extractor = OpenAPIExtractor()
        source = SourceConfig(file_path=str(main_file), hints={"protocol": "openapi"})

        with patch.object(
            extractor,
            "_fetch_external_document",
            wraps=extractor._fetch_external_document,
        ) as mock_fetch:
            extractor.extract(source)
            # The same ref appears twice — should only fetch once
            assert mock_fetch.call_count == 1


class TestExternalRefNotFoundGraceful:
    """External ref to non-existent file → returns empty, no crash."""

    def test_external_ref_not_found_graceful(self, tmp_path: Path) -> None:
        spec = _make_spec_with_ref("nonexistent.yaml")
        main_file = tmp_path / "openapi.yaml"
        main_file.write_text(yaml.dump(spec))

        # Should not raise — graceful degradation
        source = SourceConfig(file_path=str(main_file), hints={"protocol": "openapi"})
        extractor = OpenAPIExtractor()
        ir = extractor.extract(source)
        op = next(o for o in ir.operations if o.id == "createPet")
        # Params list may be empty since the ref couldn't be resolved
        assert isinstance(op.params, list)


class TestExternalRefCycleDetection:
    """File A refs File B which refs File A → no infinite loop."""

    def test_external_ref_cycle_detection(self, tmp_path: Path) -> None:
        # File A references file B
        spec_a = _make_spec_with_ref("b.yaml")
        file_a = tmp_path / "a.yaml"
        file_a.write_text(yaml.dump(spec_a))

        # File B references back to file A (cycle)
        schema_b = {
            "type": "object",
            "properties": {
                "nested": {"$ref": "a.yaml"},
            },
        }
        file_b = tmp_path / "b.yaml"
        file_b.write_text(yaml.dump(schema_b))

        # Should not hang — cycle detection breaks the loop
        source = SourceConfig(file_path=str(file_a), hints={"protocol": "openapi"})
        extractor = OpenAPIExtractor()
        ir = extractor.extract(source)
        assert ir is not None


class TestRelativePathResolution:
    """Relative path like ./models/pet.yaml resolved correctly relative to source."""

    def test_relative_path_resolution(self, tmp_path: Path) -> None:
        # Create subdirectory with model file
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        pet_file = models_dir / "pet.yaml"
        pet_file.write_text(
            yaml.dump(
                {
                    "type": "object",
                    "properties": {
                        "breed": {"type": "string"},
                    },
                }
            )
        )

        spec = _make_spec_with_ref("./models/pet.yaml")
        main_file = tmp_path / "openapi.yaml"
        main_file.write_text(yaml.dump(spec))

        params = _extract_params(main_file)
        names = {p.name for p in params}
        assert "breed" in names

    def test_url_based_external_ref(self) -> None:
        """External ref resolution when source is a URL."""
        external_schema = yaml.dump(
            {
                "type": "object",
                "properties": {
                    "color": {"type": "string"},
                },
            }
        )

        spec_content = yaml.dump(_make_spec_with_ref("schemas/pet.yaml"))

        # Mock both the main spec fetch and external schema fetch
        main_request = httpx.Request("GET", "https://api.example.com/v1/openapi.yaml")
        ext_request = httpx.Request("GET", "https://api.example.com/v1/schemas/pet.yaml")

        main_resp = httpx.Response(200, text=spec_content, request=main_request)
        ext_resp = httpx.Response(200, text=external_schema, request=ext_request)

        def mock_get(url: str, **kwargs: Any) -> httpx.Response:
            if "schemas/pet" in str(url):
                return ext_resp
            return main_resp

        with patch("libs.extractors.utils.httpx.get", side_effect=mock_get):
            with patch("libs.extractors.openapi.httpx.get", side_effect=mock_get):
                source = SourceConfig(
                    url="https://api.example.com/v1/openapi.yaml",
                    hints={"protocol": "openapi"},
                )
                extractor = OpenAPIExtractor()
                ir = extractor.extract(source)

        op = next(o for o in ir.operations if o.id == "createPet")
        names = {p.name for p in op.params}
        assert "color" in names
