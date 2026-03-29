"""Unit tests for generic-mode manifest generation."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import yaml

from libs.generator import GenericManifestConfig, generate_generic_manifests
from libs.ir import ServiceIR, deserialize_ir
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RequestSigningConfig,
    RiskLevel,
    RiskMetadata,
    SourceType,
)

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ir"
VALID_IR_PATH = FIXTURES_DIR / "service_ir_valid.json"


def _load_service_ir(path: Path = VALID_IR_PATH) -> ServiceIR:
    return deserialize_ir(path.read_text(encoding="utf-8"))


def _build_grpc_stream_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="f" * 64,
        protocol="grpc",
        service_name="grpc-stream-runtime",
        service_description="gRPC stream manifest fixture",
        base_url="grpc://inventory.example.test:443",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="watchInventory",
                name="Watch Inventory",
                description="Consume a native gRPC inventory stream.",
                method="POST",
                path="/catalog.v1.InventoryService/WatchInventory",
                params=[Param(name="payload", type="object", required=False)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="WatchInventory",
                name="WatchInventory",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/catalog.v1.InventoryService/WatchInventory",
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                ),
            )
        ],
    )


def _build_grpc_unary_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="e" * 64,
        protocol="grpc",
        service_name="grpc-unary-runtime",
        service_description="gRPC unary manifest fixture",
        base_url="grpc://inventory.example.test:443",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="LookupInventory",
                name="Lookup Inventory",
                description="Execute a native gRPC inventory lookup.",
                method="POST",
                path="/catalog.v1.InventoryService/LookupInventory",
                params=[Param(name="sku", type="string", required=True)],
                grpc_unary=GrpcUnaryRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/LookupInventory"
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
    )


def test_generate_generic_manifests_from_fixture() -> None:
    service_ir = _load_service_ir()
    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:1.2.3",
            namespace="runtime-system",
            service_port=80,
            labels={"team": "platform"},
        ),
    )

    documents = list(yaml.safe_load_all(manifest_set.yaml))
    kinds = [document["kind"] for document in documents]

    assert kinds == ["ConfigMap", "Deployment", "Service", "NetworkPolicy"]
    assert len(manifest_set.documents) == 4

    config_map = manifest_set.config_map
    assert config_map["metadata"]["name"] == "billing-runtime-ir"
    assert config_map["metadata"]["namespace"] == "runtime-system"
    assert config_map["metadata"]["labels"]["team"] == "platform"
    compressed_ir = base64.b64decode(config_map["binaryData"]["service-ir.json.gz"])
    assert json.loads(gzip.decompress(compressed_ir)) == service_ir.model_dump(mode="json")

    deployment = manifest_set.deployment
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    pod_security_context = deployment["spec"]["template"]["spec"]["securityContext"]
    container_security_context = container["securityContext"]
    env_entries = {item["name"]: item.get("value") for item in container["env"]}
    volume_mounts = {item["name"]: item for item in container["volumeMounts"]}
    volumes = {item["name"]: item for item in deployment["spec"]["template"]["spec"]["volumes"]}

    assert deployment["metadata"]["name"] == "billing-runtime"
    assert (
        deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "billing-runtime"
    )
    assert container["image"] == "ghcr.io/example/generic-runtime:1.2.3"
    assert container["ports"][0]["containerPort"] == 8003
    assert env_entries["SERVICE_IR_PATH"] == "/config/service-ir.json.gz"
    assert env_entries["TMPDIR"] == "/tmp"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert volume_mounts["service-ir"] == {
        "name": "service-ir",
        "mountPath": "/config",
        "readOnly": True,
    }
    assert volumes["service-ir"]["configMap"]["name"] == "billing-runtime-ir"
    assert volumes["service-ir"]["configMap"]["items"] == [
        {"key": "service-ir.json.gz", "path": "service-ir.json.gz"}
    ]
    assert volumes["tmp"] == {"name": "tmp", "emptyDir": {}}
    assert pod_security_context == {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container_security_context == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
    }

    service = manifest_set.service
    assert service["metadata"]["name"] == "billing-runtime"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"}
    ]
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "billing-runtime"

    network_policy = manifest_set.network_policy
    egress_rules = network_policy["spec"]["egress"]
    assert network_policy["metadata"]["name"] == "billing-runtime"
    assert network_policy["spec"]["policyTypes"] == ["Egress"]
    assert network_policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "billing-runtime",
        "app.kubernetes.io/instance": "billing-runtime",
    }
    assert egress_rules[0]["ports"] == [{"protocol": "TCP", "port": 443}]
    assert egress_rules[1]["ports"] == [
        {"protocol": "UDP", "port": 53},
        {"protocol": "TCP", "port": 53},
    ]


def test_generate_generic_manifests_sanitizes_service_names_and_suffixes() -> None:
    service_ir = _load_service_ir().model_copy(update={"service_name": "Billing Runtime_V2"})

    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:stable",
            name_suffix="Preview",
        ),
    )

    assert manifest_set.config_map["metadata"]["name"] == "billing-runtime-v2-preview-ir"
    assert manifest_set.deployment["metadata"]["name"] == "billing-runtime-v2-preview"
    assert manifest_set.service["metadata"]["name"] == "billing-runtime-v2-preview"
    assert (
        manifest_set.network_policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
        == "billing-runtime-v2-preview"
    )


def test_generate_generic_manifests_supports_version_coexistence_route_config() -> None:
    service_ir = _load_service_ir()

    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:stable",
            service_id="billing-api",
            version_number=2,
            namespace="runtime-system",
            service_port=8080,
        ),
    )

    assert manifest_set.config_map["metadata"]["name"] == "billing-runtime-v2-ir"
    assert manifest_set.deployment["metadata"]["name"] == "billing-runtime-v2"
    assert manifest_set.service["metadata"]["name"] == "billing-runtime-v2"
    assert manifest_set.deployment["metadata"]["labels"]["tool-compiler-v2/version"] == "2"

    route_config = manifest_set.route_config
    assert route_config["service_id"] == "billing-api"
    assert route_config["service_name"] == "billing-runtime-v2"
    assert route_config["default_route"]["route_id"] == "billing-api-active"
    assert route_config["default_route"]["target_service"] == {
        "name": "billing-runtime-v2",
        "namespace": "runtime-system",
        "port": 8080,
    }
    assert route_config["version_route"] == {
        "route_id": "billing-api-v2",
        "match": {"headers": {"x-tool-compiler-version": "2"}},
        "target_service": {
            "name": "billing-runtime-v2",
            "namespace": "runtime-system",
            "port": 8080,
        },
    }


def test_generate_generic_manifests_injects_runtime_auth_secret_envs() -> None:
    service_ir = _load_service_ir().model_copy(
        update={
            "auth": AuthConfig(
                type=AuthType.bearer,
                runtime_secret_ref="directus-access-token",
                request_signing=RequestSigningConfig(secret_ref="request-signing-secret"),
            )
        }
    )

    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:secure",
            namespace="runtime-system",
        ),
    )

    container = manifest_set.deployment["spec"]["template"]["spec"]["containers"][0]
    env_entries = {item["name"]: item for item in container["env"]}

    assert env_entries["DIRECTUS_ACCESS_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "tool-compiler-runtime-secrets",
        "key": "directus-access-token",
    }
    assert env_entries["REQUEST_SIGNING_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "tool-compiler-runtime-secrets",
        "key": "request-signing-secret",
    }


def test_generate_generic_manifests_enables_native_grpc_stream_runtime_when_required() -> None:
    service_ir = _build_grpc_stream_ir()

    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:grpc",
            namespace="runtime-system",
        ),
    )

    container = manifest_set.deployment["spec"]["template"]["spec"]["containers"][0]
    env_entries = {item["name"]: item["value"] for item in container["env"]}

    assert env_entries["ENABLE_NATIVE_GRPC_STREAM"] == "true"


def test_generate_generic_manifests_enables_native_grpc_unary_runtime_when_required() -> None:
    service_ir = _build_grpc_unary_ir()

    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:grpc",
            namespace="runtime-system",
        ),
    )

    container = manifest_set.deployment["spec"]["template"]["spec"]["containers"][0]
    env_entries = {item["name"]: item["value"] for item in container["env"]}

    assert env_entries["ENABLE_NATIVE_GRPC_UNARY"] == "true"
