"""CLI output parser — converts raw CLI output into structured data."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)


def detect_format(output: str) -> Literal["json", "yaml", "table", "text"]:
    """Auto-detect the format of CLI output."""
    stripped = output.strip()
    if not stripped:
        return "text"

    # JSON: starts with { or [
    if stripped[0] in "{[":
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, TypeError):
            pass

    # YAML: contains key: value patterns but not simple prose
    if re.search(r"^\w[\w\s]*:\s+\S", stripped, re.MULTILINE):
        try:
            result = yaml.safe_load(stripped)
            if isinstance(result, (dict, list)):
                return "yaml"
        except yaml.YAMLError:
            pass

    # Table: multiple lines with consistent column separators
    lines = stripped.splitlines()
    if len(lines) >= 2:
        # Check for separator-aligned columns (2+ spaces or tabs between fields)
        header_parts = re.split(r"\s{2,}|\t+", lines[0].strip())
        if len(header_parts) >= 2:
            consistent = 0
            for line in lines[1:]:
                parts = re.split(r"\s{2,}|\t+", line.strip())
                if len(parts) >= 2:
                    consistent += 1
            if consistent >= 1:
                return "table"

    return "text"


def parse_output(output: str, fmt: str = "auto") -> dict[str, Any] | list[Any] | str:
    """Parse CLI output into structured data.

    Parameters
    ----------
    output:
        Raw stdout string from the CLI command.
    fmt:
        Expected format. Use ``"auto"`` to auto-detect.

    Returns
    -------
    Parsed data — dict/list for structured formats, str for plain text.
    """
    if fmt == "auto":
        fmt = detect_format(output)

    if fmt == "json":
        return _parse_json(output)
    if fmt == "yaml":
        return _parse_yaml(output)
    if fmt == "table":
        return _parse_table(output)
    return output


def _parse_json(output: str) -> dict[str, Any] | list[Any] | str:
    try:
        result = json.loads(output.strip())
        if isinstance(result, (dict, list)):
            return result
        return str(result)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse output as JSON, returning raw text")
        return output


def _parse_yaml(output: str) -> dict[str, Any] | list[Any] | str:
    try:
        result = yaml.safe_load(output.strip())
        if isinstance(result, (dict, list)):
            return result
        return str(result) if result is not None else output
    except yaml.YAMLError:
        logger.warning("Failed to parse output as YAML, returning raw text")
        return output


def _parse_table(output: str) -> list[dict[str, str]]:
    """Parse a simple text table with whitespace-separated columns."""
    lines = output.strip().splitlines()
    if len(lines) < 2:
        return [{"line": line} for line in lines]

    # Split header by 2+ spaces or tabs
    headers = re.split(r"\s{2,}|\t+", lines[0].strip())
    headers = [h.strip().lower().replace(" ", "_") for h in headers if h.strip()]

    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-", "=", "+", "|"}:
            continue  # skip separator lines
        values = re.split(r"\s{2,}|\t+", stripped)
        row = {}
        for i, header in enumerate(headers):
            row[header] = values[i].strip() if i < len(values) else ""
        rows.append(row)
    return rows
