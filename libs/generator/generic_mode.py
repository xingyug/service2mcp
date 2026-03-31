"""Kubernetes manifest generation for the generic runtime mode."""

from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from libs.ir import ServiceIR, serialize_ir
from libs.ir.models import AuthConfig, EventSupportLevel, EventTransport, GrpcStreamMode
from libs.secret_refs import (
    ensure_no_secret_ref_name_collisions,
    kubernetes_secret_key_name,
    normalized_secret_ref_name,
)

DEFAULT_NAMESPACE = "default"
DEFAULT_SERVICE_IR_PATH = "/config/service-ir.json.gz"
DEFAULT_WORKLOAD_PORT = 8003
DEFAULT_RUNTIME_SECRET_NAME = "tool-compiler-runtime-secrets"
_DNS_PORT = 53
_MAX_RESOURCE_NAME_LENGTH = 63
_TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")
_RESERVED_SELECTOR_LABELS = frozenset({"app.kubernetes.io/name", "app.kubernetes.io/instance"})
_TEMPLATE_ORDER = (
    "configmap.yaml.j2",
    "deployment.yaml.j2",
    "service.yaml.j2",
    "networkpolicy.yaml.j2",
)


@dataclass(frozen=True)
class GenericManifestConfig:
    """Configurable inputs for generic-mode manifest generation."""

    runtime_image: str
    service_id: str | None = None
    version_number: int | None = None
    namespace: str = DEFAULT_NAMESPACE
    replicas: int = 1
    container_port: int = DEFAULT_WORKLOAD_PORT
    service_port: int = DEFAULT_WORKLOAD_PORT
    name_suffix: str | None = None
    image_pull_policy: str = "IfNotPresent"
    runtime_secret_name: str | None = DEFAULT_RUNTIME_SECRET_NAME
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.runtime_image.strip():
            raise ValueError("runtime_image must not be empty.")
        if not self.namespace.strip():
            raise ValueError("namespace must not be empty.")
        if self.replicas < 1:
            raise ValueError("replicas must be >= 1.")
        if self.container_port < 1:
            raise ValueError("container_port must be >= 1.")
        if self.service_port < 1:
            raise ValueError("service_port must be >= 1.")
        if self.service_id is not None and not self.service_id.strip():
            raise ValueError("service_id must not be empty when provided.")
        if self.version_number is not None and self.version_number < 1:
            raise ValueError("version_number must be >= 1 when provided.")
        if self.runtime_secret_name is not None and not self.runtime_secret_name.strip():
            raise ValueError("runtime_secret_name must not be empty when provided.")
        reserved_label_keys = sorted(_RESERVED_SELECTOR_LABELS & self.labels.keys())
        if reserved_label_keys:
            reserved = ", ".join(reserved_label_keys)
            raise ValueError(f"labels must not override reserved selector labels: {reserved}.")


@dataclass(frozen=True)
class GeneratedManifestSet:
    """Structured manifest output for a generic runtime deployment."""

    config_map: dict[str, Any]
    deployment: dict[str, Any]
    service: dict[str, Any]
    network_policy: dict[str, Any]
    route_config: dict[str, Any]
    yaml: str

    @property
    def documents(self) -> tuple[dict[str, Any], ...]:
        return (
            self.config_map,
            self.deployment,
            self.service,
            self.network_policy,
        )


