"""Kubernetes manifest generation for the codegen runtime mode.

In codegen mode, the compiler generates a lightweight SDK-wrapper deployment
instead of the generic MCP runtime proxy.  The container runs a pre-built
codegen image that reads the service IR and exposes a code-generated SDK
server rather than a dynamic proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from libs.generator.generic_mode import (
    GeneratedManifestSet,
    GenericManifestConfig,
    generate_generic_manifests,
)
from libs.ir import ServiceIR


@dataclass(frozen=True)
class CodegenManifestConfig:
    """Configurable inputs for codegen-mode manifest generation."""

    runtime_image: str
    codegen_image: str | None = None
    service_id: str | None = None
    version_number: int | None = None
    namespace: str = "default"
    replicas: int = 1
    container_port: int = 8003
    service_port: int = 8003
    name_suffix: str | None = None
    image_pull_policy: str = "IfNotPresent"
    runtime_secret_name: str | None = "tool-compiler-runtime-secrets"
    labels: dict[str, str] = field(default_factory=dict)


def generate_codegen_manifests(
    service_ir: ServiceIR,
    *,
    config: CodegenManifestConfig,
) -> GeneratedManifestSet:
    """Generate the codegen-mode manifest set for a ServiceIR.

    Currently delegates to the generic manifest generator with a
    ``codegen-runtime`` component label.  When a dedicated codegen
    container image becomes available, this function will produce
    manifests that use ``config.codegen_image`` as the primary
    container and mount the IR as an init-container artifact.
    """
    image = config.codegen_image or config.runtime_image
    generic_config = GenericManifestConfig(
        runtime_image=image,
        service_id=config.service_id,
        version_number=config.version_number,
        namespace=config.namespace,
        replicas=config.replicas,
        container_port=config.container_port,
        service_port=config.service_port,
        name_suffix=config.name_suffix,
        image_pull_policy=config.image_pull_policy,
        runtime_secret_name=config.runtime_secret_name,
        labels={
            **config.labels,
            "tool-compiler-v2/runtime-mode": "codegen",
        },
    )
    result = generate_generic_manifests(service_ir, config=generic_config)

    # Tag the deployment with codegen-specific annotations so
    # downstream tooling can distinguish codegen deployments.
    deployment = dict(result.deployment)
    metadata = dict(deployment.get("metadata", {}))
    annotations = dict(metadata.get("annotations", {}))
    annotations["tool-compiler-v2/runtime-mode"] = "codegen"
    metadata["annotations"] = annotations
    deployment["metadata"] = metadata

    return GeneratedManifestSet(
        config_map=result.config_map,
        deployment=deployment,
        service=result.service,
        network_policy=result.network_policy,
        route_config=result.route_config,
        yaml=result.yaml,
    )


__all__ = [
    "CodegenManifestConfig",
    "generate_codegen_manifests",
]
