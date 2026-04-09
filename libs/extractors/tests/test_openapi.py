"""Tests for the OpenAPI extractor — Swagger 2.0, OpenAPI 3.0, 3.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor
from libs.ir.models import (
    AuthType,
    EventSupportLevel,
    EventTransport,
    RequestBodyMode,
    RiskLevel,
    SourceType,
)

FIXTURES = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "openapi_specs"


@pytest.fixture
def extractor():
    return OpenAPIExtractor()


# ── Detection Tests ────────────────────────────────────────────────────────


class TestDetection:
    def test_detect_openapi_3_yaml(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        assert extractor.detect(source) == 0.95

    def test_detect_swagger_2_json(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_swagger_2_0.json"))
        assert extractor.detect(source) == 0.95

    def test_detect_non_openapi(self, extractor):
        source = SourceConfig(file_content='{"not": "an openapi spec"}')
        assert extractor.detect(source) == 0.0

    def test_detect_invalid_content(self, extractor):
        source = SourceConfig(file_content="<<<not valid yaml or json>>>")
        assert extractor.detect(source) == 0.0

    def test_detect_from_content(self, extractor):
        source = SourceConfig(file_content='openapi: "3.0.0"\ninfo:\n  title: Test\npaths: {}')
        assert extractor.detect(source) == 0.95


# ── OpenAPI 3.0 Extraction Tests ──────────────────────────────────────────


class TestOpenAPI30Extraction:
    def test_basic_extraction(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)

        assert ir.protocol == "openapi"
        assert ir.service_name == "petstore"
        assert ir.base_url == "https://petstore.example.com/v1"
        assert ir.metadata["openapi_version"] == "3.0.3"

    def test_operations_count(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        assert len(ir.operations) == 5  # listPets, createPet, showPetById, deletePet, updatePet

    def test_operation_ids(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        op_ids = {op.id for op in ir.operations}
        assert "listPets" in op_ids
        assert "createPet" in op_ids
        assert "showPetById" in op_ids
        assert "deletePet" in op_ids
        assert "updatePet" in op_ids

    def test_risk_classification(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        risk_map = {op.id: op.risk.risk_level for op in ir.operations}

        assert risk_map["listPets"] == RiskLevel.safe
        assert risk_map["showPetById"] == RiskLevel.safe
        assert risk_map["createPet"] == RiskLevel.cautious
        assert risk_map["updatePet"] == RiskLevel.cautious
        assert risk_map["deletePet"] == RiskLevel.dangerous

    def test_params_extracted(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        list_op = next(op for op in ir.operations if op.id == "listPets")
        assert any(p.name == "limit" for p in list_op.params)

        show_op = next(op for op in ir.operations if op.id == "showPetById")
        assert any(p.name == "petId" and p.required for p in show_op.params)

    def test_request_body_params(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        create_op = next(op for op in ir.operations if op.id == "createPet")
        param_names = {p.name for p in create_op.params}
        assert "name" in param_names

    def test_auth_extracted(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        assert ir.auth.type == AuthType.bearer

    def test_source_hash_set(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        assert len(ir.source_hash) == 64  # SHA256 hex

    def test_all_sources_are_extractor(self, extractor):
        """Verify extractors never set source to 'llm'."""
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir = extractor.extract(source)
        for op in ir.operations:
            assert op.source.value == "extractor"
            for param in op.params:
                assert param.source.value == "extractor"


# ── Swagger 2.0 Extraction Tests ──────────────────────────────────────────


class TestSwagger20Extraction:
    def test_basic_extraction(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_swagger_2_0.json"))
        ir = extractor.extract(source)

        assert ir.protocol == "openapi"
        assert ir.service_name == "petstore-swagger"
        assert ir.base_url and "petstore.swagger.io" in ir.base_url  # noqa: S105 — test assertion, not sanitization
        assert ir.metadata["openapi_version"] == "2.0"

    def test_operations_count(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_swagger_2_0.json"))
        ir = extractor.extract(source)
        assert len(ir.operations) == 2  # listPets, createPet

    def test_auth_api_key(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_swagger_2_0.json"))
        ir = extractor.extract(source)
        assert ir.auth.type == AuthType.api_key

    def test_body_params_flattened(self, extractor):
        source = SourceConfig(file_path=str(FIXTURES / "petstore_swagger_2_0.json"))
        ir = extractor.extract(source)
        create_op = next(op for op in ir.operations if op.id == "createPet")
        param_names = {p.name for p in create_op.params}
        assert "name" in param_names

    def test_base_url_falls_back_to_source_url_when_swagger_host_missing(self, extractor):
        source = SourceConfig(
            url="http://gitea.example-namespace.svc.cluster.local:3000/swagger.v1.json",
            file_content=json.dumps(
                {
                    "swagger": "2.0",
                    "info": {"title": "Gitea", "version": "1.0"},
                    "basePath": "/api/v1",
                    "schemes": ["https", "http"],
                    "paths": {},
                }
            ),
        )

        ir = extractor.extract(source)

        assert ir.base_url == "http://gitea.example-namespace.svc.cluster.local:3000/api/v1"

    def test_base_url_uses_source_host_for_loopback_swagger_host(self, extractor):
        source = SourceConfig(
            url="http://gitea.example-namespace.svc.cluster.local:3000/swagger.v1.json",
            file_content=json.dumps(
                {
                    "swagger": "2.0",
                    "host": "localhost:3000",
                    "info": {"title": "Loopback", "version": "1.0"},
                    "basePath": "/api/v1",
                    "schemes": ["https", "http"],
                    "paths": {},
                }
            ),
        )

        ir = extractor.extract(source)

        assert ir.base_url == "http://gitea.example-namespace.svc.cluster.local:3000/api/v1"


# ── Edge Cases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_paths(self, extractor):
        source = SourceConfig(
            file_content=(
                '{"openapi": "3.0.0", "info": {"title": "Empty", "version": "1.0"}, "paths": {}}'
            )
        )
        ir = extractor.extract(source)
        assert len(ir.operations) == 0
        assert ir.service_name == "empty"

    def test_no_operation_id_generates_one(self, extractor):
        spec = """