def generate_generic_manifests(
    service_ir: ServiceIR,
    *,
    config: GenericManifestConfig,
) -> GeneratedManifestSet:
    """Generate the generic-mode manifest set for a ServiceIR."""

    _validate_runtime_secret_configuration(
        service_ir.auth,
        runtime_secret_name=config.runtime_secret_name,
    )
    resource_name = runtime_service_name(
        service_ir.service_name,
        config.version_number,
        name_suffix=config.name_suffix,
    )
    selector_labels = {
        "app.kubernetes.io/name": resource_name,
        "app.kubernetes.io/instance": resource_name,
    }
    labels = {
        **selector_labels,
        "app.kubernetes.io/component": "generic-mcp-runtime",
        "app.kubernetes.io/managed-by": "tool-compiler-v2",
        "app.kubernetes.io/part-of": "tool-compiler-v2",
        "tool-compiler-v2/service-id": _route_base_name(service_ir, config),
        **config.labels,
    }
    if config.version_number is not None:
        labels["tool-compiler-v2/version"] = str(config.version_number)
    annotations = _annotations_for(service_ir)
    route_config = build_route_config(
        service_ir,
        config=config,
        resource_name=resource_name,
        route_base_name=_route_identity_base(service_ir, config),
    )
    runtime_secret_envs = _runtime_secret_envs(
        service_ir.auth,
        runtime_secret_name=config.runtime_secret_name,
    )

    context = {
        "annotations_yaml_4": _yaml_block(annotations, indent=4),
        "annotations_yaml_8": _yaml_block(annotations, indent=8),
        "config_map_name": _suffixed_resource_name(resource_name, "ir"),
        "container_port": config.container_port,
        "deployment_name": resource_name,
        "egress_ports": _egress_ports(service_ir),
        "enable_native_grpc_unary": _has_native_grpc_unary(service_ir),
        "enable_native_grpc_stream": _has_supported_native_grpc_stream(service_ir),
        "image_pull_policy": config.image_pull_policy,
        "labels_yaml_4": _yaml_block(labels, indent=4),
        "labels_yaml_8": _yaml_block(labels, indent=8),
        "namespace": config.namespace,
        "network_policy_name": resource_name,
        "replicas": config.replicas,
        "runtime_image": config.runtime_image,
        "runtime_secret_envs": runtime_secret_envs,
        "selector_labels_yaml_4": _yaml_block(selector_labels, indent=4),
        "selector_labels_yaml_6": _yaml_block(selector_labels, indent=6),
        "service_ir_gzip_base64": _gzip_ir_base64(serialize_ir(service_ir)),
        "service_ir_key": Path(DEFAULT_SERVICE_IR_PATH).name,
        "service_ir_path": DEFAULT_SERVICE_IR_PATH,
        "service_name": resource_name,
        "service_port": config.service_port,
    }

    rendered_documents = [
        _render_template(template_name, context) for template_name in _TEMPLATE_ORDER
    ]
    parsed_documents = [_parse_manifest(document) for document in rendered_documents]
    yaml_bundle = "\n---\n".join(rendered_documents) + "\n"

    _manifest_map = dict(zip(_TEMPLATE_ORDER, parsed_documents, strict=True))

    return GeneratedManifestSet(
        config_map=_manifest_map["configmap.yaml.j2"],
        deployment=_manifest_map["deployment.yaml.j2"],
        service=_manifest_map["service.yaml.j2"],
        network_policy=_manifest_map["networkpolicy.yaml.j2"],
        route_config=route_config,
        yaml=yaml_bundle,
    )


def render_generic_manifest_yaml(
    service_ir: ServiceIR,
    *,
    config: GenericManifestConfig,
) -> str:
    """Render the full multi-document YAML bundle for a generic runtime deployment."""

    return generate_generic_manifests(service_ir, config=config).yaml


def _template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATE_DIRECTORY),
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        undefined=StrictUndefined,
    )


def _render_template(template_name: str, context: dict[str, Any]) -> str:
    template = _template_environment().get_template(template_name)
    return template.render(**context).strip()


def _parse_manifest(rendered_manifest: str) -> dict[str, Any]:
    document = yaml.safe_load(rendered_manifest)
    if not isinstance(document, dict):
        raise ValueError("Rendered manifest is not a YAML mapping.")
    return document


def _yaml_block(mapping: dict[str, str], *, indent: int) -> str:
    rendered = yaml.safe_dump(mapping, sort_keys=True, default_flow_style=False).rstrip()
    return "\n".join(f"{' ' * indent}{line}" for line in rendered.splitlines())


def _gzip_ir_base64(payload: str) -> str:
    compressed = gzip.compress(payload.encode("utf-8"), mtime=0)
    return base64.b64encode(compressed).decode("ascii")


def _annotations_for(service_ir: ServiceIR) -> dict[str, str]:
    annotations = {
        "tool-compiler-v2/base-url": service_ir.base_url,
        "tool-compiler-v2/compiler-version": service_ir.compiler_version,
        "tool-compiler-v2/ir-version": service_ir.ir_version,
        "tool-compiler-v2/protocol": service_ir.protocol,
        "tool-compiler-v2/service-name": service_ir.service_name,
        "tool-compiler-v2/source-hash": service_ir.source_hash,
    }
    if service_ir.environment:
        annotations["tool-compiler-v2/environment"] = service_ir.environment
    if service_ir.tenant:
        annotations["tool-compiler-v2/tenant"] = service_ir.tenant
    return annotations


def _resource_name(service_name: str, suffix: str | None) -> str:
    base_name = _sanitize_dns_label(service_name)
    if suffix:
        return _suffixed_resource_name(base_name, suffix)
    return base_name


