"""CLI runtime executor — subprocess-based execution of CLI-backed MCP tools."""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

from libs.extractors.cli_output_parser import parse_output
from libs.ir.models import CliOperationConfig

logger = logging.getLogger(__name__)


async def execute_cli_tool(
    config: CliOperationConfig,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a CLI command and return parsed output.

    Builds a command line from *config* and *arguments*, runs it via
    ``asyncio.create_subprocess_exec``, and returns structured output.

    Returns
    -------
    dict with keys ``output``, ``exit_code``, and ``stderr``.
    """
    cmd = _build_command(config, arguments)
    env = dict(config.env_vars) if config.env_vars else None

    logger.info("Executing CLI command: %s", " ".join(cmd))

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=config.working_dir,
            env=env,
        )
    except FileNotFoundError:
        logger.error("CLI command not found: %s", cmd[0])
        return {
            "output": None,
            "exit_code": -1,
            "stderr": f"Command not found: {cmd[0]}",
        }
    except OSError as exc:
        logger.error("CLI command failed to start: %s", exc)
        return {
            "output": None,
            "exit_code": -1,
            "stderr": f"Failed to start command: {exc}",
        }

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=config.timeout_seconds,
        )
    except TimeoutError:
        logger.error("CLI command timed out after %ds: %s", config.timeout_seconds, cmd)
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass
        return {
            "output": None,
            "exit_code": -1,
            "stderr": f"Command timed out after {config.timeout_seconds}s",
        }

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    exit_code = process.returncode if process.returncode is not None else -1

    if exit_code != 0:
        logger.warning("CLI command exited with code %d: %s", exit_code, stderr.strip())

    parsed = parse_output(stdout, config.output_format)

    return {
        "output": parsed,
        "exit_code": exit_code,
        "stderr": stderr,
    }


def _build_command(config: CliOperationConfig, arguments: dict[str, Any]) -> list[str]:
    """Assemble the full command list from config and arguments."""
    cmd: list[str] = [config.command, *config.subcommands]

    for key, value in arguments.items():
        if value is None:
            continue

        if isinstance(value, bool):
            if value:
                flag = _make_flag(key, config.args_style)
                cmd.append(flag)
            continue

        flag = _make_flag(key, config.args_style)
        cmd.append(flag)
        cmd.append(shlex.quote(str(value)))

    return cmd


def _make_flag(name: str, style: str) -> str:
    """Create a CLI flag from a parameter name based on argument style."""
    clean = name.replace("_", "-")
    if style == "posix":
        return f"-{clean}"
    if style == "windows":
        return f"/{clean}"
    # gnu (default): --flag
    return f"--{clean}"