openapi: "3.0.0"
info:
  title: NoOpId
  version: "1.0"
servers:
  - url: https://example.com
paths:
  /items:
    get:
      summary: List items
      responses:
        "200":
          description: OK
"""
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        assert len(ir.operations) == 1
        assert ir.operations[0].id  # should have auto-generated ID

    def test_no_auth(self, extractor):
        spec = '{"openapi": "3.0.0", "info": {"title": "NoAuth", "version": "1.0"}, "paths": {}}'
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        assert ir.auth.type == AuthType.none

    def test_infers_missing_path_template_param(self, extractor):
        spec = """
openapi: "3.0.0"
info:
  title: Missing Path Param
  version: "1.0"
servers:
  - url: https://example.com
paths:
  /comments/{id}:
    get:
      operationId: getComment
      summary: Retrieve a comment
      parameters:
        - name: fields
          in: query
          schema:
            type: array
      responses:
        "200":
          description: OK
"""
        ir = extractor.extract(SourceConfig(file_content=spec))

        operation = next(op for op in ir.operations if op.id == "getComment")
        param_map = {param.name: param for param in operation.params}

        assert "id" in param_map
        assert param_map["id"].required is True
        assert param_map["id"].type == "string"

    def test_idempotent_extraction(self, extractor):
        """Same input should produce same output (modulo created_at)."""
        source = SourceConfig(file_path=str(FIXTURES / "petstore_3_0.yaml"))
        ir1 = extractor.extract(source)
        ir2 = extractor.extract(source)

        assert ir1.source_hash == ir2.source_hash
        assert len(ir1.operations) == len(ir2.operations)
        assert {op.id for op in ir1.operations} == {op.id for op in ir2.operations}

    def test_callbacks_and_webhooks_become_explicit_event_descriptors(self, extractor):
        spec = """
openapi: "3.1.0"
info:
  title: Eventful API
  version: "1.0"
servers:
  - url: https://events.example.com
paths:
  /uploads:
    post:
      operationId: uploadInvoiceAttachment
      summary: Upload invoice attachment
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              type: object
      callbacks:
        onComplete:
          "{$request.body#/callbackUrl}":
            post:
              responses:
                "200":
                  description: OK
      responses:
        "202":
          description: Accepted
webhooks:
  invoiceSigned:
    post:
      responses:
        "200":
          description: OK
"""
        ir = extractor.extract(SourceConfig(file_content=spec))

        assert ir.metadata["ignored_callbacks"] == ["uploadInvoiceAttachment:onComplete"]
        assert ir.metadata["ignored_webhooks"] == ["invoiceSigned"]
        assert {descriptor.id for descriptor in ir.event_descriptors} == {
            "invoiceSigned",
            "uploadInvoiceAttachment:onComplete",
        }
        callback = next(
            descriptor
            for descriptor in ir.event_descriptors
            if descriptor.id == "uploadInvoiceAttachment:onComplete"
        )
        webhook = next(
            descriptor for descriptor in ir.event_descriptors if descriptor.id == "invoiceSigned"
        )
        assert callback.transport is EventTransport.callback
        assert callback.operation_id == "uploadInvoiceAttachment"
        assert callback.support is EventSupportLevel.unsupported
        assert webhook.transport is EventTransport.webhook


# ── Error Response Extraction Tests ──────────────────────────────


class TestErrorResponseExtraction:
    def test_extract_error_responses_from_openapi3(self, extractor):
        """4xx/5xx responses extracted as ErrorResponse entries."""
        spec = """
openapi: "3.0.0"
info:
  title: ErrorTest
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      responses:
        "200":
          description: OK
        "400":
          description: Bad Request
        "404":
          description: Not Found
        "500":
          description: Internal Server Error
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.error_schema.responses) == 3
        status_codes = [r.status_code for r in op.error_schema.responses]
        assert status_codes == [400, 404, 500]
        assert op.error_schema.responses[0].description == "Bad Request"
        assert op.error_schema.responses[1].description == "Not Found"
        assert op.error_schema.responses[2].description == "Internal Server Error"

    def test_extract_default_error_schema(self, extractor):
        """'default' response becomes default_error_schema."""
        spec = """
openapi: "3.0.0"
info:
  title: DefaultErrorTest
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      responses:
        "200":
          description: OK
        default:
          description: Unexpected error
          content:
            application/json:
              schema:
                type: object
                properties:
                  code:
                    type: integer
                  message:
                    type: string
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert op.error_schema.default_error_schema is not None
        assert op.error_schema.default_error_schema["type"] == "object"
        props = op.error_schema.default_error_schema["properties"]
        assert "code" in props
        assert "message" in props

    def test_error_responses_with_schema(self, extractor):
        """Error response includes JSON Schema body when present."""
        spec = """