def _versioned_resource_name(base_name: str, version_number: int | None) -> str:
    if version_number is None:
        return base_name

    return _suffixed_resource_name(base_name, f"v{version_number}")


def runtime_service_name(
    service_name: str,
    version_number: int | None,
    *,
    name_suffix: str | None = None,
) -> str:
    """Return the versioned runtime Service name used by generated Kubernetes manifests."""

    return _versioned_resource_name(_resource_name(service_name, name_suffix), version_number)


def _sanitize_dns_label(value: str) -> str:
    sanitized = []
    previous_was_dash = False
    for char in value.lower():
        if char.isalnum():
            sanitized.append(char)
            previous_was_dash = False
            continue
        if previous_was_dash:
            continue
        sanitized.append("-")
        previous_was_dash = True

    label = "".join(sanitized).strip("-")
    if not label:
        label = "service"
    return label[:_MAX_RESOURCE_NAME_LENGTH].rstrip("-")


def _suffixed_resource_name(base_name: str, suffix: str) -> str:
    trimmed_base = _sanitize_dns_label(base_name)
    trimmed_suffix = _sanitize_dns_label(suffix)
    max_base_length = max(_MAX_RESOURCE_NAME_LENGTH - len(trimmed_suffix) - 1, 0)
    trimmed_base = trimmed_base[:max_base_length].rstrip("-")
    if not trimmed_base:
        trimmed_base = "service"
    max_suffix_length = max(_MAX_RESOURCE_NAME_LENGTH - len(trimmed_base) - 1, 0)
    trimmed_suffix = trimmed_suffix[:max_suffix_length].rstrip("-")
    if not trimmed_suffix:
        return trimmed_base[:_MAX_RESOURCE_NAME_LENGTH].rstrip("-") or "service"
    return f"{trimmed_base}-{trimmed_suffix}"


def _upstream_port(base_url: str) -> int:
    parsed = urlparse(base_url)
    if parsed.port is not None:
        return parsed.port
    scheme_default_ports: dict[str, int] = {
        "http": 80,
        "https": 443,
        "grpc": 50051,
        "grpcs": 443,
        "ws": 80,
        "wss": 443,
    }
    return scheme_default_ports.get(parsed.scheme, 443)


def _egress_ports(service_ir: ServiceIR) -> list[int]:
    ports = [_upstream_port(service_ir.base_url)]
    oauth2 = service_ir.auth.oauth2
    if oauth2 is not None:
        token_port = _upstream_port(oauth2.token_url)
        if token_port not in ports:
            ports.append(token_port)
    return ports


def _route_base_name(service_ir: ServiceIR, config: GenericManifestConfig) -> str:
    if config.service_id is not None:
        return _sanitize_dns_label(config.service_id)
    return _sanitize_dns_label(service_ir.service_name)


def _route_identity_base(service_ir: ServiceIR, config: GenericManifestConfig) -> str:
    base_name = _route_base_name(service_ir, config)
    scope_segments: list[str] = []
    if service_ir.tenant:
        scope_segments.extend(("tenant", _sanitize_dns_label(service_ir.tenant)))
    if service_ir.environment:
        scope_segments.extend(("env", _sanitize_dns_label(service_ir.environment)))
    if not scope_segments:
        return base_name
    return "-".join((base_name, *scope_segments))


def _has_supported_native_grpc_stream(service_ir: ServiceIR) -> bool:
    return any(
        descriptor.transport is EventTransport.grpc_stream
        and descriptor.support is EventSupportLevel.supported
        and descriptor.grpc_stream is not None
        and descriptor.grpc_stream.mode is GrpcStreamMode.server
        for descriptor in service_ir.event_descriptors
    )


def _has_native_grpc_unary(service_ir: ServiceIR) -> bool:
    return any(
        operation.enabled and operation.grpc_unary is not None
        for operation in service_ir.operations
    )


