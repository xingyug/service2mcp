"""E2E: Extraction → enhancement → validation pipeline.

Tests the core pipeline without infrastructure dependencies by feeding spec
content directly to real extractors via SourceConfig.file_content, then running
deterministic post-enhancement (no LLM) and schema validation.

Protocols tested: OpenAPI, GraphQL, gRPC, SOAP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.compiler_worker.activities.production import _apply_post_enhancement
from libs.extractors.base import SourceConfig
from libs.extractors.graphql import GraphQLExtractor
from libs.extractors.grpc import GrpcProtoExtractor
from libs.extractors.openapi import OpenAPIExtractor
from libs.extractors.soap import SOAPWSDLExtractor
from libs.ir.models import ServiceIR
from libs.validator.pre_deploy import PreDeployValidator

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(relative_path: str) -> str:
    return (_FIXTURES / relative_path).read_text()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenAPIPipeline:
    """OpenAPI spec → extract → enhance → validate."""

    async def test_petstore_pipeline(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})

        extractor = OpenAPIExtractor()
        ir = extractor.extract(source)

        assert isinstance(ir, ServiceIR)
        assert ir.protocol == "openapi"
        assert len(ir.operations) > 0

        # Deterministic post-enhancement (no LLM)
        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        assert len(enhanced.operations) > 0
        # Post-enhancement should produce resources and prompts
        assert len(enhanced.resource_definitions) > 0
        assert len(enhanced.prompt_definitions) > 0
        # Error schemas should be normalized
        for op in enhanced.operations:
            assert op.error_schema is not None

        # Validate
        async with PreDeployValidator() as v:
            report = await v.validate(enhanced)
        assert report.get_result("schema").passed, report.get_result("schema").details

    async def test_openapi_operations_have_methods(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})

        ir = OpenAPIExtractor().extract(source)
        for op in ir.operations:
            assert op.method is not None, f"Operation {op.id} missing method"


class TestGraphQLPipeline:
    """GraphQL schema → extract → enhance → validate."""

    async def test_catalog_pipeline(self) -> None:
        content = _load_fixture("graphql_schemas/catalog_introspection.json")
        source = SourceConfig(file_content=content, hints={"protocol": "graphql"})

        extractor = GraphQLExtractor()
        ir = extractor.extract(source)

        assert isinstance(ir, ServiceIR)
        assert ir.protocol == "graphql"
        assert len(ir.operations) > 0

        # All GraphQL ops should have graphql config
        for op in ir.operations:
            assert op.graphql is not None, f"Operation {op.id} missing graphql config"

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        async with PreDeployValidator() as v:
            report = await v.validate(enhanced)
        assert report.get_result("schema").passed


class TestGrpcPipeline:
    """gRPC proto → extract → enhance → validate."""

    async def test_inventory_pipeline(self) -> None:
        content = _load_fixture("grpc_protos/inventory.proto")
        source = SourceConfig(file_content=content, hints={"protocol": "grpc"})

        extractor = GrpcProtoExtractor()
        ir = extractor.extract(source)

        assert isinstance(ir, ServiceIR)
        assert ir.protocol == "grpc"
        assert len(ir.operations) > 0

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        async with PreDeployValidator() as v:
            report = await v.validate(enhanced)
        assert report.get_result("schema").passed


class TestSOAPPipeline:
    """SOAP WSDL → extract → enhance → validate."""

    async def test_order_service_pipeline(self) -> None:
        content = _load_fixture("wsdl/order_service.wsdl")
        source = SourceConfig(file_content=content, hints={"protocol": "soap"})

        extractor = SOAPWSDLExtractor()
        ir = extractor.extract(source)

        assert isinstance(ir, ServiceIR)
        assert ir.protocol == "soap"
        assert len(ir.operations) > 0

        # All SOAP ops should have soap config
        for op in ir.operations:
            assert op.soap is not None, f"Operation {op.id} missing soap config"

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        async with PreDeployValidator() as v:
            report = await v.validate(enhanced)
        assert report.get_result("schema").passed


class TestPostEnhancementDeterministic:
    """Post-enhancement without LLM is deterministic and enriching."""

    async def test_tool_intents_assigned(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
        ir = OpenAPIExtractor().extract(source)

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        # At least some operations should have tool_intent set
        intents = [op.tool_intent for op in enhanced.operations if op.tool_intent is not None]
        assert len(intents) > 0

    async def test_error_schemas_normalized(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
        ir = OpenAPIExtractor().extract(source)

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        for op in enhanced.operations:
            assert op.error_schema is not None
            # Normalized error schemas should have responses populated
            assert len(op.error_schema.responses) > 0

    async def test_resources_generated_when_absent(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
        ir = OpenAPIExtractor().extract(source)

        assert len(ir.resource_definitions) == 0

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)
        assert len(enhanced.resource_definitions) > 0

    async def test_prompts_generated_when_absent(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
        ir = OpenAPIExtractor().extract(source)

        assert len(ir.prompt_definitions) == 0

        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)
        assert len(enhanced.prompt_definitions) > 0


class TestPipelineIRIntegrity:
    """The full pipeline produces valid, well-formed IR."""

    async def test_operation_ids_unique(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
        ir = OpenAPIExtractor().extract(source)
        enhanced = _apply_post_enhancement(ir, llm_client_factory=None)

        op_ids = [op.id for op in enhanced.operations]
        assert len(op_ids) == len(set(op_ids))

    async def test_service_name_present(self) -> None:
        content = _load_fixture("openapi_specs/petstore_3_0.yaml")
        source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
        ir = OpenAPIExtractor().extract(source)
        assert ir.service_name
        assert ir.base_url
