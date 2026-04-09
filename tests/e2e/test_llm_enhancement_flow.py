"""E2E: LLM enhancement pipeline — sync, async, concurrency, and token budget.

Tests the full enhancement lifecycle from IR creation through both sync and
async enhancement paths, validating concurrency semantics, token budgets,
batch sizing, and the end-to-end production wiring (enhance_stage).

No real LLM calls — uses deterministic mock clients throughout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from libs.enhancer.enhancer import (
    EnhancerConfig,
    IREnhancer,
    LLMProvider,
    LLMResponse,
    TokenUsage,
)
from libs.enhancer.examples_generator import ExamplesGenerator
from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor
from libs.ir.models import ServiceIR

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_fixture(relative_path: str) -> str:
    return (_FIXTURES / relative_path).read_text()


def _extract_openapi(relative_path: str) -> ServiceIR:
    content = _load_fixture(relative_path)
    source = SourceConfig(file_content=content, hints={"protocol": "openapi"})
    extractor = OpenAPIExtractor()
    return extractor.extract(source)


def _make_config(
    *,
    batch_size: int = 10,
    max_concurrent_batches: int = 5,
    max_tokens: int = 50_000,
) -> EnhancerConfig:
    return EnhancerConfig(
        provider=LLMProvider.openai,
        model="test-model",
        api_key="test-key",
        batch_size=batch_size,
        max_concurrent_batches=max_concurrent_batches,
        max_tokens_per_job=max_tokens,
    )


class _MockLLMClient:
    """Deterministic mock that returns valid enhancement LLMResponse."""

    def __init__(self) -> None:
        self.call_count = 0
        self.concurrent_calls = 0
        self.max_concurrent_calls = 0
        self._lock = asyncio.Lock()

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(content='{"operations": []}', input_tokens=10, output_tokens=5)

    async def complete_async(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        async with self._lock:
            self.concurrent_calls += 1
            if self.concurrent_calls > self.max_concurrent_calls:
                self.max_concurrent_calls = self.concurrent_calls
        await asyncio.sleep(0.01)
        async with self._lock:
            self.concurrent_calls -= 1
        self.call_count += 1
        return LLMResponse(content='{"operations": []}', input_tokens=10, output_tokens=5)


class TestExtractThenEnhance:
    """Full pipeline: OpenAPI extraction -> sync enhancement."""

    async def test_petstore_extract_and_enhance(self) -> None:
        ir = _extract_openapi("openapi_specs/petstore_3_0.yaml")
        assert len(ir.operations) > 0
        original_count = len(ir.operations)

        client = _MockLLMClient()
        enhancer = IREnhancer(client=client, config=_make_config())
        result = enhancer.enhance(ir)

        assert len(result.enhanced_ir.operations) == original_count
        assert isinstance(result.token_usage, TokenUsage)
        assert client.call_count > 0

    async def test_large_surface_extract_and_enhance(self) -> None:
        ir = _extract_openapi("openapi_specs/large_surface_api.yaml")
        assert len(ir.operations) >= 50

        client = _MockLLMClient()
        config = _make_config(batch_size=10)
        enhancer = IREnhancer(client=client, config=config)
        result = enhancer.enhance(ir)

        ops_needing_enhancement = [
            op
            for op in ir.operations
            if len(op.description) < 20 or any(len(p.description) < 10 for p in op.params)
        ]
        expected_batches = (len(ops_needing_enhancement) + 9) // 10
        assert client.call_count == expected_batches
        assert len(result.enhanced_ir.operations) == len(ir.operations)


class TestAsyncEnhancementPipeline:
    """Async enhancement path with concurrency control."""

    async def test_async_enhance_respects_concurrency(self) -> None:
        ir = _extract_openapi("openapi_specs/large_surface_api.yaml")

        max_concurrent = 3
        client = _MockLLMClient()
        enhancer = IREnhancer(
            client=client,
            config=_make_config(batch_size=5, max_concurrent_batches=max_concurrent),
        )
        result = await enhancer.enhance_async(ir)

        assert len(result.enhanced_ir.operations) == len(ir.operations)
        assert client.max_concurrent_calls <= max_concurrent
        ops_needing_enhancement = [
            op
            for op in ir.operations
            if len(op.description) < 20 or any(len(p.description) < 10 for p in op.params)
        ]
        expected_batches = (len(ops_needing_enhancement) + 4) // 5
        assert client.call_count == expected_batches

    async def test_async_enhance_single_concurrency(self) -> None:
        ir = _extract_openapi("openapi_specs/petstore_3_0.yaml")

        client = _MockLLMClient()
        enhancer = IREnhancer(client=client, config=_make_config(max_concurrent_batches=1))
        result = await enhancer.enhance_async(ir)

        assert len(result.enhanced_ir.operations) == len(ir.operations)
        assert client.max_concurrent_calls <= 1

    async def test_async_enhance_preserves_ir_integrity(self) -> None:
        ir = _extract_openapi("openapi_specs/petstore_3_0.yaml")
        original_op_ids = {op.id for op in ir.operations}

        client = _MockLLMClient()
        enhancer = IREnhancer(client=client, config=_make_config())
        result = await enhancer.enhance_async(ir)

        enhanced_op_ids = {op.id for op in result.enhanced_ir.operations}
        assert enhanced_op_ids == original_op_ids


class TestTokenBudgetEnforcement:
    """Enhancement respects token budget limits."""

    async def test_zero_budget_skips_enhancement(self) -> None:
        ir = _extract_openapi("openapi_specs/petstore_3_0.yaml")

        client = _MockLLMClient()
        enhancer = IREnhancer(client=client, config=_make_config(max_tokens=0))
        result = enhancer.enhance(ir)

        assert client.call_count == 0
        assert len(result.enhanced_ir.operations) == len(ir.operations)


class TestExamplesGeneratorAsync:
    """Async examples generation with parallel per-operation LLM calls."""

    async def test_generate_async_for_extracted_ir(self) -> None:
        ir = _extract_openapi("openapi_specs/petstore_3_0.yaml")

        client = _MockLLMClient()
        gen = ExamplesGenerator(llm_client=client, max_concurrency=3)
        result_ir = await gen.generate_async(ir)

        assert len(result_ir.operations) == len(ir.operations)


class TestTokenUsageConcurrency:
    """TokenUsage.add_async is safe under concurrent updates."""

    async def test_concurrent_add_async_no_lost_updates(self) -> None:
        usage = TokenUsage()

        async def add_tokens(n: int) -> None:
            await usage.add_async(n, n)

        tasks = [add_tokens(10) for _ in range(100)]
        await asyncio.gather(*tasks)

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 1000


class TestEnhanceStageWiring:
    """The production enhance_stage correctly invokes async enhancement."""

    async def test_post_enhancement_async_produces_valid_ir(self) -> None:
        ir = _extract_openapi("openapi_specs/petstore_3_0.yaml")

        from apps.compiler_worker.activities.production import (
            _apply_post_enhancement_async,
        )

        result = await _apply_post_enhancement_async(ir)
        assert isinstance(result, ServiceIR)
        assert len(result.operations) == len(ir.operations)
