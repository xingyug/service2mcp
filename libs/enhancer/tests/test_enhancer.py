"""Tests for the LLM enhancer — uses mock LLM client to verify behavior."""

from __future__ import annotations

import json
from types import SimpleNamespace

from libs.enhancer.enhancer import (
    EnhancerConfig,
    IREnhancer,
    LLMProvider,
    LLMResponse,
    TokenUsage,
    create_llm_client,
)
from libs.ir.models import (
    AsyncJobConfig,
    EventDescriptor,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RequestBodyMode,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SoapOperationConfig,
    SourceType,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
)

# ── Mock LLM Client ───────────────────────────────────────────────────────


class MockLLMClient:
    """Mock LLM client that returns canned responses."""

    def __init__(self, response: str | None = None, fail: bool = False) -> None:
        self._response = response
        self._fail = fail
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        self.calls.append(prompt)
        if self._fail:
            raise RuntimeError("LLM API error")
        return LLMResponse(
            content=self._response or "[]",
            input_tokens=100,
            output_tokens=50,
        )


# ── Fixtures ───────────────────────────────────────────────────────────────


def make_raw_ir(num_ops: int = 3) -> ServiceIR:
    """Create a raw ServiceIR with minimal descriptions."""
    operations = []
    for i in range(num_ops):
        operations.append(Operation(
            id=f"op_{i}",
            name=f"Operation {i}",
            description="",  # empty — needs enhancement
            method="GET",
            path=f"/endpoint_{i}",
            params=[
                Param(name="id", type="integer", required=True, description="", confidence=0.9),
                Param(name="filter", type="string", required=False, description="", confidence=0.9),
            ],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            source=SourceType.extractor,
            confidence=0.9,
            enabled=True,
        ))
    return ServiceIR(
        source_hash="abc123",
        protocol="openapi",
        service_name="test-api",
        base_url="https://api.example.com",
        operations=operations,
    )


