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
        operations.append(
            Operation(
                id=f"op_{i}",
                name=f"Operation {i}",
                description="",  # empty — needs enhancement
                method="GET",
                path=f"/endpoint_{i}",
                params=[
                    Param(name="id", type="integer", required=True, description="", confidence=0.9),
                    Param(
                        name="filter", type="string", required=False, description="", confidence=0.9
                    ),
                ],
                risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
                source=SourceType.extractor,
                confidence=0.9,
                enabled=True,
            )
        )
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
        result.append(
            {
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
            }
        )
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


def test_getenv_stripped_returns_none_for_missing_env():
    """Test _getenv_stripped returns None for missing environment variables."""
    import os

    from libs.enhancer.enhancer import _getenv_stripped

    # Ensure env var doesn't exist
    if "NONEXISTENT_VAR_TEST" in os.environ:
        del os.environ["NONEXISTENT_VAR_TEST"]

    result = _getenv_stripped("NONEXISTENT_VAR_TEST")
    assert result is None


def test_getenv_stripped_returns_default_for_missing_env():
    """Test _getenv_stripped returns default for missing environment variables."""
    import os

    from libs.enhancer.enhancer import _getenv_stripped

    # Ensure env var doesn't exist
    if "NONEXISTENT_VAR_TEST" in os.environ:
        del os.environ["NONEXISTENT_VAR_TEST"]

    result = _getenv_stripped("NONEXISTENT_VAR_TEST", "default_value")
    assert result == "default_value"


def test_getenv_stripped_returns_default_for_empty_env():
    """Test _getenv_stripped returns default for empty environment variables."""
    import os

    from libs.enhancer.enhancer import _getenv_stripped

    os.environ["EMPTY_VAR_TEST"] = "   "
    result = _getenv_stripped("EMPTY_VAR_TEST", "default_value")
    assert result == "default_value"


def test_getenv_bool_returns_default_for_missing_env():
    """Test _getenv_bool returns default for missing environment variables."""
    import os

    from libs.enhancer.enhancer import _getenv_bool

    # Ensure env var doesn't exist
    if "NONEXISTENT_BOOL_VAR" in os.environ:
        del os.environ["NONEXISTENT_BOOL_VAR"]

    result = _getenv_bool("NONEXISTENT_BOOL_VAR", default=True)
    assert result is True


def test_getenv_bool_returns_default_for_invalid_value():
    """Test _getenv_bool returns default for invalid boolean values."""
    import os

    from libs.enhancer.enhancer import _getenv_bool

    os.environ["INVALID_BOOL_VAR"] = "maybe"
    result = _getenv_bool("INVALID_BOOL_VAR", default=False)
    assert result is False


def test_vertex_ai_llm_client_with_project():
    """Test VertexAILLMClient initialization with project."""
    from libs.enhancer.enhancer import VertexAILLMClient

    client = VertexAILLMClient(project="test-project")
    assert client.project == "test-project"
    assert client.model == "gemini-2.0-flash"
    assert client.location == "us-central1"


def test_vertex_ai_llm_client_complete_mocked(monkeypatch):
    """Test VertexAILLMClient.complete method."""
    from types import SimpleNamespace

    from libs.enhancer.enhancer import VertexAILLMClient

    # Mock the vertexai modules
    fake_usage = SimpleNamespace()
    fake_usage.prompt_token_count = 150
    fake_usage.candidates_token_count = 75

    fake_response = SimpleNamespace()
    fake_response.text = "test response"
    fake_response.usage_metadata = fake_usage

    fake_model = SimpleNamespace()
    fake_model.generate_content = lambda prompt, generation_config: fake_response

    fake_generative_models = SimpleNamespace()
    fake_generative_models.GenerativeModel = lambda model: fake_model

    fake_vertexai = SimpleNamespace()
    fake_vertexai.init = lambda **kwargs: None

    def fake_import_module(name):
        if name == "vertexai":
            return fake_vertexai
        elif name == "vertexai.generative_models":
            return fake_generative_models
        return None

    monkeypatch.setattr("libs.enhancer.enhancer.import_module", fake_import_module)

    client = VertexAILLMClient(project="test-project")
    response = client.complete("test prompt", max_tokens=2048)

    assert response.content == "test response"
    assert response.input_tokens == 150
    assert response.output_tokens == 75


def test_create_llm_client_raises_for_missing_api_key():
    """Test create_llm_client raises ValueError when api_key is required but missing."""
    from libs.enhancer.enhancer import EnhancerConfig, LLMProvider, create_llm_client

    config = EnhancerConfig(provider=LLMProvider.anthropic, api_key=None)

    try:
        create_llm_client(config)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "LLM_API_KEY is required" in str(e)


def test_create_llm_client_anthropic():
    """Test create_llm_client creates AnthropicLLMClient."""
    from libs.enhancer.enhancer import EnhancerConfig, LLMProvider, create_llm_client

    config = EnhancerConfig(provider=LLMProvider.anthropic, api_key="test-key")
    client = create_llm_client(config)

    assert client.__class__.__name__ == "AnthropicLLMClient"


