"""Integration tests for CLI runtime dispatch through RuntimeProxy."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime import create_app
from libs.ir.models import (
    AuthConfig,
    AuthType,
    CliOperationConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.schema import serialize_ir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cli_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="c" * 64,
        protocol="cli",
        service_name="test-cli-service",
        service_description="CLI integration test service",
        base_url="",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="cli-list-files",
                name="List Files",
                description="List files in directory",
                method=None,
                path=None,
                params=[
                    Param(name="path", type="string", required=False),
                ],
                risk=RiskMetadata(
                    writes_state=False,
                    destructive=False,
                    idempotent=True,
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                ),
                enabled=True,
                cli=CliOperationConfig(
                    command="ls",
                    subcommands=[],
                    args_style="gnu",
                    output_format="text",
                    timeout_seconds=10,
                ),
            ),
        ],
    )


def _make_mixed_ir() -> ServiceIR:
    """IR with both CLI and HTTP operations."""
    return ServiceIR(
        source_hash="d" * 64,
        protocol="cli",
        service_name="test-mixed-service",
        service_description="Mixed CLI/HTTP integration test",
        base_url="https://api.example.com",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="cli-version",
                name="Get Version",
                description="Print tool version",
                method=None,
                path=None,
                params=[],
                risk=RiskMetadata(
                    writes_state=False,
                    destructive=False,
                    idempotent=True,
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                ),
                enabled=True,
                cli=CliOperationConfig(
                    command="mytool",
                    subcommands=["version"],
                    args_style="gnu",
                    output_format="text",
                    timeout_seconds=5,
                ),
            ),
            Operation(
                id="http-list-items",
                name="List Items",
                description="List items via HTTP",
                method="GET",
                path="/items",
                params=[],
                risk=RiskMetadata(
                    writes_state=False,
                    destructive=False,
                    idempotent=True,
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                ),
                enabled=True,
            ),
        ],
    )


def _write_ir(tmp_path: Path, ir: ServiceIR) -> Path:
    output_path = tmp_path / "service_ir.json"
    output_path.write_text(serialize_ir(ir), encoding="utf-8")
    return output_path


async def _mock_subprocess_ok(*args: Any, **kwargs: Any) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"file1.txt\nfile2.txt\n", b""))
    proc.returncode = 0
    return proc


async def _mock_subprocess_fail(*args: Any, **kwargs: Any) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"ls: cannot access: No such file"))
    proc.returncode = 2
    return proc


async def _mock_subprocess_timeout(*args: Any, **kwargs: Any) -> AsyncMock:
    proc = AsyncMock()

    async def _hang() -> tuple[bytes, bytes]:
        raise TimeoutError

    proc.communicate = _hang
    proc.returncode = None
    proc.kill = AsyncMock()
    return proc


async def _mock_subprocess_not_found(*args: Any, **kwargs: Any) -> AsyncMock:
    raise FileNotFoundError("No such file or directory: 'nonexistent'")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCLIRuntimeDispatch:
    """Verify CLI operations dispatch through the runtime proxy."""

    @pytest.mark.asyncio
    @patch("apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec")
    async def test_cli_dispatch_success(self, mock_exec: AsyncMock, tmp_path: Path) -> None:
        mock_exec.side_effect = _mock_subprocess_ok
        ir = _make_cli_ir()
        ir_path = _write_ir(tmp_path, ir)
        app = create_app(service_ir_path=ir_path)

        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "cli-list-files",
            {"path": "/home"},
        )

        assert structured["status"] == "ok"
        assert structured["operation_id"] == "cli-list-files"
        assert structured["result"]["exit_code"] == 0
        assert "file1.txt" in structured["result"]["output"]
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec")
    async def test_cli_dispatch_nonzero_exit_raises_tool_error(
        self, mock_exec: AsyncMock, tmp_path: Path
    ) -> None:
        mock_exec.side_effect = _mock_subprocess_fail
        ir = _make_cli_ir()
        ir_path = _write_ir(tmp_path, ir)
        app = create_app(service_ir_path=ir_path)

        with pytest.raises(ToolError, match="exited with code 2"):
            await app.state.runtime_state.mcp_server.call_tool(
                "cli-list-files",
                {"path": "/nonexistent"},
            )

    @pytest.mark.asyncio
    @patch("apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec")
    async def test_cli_dispatch_timeout(self, mock_exec: AsyncMock, tmp_path: Path) -> None:
        mock_exec.side_effect = _mock_subprocess_timeout
        ir = _make_cli_ir()
        ir_path = _write_ir(tmp_path, ir)
        app = create_app(service_ir_path=ir_path)

        # Timeout returns exit_code=-1 which triggers ToolError
        with pytest.raises(ToolError, match="exited with code -1"):
            await app.state.runtime_state.mcp_server.call_tool(
                "cli-list-files",
                {"path": "/slow"},
            )

    @pytest.mark.asyncio
    @patch("apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec")
    async def test_cli_coexists_with_http_operations(
        self, mock_exec: AsyncMock, tmp_path: Path
    ) -> None:
        mock_exec.side_effect = _mock_subprocess_ok
        ir = _make_mixed_ir()
        ir_path = _write_ir(tmp_path, ir)
        app = create_app(service_ir_path=ir_path)

        # CLI operation should work
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "cli-version",
            {},
        )
        assert structured["status"] == "ok"
        assert structured["operation_id"] == "cli-version"

        # HTTP operation should be registered too
        registered = app.state.runtime_state.registered_operations
        assert "cli-version" in registered
        assert "http-list-items" in registered

    @pytest.mark.asyncio
    @patch("apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec")
    async def test_cli_dispatch_command_not_found(
        self, mock_exec: AsyncMock, tmp_path: Path
    ) -> None:
        mock_exec.side_effect = _mock_subprocess_not_found
        ir = _make_cli_ir()
        ir_path = _write_ir(tmp_path, ir)
        app = create_app(service_ir_path=ir_path)

        # FileNotFoundError in executor returns exit_code=-1
        with pytest.raises(ToolError, match="exited with code -1"):
            await app.state.runtime_state.mcp_server.call_tool(
                "cli-list-files",
                {"path": "/any"},
            )