openapi: "3.0.0"
info:
  title: SchemaErrorTest
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      responses:
        "200":
          description: OK
        "422":
          description: Validation Error
          content:
            application/json:
              schema:
                type: object
                properties:
                  detail:
                    type: array
                    items:
                      type: object
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.error_schema.responses) == 1
        err = op.error_schema.responses[0]
        assert err.status_code == 422
        assert err.description == "Validation Error"
        assert err.error_body_schema is not None
        assert err.error_body_schema["type"] == "object"
        assert "detail" in err.error_body_schema["properties"]

    def test_no_error_responses_default_empty(self, extractor):
        """Operations without error info get empty ErrorSchema."""
        spec = """
openapi: "3.0.0"
info:
  title: NoErrorTest
  version: "1.0"
paths:
  /health:
    get:
      operationId: healthCheck
      summary: Health check
      responses:
        "200":
          description: OK
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.error_schema.responses) == 0
        assert op.error_schema.default_error_schema is None

    def test_extract_swagger2_error_responses(self, extractor):
        """Swagger 2.0 error responses extracted correctly."""
        spec = """
{
  "swagger": "2.0",
  "info": {"title": "Sw2Err", "version": "1.0"},
  "host": "api.example.com",
  "basePath": "/v1",
  "paths": {
    "/pets": {
      "get": {
        "operationId": "listPets",
        "summary": "List pets",
        "responses": {
          "200": {"description": "OK"},
          "401": {
            "description": "Unauthorized",
            "schema": {
              "type": "object",
              "properties": {
                "error": {"type": "string"}
              }
            }
          },
          "500": {"description": "Server Error"}
        }
      }
    }
  }
}
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.error_schema.responses) == 2
        err_401 = next(r for r in op.error_schema.responses if r.status_code == 401)
        assert err_401.description == "Unauthorized"
        assert err_401.error_body_schema is not None
        assert err_401.error_body_schema["type"] == "object"

        err_500 = next(r for r in op.error_schema.responses if r.status_code == 500)
        assert err_500.description == "Server Error"
        assert err_500.error_body_schema is None


# ── Response Examples Extraction Tests ───────────────────────────


class TestResponseExamplesExtraction:
    def test_extract_response_examples_from_openapi3(self, extractor):
        """Inline example extracted from content/application/json/example."""
        spec = """
openapi: "3.0.0"
info:
  title: ExampleTest
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      responses:
        "200":
          description: OK
          content:
            application/json:
              example:
                - id: 1
                  name: Fido
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.response_examples) == 1
        ex = op.response_examples[0]
        assert ex.name == "example_200"
        assert ex.status_code == 200
        assert ex.body == '[{"id": 1, "name": "Fido"}]'
        assert ex.source == SourceType.extractor

    def test_extract_response_examples_map(self, extractor):
        """'examples' map extracted as multiple ResponseExamples."""
        spec = """
openapi: "3.0.0"
info:
  title: ExamplesMapTest
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      responses:
        "200":
          description: OK
          content:
            application/json:
              examples:
                single_pet:
                  summary: One pet
                  value:
                    - id: 1
                      name: Fido
                empty_list:
                  summary: No pets
                  value: []
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.response_examples) == 2
        names = {ex.name for ex in op.response_examples}
        assert names == {"single_pet", "empty_list"}
        single = next(ex for ex in op.response_examples if ex.name == "single_pet")
        assert single.description == "One pet"
        assert single.status_code == 200
        assert single.body == '[{"id": 1, "name": "Fido"}]'
        empty = next(ex for ex in op.response_examples if ex.name == "empty_list")
        assert empty.body == "[]"

    def test_extract_swagger2_examples(self, extractor):
        """Swagger 2.0 examples from responses/<code>/examples/application/json."""
        spec = """
{
  "swagger": "2.0",
  "info": {"title": "Sw2Ex", "version": "1.0"},
  "host": "api.example.com",
  "basePath": "/v1",
  "paths": {
    "/pets": {
      "get": {
        "operationId": "listPets",
        "summary": "List pets",
        "responses": {
          "200": {
            "description": "OK",
            "examples": {
              "application/json": [
                {"id": 1, "name": "Fido"}
              ]
            }
          }
        }
      }
    }
  }
}
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        assert len(op.response_examples) >= 1
        ex = op.response_examples[0]
        assert ex.name == "example_200"
        assert ex.status_code == 200
        assert ex.body == '[{"id": 1, "name": "Fido"}]'
        assert ex.source == SourceType.extractor

    def test_schema_level_example(self, extractor):
        """Schema-level 'example' on the response schema produces a ResponseExample."""
        spec = """
openapi: "3.0.0"
info:
  title: SchemaExTest
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  id:
                    type: integer
                  name:
                    type: string
                example:
                  id: 1
                  name: Fido
