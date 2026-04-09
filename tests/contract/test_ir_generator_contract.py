"""Contract: the generator MUST accept any valid ServiceIR and produce deployment artifacts.

Tests that generate_generic_manifests() and generate_codegen_manifests()
produce a complete GeneratedManifestSet (ConfigMap, Deployment, Service,
NetworkPolicy, route_config, YAML) for various IR shapes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from libs.generator import (
    CodegenManifestConfig,
    GeneratedManifestSet,
    GenericManifestConfig,
    generate_codegen_manifests,
    generate_generic_manifests,
)
from libs.ir.models import (
    AuthConfig,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_RUNTIME_IMAGE = "ghcr.io/test/runtime:latest"

# ── helpers ────────────────────────────────────────────────────────────────


def _minimal_ir(
    *,
    protocol: str = "openapi",
    ops: list[Operation] | None = None,
    service_name: str = "gen-contract-svc",
    auth: AuthConfig | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_hash="c" * 64,
        protocol=protocol,
        service_name=service_name,
        base_url="https://upstream.example.com",
        auth=auth or AuthConfig(),
        operations=ops or [],
    )


def _safe_op(op_id: str) -> Operation:
    return Operation(
        id=op_id,
        name=op_id.replace("_", " ").title(),
        method="GET",
        path=f"/{op_id}",
        risk=RiskMetadata(
            writes_state=False,
            destructive=False,
            external_side_effect=False,
            idempotent=True,
            risk_level=RiskLevel.safe,
            confidence=1.0,
            source=SourceType.extractor,
        ),
        source=SourceType.extractor,
        confidence=1.0,
    )


def _load_fixture_ir() -> ServiceIR:
    data = json.loads((FIXTURES / "ir" / "service_ir_valid.json").read_text())
    return ServiceIR.model_validate(data)


def _default_config() -> GenericManifestConfig:
    return GenericManifestConfig(runtime_image=_RUNTIME_IMAGE)


_K8S_DOCUMENT_KEYS = {"apiVersion", "kind", "metadata"}

# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.contract
class TestGeneratorProducesArtifacts:
    """generate_generic_manifests MUST produce a complete manifest set."""

    def test_fixture_ir_generates_manifests(self) -> None:
        ir = _load_fixture_ir()
        result = generate_generic_manifests(ir, config=_default_config())
        assert isinstance(result, GeneratedManifestSet)

    def test_all_k8s_documents_present(self) -> None:
        ir = _load_fixture_ir()
        result = generate_generic_manifests(ir, config=_default_config())
        for doc in result.documents:
            assert _K8S_DOCUMENT_KEYS.issubset(doc.keys()), f"Missing keys in {doc.get('kind')}"

    def test_yaml_output_is_nonempty(self) -> None:
        ir = _load_fixture_ir()
        result = generate_generic_manifests(ir, config=_default_config())
        assert len(result.yaml) > 100

    def test_route_config_has_service_name(self) -> None:
        ir = _load_fixture_ir()
        result = generate_generic_manifests(ir, config=_default_config())
        assert "service_name" in result.route_config

    def test_empty_operations_ir_generates(self) -> None:
        ir = _minimal_ir(ops=[])
        result = generate_generic_manifests(ir, config=_default_config())
        assert isinstance(result, GeneratedManifestSet)
        assert "service_name" in result.route_config

    def test_many_operations_ir_generates(self) -> None:
        ops = [_safe_op(f"gen_op_{i}") for i in range(30)]
        ir = _minimal_ir(ops=ops)
        result = generate_generic_manifests(ir, config=_default_config())
        assert isinstance(result, GeneratedManifestSet)

    @pytest.mark.parametrize(
        "protocol",
        ["openapi", "graphql", "grpc", "soap", "sql", "jsonrpc", "odata", "scim"],
    )
    def test_all_protocols_generate(self, protocol: str) -> None:
        ir = _minimal_ir(protocol=protocol, ops=[_safe_op("p_check")])
        result = generate_generic_manifests(ir, config=_default_config())
        assert isinstance(result, GeneratedManifestSet)

    def test_codegen_mode_generates(self) -> None:
        ir = _load_fixture_ir()
        config = CodegenManifestConfig(runtime_image=_RUNTIME_IMAGE)
        result = generate_codegen_manifests(ir, config=config)
        assert isinstance(result, GeneratedManifestSet)

    def test_configmap_contains_compressed_ir(self) -> None:
        ir = _load_fixture_ir()
        result = generate_generic_manifests(ir, config=_default_config())
        cm_data = result.config_map.get("binaryData") or result.config_map.get("data", {})
        assert cm_data, "ConfigMap must carry IR data"

    def test_deployment_references_runtime_image(self) -> None:
        ir = _load_fixture_ir()
        result = generate_generic_manifests(ir, config=_default_config())
        yaml_str = result.yaml
        assert _RUNTIME_IMAGE in yaml_str

    def test_custom_namespace_propagates(self) -> None:
        ir = _minimal_ir(ops=[_safe_op("ns")])
        config = GenericManifestConfig(runtime_image=_RUNTIME_IMAGE, namespace="custom-ns")
        result = generate_generic_manifests(ir, config=config)
        assert result.deployment["metadata"]["namespace"] == "custom-ns"