def make_llm_response(operations: list[Operation]) -> str:
    """Create a mock LLM response for the given operations."""
    result = []
    for op in operations:
        result.append({
            "operation_id": op.id,
            "description": f"Enhanced: {op.name} retrieves data from the server",
            "confidence": 0.85,
            "params": [
                {
                    "name": p.name,
                    "description": f"Enhanced: The {p.name} parameter",
                    "confidence": 0.8,
                }
                for p in op.params
            ],
        })
    return json.dumps(result)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestIREnhancer:
    def test_enhance_improves_descriptions(self):
        ir = make_raw_ir(2)
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)

        assert result.operations_enhanced == 2
        for op in result.enhanced_ir.operations:
            assert op.description.startswith("Enhanced:")
            assert op.source == SourceType.llm
            assert op.confidence == 0.85

    def test_structural_fields_unchanged(self):
        ir = make_raw_ir(1)
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)
        original_op = ir.operations[0]
        enhanced_op = result.enhanced_ir.operations[0]

        # Structural fields must be identical
        assert enhanced_op.id == original_op.id
        assert enhanced_op.name == original_op.name
        assert enhanced_op.method == original_op.method
        assert enhanced_op.path == original_op.path
        assert len(enhanced_op.params) == len(original_op.params)
        for orig_p, enh_p in zip(original_op.params, enhanced_op.params):
            assert enh_p.name == orig_p.name
            assert enh_p.type == orig_p.type
            assert enh_p.required == orig_p.required

    def test_llm_source_tagging(self):
        ir = make_raw_ir(1)
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)
        enhanced_op = result.enhanced_ir.operations[0]

        assert enhanced_op.source == SourceType.llm
        for p in enhanced_op.params:
            assert p.source == SourceType.llm
            assert 0.0 <= p.confidence <= 1.0

    def test_enhance_preserves_typed_execution_metadata_and_event_descriptors(self):
        ir = ServiceIR(
            source_hash="abc123",
            protocol="mixed",
            service_name="typed-api",
            base_url="https://api.example.com",
            operations=[
                Operation(
                    id="graphql_query",
                    name="GraphQL Query",
                    description="",
                    method="POST",
                    path="/graphql",
                    params=[Param(name="term", type="string", description="", confidence=0.9)],
                    graphql=GraphQLOperationConfig(
                        operation_type=GraphQLOperationType.query,
                        operation_name="SearchProducts",
                        document=(
                            "query SearchProducts($term: String!) "
                            "{ searchProducts(term: $term) { sku } }"
                        ),
                        variable_names=["term"],
                    ),
                    risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
                    enabled=True,
                ),
                Operation(
                    id="grpc_list",
                    name="List Items",
                    description="",
                    method="POST",
                    path="/catalog.v1.InventoryService/ListItems",
                    params=[
                        Param(name="location_id", type="string", description="", confidence=0.9),
                        Param(name="page_size", type="integer", description="", confidence=0.9),
                    ],
                    grpc_unary=GrpcUnaryRuntimeConfig(
                        rpc_path="/catalog.v1.InventoryService/ListItems",
                    ),
                    risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
                    enabled=True,
                ),
                Operation(
                    id="soap_status",
                    name="Get Order Status",
                    description="",
                    method="POST",
                    path="/soap/order-service",
                    params=[Param(name="orderId", type="string", description="", confidence=0.9)],
                    soap=SoapOperationConfig(
                        target_namespace="urn:orders",
                        request_element="GetOrderStatusRequest",
                        response_element="GetOrderStatusResponse",
                        soap_action="GetOrderStatus",
                    ),
                    risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
                    enabled=True,
                ),
                Operation(
                    id="sql_query",
                    name="Query Orders",
                    description="",
                    method="GET",
                    path="/sql/query_order_summaries",
                    params=[Param(name="limit", type="integer", description="", confidence=0.9)],
                    sql=SqlOperationConfig(
                        schema_name="public",
                        relation_name="order_summaries",
                        relation_kind=SqlRelationKind.table,
                        action=SqlOperationType.query,
                        filterable_columns=["status"],
                    ),
                    risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
                    enabled=True,
                ),
                Operation(
                    id="async_rest",
                    name="Submit Export",
                    description="",
                    method="POST",
                    path="/exports",
                    params=[Param(name="payload", type="object", description="", confidence=0.9)],
                    request_body_mode=RequestBodyMode.raw,
                    body_param_name="payload",
                    async_job=AsyncJobConfig(status_url_field="status_url"),
                    tags=["exports"],
                    risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9),
                    enabled=True,
                ),
            ],
            event_descriptors=[
                EventDescriptor(
                    id="inventory_stream",
                    name="Inventory Stream",
                    transport=EventTransport.grpc_stream,
                    direction=EventDirection.outbound,
                    support=EventSupportLevel.supported,
                    operation_id="grpc_list",
                    channel="/catalog.v1.InventoryService/WatchInventory",
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/catalog.v1.InventoryService/WatchInventory",
                        mode=GrpcStreamMode.server,
                    ),
                )
            ],
            metadata={"protocols": ["graphql", "grpc", "soap", "sql", "rest"]},
        )
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)

        enhanced_ops = {op.id: op for op in result.enhanced_ir.operations}
        assert enhanced_ops["graphql_query"].graphql == ir.operations[0].graphql
        assert enhanced_ops["grpc_list"].grpc_unary == ir.operations[1].grpc_unary
        assert enhanced_ops["soap_status"].soap == ir.operations[2].soap
        assert enhanced_ops["sql_query"].sql == ir.operations[3].sql
        assert enhanced_ops["async_rest"].async_job == ir.operations[4].async_job
        assert enhanced_ops["async_rest"].request_body_mode == RequestBodyMode.raw
        assert enhanced_ops["async_rest"].body_param_name == "payload"
        assert enhanced_ops["async_rest"].tags == ["exports"]
        assert result.enhanced_ir.event_descriptors == ir.event_descriptors
        assert result.enhanced_ir.metadata == ir.metadata

    def test_llm_failure_returns_original_ir(self):
        ir = make_raw_ir(2)
        client = MockLLMClient(fail=True)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)

        assert result.operations_enhanced == 0
        assert result.enhanced_ir is ir  # same object — unchanged

    def test_token_usage_tracked(self):
        ir = make_raw_ir(2)
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)

        assert result.token_usage.input_tokens > 0
        assert result.token_usage.output_tokens > 0
        assert result.token_usage.total_calls >= 1

    def test_skip_operations_with_good_descriptions(self):
        ir = make_raw_ir(2)
        # Give one operation a good description
        good_op = Operation(
            id="good_op",
            name="Good Operation",
            description=(
                "This operation retrieves user data with full detail and pagination support"
            ),
            method="GET",
            path="/good",
            params=[
                Param(
                    name="id",
                    type="integer",
                    required=True,
                    description="The unique user identifier to look up",
                    confidence=0.9,
                )
            ],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            enabled=True,
        )
        ir_with_good = ServiceIR(
            source_hash="abc123",
            protocol="openapi",
            service_name="test-api",
            base_url="https://api.example.com",
            operations=[good_op] + list(ir.operations),
        )

        # LLM response only for the ops that need enhancement
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=True)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir_with_good)

        # The good_op should be skipped (kept unchanged)
        assert result.operations_skipped >= 1
        good_result = next(op for op in result.enhanced_ir.operations if op.id == "good_op")
        assert good_result.source == SourceType.extractor  # not changed to llm

    def test_malformed_llm_response_handled(self):
        ir = make_raw_ir(1)
        client = MockLLMClient(response="not valid json at all")
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)
        # Should not crash — returns original IR
        assert result.operations_enhanced == 0

    def test_llm_response_with_markdown_fences(self):
        ir = make_raw_ir(1)
        inner_json = make_llm_response(ir.operations)
        fenced = f"```json\n{inner_json}\n```"
        client = MockLLMClient(response=fenced)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)
        assert result.operations_enhanced == 1

    def test_batch_processing(self):
        ir = make_raw_ir(15)  # more than batch_size=10
        mock_response = make_llm_response(ir.operations[:10])
        mock_response2 = make_llm_response(ir.operations[10:])

        class BatchMockClient:
            def __init__(self):
                self.calls = []

            def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
                self.calls.append(prompt)
                # Return different responses for each batch
                if len(self.calls) == 1:
                    return LLMResponse(content=mock_response, input_tokens=200, output_tokens=100)
                return LLMResponse(content=mock_response2, input_tokens=100, output_tokens=50)

        client = BatchMockClient()
        config = EnhancerConfig(skip_if_description_exists=False, batch_size=10)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir)
        assert len(client.calls) == 2  # two batches
        assert result.token_usage.total_calls == 2


