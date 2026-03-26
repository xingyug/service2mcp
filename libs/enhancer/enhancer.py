"""LLM Enhancer — improves IR descriptions using LLM APIs.

Takes a raw ServiceIR and returns an enhanced copy with improved
operation and parameter descriptions.  All LLM-contributed fields are
tagged with source="llm" and a confidence score.  Structural fields
(names, types, IDs) are never modified.

Supports Anthropic, OpenAI, and Vertex AI providers.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import Any, Protocol

from libs.ir.models import Operation, Param, ServiceIR, SourceType

logger = logging.getLogger(__name__)


# ── Configuration ──────────────────────────────────────────────────────────


class LLMProvider(StrEnum):
    anthropic = "anthropic"
    deepseek = "deepseek"
    openai = "openai"
    vertexai = "vertexai"


@dataclass
class EnhancerConfig:
    """Configuration for the IR enhancer."""

    provider: LLMProvider = LLMProvider.openai
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    api_base_url: str | None = None
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    max_tokens_per_job: int = 50_000
    temperature: float = 0.3
    batch_size: int = 10  # operations per LLM call
    skip_if_description_exists: bool = True  # skip ops with good descriptions

    @classmethod
    def from_env(cls) -> EnhancerConfig:
        """Create config from environment variables."""
        provider_str = _getenv_stripped("LLM_PROVIDER") or "openai"
        provider = LLMProvider(provider_str)
        model = _getenv_stripped("LLM_MODEL") or _default_model_for_provider(provider)
        api_base_url = _getenv_stripped("LLM_API_BASE_URL")
        if provider is LLMProvider.deepseek and not api_base_url:
            api_base_url = _getenv_stripped(
                "DEEPSEEK_API_BASE_URL",
                _default_api_base_url_for_provider(provider),
            )
        vertex_location = _getenv_stripped("VERTEX_LOCATION") or "us-central1"
        max_tokens_per_job = int(_getenv_stripped("LLM_MAX_TOKENS_PER_JOB") or "50000")
        skip_if_description_exists = _getenv_bool(
            "LLM_SKIP_IF_DESCRIPTION_EXISTS",
            default=True,
        )
        return cls(
            provider=provider,
            model=model,
            api_key=_getenv_stripped("LLM_API_KEY"),
            api_base_url=api_base_url,
            vertex_project=_getenv_stripped("VERTEX_PROJECT_ID"),
            vertex_location=vertex_location,
            max_tokens_per_job=max_tokens_per_job,
            skip_if_description_exists=skip_if_description_exists,
        )


def _getenv_stripped(name: str, default: str | None = None) -> str | None:
    """Read an env var and trim secret-file newlines and incidental whitespace."""
    value = os.environ.get(name)
    if value is None:
        return default
    stripped = value.strip()
    if not stripped:
        return default
    return stripped


def _getenv_bool(name: str, *, default: bool) -> bool:
    value = _getenv_stripped(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


# ── Token Usage Tracking ──────────────────────────────────────────────────


@dataclass
class TokenUsage:
    """Tracks LLM token consumption for a single enhancement job."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    total_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_calls += 1


# ── LLM Client Protocol ───────────────────────────────────────────────────


class LLMClient(Protocol):
    """Interface for LLM API clients."""

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        """Send a prompt and return the response."""
        ...


@dataclass
class LLMResponse:
    """Response from an LLM API call."""

    content: str
    input_tokens: int = 0
    output_tokens: int = 0


# ── Concrete LLM Clients ──────────────────────────────────────────────────


class AnthropicLLMClient:
    """LLM client using the Anthropic API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514") -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        anthropic = import_module("anthropic")
        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class OpenAILLMClient:
    """LLM client using the OpenAI API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        *,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        openai = import_module("openai")
        client_kwargs: dict[str, str] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = openai.OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        return LLMResponse(
            content=content,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


class VertexAILLMClient:
    """LLM client using Vertex AI generative models."""

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        project: str | None = None,
        location: str = "us-central1",
    ) -> None:
        self.model = model
        self.project = project
        self.location = location

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        vertexai = import_module("vertexai")
        generative_models = import_module("vertexai.generative_models")

        init_kwargs: dict[str, str] = {"location": self.location}
        if self.project:
            init_kwargs["project"] = self.project
        vertexai.init(**init_kwargs)

        model = generative_models.GenerativeModel(self.model)
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens},
        )
        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            content=getattr(response, "text", "") or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
        )


