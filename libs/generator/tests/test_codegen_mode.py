"""Unit tests for codegen-mode manifest generation."""

from __future__ import annotations

from pathlib import Path

from libs.generator.codegen_mode import CodegenManifestConfig, generate_codegen_manifests
from libs.ir import ServiceIR, deserialize_ir

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ir"
VALID_IR_PATH = FIXTURES_DIR / "service_ir_valid.json"


def _load_service_ir() -> ServiceIR:
    return deserialize_ir(VALID_IR_PATH.read_text(encoding="utf-8"))


class TestCodegenManifestGeneration:
    def test_codegen_labels_present(self) -> None:
        ir = _load_service_ir()
        config = CodegenManifestConfig(runtime_image="runtime:latest")
        result = generate_codegen_manifests(ir, config=config)
        deploy_labels = result.deployment["metadata"]["labels"]
        assert deploy_labels["tool-compiler-v2/runtime-mode"] == "codegen"

    def test_codegen_annotation_present(self) -> None:
        ir = _load_service_ir()
        config = CodegenManifestConfig(runtime_image="runtime:latest")
        result = generate_codegen_manifests(ir, config=config)
        annotations = result.deployment["metadata"]["annotations"]
        assert annotations["tool-compiler-v2/runtime-mode"] == "codegen"

    def test_codegen_image_override(self) -> None:
        ir = _load_service_ir()
        config = CodegenManifestConfig(
            runtime_image="runtime:latest",
            codegen_image="codegen:v2",
        )
        result = generate_codegen_manifests(ir, config=config)
        containers = result.deployment["spec"]["template"]["spec"]["containers"]
        assert containers[0]["image"] == "codegen:v2"

    def test_codegen_falls_back_to_runtime_image(self) -> None:
        ir = _load_service_ir()
        config = CodegenManifestConfig(runtime_image="runtime:latest")
        result = generate_codegen_manifests(ir, config=config)
        containers = result.deployment["spec"]["template"]["spec"]["containers"]
        assert containers[0]["image"] == "runtime:latest"

    def test_generates_all_documents(self) -> None:
        ir = _load_service_ir()
        config = CodegenManifestConfig(runtime_image="runtime:latest")
        result = generate_codegen_manifests(ir, config=config)
        assert result.config_map is not None
        assert result.deployment is not None
        assert result.service is not None
        assert result.network_policy is not None
        assert result.yaml
        assert result.route_config is not None
