"""Tests for the OpenAPI extractor — Swagger 2.0, OpenAPI 3.0, 3.1."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor
from libs.ir.models import AuthType, EventSupportLevel, EventTransport, RiskLevel, SourceType

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
        assert "petstore.swagger.io" in ir.base_url
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


# ── DEP-002: Error Response Extraction Tests ──────────────────────────────


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


# ── DEP-003: Response Examples Extraction Tests ───────────────────────────


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