def create_llm_client(config: EnhancerConfig) -> LLMClient:
    """Factory for LLM clients based on config."""
    if config.provider == LLMProvider.vertexai:
        return VertexAILLMClient(
            model=config.model,
            project=config.vertex_project,
            location=config.vertex_location,
        )

    if not config.api_key:
        raise ValueError("LLM_API_KEY is required for enhancement")

    if config.provider == LLMProvider.anthropic:
        return AnthropicLLMClient(api_key=config.api_key, model=config.model)
    if config.provider == LLMProvider.deepseek:
        return OpenAILLMClient(
            api_key=config.api_key,
            model=config.model,
            base_url=config.api_base_url or _default_api_base_url_for_provider(config.provider),
        )
    if config.provider == LLMProvider.openai:
        return OpenAILLMClient(
            api_key=config.api_key,
            model=config.model,
            base_url=config.api_base_url,
        )

    raise ValueError(f"Unsupported LLM provider: {config.provider}")


# ── Enhancement Prompt ─────────────────────────────────────────────────────

ENHANCE_PROMPT_TEMPLATE = """\
You are an API documentation expert. Given the following API operations, \
improve their descriptions to be clear, concise, and useful for an AI agent \
that needs to decide which tool to call.

Rules:
1. Keep descriptions factual — describe what the operation DOES, not what it IS.
2. For parameters, explain what values are expected and their effect.
3. Return ONLY valid JSON matching the schema below. No markdown, no explanation.
4. Include a "confidence" field (0.0-1.0) for each description indicating your certainty.
5. Do NOT change operation IDs, parameter names, or parameter types.

Service: {service_name} ({protocol})
Base URL: {base_url}

Operations to enhance:
{operations_json}

Return JSON array:
[
  {{
    "operation_id": "<id>",
    "description": "<improved description>",
    "confidence": 0.85,
    "params": [
      {{
        "name": "<param_name>",
        "description": "<improved param description>",
        "confidence": 0.8
      }}
    ]
  }}
]
"""


def _default_model_for_provider(provider: LLMProvider) -> str:
    if provider is LLMProvider.anthropic:
        return "claude-sonnet-4-20250514"
    if provider is LLMProvider.deepseek:
        return "deepseek-chat"
    if provider is LLMProvider.vertexai:
        return "gemini-2.0-flash"
    return "gpt-4o-mini"


def _default_api_base_url_for_provider(provider: LLMProvider) -> str | None:
    if provider is LLMProvider.deepseek:
        return "https://api.deepseek.com"
    return None


# ── IR Enhancer ────────────────────────────────────────────────────────────


@dataclass
class EnhancementResult:
    """Result of an IR enhancement job."""

    enhanced_ir: ServiceIR
    token_usage: TokenUsage
    operations_enhanced: int
    operations_skipped: int


