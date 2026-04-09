"""CLI extractor — parses .cli.yaml / .cli.json spec files into IR operations."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import yaml

from libs.extractors.base import SourceConfig
from libs.extractors.utils import get_content, slugify
from libs.ir.models import (
    AuthConfig,
    AuthType,
    CliOperationConfig,
    ErrorResponse,
    ErrorSchema,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

logger = logging.getLogger(__name__)

_SAFE_PREFIXES = (
    "get",
    "list",
    "query",
    "fetch",
    "describe",
    "find",
    "search",
    "count",
    "show",
    "read",
)
_DANGEROUS_PREFIXES = ("delete", "remove", "purge", "drop", "destroy")

_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _json_schema_for_param(arg: dict[str, Any], ir_type: str) -> dict[str, Any] | None:
    """Build json_schema for a CLI param when it carries structure."""
    if ir_type == "object":
        properties = arg.get("properties")
        if properties:
            result: dict[str, Any] = {"type": "object", "properties": properties}
            required = arg.get("required_fields")
            if required:
                result["required"] = required
            return result
        return None
    if ir_type == "array":
        items = arg.get("items", {})
        if not items:
            items = {"type": "string"}
        return {"type": "array", "items": items}
    return None


def _classify_risk(command_name: str, risk_spec: dict[str, Any] | None = None) -> RiskMetadata:
    """Derive risk from explicit spec or heuristic name analysis."""
    if risk_spec is not None:
        level_raw = risk_spec.get("risk_level", "unknown")
        try:
            level = RiskLevel(level_raw)
        except ValueError:
            level = RiskLevel.unknown
        return RiskMetadata(
            risk_level=level,
            writes_state=bool(risk_spec.get("writes_state", False)),
            destructive=bool(risk_spec.get("destructive", False)),
            external_side_effect=bool(risk_spec.get("external_side_effect", False)),
            idempotent=bool(risk_spec.get("idempotent", True)),
            confidence=0.9,
        )

    segment = command_name.lower().replace("-", "_").split("_")[-1]
    if any(segment.startswith(p) for p in _SAFE_PREFIXES):
        return RiskMetadata(
            risk_level=RiskLevel.safe,
            writes_state=False,
            destructive=False,
            confidence=0.7,
        )
    if any(segment.startswith(p) for p in _DANGEROUS_PREFIXES):
        return RiskMetadata(
            risk_level=RiskLevel.dangerous,
            writes_state=True,
            destructive=True,
            confidence=0.7,
        )
    # Default: cautious for unknown commands
    return RiskMetadata(
        risk_level=RiskLevel.cautious,
        writes_state=True,
        destructive=False,
        confidence=0.5,
    )


class CLIExtractor:
    """Extract CLI tool operations from .cli.yaml / .cli.json spec files."""

    protocol_name: str = "cli"

    # ── detection ──────────────────────────────────────────────────────────

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "cli":
            return 0.95

        # Check file path for .cli.yaml / .cli.json extension
        if source.file_path:
            lower = source.file_path.lower()
            if lower.endswith((".cli.yaml", ".cli.yml", ".cli.json")):
                return 0.90

        content = get_content(source)
        if content is None:
            return 0.0

        # Check file_content for .cli.yaml / .cli.json reference
        if source.file_content:
            lower_content = source.file_content.lower()
            if ".cli.yaml" in lower_content or ".cli.json" in lower_content:
                return 0.90

        # Try parsing and look for top-level `commands:` key
        data = self._try_parse(content)
        if data is not None and isinstance(data.get("commands"), list):
            return 0.85

        return 0.0

    # ── extraction ─────────────────────────────────────────────────────────

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = get_content(source)
        if content is None:
            raise ValueError("CLI spec: no content available from source")

        data = self._try_parse(content)
        if data is None or not isinstance(data, dict):
            raise ValueError("CLI spec: content is not valid YAML or JSON")

        commands = data.get("commands")
        if not isinstance(commands, list):
            raise ValueError("CLI spec: missing or invalid 'commands' list")

        service_name = slugify(str(data.get("name", "cli-tool")))
        description = str(data.get("description", ""))
        version = str(data.get("version", "0.0.0"))
        base_command = str(data.get("base_command", service_name))

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        operations: list[Operation] = []
        for cmd in commands:
            if not isinstance(cmd, dict):
                logger.warning("CLI spec: skipping non-dict command entry")
                continue
            op = self._command_to_operation(cmd, base_command, service_name)
            if op is not None:
                operations.append(op)

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="cli",
            service_name=service_name,
            service_description=description,
            base_url="",
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "service_version": version,
                "base_command": base_command,
                "command_count": len(operations),
            },
        )

    # ── private helpers ────────────────────────────────────────────────────

    @staticmethod
    def _command_to_operation(
        cmd: dict[str, Any],
        base_command: str,
        service_name: str,
    ) -> Operation | None:
        name = cmd.get("name")
        if not name:
            logger.warning("CLI spec: command missing 'name', skipping")
            return None

        op_id = slugify(f"{service_name}_{name}")
        description = str(cmd.get("description", ""))
        subcommands: list[str] = cmd.get("subcommands", [])
        output_format = cmd.get("output_format", "auto")
        env_vars: dict[str, str] = cmd.get("env_vars", {})
        args_style = cmd.get("args_style", "gnu")
        timeout = cmd.get("timeout_seconds", 30)
        sandbox_mode = cmd.get("sandbox_mode", "none")

        # Build params from args
        params: list[Param] = []
        for arg in cmd.get("args", []):
            if not isinstance(arg, dict):
                continue
            arg_name = arg.get("name")
            if not arg_name:
                continue
            raw_type = arg.get("type", "string")
            ir_type = _TYPE_MAP.get(raw_type, "string")
            json_schema = _json_schema_for_param(arg, ir_type)
            params.append(
                Param(
                    name=str(arg_name),
                    type=ir_type,
                    required=bool(arg.get("required", False)),
                    description=str(arg.get("description", "")),
                    default=arg.get("default"),
                    json_schema=json_schema,
                )
            )

        cli_config = CliOperationConfig(
            command=base_command,
            subcommands=subcommands,
            args_style=args_style,
            env_vars=env_vars,
            timeout_seconds=timeout,
            sandbox_mode=sandbox_mode,
            output_format=output_format,
        )

        risk = _classify_risk(str(name), cmd.get("risk"))

        return Operation(
            id=op_id,
            name=str(name),
            description=description,
            method=None,
            path=None,
            params=params,
            cli=cli_config,
            risk=risk,
            source=SourceType.extractor,
            error_schema=ErrorSchema(
                responses=[
                    ErrorResponse(
                        error_code="nonzero_exit",
                        description="Command exited with a non-zero exit code",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "exit_code": {"type": "integer"},
                                "stderr": {"type": "string"},
                            },
                        },
                    ),
                    ErrorResponse(
                        error_code="timeout",
                        description="Command execution exceeded the configured timeout",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "timeout_seconds": {"type": "number"},
                            },
                        },
                    ),
                    ErrorResponse(
                        error_code="not_found",
                        description="Command executable was not found on PATH",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "command": {"type": "string"},
                            },
                        },
                    ),
                ],
                default_error_schema={
                    "type": "object",
                    "properties": {
                        "exit_code": {"type": "integer"},
                        "stderr": {"type": "string"},
                    },
                },
            ),
        )

    @staticmethod
    def _try_parse(content: str) -> dict[str, Any] | None:
        """Attempt to parse content as JSON first, then YAML."""
        # Try JSON
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

        # Try YAML
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                return data
        except yaml.YAMLError:
            pass

        return None
