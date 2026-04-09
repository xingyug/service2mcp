"""Tests for the CLI output parser."""

from __future__ import annotations

import json

import yaml

from libs.extractors.cli_output_parser import detect_format, parse_output


class TestDetectFormat:
    def test_detect_json_format(self) -> None:
        assert detect_format('{"key": "value"}') == "json"

    def test_detect_json_array_format(self) -> None:
        assert detect_format('[{"a": 1}, {"b": 2}]') == "json"

    def test_detect_yaml_format(self) -> None:
        assert detect_format("name: test\nversion: 1.0\n") == "yaml"

    def test_detect_table_format(self) -> None:
        table = (
            "NAME          STATUS    AGE\npod-1         Running   5d\npod-2         Pending   1h"
        )
        assert detect_format(table) == "table"

    def test_detect_text_format(self) -> None:
        assert detect_format("just a simple text line") == "text"

    def test_detect_empty_returns_text(self) -> None:
        assert detect_format("") == "text"
        assert detect_format("   ") == "text"


class TestParseOutput:
    def test_parse_json_output(self) -> None:
        data = {"items": [1, 2, 3]}
        result = parse_output(json.dumps(data), "json")
        assert result == data

    def test_parse_yaml_output(self) -> None:
        data = {"name": "test", "count": 5}
        result = parse_output(yaml.dump(data), "yaml")
        assert result == data

    def test_parse_table_output(self) -> None:
        table = (
            "NAME          STATUS    AGE\npod-1         Running   5d\npod-2         Pending   1h"
        )
        result = parse_output(table, "table")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "pod-1"
        assert result[0]["status"] == "Running"
        assert result[1]["name"] == "pod-2"

    def test_parse_text_fallback(self) -> None:
        text = "some raw output"
        result = parse_output(text, "text")
        assert isinstance(result, str)
        assert result == text

    def test_parse_auto_detects_json(self) -> None:
        data = {"auto": True}
        result = parse_output(json.dumps(data), "auto")
        assert result == data

    def test_parse_auto_detects_table(self) -> None:
        table = "COL1          COL2\nval1          val2"
        result = parse_output(table, "auto")
        assert isinstance(result, list)

    def test_parse_empty_input(self) -> None:
        result = parse_output("", "auto")
        assert result == ""

    def test_parse_invalid_json_returns_raw(self) -> None:
        result = parse_output("{not valid json", "json")
        assert isinstance(result, str)

    def test_parse_table_with_separator_line(self) -> None:
        table = "NAME    AGE\n------  ---\nalice   30\nbob     25"
        result = parse_output(table, "table")
        assert isinstance(result, list)
        # Separator line "------  ---" is not stripped by parser (only pure separator chars)
        assert len(result) == 3
        assert result[1]["name"] == "alice"
        assert result[2]["name"] == "bob"