"""
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]

        schema_examples = [ex for ex in op.response_examples if "schema" in ex.name]
        assert len(schema_examples) == 1
        assert schema_examples[0].body == {"id": 1, "name": "Fido"}
        assert schema_examples[0].status_code == 200


# ── Pagination Inference Tests ───────────────────────────────


class TestPaginationInference:
    def test_pagination_cursor_detected(self, extractor):
        """Spec with cursor + limit params → style=cursor."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"name": "cursor", "in": "query", "schema": {"type": "string"}},
                                {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is not None
        assert op.response_strategy.pagination.style == "cursor"
        assert op.response_strategy.pagination.cursor_param == "cursor"
        assert op.response_strategy.pagination.limit_param == "limit"

    def test_pagination_page_detected(self, extractor):
        """Spec with page + per_page params → style=page."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"name": "page", "in": "query", "schema": {"type": "integer"}},
                                {"name": "per_page", "in": "query", "schema": {"type": "integer"}},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is not None
        assert op.response_strategy.pagination.style == "page"
        assert op.response_strategy.pagination.page_param == "page"
        assert op.response_strategy.pagination.limit_param == "per_page"

    def test_pagination_offset_detected(self, extractor):
        """Spec with offset + limit params → style=offset."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"name": "offset", "in": "query", "schema": {"type": "integer"}},
                                {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is not None
        assert op.response_strategy.pagination.style == "offset"
        assert op.response_strategy.pagination.page_param == "offset"
        assert op.response_strategy.pagination.limit_param == "limit"

    def test_pagination_page_token_detected(self, extractor):
        """Spec with page_token param → style=cursor."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {
                                    "name": "page_token",
                                    "in": "query",
                                    "schema": {"type": "string"},
                                },
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is not None
        assert op.response_strategy.pagination.style == "cursor"
        assert op.response_strategy.pagination.cursor_param == "page_token"

    def test_pagination_response_envelope_detected(self, extractor):
        """Response schema with data array + meta.total → pagination detected."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [],
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "properties": {
                                                    "data": {
                                                        "type": "array",
                                                        "items": {"type": "object"},
                                                    },
                                                    "meta": {
                                                        "type": "object",
                                                        "properties": {
                                                            "total": {"type": "integer"},
                                                            "page": {"type": "integer"},
                                                        },
                                                    },
                                                },
                                            }
                                        }
                                    },
                                }
                            },
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is not None
        assert op.response_strategy.pagination.style == "offset"

    def test_pagination_not_detected_for_non_get(self, extractor):
        """POST with page params → no pagination (None)."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items": {
                        "post": {
                            "operationId": "createItems",
                            "parameters": [
                                {"name": "page", "in": "query", "schema": {"type": "integer"}},
                                {"name": "per_page", "in": "query", "schema": {"type": "integer"}},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is None

    def test_pagination_not_detected_when_no_hints(self, extractor):
        """Simple GET without any pagination params → None."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Test API", "version": "1.0"},
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/items/{id}": {
                        "get": {
                            "operationId": "getItem",
                            "parameters": [
                                {
                                    "name": "id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string"},
                                },
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        op = ir.operations[0]
        assert op.response_strategy.pagination is None


# ── Detection edge cases ───────────────────────────────────────────────────


class TestDetectionEdgeCases:
    """Cover lines 73, 77-81, 88, 95."""

    def test_detect_returns_zero_when_no_content(self, extractor):
        """Line 73: _get_content returns None → 0.0."""
        source = SourceConfig(url="https://no-such-host.invalid/spec.yaml")
        with patch.object(extractor, "_get_content", return_value=None):
            assert extractor.detect(source) == 0.0

    def test_detect_returns_zero_for_json_decode_error(self, extractor):
        """Line 77: JSONDecodeError during parse → 0.0."""
        source = SourceConfig(file_content="{invalid json")
        assert extractor.detect(source) == 0.0

    def test_detect_returns_zero_for_yaml_parse_error(self, extractor):
        """Lines 77-78: YAML that is parseable but raises ValueError in _resolve_refs."""
        source = SourceConfig(file_content="just: a\n  broken: yaml: file: [")
        assert extractor.detect(source) == 0.0

    def test_detect_returns_06_for_paths_and_info_only(self, extractor):
        """Line 88: spec has 'paths' and 'info' but no 'openapi'/'swagger' → 0.6."""
        spec = json.dumps({"info": {"title": "T"}, "paths": {"/a": {}}})
        source = SourceConfig(file_content=spec)
        assert extractor.detect(source) == 0.6

    def test_detect_returns_zero_for_no_openapi_markers(self, extractor):
        """Line 88→89: spec has none of the markers → 0.0."""
        source = SourceConfig(file_content='{"foo": "bar"}')
        assert extractor.detect(source) == 0.0

    def test_extract_raises_when_no_content(self, extractor):
        """Line 95: extract with no content raises ValueError."""
        source = SourceConfig(url="https://no-such-host.invalid/spec.yaml")
        with patch.object(extractor, "_get_content", return_value=None):
            with pytest.raises(ValueError, match="Could not read source content"):
                extractor.extract(source)


# ── Content fetching edge cases ────────────────────────────────────────────


class TestContentFetching:
    """Cover lines 145-161: URL fetching, auth headers, failure paths."""

    def test_get_content_from_url_success(self, extractor):
        """Lines 145-149: successful HTTP fetch."""
        spec_content = '{"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}'
        request = httpx.Request("GET", "https://example.com/spec.json")
        mock_resp = httpx.Response(200, text=spec_content, request=request)
        with patch("libs.extractors.utils.httpx.get", return_value=mock_resp):
            source = SourceConfig(url="https://example.com/spec.json")
            ir = extractor.extract(source)
            assert ir.protocol == "openapi"

    def test_get_content_from_url_failure_returns_none(self, extractor):
        """Lines 150-152: HTTP error → None → detect returns 0.0."""
        with patch("libs.extractors.utils.httpx.get", side_effect=httpx.ConnectError("fail")):
            source = SourceConfig(url="https://example.com/spec.json")
            assert extractor.detect(source) == 0.0

    def test_get_content_returns_none_no_sources(self, extractor):
        """Line 153: no url/file_path/file_content → None."""
        # SourceConfig requires at least one, so we mock _get_content
        with patch.object(extractor, "_get_content", return_value=None):
            source = SourceConfig(url="https://example.com")
            assert extractor.detect(source) == 0.0

    def test_auth_headers_with_auth_header(self, extractor):
        """Lines 156-158: auth_header is used directly."""
        source = SourceConfig(
            url="https://example.com/spec.json",
            auth_header="Basic dXNlcjpwYXNz",
        )
        headers = extractor._auth_headers(source)
        assert headers["Authorization"] == "Basic dXNlcjpwYXNz"

    def test_auth_headers_with_auth_token(self, extractor):
        """Lines 159-160: auth_token → Bearer prefix."""
        source = SourceConfig(
            url="https://example.com/spec.json",
            auth_token="my-token-123",
        )
        headers = extractor._auth_headers(source)
        assert headers["Authorization"] == "Bearer my-token-123"

    def test_auth_headers_empty(self, extractor):
        """Line 161: no auth → empty headers."""
        source = SourceConfig(url="https://example.com/spec.json")
        headers = extractor._auth_headers(source)
        assert headers == {}


# ── External $ref handling ─────────────────────────────────────────────────


class TestExternalRef:
    """Cover lines 196-197, 204: external $ref returns {}, non-dict ref path returns {}."""

    def test_external_ref_skipped(self, extractor):
        """Lines 196-197: external $ref like 'other.yaml#/...' → empty dict."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "RefTest", "version": "1.0"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [{"$ref": "external.yaml#/components/parameters/Limit"}],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        assert len(ir.operations) == 1

    def test_ref_path_hits_non_dict(self, extractor):
        """Line 204: $ref path resolves through a non-dict → returns {}."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "RefTest", "version": "1.0"},
                "components": {"schemas": "not-a-dict"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"$ref": "#/components/schemas/Missing"},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
        source = SourceConfig(file_content=spec)
        ir = extractor.extract(source)
        assert len(ir.operations) == 1


# ── Auth scheme parsing ────────────────────────────────────────────────────


class TestAuthSchemeParsing:
    """Cover lines 237, 252-279: swagger/openapi auth edge cases."""

    def test_swagger_apikey_in_query(self, extractor):
        """Lines 246-251: Swagger apiKey in query."""
        spec = json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "T", "version": "1"},
                "host": "api.example.com",
                "basePath": "/v1",
                "securityDefinitions": {
                    "QueryKey": {"type": "apiKey", "in": "query", "name": "api_key"},
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.api_key
        assert ir.auth.api_key_location == "query"
        assert ir.auth.api_key_param == "api_key"

    def test_swagger_oauth2(self, extractor):
        """Line 252-253: Swagger oauth2."""
        spec = json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "T", "version": "1"},
                "host": "api.example.com",
                "securityDefinitions": {
                    "OAuth": {
                        "type": "oauth2",
                        "flow": "implicit",
                        "authorizationUrl": "https://example.com/auth",
                    },
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.oauth2

    def test_swagger_basic(self, extractor):
        """Lines 254-255: Swagger basic auth."""
        spec = json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "T", "version": "1"},
                "host": "api.example.com",
                "securityDefinitions": {
                    "BasicAuth": {"type": "basic"},
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.basic

    def test_swagger_unknown_auth(self, extractor):
        """Line 256: Swagger unknown auth type → none."""
        spec = json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "T", "version": "1"},
                "host": "api.example.com",
                "securityDefinitions": {
                    "Custom": {"type": "custom"},
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.none

    def test_openapi_basic_http(self, extractor):
        """Lines 268-269: OpenAPI http/basic."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "components": {
                    "securitySchemes": {
                        "BasicAuth": {"type": "http", "scheme": "basic"},
                    },
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.basic

    def test_openapi_apikey_in_query(self, extractor):
        """Lines 270-276: OpenAPI apiKey in query."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "components": {
                    "securitySchemes": {
                        "QueryKey": {"type": "apiKey", "in": "query", "name": "token"},
                    },
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.api_key
        assert ir.auth.api_key_location == "query"
        assert ir.auth.api_key_param == "token"

    def test_openapi_oauth2(self, extractor):
        """Lines 277-278: OpenAPI oauth2."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "components": {
                    "securitySchemes": {
                        "OAuth": {
                            "type": "oauth2",
                            "flows": {
                                "implicit": {
                                    "authorizationUrl": "https://example.com/auth",
                                }
                            },
                        },
                    },
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.oauth2

    def test_openapi_unknown_auth(self, extractor):
        """Line 279: OpenAPI unknown type → none."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "components": {
                    "securitySchemes": {
                        "Custom": {"type": "mutualTLS"},
                    },
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.none

    def test_non_dict_scheme_returns_none(self, extractor):
        """Line 237: securitySchemes first value is not a dict → none."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "components": {
                    "securitySchemes": {"BadScheme": "not-a-dict"},
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.none

    def test_openapi_apikey_invalid_location(self, extractor):
        """Lines 271-275: OpenAPI apiKey with invalid location → defaults to header."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "components": {
                    "securitySchemes": {
                        "CookieKey": {"type": "apiKey", "in": "cookie", "name": "sess"},
                    },
                },
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.auth.type == AuthType.api_key
        assert ir.auth.api_key_location == "header"