def test_create_llm_client_openai():
    """Test create_llm_client creates OpenAILLMClient for OpenAI provider."""
    from libs.enhancer.enhancer import EnhancerConfig, LLMProvider, create_llm_client

    config = EnhancerConfig(
        provider=LLMProvider.openai, api_key="test-key", api_base_url="https://api.openai.com/v1"
    )
    client = create_llm_client(config)

    assert client.__class__.__name__ == "OpenAILLMClient"


def test_create_llm_client_raises_for_unsupported_provider():
    """Test create_llm_client raises ValueError for unsupported provider."""
    from libs.enhancer.enhancer import EnhancerConfig, create_llm_client

    # Create a fake provider that doesn't exist
    class FakeProvider:
        pass

    config = EnhancerConfig(provider=FakeProvider(), api_key="test-key")

    try:
        create_llm_client(config)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unsupported LLM provider" in str(e)


def test_default_model_for_provider():
    """Test _default_model_for_provider returns correct models."""
    from libs.enhancer.enhancer import LLMProvider, _default_model_for_provider

    assert _default_model_for_provider(LLMProvider.anthropic) == "claude-sonnet-4-20250514"
    assert _default_model_for_provider(LLMProvider.deepseek) == "deepseek-chat"
    assert _default_model_for_provider(LLMProvider.vertexai) == "gemini-2.0-flash"
    assert _default_model_for_provider(LLMProvider.openai) == "gpt-4o-mini"


def test_default_api_base_url_for_provider():
    """Test _default_api_base_url_for_provider returns correct URLs."""
    from libs.enhancer.enhancer import LLMProvider, _default_api_base_url_for_provider

    assert _default_api_base_url_for_provider(LLMProvider.deepseek) == "https://api.deepseek.com"
    assert _default_api_base_url_for_provider(LLMProvider.openai) is None
    assert _default_api_base_url_for_provider(LLMProvider.anthropic) is None


def test_enhancer_skips_all_operations_with_good_descriptions():
    """Test enhancer returns early when all operations have good descriptions."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    # Create IR with all good descriptions
    good_op = Operation(
        id="good_op",
        name="Good Operation",
        description="This operation retrieves user data with full details",
        method="GET",
        path="/good",
        params=[
            Param(
                name="id",
                type="integer",
                required=True,
                description="The unique user identifier",
                confidence=0.9,
            )
        ],
        risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
        enabled=True,
    )

    ir = ServiceIR(
        source_hash="abc123",
        protocol="openapi",
        service_name="test-api",
        base_url="https://api.example.com",
        operations=[good_op],
    )

    client = MockLLMClient()
    config = EnhancerConfig(skip_if_description_exists=True)
    enhancer = IREnhancer(client=client, config=config)

    result = enhancer.enhance(ir)

    # Should skip all operations and not call LLM
    assert len(client.calls) == 0
    assert result.operations_enhanced == 0
    assert result.operations_skipped == 1


def test_enhancer_handles_llm_batch_failure():
    """Test enhancer continues with other batches when one fails."""
    ir = make_raw_ir(5)

    class FailingBatchClient:
        def __init__(self):
            self.calls = []

        def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
            self.calls.append(prompt)
            if len(self.calls) == 1:
                raise RuntimeError("First batch failed")
            # Second batch succeeds
            return LLMResponse(
                content=make_llm_response(ir.operations[2:]), input_tokens=100, output_tokens=50
            )

    client = FailingBatchClient()
    config = EnhancerConfig(skip_if_description_exists=False, batch_size=2)
    enhancer = IREnhancer(client=client, config=config)

    enhancer.enhance(ir)

    # Should have made multiple calls despite one failure
    assert len(client.calls) >= 2


def test_enhancer_returns_original_when_no_enhancements():
    """Test enhancer returns original IR when no enhancements are produced."""
    ir = make_raw_ir(2)

    class NoEnhancementsClient:
        def __init__(self):
            self.calls = []

        def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
            self.calls.append(prompt)
            # Return empty or invalid response
            return LLMResponse(content="[]", input_tokens=100, output_tokens=50)

    client = NoEnhancementsClient()
    config = EnhancerConfig(skip_if_description_exists=False)
    enhancer = IREnhancer(client=client, config=config)

    result = enhancer.enhance(ir)

    assert result.operations_enhanced == 0
    assert result.enhanced_ir is ir  # Should be original object


def test_enhancer_respects_token_budget():
    """Test enhancer skips batches when token budget is exhausted."""
    ir = make_raw_ir(2)

    class TokenConsumingClient:
        def __init__(self):
            self.calls = []

        def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
            self.calls.append(prompt)
            # Return a response that uses many tokens
            return LLMResponse(content="[]", input_tokens=1000, output_tokens=500)

    client = TokenConsumingClient()
    # Set very low token budget
    config = EnhancerConfig(skip_if_description_exists=False, max_tokens_per_job=1200)
    enhancer = IREnhancer(client=client, config=config)

    enhancer.enhance(ir)

    # Should only make one call due to token budget
    assert len(client.calls) == 1


def test_parse_llm_response_handles_non_list_response():
    """Test _parse_llm_response handles non-list JSON responses."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    client = MockLLMClient()
    config = EnhancerConfig()
    enhancer = IREnhancer(client=client, config=config)

    # Test with dict instead of list
    result = enhancer._parse_llm_response('{"error": "not a list"}')
    assert result == {}


