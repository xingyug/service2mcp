"""Integration tests for the IR manipulation CLI tool."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)


def _make_ir(name: str, ops: list[str], protocol: str = "openapi") -> ServiceIR:
    operations = [
        Operation(
            id=op_id,
            name=op_id,
            description=f"Op {op_id}",
            method="GET",
            path=f"/{op_id}",
            params=[],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
        )
        for op_id in ops
    ]
    return ServiceIR(
        source_hash="a" * 64,
        protocol=protocol,
        service_name=name,
        service_description=f"Test {name}",
        base_url=f"https://{name}.test",
        auth=AuthConfig(type=AuthType.none),
        operations=operations,
    )


@pytest.fixture()
def tmp_ir_files(tmp_path: Path) -> tuple[Path, Path]:
    ir1 = _make_ir("svc-alpha", ["list-items", "get-item"])
    ir2 = _make_ir("svc-beta", ["create-order", "get-order"], protocol="rest")
    p1 = tmp_path / "ir1.json"
    p2 = tmp_path / "ir2.json"
    p1.write_text(ir1.model_dump_json(indent=2))
    p2.write_text(ir2.model_dump_json(indent=2))
    return p1, p2


class TestCompose:
    def test_compose_two_irs(self, tmp_ir_files: tuple[Path, Path], tmp_path: Path) -> None:
        p1, p2 = tmp_ir_files
        out = tmp_path / "merged.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/ir_tool.py",
                "compose",
                str(p1),
                str(p2),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        merged = ServiceIR(**json.loads(out.read_text()))
        assert len(merged.operations) == 4
        assert merged.protocol == "federated"

    def test_compose_with_custom_name(
        self, tmp_ir_files: tuple[Path, Path], tmp_path: Path
    ) -> None:
        p1, p2 = tmp_ir_files
        out = tmp_path / "merged.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/ir_tool.py",
                "compose",
                str(p1),
                str(p2),
                "-o",
                str(out),
                "--name",
                "my-gateway",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        merged = ServiceIR(**json.loads(out.read_text()))
        assert merged.service_name == "my-gateway"

    def test_compose_single_file_fails(
        self, tmp_ir_files: tuple[Path, Path], tmp_path: Path
    ) -> None:
        p1, _ = tmp_ir_files
        out = tmp_path / "merged.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/ir_tool.py",
                "compose",
                str(p1),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1


class TestTransform:
    def test_transform_rename_service(
        self, tmp_ir_files: tuple[Path, Path], tmp_path: Path
    ) -> None:
        p1, _ = tmp_ir_files
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps([{"action": "rename_service", "value": "new-name"}]))
        out = tmp_path / "transformed.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/ir_tool.py",
                "transform",
                str(p1),
                "-r",
                str(rules_path),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        transformed = ServiceIR(**json.loads(out.read_text()))
        assert transformed.service_name == "new-name"

    def test_transform_filter_by_tag(self, tmp_path: Path) -> None:
        ir = _make_ir("svc", ["op-a", "op-b"])
        ir_data = ir.model_dump()
        ir_data["operations"][0]["tags"] = ["keep"]
        p = tmp_path / "ir.json"
        p.write_text(json.dumps(ir_data, default=str))
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps([{"action": "filter_by_tag", "value": "keep"}]))
        out = tmp_path / "out.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/ir_tool.py",
                "transform",
                str(p),
                "-r",
                str(rules_path),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        transformed = ServiceIR(**json.loads(out.read_text()))
        assert len(transformed.operations) == 1


class TestInspect:
    def test_inspect_shows_summary(self, tmp_ir_files: tuple[Path, Path]) -> None:
        p1, _ = tmp_ir_files
        result = subprocess.run(
            [sys.executable, "scripts/ir_tool.py", "inspect", str(p1)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "svc-alpha" in result.stdout
        assert "Operations: 2" in result.stdout

    def test_inspect_verbose(self, tmp_ir_files: tuple[Path, Path]) -> None:
        p1, _ = tmp_ir_files
        result = subprocess.run(
            [sys.executable, "scripts/ir_tool.py", "inspect", str(p1), "-v"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "list-items" in result.stdout