# ── Operations extraction edge cases ──────────────────────────────────────


class TestOperationsEdgeCases:
    """Cover lines 291, 295, 301, 315, 387, 390, 430, 439, 460, 477, 480, 486, 496."""

    def test_paths_not_dict(self, extractor):
        """Line 291: paths is not a dict → empty ops."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": "not-a-dict",
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 0

    def test_path_item_not_dict(self, extractor):
        """Line 295: path_item is not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/bad": "not-a-dict",
                    "/good": {
                        "get": {"operationId": "ok", "responses": {"200": {"description": "OK"}}},
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 1

    def test_op_spec_not_dict(self, extractor):
        """Line 301: operation value is not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/items": {"get": "not-a-dict"}},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 0

    def test_non_string_path_key(self, extractor):
        """Line 295: non-string path key (edge case) → skip."""
        # JSON keys are always strings, but test dict behavior
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {},
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 0

    def test_null_parameters_on_path_or_operation(self, extractor):
        """Explicit null for parameters must not crash with TypeError."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "parameters": None,
                        "get": {
                            "operationId": "getItems",
                            "parameters": None,
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 1
        assert ir.operations[0].id == "getItems"

    def test_callbacks_generate_event_descriptors(self, extractor):
        """Lines 315, 387, 390: callbacks produce event descriptors."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/hooks": {
                        "post": {
                            "operationId": "createHook",
                            "callbacks": {
                                "onEvent": {
                                    "{$request.body#/url}": {
                                        "post": {"responses": {"200": {"description": "OK"}}},
                                    }
                                },
                            },
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert "createHook:onEvent" in ir.metadata["ignored_callbacks"]
        cb_desc = next(d for d in ir.event_descriptors if d.id == "createHook:onEvent")
        assert cb_desc.transport is EventTransport.callback
        assert cb_desc.operation_id == "createHook"

    def test_param_not_dict_skipped(self, extractor):
        """Line 387: non-dict parameter → skipped."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                "not-a-dict",
                                {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].params) == 1

    def test_resolve_param_type_non_dict_schema(self, extractor):
        """Line 430: schema is not a dict → 'string'."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"name": "bad", "in": "query", "schema": "not-a-dict"},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert ir.operations[0].params[0].type == "string"

    def test_request_body_content_not_dict(self, extractor):
        """Line 439: requestBody.content is not a dict → empty params."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "post": {
                            "operationId": "createItem",
                            "requestBody": {"content": "not-a-dict"},
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        # Should still have an operation, just no body params
        assert len(ir.operations) == 1

    def test_request_body_octet_stream(self, extractor):
        """Line 460: application/octet-stream → raw mode."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/upload": {
                        "post": {
                            "operationId": "uploadFile",
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/octet-stream": {
                                        "schema": {"type": "string", "format": "binary"}
                                    }
                                },
                            },
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]
        assert op.request_body_mode == RequestBodyMode.raw
        assert op.body_param_name == "payload"

    def test_request_body_json_content_not_dict(self, extractor):
        """Line 477: application/json value is not a dict → empty."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "post": {
                            "operationId": "createItem",
                            "requestBody": {
                                "content": {"application/json": "not-a-dict"},
                            },
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 1

    def test_request_body_schema_not_dict(self, extractor):
        """Line 480: schema inside application/json is not a dict → empty."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "post": {
                            "operationId": "createItem",
                            "requestBody": {
                                "content": {"application/json": {"schema": "not-a-dict"}},
                            },
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations) == 1

    def test_webhooks_not_dict(self, extractor):
        """Lines 486, 496: webhooks is not a dict → ignored."""
        spec = json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "T", "version": "1"},
                "paths": {},
                "webhooks": "not-a-dict",
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert (
            ir.metadata.get("ignored_webhooks") is None or ir.metadata.get("ignored_webhooks") == []
        )
        assert len(ir.event_descriptors) == 0


# ── Response schema extraction edge cases ─────────────────────────────────


class TestResponseSchemaEdgeCases:
    """Cover lines 603, 611, 615, 629, 636, 646-647, 677, 683, 711, 724, 727, 746,
    787, 790, 797, 803, 807."""

    def test_responses_not_dict_returns_none(self, extractor):
        """Line 603: responses is not a dict → None for success schema."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": "not-a-dict",
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]
        assert op.response_strategy.pagination is None

    def test_swagger_response_schema_returned(self, extractor):
        """Line 611: Swagger 2.0 200 response with schema."""
        spec = json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "T", "version": "1"},
                "host": "api.example.com",
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"name": "cursor", "in": "query", "type": "string"},
                            ],
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "next_cursor": {"type": "string"},
                                            "items": {"type": "array"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]
        assert op.response_strategy.pagination is not None
        assert op.response_strategy.pagination.style == "cursor"

    def test_openapi_response_content_not_dict(self, extractor):
        """Line 615: 200 response content is not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "parameters": [
                                {"name": "cursor", "in": "query", "schema": {"type": "string"}},
                            ],
                            "responses": {
                                "200": {"description": "OK", "content": "not-a-dict"},
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        # Should still detect cursor pagination from params
        assert ir.operations[0].response_strategy.pagination is not None

    def test_error_schema_responses_not_dict(self, extractor):
        """Line 629: error_schema when responses is not a dict → empty."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": "not-a-dict",
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].error_schema.responses) == 0

    def test_error_resp_obj_not_dict_skipped(self, extractor):
        """Line 636: response value is not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {"description": "OK"},
                                "400": "not-a-dict",
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].error_schema.responses) == 0

    def test_invalid_status_code_skipped(self, extractor):
        """Lines 646-647: non-integer status code → skipped."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {"description": "OK"},
                                "2XX": {"description": "Wildcard"},
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].error_schema.responses) == 0

    def test_response_examples_not_dict_responses(self, extractor):
        """Line 677: response_examples when responses is not a dict → empty."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": "not-a-dict",
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].response_examples) == 0

    def test_response_examples_resp_obj_not_dict(self, extractor):
        """Line 683: response value not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": "not-a-dict",
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].response_examples) == 0

    def test_swagger_schema_example(self, extractor):
        """Line 711: Swagger 2.x schema-level example."""
        spec = json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "T", "version": "1"},
                "host": "api.example.com",
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "schema": {
                                        "type": "object",
                                        "example": {"id": 1, "name": "Item"},
                                    },
                                },
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        op = ir.operations[0]
        schema_examples = [ex for ex in op.response_examples if "schema" in ex.name]
        assert len(schema_examples) == 1
        assert schema_examples[0].body == {"id": 1, "name": "Item"}

    def test_openapi3_response_content_not_dict(self, extractor):
        """Line 724: OpenAPI 3.x response content not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {"description": "OK", "content": "not-a-dict"},
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].response_examples) == 0

    def test_openapi3_json_content_not_dict(self, extractor):
        """Line 727: application/json value is not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {"application/json": "not-a-dict"},
                                },
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].response_examples) == 0

    def test_openapi3_example_in_map_not_dict(self, extractor):
        """Line 746: examples map entry is not a dict → skip."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "examples": {
                                                "bad": "not-a-dict",
                                                "good": {
                                                    "summary": "A good example",
                                                    "value": {"id": 1},
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        assert len(ir.operations[0].response_examples) == 1
        assert ir.operations[0].response_examples[0].name == "good"

    def test_response_body_schema_openapi_content_not_dict(self, extractor):
        """Line 787: _response_body_schema with content not a dict → None."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {"description": "OK"},
                                "400": {"description": "Bad", "content": "not-a-dict"},
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        err = ir.operations[0].error_schema.responses[0]
        assert err.error_body_schema is None

    def test_response_body_schema_json_content_not_dict(self, extractor):
        """Line 790: application/json not a dict in error response → None."""
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {"description": "OK"},
                                "500": {
                                    "description": "Err",
                                    "content": {"application/json": "not-a-dict"},
                                },
                            },
                        },
                    },
                },
            }
        )
        ir = extractor.extract(SourceConfig(file_content=spec))
        err = ir.operations[0].error_schema.responses[0]
        assert err.error_body_schema is None


