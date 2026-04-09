"""Tests for the CLI executor (subprocess-based)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.mcp_runtime.cli_executor import _build_command, _make_flag, execute_cli_tool
from libs.ir.models import CliOperationConfig

_SUBPROCESS_EXEC = "apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec"


@pytest.fixture
def simple_config() -> CliOperationConfig:
    return CliOperationConfig(
        command="my-tool",
        subcommands=["list"],
        args_style="gnu",
        timeout_seconds=10,
        output_format="json",
    )


@pytest.fixture
def posix_config() -> CliOperationConfig:
    return CliOperationConfig(
        command="ls",
        subcommands=[],
        args_style="posix",
        timeout_seconds=5,
        output_format="text",
    )


def _make_process_mock(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> AsyncMock:
    """Create a mock for asyncio.create_subprocess_exec."""
    process = AsyncMock()
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.returncode = returncode
    return process


class TestBuildCommand:
    def test_simple_command(self, simple_config: CliOperationConfig) -> None:
        cmd = _build_command(simple_config, {})
        assert cmd == ["my-tool", "list"]

    def test_command_with_subcommands(self) -> None:
        config = CliOperationConfig(
            command="kubectl",
            subcommands=["get", "pods"],
        )
        cmd = _build_command(config, {})
        assert cmd == ["kubectl", "get", "pods"]

    def test_command_with_arguments(self, simple_config: CliOperationConfig) -> None:
        cmd = _build_command(simple_config, {"namespace": "default", "output": "json"})
        assert "--namespace" in cmd
        assert "--output" in cmd

    def test_command_with_bool_true(self, simple_config: CliOperationConfig) -> None:
        cmd = _build_command(simple_config, {"verbose": True})
        assert "--verbose" in cmd

    def test_command_with_bool_false(self, simple_config: CliOperationConfig) -> None:
        cmd = _build_command(simple_config, {"verbose": False})
        assert "--verbose" not in cmd

    def test_command_skips_none_value(self, simple_config: CliOperationConfig) -> None:
        cmd = _build_command(simple_config, {"skip": None})
        assert "--skip" not in cmd

    def test_posix_style_args(self, posix_config: CliOperationConfig) -> None:
        cmd = _build_command(posix_config, {"l": True, "a": True})
        assert "-l" in cmd
        assert "-a" in cmd

    def test_windows_style_args(self) -> None:
        config = CliOperationConfig(
            command="dir",
            args_style="windows",
        )
        cmd = _build_command(config, {"sort": "name"})
        assert "/sort" in cmd


class TestMakeFlag:
    def test_gnu_flag(self) -> None:
        assert _make_flag("namespace", "gnu") == "--namespace"

    def test_posix_flag(self) -> None:
        assert _make_flag("n", "posix") == "-n"

    def test_windows_flag(self) -> None:
        assert _make_flag("sort", "windows") == "/sort"

    def test_underscores_converted_to_dashes(self) -> None:
        assert _make_flag("dry_run", "gnu") == "--dry-run"


@pytest.mark.asyncio
class TestExecuteCLITool:
    async def test_execute_simple_command(self, simple_config: CliOperationConfig) -> None:
        stdout = b'{"items": [1, 2, 3]}'
        process_mock = _make_process_mock(stdout=stdout, returncode=0)

        with patch(_SUBPROCESS_EXEC, return_value=process_mock):
            result = await execute_cli_tool(simple_config, {})
        assert result["output"] == {"items": [1, 2, 3]}

    async def test_execute_with_subcommands(self) -> None:
        config = CliOperationConfig(
            command="kubectl",
            subcommands=["get", "pods"],
            output_format="json",
        )
        stdout = b'{"items": []}'
        process_mock = _make_process_mock(stdout=stdout, returncode=0)

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ) as mock_exec:
            await execute_cli_tool(config, {})
            # Verify first positional args include command + subcommands
            call_args = mock_exec.call_args
            positional = call_args[0]
            assert positional[0] == "kubectl"
            assert positional[1] == "get"
            assert positional[2] == "pods"

    async def test_execute_with_arguments(self, simple_config: CliOperationConfig) -> None:
        process_mock = _make_process_mock(stdout=b"[]", returncode=0)

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ) as mock_exec:
            await execute_cli_tool(simple_config, {"format": "json"})
            call_args = mock_exec.call_args
            positional = call_args[0]
            assert "--format" in positional

    async def test_execute_timeout(self, simple_config: CliOperationConfig) -> None:
        process_mock = AsyncMock()
        process_mock.communicate = AsyncMock(side_effect=TimeoutError)
        process_mock.kill = MagicMock()
        process_mock.wait = AsyncMock()

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ):
            result = await execute_cli_tool(simple_config, {})

        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"]
        process_mock.kill.assert_called_once()

    async def test_execute_timeout_process_already_exited(
        self, simple_config: CliOperationConfig
    ) -> None:
        """Kill after timeout should handle ProcessLookupError gracefully."""
        process_mock = AsyncMock()
        process_mock.communicate = AsyncMock(side_effect=TimeoutError)
        process_mock.kill = MagicMock(side_effect=ProcessLookupError)
        process_mock.wait = AsyncMock()

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ):
            result = await execute_cli_tool(simple_config, {})

        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"]

    async def test_execute_nonzero_exit(self, simple_config: CliOperationConfig) -> None:
        process_mock = _make_process_mock(
            stdout=b"",
            stderr=b"Error: item not found",
            returncode=1,
        )

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ):
            result = await execute_cli_tool(simple_config, {})

        assert result["exit_code"] == 1
        assert "item not found" in result["stderr"]

    async def test_execute_json_output_parsed(self) -> None:
        config = CliOperationConfig(command="tool", output_format="json")
        stdout = b'{"status": "ok"}'
        process_mock = _make_process_mock(stdout=stdout, returncode=0)

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ):
            result = await execute_cli_tool(config, {})

        assert isinstance(result["output"], dict)
        assert result["output"]["status"] == "ok"

    async def test_execute_posix_style_args(self, posix_config: CliOperationConfig) -> None:
        process_mock = _make_process_mock(stdout=b"file1\nfile2", returncode=0)

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ) as mock_exec:
            await execute_cli_tool(posix_config, {"l": True})
            call_args = mock_exec.call_args
            positional = call_args[0]
            assert "-l" in positional

    async def test_execute_with_env_vars(self) -> None:
        config = CliOperationConfig(
            command="tool",
            env_vars={"API_KEY": "secret", "ENV": "test"},
            output_format="text",
        )
        process_mock = _make_process_mock(stdout=b"ok", returncode=0)

        with patch(
            _SUBPROCESS_EXEC,
            return_value=process_mock,
        ) as mock_exec:
            await execute_cli_tool(config, {})
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["env"] == {"API_KEY": "secret", "ENV": "test"}

    async def test_execute_command_not_found(self) -> None:
        config = CliOperationConfig(command="nonexistent-tool", output_format="text")

        with patch(
            "apps.mcp_runtime.cli_executor.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            result = await execute_cli_tool(config, {})

        assert result["exit_code"] == -1
        assert "not found" in result["stderr"].lower()