def test_parse_llm_response_handles_markdown_with_tildes():
    """Test _parse_llm_response handles markdown fences with tildes."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    client = MockLLMClient()
    config = EnhancerConfig()
    enhancer = IREnhancer(client=client, config=config)

    json_content = '[{"operation_id": "test", "description": "test"}]'
    fenced_content = f"~~~json\n{json_content}\n~~~"

    result = enhancer._parse_llm_response(fenced_content)
    assert "test" in result


def test_apply_enhancements_handles_non_dict_enhancement():
    """Test _apply_enhancements handles non-dict enhancement values."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    ir = make_raw_ir(1)
    client = MockLLMClient()
    config = EnhancerConfig()
    enhancer = IREnhancer(client=client, config=config)

    # Pass non-dict value
    enhancements = {ir.operations[0].id: "not a dict"}

    new_ir = enhancer._apply_enhancements(ir, enhancements)

    # Should keep original operation unchanged
    assert new_ir.operations[0].description == ir.operations[0].description


def test_apply_enhancements_handles_invalid_confidence():
    """Test _apply_enhancements handles invalid confidence values."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    ir = make_raw_ir(1)
    client = MockLLMClient()
    config = EnhancerConfig()
    enhancer = IREnhancer(client=client, config=config)

    enhancements = {
        ir.operations[0].id: {
            "description": "Enhanced description",
            "confidence": "not a number",  # Invalid confidence
            "params": [
                {
                    "name": ir.operations[0].params[0].name,
                    "description": "Enhanced param",
                    "confidence": "also invalid",
                }
            ],
        }
    }

    new_ir = enhancer._apply_enhancements(ir, enhancements)

    # Should use default confidence values
    enhanced_op = new_ir.operations[0]
    assert enhanced_op.confidence == 0.7  # Default
    assert enhanced_op.params[0].confidence == 0.7  # Default


# ── Additional coverage tests ──────────────────────────────────────────────


def test_getenv_bool_returns_false_for_false_values():
    """Test _getenv_bool returns False when env var is set to false/no/0/off (line 99)."""
    import os

    from libs.enhancer.enhancer import _getenv_bool

    for false_value in ("false", "no", "0", "off", "False", "NO", "OFF"):
        os.environ["TEST_BOOL_FALSE"] = false_value
        result = _getenv_bool("TEST_BOOL_FALSE", default=True)
        assert result is False, f"Expected False for '{false_value}', got {result}"

    # Cleanup
    os.environ.pop("TEST_BOOL_FALSE", None)


def test_select_operations_picks_op_with_short_param_description():
    """_select_operations picks ops with long description but short param text (403-404)."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    # Long description (>= 20 chars) but one param still has short description (< 10).
    op = Operation(
        id="op_with_short_param",
        name="GetUser",
        description="This operation retrieves user data from the backend service",
        method="GET",
        path="/users/{id}",
        params=[
            Param(name="id", type="integer", required=True, description="short", confidence=0.9),
        ],
        risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
        source=SourceType.extractor,
        confidence=0.9,
        enabled=True,
    )
    ir = ServiceIR(
        source_hash="hash1",
        protocol="rest",
        service_name="test-api",
        base_url="https://api.example.com",
        operations=[op],
    )

    client = MockLLMClient()
    config = EnhancerConfig(skip_if_description_exists=True)
    enhancer = IREnhancer(client=client, config=config)

    selected = enhancer._select_operations(ir)
    assert len(selected) == 1
    assert selected[0].id == "op_with_short_param"


def test_batch_operations_splits_correctly():
    """_batch_operations splits large op lists into batches (lines 157-164)."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    ir = make_raw_ir(7)

    # Use response that covers all 7 ops so enhance actually completes
    response_json = make_llm_response(ir.operations)
    client = MockLLMClient(response=response_json)
    config = EnhancerConfig(skip_if_description_exists=False, batch_size=3)
    enhancer = IREnhancer(client=client, config=config)

    result = enhancer.enhance(ir)

    # 7 ops with batch_size=3 → 3 batches (3 + 3 + 1)
    assert len(client.calls) == 3
    assert result.operations_enhanced > 0


def test_enhance_batch_skips_when_token_budget_exhausted():
    """Test _enhance_batch returns {} when token budget is already exceeded (lines 442-443)."""
    from libs.enhancer.enhancer import EnhancerConfig, IREnhancer

    ir = make_raw_ir(2)
    client = MockLLMClient(response=make_llm_response(ir.operations))
    config = EnhancerConfig(skip_if_description_exists=False, max_tokens_per_job=10)
    enhancer = IREnhancer(client=client, config=config)

    # Artificially exhaust the token budget before calling _enhance_batch
    enhancer.token_usage.add(input_tokens=100, output_tokens=100)

    batch = list(ir.operations)
    result = enhancer._enhance_batch(ir, batch)

    assert result == {}
    # Should NOT have called the LLM
    assert len(client.calls) == 0