def build_route_config(
    service_ir: ServiceIR,
    *,
    config: GenericManifestConfig,
    resource_name: str | None = None,
    route_base_name: str | None = None,
) -> dict[str, Any]:
    """Build gateway route metadata for stable and version-pinned traffic."""

    resolved_resource_name = resource_name or runtime_service_name(
        service_ir.service_name,
        config.version_number,
        name_suffix=config.name_suffix,
    )
    stable_service_id = _route_base_name(service_ir, config)
    resolved_route_base = route_base_name or _route_identity_base(service_ir, config)
    target_service = {
        "name": resolved_resource_name,
        "namespace": config.namespace,
        "port": config.service_port,
    }
    route_config: dict[str, Any] = {
        "service_id": stable_service_id,
        "service_name": resolved_resource_name,
        "namespace": config.namespace,
        "default_route": {
            "route_id": f"{resolved_route_base}-active",
            "target_service": target_service,
            "switch_strategy": "atomic-upstream-swap",
        },
    }
    if service_ir.tenant:
        route_config["tenant"] = service_ir.tenant
    if service_ir.environment:
        route_config["environment"] = service_ir.environment
    if config.version_number is not None:
        route_config["version_number"] = config.version_number
        route_config["version_route"] = {
            "route_id": f"{resolved_route_base}-v{config.version_number}",
            "match": {"headers": {"x-tool-compiler-version": str(config.version_number)}},
            "target_service": target_service,
        }
    else:
        route_config["version_route"] = None
    return route_config


def _runtime_secret_envs(
    auth: AuthConfig,
    *,
    runtime_secret_name: str | None,
) -> list[dict[str, str]]:
    if runtime_secret_name is None:
        return []

    secret_refs = _runtime_secret_refs(auth)
    return [
        {
            "env_name": _secret_ref_env_name(secret_ref),
            "secret_name": runtime_secret_name,
            "secret_key": _secret_ref_secret_key(secret_ref),
        }
        for secret_ref in secret_refs
    ]


def _runtime_secret_refs(auth: AuthConfig) -> list[str]:
    refs: list[str] = []
    for secret_ref in (
        auth.runtime_secret_ref,
        auth.basic_password_ref,
        auth.oauth2.client_id_ref if auth.oauth2 is not None else None,
        auth.oauth2.client_secret_ref if auth.oauth2 is not None else None,
        auth.mtls.cert_ref if auth.mtls is not None else None,
        auth.mtls.key_ref if auth.mtls is not None else None,
        auth.mtls.ca_ref if auth.mtls is not None else None,
        auth.request_signing.secret_ref if auth.request_signing is not None else None,
    ):
        if secret_ref and secret_ref not in refs:
            refs.append(secret_ref)
    return refs


def _secret_ref_env_name(secret_ref: str) -> str:
    return normalized_secret_ref_name(secret_ref)


def _secret_ref_secret_key(secret_ref: str) -> str:
    return kubernetes_secret_key_name(secret_ref)


def _validate_runtime_secret_configuration(
    auth: AuthConfig,
    *,
    runtime_secret_name: str | None,
) -> None:
    secret_refs = _runtime_secret_refs(auth)
    if secret_refs and runtime_secret_name is None:
        refs = ", ".join(secret_refs)
        raise ValueError(
            "runtime_secret_name must be configured when auth requires runtime secret refs: "
            f"{refs}."
        )
    ensure_no_secret_ref_name_collisions(
        secret_refs,
        context="generic manifest runtime secret wiring",
    )


__all__ = [
    "DEFAULT_SERVICE_IR_PATH",
    "DEFAULT_WORKLOAD_PORT",
    "GeneratedManifestSet",
    "GenericManifestConfig",
    "build_capability_manifest",
    "build_route_config",
    "generate_generic_manifests",
    "render_generic_manifest_yaml",
]


def build_capability_manifest(service_ir: ServiceIR) -> dict[str, Any]:
    """Build a capability manifest with tools, resources, and prompts.

    Output structure:
    {
      "tools": [{"id": ..., "name": ..., "description": ..., "method": ..., "path": ...}],
      "resources": [{"uri": ..., "name": ..., "description": ..., "mime_type": ...}],
      "prompts": [{"name": ..., "description": ..., "arguments": [...]}]
    }
    """
    tools = [
        {
            "id": op.id,
            "name": op.name,
            "description": op.description,
            "method": op.method,
            "path": op.path,
        }
        for op in service_ir.operations
        if op.enabled
    ]
    resources = [
        {
            "uri": r.uri,
            "name": r.name,
            "description": r.description,
            "mime_type": r.mime_type,
        }
        for r in service_ir.resource_definitions
    ]
    prompts = [
        {
            "name": p.name,
            "description": p.description,
            "arguments": [
                {
                    "name": a.name,
                    "description": a.description,
                    "required": a.required,
                }
                for a in p.arguments
            ],
        }
        for p in service_ir.prompt_definitions
    ]
    return {
        "tools": tools,
        "resources": resources,
        "prompts": prompts,
    }