class TestLLMClientFactory:
    def test_vertexai_provider_does_not_require_api_key(self):
        config = EnhancerConfig(
            provider=LLMProvider.vertexai,
            model="gemini-2.0-flash",
            vertex_project="test-project",
            vertex_location="us-central1",
        )

        factory_client = create_llm_client(config)
        assert factory_client.__class__.__name__ == "VertexAILLMClient"

    def test_deepseek_provider_uses_official_base_url(self):
        config = EnhancerConfig(
            provider=LLMProvider.deepseek,
            model="deepseek-chat",
            api_key="test-key",
        )

        factory_client = create_llm_client(config)
        assert factory_client.__class__.__name__ == "OpenAILLMClient"
        assert getattr(factory_client, "base_url") == "https://api.deepseek.com"

    def test_openai_client_passes_configured_base_url(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeCompletions:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured["completion_kwargs"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
                )

        class FakeOpenAIClient:
            def __init__(self, **kwargs: object) -> None:
                captured["client_kwargs"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())

        fake_openai = SimpleNamespace(OpenAI=FakeOpenAIClient)
        monkeypatch.setattr(
            "libs.enhancer.enhancer.import_module",
            lambda module_name: fake_openai if module_name == "openai" else None,
        )

        config = EnhancerConfig(
            provider=LLMProvider.deepseek,
            model="deepseek-chat",
            api_key="test-key",
            api_base_url="https://api.deepseek.com",
        )
        client = create_llm_client(config)
        response = client.complete("hello", max_tokens=64)

        assert captured["client_kwargs"] == {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
        }
        assert captured["completion_kwargs"] == {
            "model": "deepseek-chat",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        }
        assert response.content == "[]"

    def test_ir_metadata_preserved(self):
        ir = make_raw_ir(1)
        ir_with_meta = ServiceIR(
            source_hash=ir.source_hash,
            protocol=ir.protocol,
            service_name=ir.service_name,
            base_url=ir.base_url,
            operations=ir.operations,
            tenant="acme",
            environment="staging",
            metadata={"openapi_version": "3.0.0"},
        )
        mock_response = make_llm_response(ir.operations)
        client = MockLLMClient(response=mock_response)
        config = EnhancerConfig(skip_if_description_exists=False)
        enhancer = IREnhancer(client=client, config=config)

        result = enhancer.enhance(ir_with_meta)
        assert result.enhanced_ir.tenant == "acme"
        assert result.enhanced_ir.environment == "staging"
        assert result.enhanced_ir.metadata["openapi_version"] == "3.0.0"


class TestTokenUsage:
    def test_add_tokens(self):
        usage = TokenUsage(model="test-model")
        usage.add(100, 50)
        usage.add(200, 100)
        assert usage.input_tokens == 300
        assert usage.output_tokens == 150
        assert usage.total_tokens == 450
        assert usage.total_calls == 2


class TestEnhancerConfig:
    def test_default_config(self):
        config = EnhancerConfig()
        assert config.provider == LLMProvider.openai
        assert config.model == "gpt-4o-mini"
        assert config.max_tokens_per_job == 50_000

    def test_from_env_uses_deepseek_defaults(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "test-key")

        config = EnhancerConfig.from_env()

        assert config.provider == LLMProvider.deepseek
        assert config.model == "deepseek-chat"
        assert config.api_base_url == "https://api.deepseek.com"

    def test_from_env_strips_secret_file_whitespace(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", " deepseek \n")
        monkeypatch.setenv("LLM_MODEL", " deepseek-chat \n")
        monkeypatch.setenv("LLM_API_KEY", " test-key \n")
        monkeypatch.setenv("LLM_API_BASE_URL", " https://api.deepseek.com \n")
        monkeypatch.setenv("VERTEX_PROJECT_ID", " demo-project \n")
        monkeypatch.setenv("VERTEX_LOCATION", " europe-west1 \n")

        config = EnhancerConfig.from_env()

        assert config.provider == LLMProvider.deepseek
        assert config.model == "deepseek-chat"
        assert config.api_key == "test-key"
        assert config.api_base_url == "https://api.deepseek.com"
        assert config.vertex_project == "demo-project"
        assert config.vertex_location == "europe-west1"

    def test_from_env_can_force_enhancement_even_with_existing_descriptions(
        self,
        monkeypatch,
    ):
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_SKIP_IF_DESCRIPTION_EXISTS", "false")

        config = EnhancerConfig.from_env()

        assert config.provider == LLMProvider.deepseek
        assert config.skip_if_description_exists is False