class IREnhancer:
    """Enhances a ServiceIR with LLM-generated descriptions."""

    def __init__(self, client: LLMClient, config: EnhancerConfig | None = None) -> None:
        self.client = client
        self.config = config or EnhancerConfig()
        self.token_usage = TokenUsage(model=self.config.model)

    def enhance(self, ir: ServiceIR) -> EnhancementResult:
        """Enhance a ServiceIR with improved descriptions.

        Returns the enhanced IR along with usage stats.  On LLM failure,
        returns the original IR unchanged with zero enhancements.
        """
        self.token_usage = TokenUsage(model=self.config.model)

        # Identify operations that need enhancement
        ops_to_enhance = self._select_operations(ir)
        if not ops_to_enhance:
            logger.info("All operations already have good descriptions — skipping enhancement")
            return EnhancementResult(
                enhanced_ir=ir,
                token_usage=self.token_usage,
                operations_enhanced=0,
                operations_skipped=len(ir.operations),
            )

        # Batch operations and call LLM
        enhancements: dict[str, dict[str, Any]] = {}
        for batch in self._batch_operations(ops_to_enhance):
            try:
                batch_result = self._enhance_batch(ir, batch)
                enhancements.update(batch_result)
            except Exception:
                logger.warning("LLM enhancement failed for batch", exc_info=True)
                # Continue with remaining batches

        if not enhancements:
            logger.warning("No enhancements produced — returning original IR")
            return EnhancementResult(
                enhanced_ir=ir,
                token_usage=self.token_usage,
                operations_enhanced=0,
                operations_skipped=len(ir.operations),
            )

        # Apply enhancements to a copy of the IR
        enhanced_ir = self._apply_enhancements(ir, enhancements)

        return EnhancementResult(
            enhanced_ir=enhanced_ir,
            token_usage=self.token_usage,
            operations_enhanced=len(enhancements),
            operations_skipped=len(ir.operations) - len(enhancements),
        )

    def _select_operations(self, ir: ServiceIR) -> list[Operation]:
        """Select operations that need enhancement."""
        if not self.config.skip_if_description_exists:
            return list(ir.operations)

        needs_enhancement = []
        for op in ir.operations:
            # Enhance if description is empty/short or params lack descriptions
            if len(op.description) < 20:
                needs_enhancement.append(op)
            elif any(len(p.description) < 10 for p in op.params):
                needs_enhancement.append(op)
        return needs_enhancement

    def _batch_operations(self, operations: list[Operation]) -> list[list[Operation]]:
        """Split operations into batches."""
        batch_size = self.config.batch_size
        return [operations[i : i + batch_size] for i in range(0, len(operations), batch_size)]

    def _enhance_batch(
        self, ir: ServiceIR, batch: list[Operation]
    ) -> dict[str, dict[str, Any]]:
        """Call LLM to enhance a batch of operations."""
        # Build ops summary for the prompt
        ops_json = json.dumps(
            [
                {
                    "operation_id": op.id,
                    "name": op.name,
                    "description": op.description,
                    "method": op.method,
                    "path": op.path,
                    "params": [
                        {"name": p.name, "type": p.type, "description": p.description}
                        for p in op.params
                    ],
                }
                for op in batch
            ],
            indent=2,
        )

        prompt = ENHANCE_PROMPT_TEMPLATE.format(
            service_name=ir.service_name,
            protocol=ir.protocol,
            base_url=ir.base_url,
            operations_json=ops_json,
        )

        # Check token budget
        if self.token_usage.total_tokens >= self.config.max_tokens_per_job:
            logger.warning("Token budget exhausted — skipping batch")
            return {}

        response = self.client.complete(prompt, max_tokens=4096)
        self.token_usage.add(response.input_tokens, response.output_tokens)

        # Parse LLM response
        return self._parse_llm_response(response.content)

    def _parse_llm_response(self, content: str) -> dict[str, dict[str, Any]]:
        """Parse the LLM JSON response into a map of op_id → enhancements."""
        try:
            # Strip markdown code fences if present
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[-1].strip() == "```":
                    text = "\n".join(lines[1:-1])
                else:
                    text = "\n".join(lines[1:])
                text = text.strip()

            data = json.loads(text)
            if not isinstance(data, list):
                logger.warning("LLM response is not a JSON array")
                return {}

            result: dict[str, dict[str, Any]] = {}
            for item in data:
                op_id = item.get("operation_id")
                if op_id:
                    result[op_id] = item
            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Failed to parse LLM response as JSON", exc_info=True)
            return {}

    def _apply_enhancements(
        self, ir: ServiceIR, enhancements: dict[str, dict[str, Any]]
    ) -> ServiceIR:
        """Apply enhancements to a copy of the IR.

        Only modifies description and param descriptions.  Tags all
        LLM-contributed fields with source="llm" and confidence.
        """
        new_operations: list[Operation] = []

        for op in ir.operations:
            if op.id not in enhancements:
                new_operations.append(op)
                continue

            enh = enhancements[op.id]
            op_confidence = float(enh.get("confidence", 0.7))

            # Build enhanced params
            param_enhancements = {p["name"]: p for p in enh.get("params", []) if "name" in p}
            new_params: list[Param] = []
            for p in op.params:
                if p.name in param_enhancements:
                    pe = param_enhancements[p.name]
                    new_params.append(
                        p.model_copy(
                            update={
                                "description": pe.get("description", p.description),
                                "source": SourceType.llm,
                                "confidence": float(pe.get("confidence", 0.7)),
                            }
                        )
                    )
                else:
                    new_params.append(p)

            new_op = op.model_copy(
                update={
                    "description": enh.get("description", op.description),
                    "params": new_params,
                    "source": SourceType.llm,
                    "confidence": op_confidence,
                }
            )
            new_operations.append(new_op)

        return ir.model_copy(update={"operations": new_operations})