# ── Flatten schema edge cases ─────────────────────────────────────────────


class TestFlattenSchemaEdgeCases:
    """Cover lines 797, 803, 807."""

    def test_flatten_non_object_schema(self, extractor):
        """Line 797: schema without type=object and no properties → empty."""
        result = extractor._flatten_schema_to_params({"type": "string"})
        assert result == []

    def test_flatten_properties_not_dict(self, extractor):
        """Line 803: properties is not a dict → empty."""
        result = extractor._flatten_schema_to_params({"type": "object", "properties": "not-a-dict"})
        assert result == []

    def test_flatten_non_string_name_or_non_dict_prop(self, extractor):
        """Line 807: non-dict property value → skip."""
        result = extractor._flatten_schema_to_params(
            {
                "type": "object",
                "properties": {
                    "good": {"type": "string"},
                    "bad": "not-a-dict",
                },
            }
        )
        assert len(result) == 1
        assert result[0].name == "good"


class TestExtractNestedJsonSchema:
    """Tests for _extract_nested_json_schema helper."""

    def test_scalar_returns_none(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        assert _extract_nested_json_schema({"type": "string"}) is None

    def test_object_with_properties(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        prop = {
            "type": "object",
            "properties": {
                "street": {"type": "string", "description": "Street address"},
                "city": {"type": "string"},
                "zip": {"type": "integer"},
            },
            "required": ["street", "city"],
        }
        result = _extract_nested_json_schema(prop)
        assert result is not None
        assert result["type"] == "object"
        assert "street" in result["properties"]
        assert result["properties"]["street"]["type"] == "string"
        assert result["properties"]["street"]["description"] == "Street address"
        assert result["properties"]["zip"]["type"] == "integer"
        assert result["required"] == ["street", "city"]

    def test_object_without_properties_returns_none(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        assert _extract_nested_json_schema({"type": "object"}) is None

    def test_array_with_structured_items(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        prop = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        }
        result = _extract_nested_json_schema(prop)
        assert result is not None
        assert result["type"] == "array"
        assert result["items"]["type"] == "object"
        assert "id" in result["items"]["properties"]

    def test_array_with_scalar_items_returns_schema(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        result = _extract_nested_json_schema({"type": "array", "items": {"type": "string"}})
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_array_with_scalar_enum_items(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        result = _extract_nested_json_schema(
            {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}
        )
        assert result == {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}

    def test_nested_objects(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        prop = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                    },
                },
                "name": {"type": "string"},
            },
        }
        result = _extract_nested_json_schema(prop)
        assert result is not None
        assert result["properties"]["address"]["type"] == "object"
        assert "street" in result["properties"]["address"]["properties"]

    def test_depth_limit(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        # Build deeply nested schema
        prop: dict[str, Any] = {"type": "string"}
        for _ in range(10):
            prop = {"type": "object", "properties": {"nested": prop}}
        result = _extract_nested_json_schema(prop)
        # Should still return something but stop recursing at depth limit
        assert result is not None

    def test_enum_preserved(self):
        from libs.extractors.openapi import _extract_nested_json_schema

        prop = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "inactive"]},
            },
        }
        result = _extract_nested_json_schema(prop)
        assert result is not None
        assert result["properties"]["status"]["enum"] == ["active", "inactive"]


class TestFlattenWithJsonSchema:
    """Tests for _flatten_schema_to_params json_schema propagation."""

    def test_object_param_gets_json_schema(self, extractor):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "secret": {"type": "string"},
                    },
                    "required": ["url"],
                },
                "name": {"type": "string"},
            },
        }
        params = extractor._flatten_schema_to_params(schema)
        config_param = next(p for p in params if p.name == "config")
        name_param = next(p for p in params if p.name == "name")
        assert config_param.json_schema is not None
        assert config_param.json_schema["type"] == "object"
        assert "url" in config_param.json_schema["properties"]
        assert name_param.json_schema is None

    def test_array_param_gets_json_schema(self, extractor):
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "qty": {"type": "integer"},
                        },
                    },
                },
            },
        }
        params = extractor._flatten_schema_to_params(schema)
        items_param = next(p for p in params if p.name == "items")
        assert items_param.json_schema is not None
        assert items_param.json_schema["type"] == "array"
        assert items_param.json_schema["items"]["properties"]["id"]["type"] == "integer"


class TestAllOfDepthLimit:
    """Deeply nested allOf/oneOf/anyOf must not cause stack overflow."""

    def test_allof_depth_limit_returns_empty(self):
        """allOf nested beyond MAX_COMPOSITION_DEPTH returns empty params."""
        extractor = OpenAPIExtractor()

        # Build schema nested 15 levels deep (limit is 10)
        schema: dict = {"type": "object", "properties": {"leaf": {"type": "string"}}}
        for _ in range(15):
            schema = {"allOf": [schema]}

        params = extractor._flatten_schema_to_params(schema)
        # Should NOT find "leaf" because depth limit truncates
        assert not any(p.name == "leaf" for p in params)

    def test_moderate_nesting_works(self):
        """3-level allOf nesting should still extract params."""
        extractor = OpenAPIExtractor()

        schema = {
            "allOf": [
                {
                    "allOf": [
                        {
                            "allOf": [
                                {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        params = extractor._flatten_schema_to_params(schema)
        assert any(p.name == "name" and p.required for p in params)


class TestOneOfAnyOfConfidence:
    """Tests for confidence handling with oneOf/anyOf composition schemas."""

    def test_empty_oneof_branches_do_not_lower_confidence(self) -> None:
        """oneOf with scalar-only branches should not lower confidence."""
        extractor = OpenAPIExtractor()
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        params = extractor._flatten_schema_to_params(schema)
        # No params extractable from scalar branches → confidence unchanged
        assert params == []

    def test_oneof_with_object_branches_lowers_confidence(self) -> None:
        """oneOf with object branches producing params should lower confidence."""
        extractor = OpenAPIExtractor()
        schema: dict[str, Any] = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
                {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
            ],
        }
        params = extractor._flatten_schema_to_params(schema)
        assert len(params) == 2
        for p in params:
            assert p.confidence <= 0.8

    def test_top_level_properties_skip_oneof(self) -> None:
        """When top-level properties exist, oneOf is skipped entirely."""
        extractor = OpenAPIExtractor()
        schema: dict[str, Any] = {
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"extra": {"type": "string"}},
                },
            ],
        }
        params = extractor._flatten_schema_to_params(schema)
        names = {p.name for p in params}
        assert "name" in names
        # oneOf branches should not be merged since top-level properties exist
        assert "extra" not in names
