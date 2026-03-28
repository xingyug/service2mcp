"""Default production activity handlers for the compilation workflow."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_worker.activities.pipeline import ActivityRegistry
from apps.compiler_worker.models import (
    CompilationContext,
    CompilationStage,
    StageExecutionResult,
)
from libs.db_models import ServiceVersion
from libs.enhancer.enhancer import EnhancerConfig, IREnhancer, create_llm_client
from libs.enhancer.tool_intent import bifurcate_descriptions, derive_tool_intents
from libs.extractors import (
    GraphQLExtractor,
    GrpcProtoExtractor,
    JsonRpcExtractor,
    ODataExtractor,
    OpenAPIExtractor,
    RESTExtractor,
    SCIMExtractor,
    SOAPWSDLExtractor,
    SQLExtractor,
)
from libs.extractors.base import ExtractorProtocol, SourceConfig, TypeDetector
from libs.generator import GeneratedManifestSet, GenericManifestConfig, generate_generic_manifests
from libs.ir import ServiceIR, serialize_ir
from libs.ir.models import (
    EventSupportLevel,
    EventTransport,
    GraphQLOperationType,
    Operation,
    Param,
    SqlOperationType,
)
from libs.registry_client.models import ArtifactRecordPayload, ArtifactVersionCreate
from libs.validator import PostDeployValidator, PreDeployValidator

_logger = logging.getLogger(__name__)

ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
RuntimeHttpClientFactory = Callable[[str], httpx.AsyncClient]
ToolInvokerFactory = Callable[[str], ToolInvoker]

_DEFAULT_RUNTIME_IMAGE = "tool-compiler/mcp-runtime:latest"
_DEFAULT_IMAGE_PULL_POLICY = "IfNotPresent"
_DEFAULT_ROUTE_PUBLISH_MODE = "deferred"
_DEFAULT_PROXY_TIMEOUT_SECONDS = 10.0
_DEFAULT_ROUTE_PUBLISH_TIMEOUT_SECONDS = 10.0
_DEFAULT_RUNTIME_STARTUP_TIMEOUT_SECONDS = 10.0
_DEFAULT_RUNTIME_STARTUP_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class DeploymentResult:
    """Resolved deployment details returned by the manifest deployer."""

    deployment_revision: str
    runtime_base_url: str
    manifest_storage_path: str


class ManifestDeployer(Protocol):
    """Apply and remove generated runtime manifests."""

    async def deploy(self, manifest_set: GeneratedManifestSet) -> DeploymentResult: ...

    async def rollback(
        self,
        manifest_set: GeneratedManifestSet,
        deployment: DeploymentResult,
    ) -> None: ...


class RoutePublisher(Protocol):
    """Publish and optionally rollback route metadata for a compiled service."""

    async def publish(self, route_config: dict[str, Any]) -> dict[str, Any] | None: ...

    async def rollback(
        self,
        route_config: dict[str, Any],
        publication: dict[str, Any] | None,
    ) -> None: ...


@dataclass(frozen=True)
class ProductionActivitySettings:
    """Environment-backed settings for the default production activity handlers."""

    runtime_image: str = _DEFAULT_RUNTIME_IMAGE
    namespace: str = "default"
    image_pull_policy: str = _DEFAULT_IMAGE_PULL_POLICY
    route_publish_mode: str = _DEFAULT_ROUTE_PUBLISH_MODE
    access_control_url: str | None = None
    proxy_timeout_seconds: float = _DEFAULT_PROXY_TIMEOUT_SECONDS
    route_publish_timeout_seconds: float = _DEFAULT_ROUTE_PUBLISH_TIMEOUT_SECONDS
    runtime_startup_timeout_seconds: float = _DEFAULT_RUNTIME_STARTUP_TIMEOUT_SECONDS
    runtime_startup_poll_seconds: float = _DEFAULT_RUNTIME_STARTUP_POLL_SECONDS

    @classmethod
    def from_env(cls) -> ProductionActivitySettings:
        namespace = (
            os.getenv("COMPILER_TARGET_NAMESPACE") or _read_service_account_namespace() or "default"
        )
        runtime_image = (
            os.getenv("MCP_RUNTIME_IMAGE")
            or os.getenv("COMPILER_RUNTIME_IMAGE")
            or _DEFAULT_RUNTIME_IMAGE
        )
        return cls(
            runtime_image=runtime_image,
            namespace=namespace,
            image_pull_policy=os.getenv(
                "MCP_RUNTIME_IMAGE_PULL_POLICY",
                _DEFAULT_IMAGE_PULL_POLICY,
            ),
            route_publish_mode=os.getenv("ROUTE_PUBLISH_MODE", _DEFAULT_ROUTE_PUBLISH_MODE),
            access_control_url=os.getenv("ACCESS_CONTROL_URL"),
            proxy_timeout_seconds=float(
                os.getenv("COMPILER_PROXY_TIMEOUT_SECONDS", str(_DEFAULT_PROXY_TIMEOUT_SECONDS))
            ),
            route_publish_timeout_seconds=float(
                os.getenv(
                    "COMPILER_ROUTE_PUBLISH_TIMEOUT_SECONDS",
                    str(_DEFAULT_ROUTE_PUBLISH_TIMEOUT_SECONDS),
                )
            ),
            runtime_startup_timeout_seconds=float(
                os.getenv(
                    "COMPILER_RUNTIME_STARTUP_TIMEOUT_SECONDS",
                    str(_DEFAULT_RUNTIME_STARTUP_TIMEOUT_SECONDS),
                )
            ),
            runtime_startup_poll_seconds=float(
                os.getenv(
                    "COMPILER_RUNTIME_STARTUP_POLL_SECONDS",
                    str(_DEFAULT_RUNTIME_STARTUP_POLL_SECONDS),
                )
            ),
        )


@dataclass(frozen=True)
class DeferredRoutePublisher:
    """Default route publisher that records route metadata without touching a gateway."""

    mode: str = _DEFAULT_ROUTE_PUBLISH_MODE

    async def publish(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        return {
            "mode": self.mode,
            "default_route_id": route_config["default_route"]["route_id"],
            "version_route_id": (
                route_config["version_route"]["route_id"]
                if isinstance(route_config.get("version_route"), dict)
                else None
            ),
        }

    async def rollback(
        self,
        route_config: dict[str, Any],
        publication: dict[str, Any] | None,
    ) -> None:
        del route_config, publication


@dataclass(frozen=True)
class AccessControlRoutePublisher:
    """Route publisher that delegates publication to the access-control service."""

    base_url: str
    timeout_seconds: float = _DEFAULT_ROUTE_PUBLISH_TIMEOUT_SECONDS
    client: httpx.AsyncClient | None = None

    async def publish(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        payload = await self._post(
            "/api/v1/gateway-binding/service-routes/sync",
            route_config=route_config,
        )
        payload["mode"] = "access-control"
        return payload

    async def rollback(
        self,
        route_config: dict[str, Any],
        publication: dict[str, Any] | None,
    ) -> None:
        await self._post(
            "/api/v1/gateway-binding/service-routes/rollback",
            route_config=route_config,
            previous_routes=cast(
                dict[str, dict[str, Any]],
                (publication or {}).get("previous_routes", {}),
            ),
        )

    async def _post(
        self,
        path: str,
        *,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout_seconds,
        )
        try:
            response = await client.post(
                path,
                json={
                    "route_config": route_config,
                    "previous_routes": previous_routes or {},
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Access control route publisher returned a non-object response.")
            return cast(dict[str, Any], payload)
        finally:
            if owns_client:
                await client.aclose()


@dataclass
class KubernetesAPISession:
    """Minimal async client for the in-cluster Kubernetes API."""

    client: httpx.AsyncClient
    namespace: str

    @classmethod
    def from_in_cluster(
        cls,
        *,
        namespace: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> KubernetesAPISession:
        if client is not None:
            return cls(client=client, namespace=namespace)

        host = os.getenv("KUBERNETES_SERVICE_HOST")
        port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            raise RuntimeError(
                "Kubernetes manifest deployment requires KUBERNETES_SERVICE_HOST "
                "or an explicit HTTP client."
            )

        token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        cert_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        if not token_path.exists() or not cert_path.exists():
            raise RuntimeError(
                "Kubernetes manifest deployment requires an in-cluster service account token "
                "or an explicit HTTP client."
            )

        token = token_path.read_text(encoding="utf-8").strip()
        base_url = f"https://{host}:{port}"
        return cls(
            client=httpx.AsyncClient(
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
                verify=str(cert_path),
                timeout=timeout,
            ),
            namespace=namespace,
        )

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass
class KubernetesManifestDeployer:
    """Deploy generated manifests into the target Kubernetes namespace."""

    api: KubernetesAPISession
    owns_api_client: bool = True
    rollout_poll_seconds: float = 0.5
    rollout_timeout_seconds: float = 60.0

    async def deploy(self, manifest_set: GeneratedManifestSet) -> DeploymentResult:
        try:
            await self._apply_manifest("configmaps", "v1", manifest_set.config_map)
            deployment_response = await self._apply_manifest(
                "deployments",
                "apps/v1",
                manifest_set.deployment,
            )
            await self._apply_manifest("services", "v1", manifest_set.service)
            await self._apply_manifest(
                "networkpolicies",
                "networking.k8s.io/v1",
                manifest_set.network_policy,
            )
            deploy_meta = manifest_set.deployment.get("metadata", {})
            deploy_spec = manifest_set.deployment.get("spec", {})
            svc_meta = manifest_set.service.get("metadata", {})
            svc_ports = manifest_set.service.get("spec", {}).get("ports", [])

            observed_generation = await self._wait_for_rollout(
                deploy_meta.get("name", "unknown"),
                expected_replicas=int(deploy_spec.get("replicas", 1)),
            )
            deployment_name = str(deploy_meta.get("name", "unknown"))
            service_name = str(svc_meta.get("name", "unknown"))
            service_port = int(svc_ports[0]["port"]) if svc_ports else 8080
            resource_version = str(
                deployment_response.get("metadata", {}).get("resourceVersion", "unknown")
            )
            namespace = self.api.namespace
            return DeploymentResult(
                deployment_revision=f"{deployment_name}@g{observed_generation}-rv{resource_version}",
                runtime_base_url=(
                    f"http://{service_name}.{namespace}.svc.cluster.local:{service_port}"
                ),
                manifest_storage_path=f"k8s://{namespace}/deployments/{deployment_name}",
            )
        finally:
            if self.owns_api_client:
                await self.api.aclose()

    async def rollback(
        self,
        manifest_set: GeneratedManifestSet,
        deployment: DeploymentResult,
    ) -> None:
        del deployment
        try:
            await self._delete_manifest(
                "networkpolicies",
                "networking.k8s.io/v1",
                str(manifest_set.network_policy["metadata"]["name"]),
            )
            await self._delete_manifest(
                "services",
                "v1",
                str(manifest_set.service["metadata"]["name"]),
            )
            await self._delete_manifest(
                "deployments",
                "apps/v1",
                str(manifest_set.deployment["metadata"]["name"]),
            )
            await self._delete_manifest(
                "configmaps",
                "v1",
                str(manifest_set.config_map["metadata"]["name"]),
            )
        finally:
            if self.owns_api_client:
                await self.api.aclose()

    async def _apply_manifest(
        self,
        plural: str,
        api_version: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(manifest["metadata"]["name"])
        named_path, collection_path = self._resource_paths(plural, api_version, name)
        response = await self.api.client.patch(
            named_path,
            json=manifest,
            headers={"Content-Type": "application/merge-patch+json"},
        )
        if response.status_code == 404:
            response = await self.api.client.post(collection_path, json=manifest)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def _delete_manifest(self, plural: str, api_version: str, name: str) -> None:
        named_path, _ = self._resource_paths(plural, api_version, name)
        response = await self.api.client.delete(named_path)
        if response.status_code not in {200, 202, 404}:
            response.raise_for_status()

    async def _wait_for_rollout(self, deployment_name: str, *, expected_replicas: int) -> int:
        named_path, _ = self._resource_paths("deployments", "apps/v1", deployment_name)
        timeout_seconds = self.rollout_timeout_seconds
        poll_seconds = self.rollout_poll_seconds
        elapsed = 0.0
        while elapsed < timeout_seconds:
            response = await self.api.client.get(named_path)
            response.raise_for_status()
            deployment = cast(dict[str, Any], response.json())
            metadata = cast(dict[str, Any], deployment.get("metadata", {}))
            status = cast(dict[str, Any], deployment.get("status", {}))
            observed_generation = int(status.get("observedGeneration", 0) or 0)
            generation = int(metadata.get("generation", 0) or 0)
            available_replicas = int(status.get("availableReplicas", 0) or 0)
            if observed_generation >= generation and available_replicas >= expected_replicas:
                return observed_generation
            await _sleep_seconds(poll_seconds)
            elapsed += poll_seconds
        raise RuntimeError(
            f"Timed out waiting for Kubernetes rollout of deployment {deployment_name}."
        )

    def _resource_paths(
        self,
        plural: str,
        api_version: str,
        name: str,
    ) -> tuple[str, str]:
        namespace = self.api.namespace
        if "/" in api_version:
            group, version = api_version.split("/", 1)
            base = f"/apis/{group}/{version}/namespaces/{namespace}/{plural}"
        else:
            base = f"/api/{api_version}/namespaces/{namespace}/{plural}"
        return f"{base}/{name}", base


def create_default_activity_registry(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: ProductionActivitySettings | None = None,
    deployer: ManifestDeployer | None = None,
    route_publisher: RoutePublisher | None = None,
    runtime_http_client_factory: RuntimeHttpClientFactory | None = None,
    tool_invoker_factory: ToolInvokerFactory | None = None,
) -> ActivityRegistry:
    """Build the default production workflow activity registry."""

    resolved_settings = settings or ProductionActivitySettings.from_env()
    resolved_route_publisher: RoutePublisher
    if route_publisher is None:
        if resolved_settings.route_publish_mode == "deferred":
            resolved_route_publisher = DeferredRoutePublisher(mode="deferred")
        elif resolved_settings.route_publish_mode == "access-control":
            if not resolved_settings.access_control_url:
                raise RuntimeError("ROUTE_PUBLISH_MODE=access-control requires ACCESS_CONTROL_URL.")
            resolved_route_publisher = AccessControlRoutePublisher(
                base_url=resolved_settings.access_control_url,
                timeout_seconds=resolved_settings.route_publish_timeout_seconds,
            )
        else:
            raise RuntimeError(
                f"Unsupported ROUTE_PUBLISH_MODE: {resolved_settings.route_publish_mode}."
            )
    else:
        resolved_route_publisher = route_publisher
    if deployer is None:

        def deployer_factory() -> ManifestDeployer:
            return KubernetesManifestDeployer(
                api=KubernetesAPISession.from_in_cluster(namespace=resolved_settings.namespace)
            )
    else:
        assert deployer is not None

        def deployer_factory() -> ManifestDeployer:
            return deployer

    resolved_runtime_http_client_factory: RuntimeHttpClientFactory
    if runtime_http_client_factory is None:

        def default_runtime_http_client_factory(base_url: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url=base_url,
                follow_redirects=True,
                timeout=resolved_settings.proxy_timeout_seconds,
            )

        resolved_runtime_http_client_factory = default_runtime_http_client_factory
    else:
        resolved_runtime_http_client_factory = runtime_http_client_factory

    resolved_tool_invoker_factory: ToolInvokerFactory
    if tool_invoker_factory is None:

        def default_tool_invoker_factory(base_url: str) -> ToolInvoker:
            return build_streamable_http_tool_invoker(
                base_url,
                http_client_factory=resolved_runtime_http_client_factory,
            )

        resolved_tool_invoker_factory = default_tool_invoker_factory
    else:
        resolved_tool_invoker_factory = tool_invoker_factory

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        extractors = _build_extractors()
        try:
            detector = TypeDetector(extractors)
            detection = detector.detect(_source_config_from_context(context))
            return _stage_result(
                context_updates={"detection_confidence": detection.confidence},
                event_detail={"confidence": detection.confidence},
                protocol=detection.protocol_name,
            )
        finally:
            _close_extractors(extractors)

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        source = _source_config_from_context(context)
        extractors = _build_extractors()
        try:
            extractor = _resolve_extractor(context, source, extractors)
            service_ir = extractor.extract(source)
            if context.request.service_name:
                service_ir = service_ir.model_copy(
                    update={"service_name": context.request.service_name}
                )
            service_id = context.request.service_name or service_ir.service_name
            version_number = await _next_version_number(session_factory, service_id)
            return _stage_result(
                context_updates={
                    "service_id": service_id,
                    "service_ir": service_ir.model_dump(mode="json"),
                    "source_hash": service_ir.source_hash,
                    "version_number": version_number,
                },
                event_detail={"operation_count": len(service_ir.operations)},
                protocol=service_ir.protocol,
                service_name=service_id,
            )
        finally:
            _close_extractors(extractors)

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        if not _enhancement_enabled():
            # Still apply deterministic intent derivation even without LLM.
            service_ir = _apply_post_enhancement(service_ir)
            return _stage_result(
                context_updates={
                    "service_ir": service_ir.model_dump(mode="json"),
                    "token_usage": {
                        "model": "disabled",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_calls": 0,
                    },
                },
                event_detail={"mode": "passthrough"},
                protocol=service_ir.protocol,
                service_name=context.payload.get("service_id"),
            )

        enhancer_config = EnhancerConfig.from_env()
        result = IREnhancer(
            create_llm_client(enhancer_config),
            config=enhancer_config,
        ).enhance(service_ir)
        enhanced_ir = _apply_post_enhancement(
            result.enhanced_ir,
            llm_client_factory=lambda: create_llm_client(enhancer_config),
        )
        token_usage = {
            "model": result.token_usage.model,
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
            "total_calls": result.token_usage.total_calls,
        }
        return _stage_result(
            context_updates={
                "service_ir": enhanced_ir.model_dump(mode="json"),
                "token_usage": token_usage,
            },
            event_detail={
                "operations_enhanced": result.operations_enhanced,
                "operations_skipped": result.operations_skipped,
                "model": result.token_usage.model,
            },
            protocol=enhanced_ir.protocol,
            service_name=context.payload.get("service_id"),
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        async with PreDeployValidator(
            allow_native_grpc_stream=_has_supported_native_grpc_stream(service_ir),
            allow_native_grpc_unary=_has_native_grpc_unary(service_ir),
        ) as validator:
            report = await validator.validate(service_ir)
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
            protocol=context.protocol,
            service_name=context.payload.get("service_id"),
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image=resolved_settings.runtime_image,
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                namespace=resolved_settings.namespace,
                image_pull_policy=resolved_settings.image_pull_policy,
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
                "generated_manifest_set": _serialize_manifest_set(manifest_set),
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"manifest_set": _serialize_manifest_set(manifest_set)},
            protocol=service_ir.protocol,
            service_name=context.payload.get("service_id"),
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        manifest_set = _manifest_set_from_context(context)
        deployment_result = await deployer_factory().deploy(manifest_set)
        return _stage_result(
            context_updates={
                "deployment_revision": deployment_result.deployment_revision,
                "runtime_base_url": deployment_result.runtime_base_url,
                "manifest_storage_path": deployment_result.manifest_storage_path,
            },
            event_detail={
                "deployment_revision": deployment_result.deployment_revision,
                "runtime_base_url": deployment_result.runtime_base_url,
            },
            rollback_payload={
                "manifest_set": _serialize_manifest_set(manifest_set),
                "deployment": {
                    "deployment_revision": deployment_result.deployment_revision,
                    "runtime_base_url": deployment_result.runtime_base_url,
                    "manifest_storage_path": deployment_result.manifest_storage_path,
                },
            },
            protocol=context.protocol,
            service_name=context.payload.get("service_id"),
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        runtime_base_url = str(context.payload["runtime_base_url"])
        await _wait_for_runtime_http_ready(
            runtime_base_url,
            client_factory=resolved_runtime_http_client_factory,
            timeout_seconds=resolved_settings.runtime_startup_timeout_seconds,
            poll_seconds=resolved_settings.runtime_startup_poll_seconds,
        )
        sample_invocations = _build_sample_invocations(service_ir)
        client = resolved_runtime_http_client_factory(runtime_base_url)
        try:
            async with PostDeployValidator(
                client=client,
                tool_invoker=resolved_tool_invoker_factory(runtime_base_url),
            ) as validator:
                report = await validator.validate(
                    runtime_base_url,
                    service_ir,
                    sample_invocations=sample_invocations,
                )
        finally:
            await client.aclose()

        if not report.overall_passed:
            raise RuntimeError(_validation_failure_message("Post-deploy validation failed", report))

        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
            protocol=service_ir.protocol,
            service_name=context.payload.get("service_id"),
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = cast(dict[str, Any], context.payload["route_config"])
        publication = await resolved_route_publisher.publish(route_config)
        return _stage_result(
            context_updates={"route_publication": publication},
            event_detail={
                "route_id": route_config["default_route"]["route_id"],
                "publication_mode": resolved_settings.route_publish_mode,
            },
            rollback_payload={
                "route_config": route_config,
                "publication": publication,
            },
            protocol=context.protocol,
            service_name=context.payload.get("service_id"),
        )

    async def register_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        service_id = str(context.payload["service_id"])
        version_number = int(context.payload["version_number"])
        manifest_yaml = str(context.payload["manifest_yaml"])
        manifest_storage_path = str(
            context.payload.get("manifest_storage_path", f"inline://generated/{context.job_id}")
        )
        deployment_revision = cast(str | None, context.payload.get("deployment_revision"))
        route_config = cast(dict[str, Any], context.payload.get("route_config"))
        validation_report = cast(
            dict[str, Any] | None,
            context.payload.get("post_validation_report"),
        )
        artifact_payload = ArtifactVersionCreate(
            service_id=service_id,
            version_number=version_number,
            ir_json=service_ir.model_dump(mode="json"),
            compiler_version=service_ir.compiler_version,
            source_url=service_ir.source_url,
            source_hash=service_ir.source_hash,
            protocol=service_ir.protocol,
            validation_report=validation_report,
            deployment_revision=deployment_revision,
            route_config=route_config,
            tenant=service_ir.tenant,
            environment=service_ir.environment,
            is_active=True,
            artifacts=[
                ArtifactRecordPayload(
                    artifact_type="manifest",
                    content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                    storage_path=manifest_storage_path,
                ),
                ArtifactRecordPayload(
                    artifact_type="service_ir",
                    content_hash=hashlib.sha256(
                        serialize_ir(service_ir).encode("utf-8")
                    ).hexdigest(),
                    storage_path=(f"inline://service-ir/{service_id}/v{version_number}"),
                ),
            ],
        )
        async with session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            created = await repository.create_version(artifact_payload)
        return _stage_result(
            context_updates={"registered_version": created.version_number},
            event_detail={
                "service_id": created.service_id,
                "version_number": created.version_number,
            },
            protocol=service_ir.protocol,
            service_name=service_id,
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        rollback_payload = result.rollback_payload or {}
        manifest_payload = rollback_payload.get("manifest_set")
        deployment_payload = rollback_payload.get("deployment")
        if not isinstance(manifest_payload, dict) or not isinstance(deployment_payload, dict):
            _logger.warning(
                "deploy_rollback skipped: invalid rollback payload types "
                "(manifest_payload=%s, deployment_payload=%s)",
                type(manifest_payload).__name__,
                type(deployment_payload).__name__,
            )
            return
        manifest_set = _deserialize_manifest_set(manifest_payload)
        deployment = DeploymentResult(
            deployment_revision=str(deployment_payload["deployment_revision"]),
            runtime_base_url=str(deployment_payload["runtime_base_url"]),
            manifest_storage_path=str(deployment_payload["manifest_storage_path"]),
        )
        await deployer_factory().rollback(manifest_set, deployment)
        del context

    async def route_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        rollback_payload = result.rollback_payload or {}
        route_config = rollback_payload.get("route_config")
        if not isinstance(route_config, dict):
            _logger.warning(
                "route_rollback skipped: route_config is %s, not dict",
                type(route_config).__name__,
            )
            return
        publication = cast(dict[str, Any] | None, rollback_payload.get("publication"))
        await resolved_route_publisher.rollback(route_config, publication)
        del context

    return ActivityRegistry(
        stage_handlers={
            CompilationStage.DETECT: detect_stage,
            CompilationStage.EXTRACT: extract_stage,
            CompilationStage.ENHANCE: enhance_stage,
            CompilationStage.VALIDATE_IR: validate_ir_stage,
            CompilationStage.GENERATE: generate_stage,
            CompilationStage.DEPLOY: deploy_stage,
            CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
            CompilationStage.ROUTE: route_stage,
            CompilationStage.REGISTER: register_stage,
        },
        rollback_handlers={
            CompilationStage.DEPLOY: deploy_rollback,
            CompilationStage.ROUTE: route_rollback,
        },
    )


def _source_config_from_context(context: CompilationContext) -> SourceConfig:
    options = context.request.options
    raw_hints = options.get("hints", {})
    hints = (
        {str(key): str(value) for key, value in raw_hints.items()}
        if isinstance(raw_hints, Mapping)
        else {}
    )
    protocol_hint = options.get("protocol")
    if isinstance(protocol_hint, str) and protocol_hint:
        hints.setdefault("protocol", protocol_hint)

    auth_header = options.get("auth_header")
    auth_token = options.get("auth_token")
    source_file_path = options.get("source_file_path")

    return SourceConfig(
        url=context.request.source_url,
        file_path=str(source_file_path) if isinstance(source_file_path, str) else None,
        file_content=context.request.source_content,
        auth_header=str(auth_header) if isinstance(auth_header, str) else None,
        auth_token=str(auth_token) if isinstance(auth_token, str) else None,
        hints=hints,
    )


def _build_extractors() -> list[ExtractorProtocol]:
    return [
        OpenAPIExtractor(),
        GraphQLExtractor(),
        GrpcProtoExtractor(),
        SOAPWSDLExtractor(),
        ODataExtractor(),
        SCIMExtractor(),
        JsonRpcExtractor(),
        SQLExtractor(),
        RESTExtractor(),
    ]


def _resolve_extractor(
    context: CompilationContext,
    source: SourceConfig,
    extractors: list[ExtractorProtocol],
) -> ExtractorProtocol:
    protocol = context.protocol or source.hints.get("protocol")
    if protocol:
        for extractor in extractors:
            if extractor.protocol_name == protocol:
                return extractor
    detector = TypeDetector(extractors)
    detection = detector.detect(source)
    return detection.extractor


def _close_extractors(extractors: list[ExtractorProtocol]) -> None:
    for extractor in extractors:
        close = getattr(extractor, "close", None)
        if callable(close):
            close()


async def _next_version_number(
    session_factory: async_sessionmaker[AsyncSession],
    service_id: str,
) -> int:
    async with session_factory() as session:
        current_max = await session.scalar(
            select(func.max(ServiceVersion.version_number)).where(
                ServiceVersion.service_id == service_id
            )
        )
    return int(current_max or 0) + 1


def _enhancement_enabled() -> bool:
    if os.getenv("WORKER_ENABLE_LLM_ENHANCEMENT", "").lower() in {"1", "true", "yes"}:
        return True
    return bool(os.getenv("LLM_API_KEY") or os.getenv("VERTEX_PROJECT_ID"))


def _tool_grouping_enabled() -> bool:
    return os.getenv("WORKER_ENABLE_TOOL_GROUPING", "").lower() in {"1", "true", "yes"}


def _apply_post_enhancement(
    ir: ServiceIR,
    *,
    llm_client_factory: Callable[[], Any] | None = None,
) -> ServiceIR:
    """Apply deterministic post-enhancement transforms to a ServiceIR.

    Always runs:
    - ``derive_tool_intents`` — tags each operation with discovery/action intent
    - ``bifurcate_descriptions`` — prepends [DISCOVERY]/[ACTION] to descriptions
    - ``normalize_error_schemas`` — ensures every operation has a non-empty error model

    Opt-in (``WORKER_ENABLE_TOOL_GROUPING=1`` + LLM available):
    - ``ToolGrouper`` — clusters operations into business-intent groups

    Opt-in (LLM available):
    - ``ExamplesGenerator`` — synthesises response examples from schema
    """
    from libs.enhancer.error_normalizer import normalize_error_schemas
    from libs.enhancer.examples_generator import ExamplesGenerator
    from libs.enhancer.tool_grouping import ToolGrouper, apply_grouping

    ir = derive_tool_intents(ir)
    ir = bifurcate_descriptions(ir)

    if _tool_grouping_enabled() and llm_client_factory is not None:
        try:
            grouper = ToolGrouper(llm_client_factory())
            grouping_result = grouper.group(ir)
            ir = apply_grouping(ir, grouping_result)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Tool grouping failed; continuing without grouping",
                exc_info=True,
            )

    # Error normalization: always run (deterministic, no LLM needed)
    ir = normalize_error_schemas(ir)

    # Examples generation: only if LLM client is available
    if llm_client_factory is not None:
        try:
            generator = ExamplesGenerator(llm_client_factory())
            ir = generator.generate(ir)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Examples generation failed; continuing without examples",
                exc_info=True,
            )

    return ir


def _stage_result(
    *,
    context_updates: dict[str, Any] | None = None,
    event_detail: dict[str, Any] | None = None,
    rollback_payload: dict[str, Any] | None = None,
    protocol: str | None = None,
    service_name: str | None = None,
) -> StageExecutionResult:
    return StageExecutionResult(
        context_updates=context_updates or {},
        event_detail=event_detail,
        rollback_payload=rollback_payload,
        protocol=protocol,
        service_name=service_name,
    )


def _serialize_manifest_set(manifest_set: GeneratedManifestSet) -> dict[str, Any]:
    return {
        "config_map": manifest_set.config_map,
        "deployment": manifest_set.deployment,
        "service": manifest_set.service,
        "network_policy": manifest_set.network_policy,
        "route_config": manifest_set.route_config,
        "yaml": manifest_set.yaml,
    }


def _deserialize_manifest_set(payload: dict[str, Any]) -> GeneratedManifestSet:
    return GeneratedManifestSet(
        config_map=cast(dict[str, Any], payload["config_map"]),
        deployment=cast(dict[str, Any], payload["deployment"]),
        service=cast(dict[str, Any], payload["service"]),
        network_policy=cast(dict[str, Any], payload["network_policy"]),
        route_config=cast(dict[str, Any], payload["route_config"]),
        yaml=str(payload["yaml"]),
    )


def _manifest_set_from_context(context: CompilationContext) -> GeneratedManifestSet:
    serialized = context.payload.get("generated_manifest_set")
    if not isinstance(serialized, dict):
        raise RuntimeError("Generated manifest set missing from workflow context.")
    return _deserialize_manifest_set(serialized)


def _sample_value(param: Param) -> Any:
    if param.default is not None:
        return param.default
    lowered_name = param.name.lower()
    if lowered_name == "status":
        return "available"
    if param.type == "integer":
        return 1
    if param.type == "number":
        return 1.0
    if param.type == "boolean":
        return True
    if param.type == "array":
        return ["sample"]
    if param.type == "object":
        return {"name": "sample"}
    if lowered_name.endswith("id"):
        return "1"
    return "sample"


def build_sample_invocations(service_ir: ServiceIR) -> dict[str, dict[str, Any]]:
    return {
        operation.id: _sample_arguments_for_operation(service_ir, operation)
        for operation in service_ir.operations
        if operation.enabled
    }


# Backwards-compatible alias for existing internal callers and tests.
_build_sample_invocations = build_sample_invocations


def _sample_arguments_for_operation(
    service_ir: ServiceIR,
    operation: Operation,
) -> dict[str, Any]:
    if operation.sql is not None:
        return _sample_sql_arguments(operation)
    if operation.graphql is not None:
        return _sample_graphql_arguments(operation)
    if service_ir.protocol == "grpc":
        return _sample_grpc_arguments(operation)
    return {param.name: _sample_value(param) for param in operation.params}


def _sample_grpc_arguments(operation: Operation) -> dict[str, Any]:
    arguments = {
        param.name: _sample_value(param)
        for param in operation.params
        if param.required or param.default is not None
    }
    for param in operation.params:
        if param.name in arguments:
            continue
        if param.type in {"array", "object"}:
            continue
        if _is_safe_optional_grpc_sample_param(param):
            arguments[param.name] = _sample_value(param)
    return arguments


def _is_safe_optional_grpc_sample_param(param: Param) -> bool:
    lowered_name = param.name.lower()
    if lowered_name.endswith("id"):
        return True
    return lowered_name in {
        "cursor",
        "limit",
        "location",
        "location_id",
        "offset",
        "page",
        "page_size",
        "page_token",
        "q",
        "query",
        "search",
        "sku",
        "term",
    }


def _sample_graphql_arguments(operation: Operation) -> dict[str, Any]:
    if operation.graphql is None:
        return {param.name: _sample_value(param) for param in operation.params}

    arguments: dict[str, Any] = {}
    for param in operation.params:
        if param.required or param.default is not None:
            arguments[param.name] = _sample_value(param)

    if arguments:
        return arguments

    if operation.graphql.operation_type is GraphQLOperationType.query:
        return {}

    return {param.name: _sample_value(param) for param in operation.params}


def _sample_sql_arguments(operation: Operation) -> dict[str, Any]:
    if operation.sql is None:
        return {param.name: _sample_value(param) for param in operation.params}

    if operation.sql.action is SqlOperationType.query:
        arguments: dict[str, Any] = {}
        for param in operation.params:
            if param.name == "limit":
                arguments[param.name] = param.default if param.default is not None else 1
                continue
            if param.required:
                arguments[param.name] = _sample_value(param)
        return arguments

    return {param.name: _sample_value(param) for param in operation.params if param.required}


def _validation_failure_message(prefix: str, report: Any) -> str:
    failed_results = [
        f"{result.stage}: {result.details}" for result in report.results if not result.passed
    ]
    if not failed_results:
        return prefix
    return f"{prefix}: {'; '.join(failed_results)}"


async def _wait_for_runtime_http_ready(
    runtime_base_url: str,
    *,
    client_factory: RuntimeHttpClientFactory,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    health_url = f"{runtime_base_url.rstrip('/')}/healthz"
    ready_url = f"{runtime_base_url.rstrip('/')}/readyz"
    elapsed = 0.0
    last_error: str | None = None

    while elapsed <= timeout_seconds:
        client = client_factory(runtime_base_url)
        try:
            health_response = await client.get(health_url)
            ready_response = await client.get(ready_url)
        except httpx.RequestError as exc:
            last_error = str(exc)
        else:
            if health_response.status_code == 200 and ready_response.status_code == 200:
                return
            last_error = (
                f"healthz={health_response.status_code}, readyz={ready_response.status_code}"
            )
        finally:
            await client.aclose()

        if elapsed >= timeout_seconds:
            break

        sleep_seconds = min(poll_seconds, timeout_seconds - elapsed)
        if sleep_seconds <= 0:
            break
        await _sleep_seconds(sleep_seconds)
        elapsed += sleep_seconds

    if last_error is None:
        last_error = "runtime did not become reachable"
    raise RuntimeError(
        f"Runtime readiness check timed out after {timeout_seconds:.1f}s: {last_error}"
    )


def build_streamable_http_tool_invoker(
    runtime_base_url: str,
    *,
    http_client_factory: RuntimeHttpClientFactory | None = None,
) -> ToolInvoker:
    endpoint = f"{runtime_base_url.rstrip('/')}/mcp/mcp"

    async def invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        client = (
            http_client_factory(runtime_base_url)
            if http_client_factory is not None
            else httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        )
        async with client:
            async with streamable_http_client(endpoint, http_client=client) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
        if result.isError:
            return {"status": "error", "error": result.content}
        structured = result.structuredContent
        if isinstance(structured, dict):
            return structured
        return {"status": "ok", "result": structured}

    return invoke


def _read_service_account_namespace() -> str | None:
    namespace_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not namespace_path.exists():
        return None
    namespace = namespace_path.read_text(encoding="utf-8").strip()
    return namespace or None


def _has_supported_native_grpc_stream(service_ir: ServiceIR) -> bool:
    return any(
        descriptor.transport is EventTransport.grpc_stream
        and descriptor.support is EventSupportLevel.supported
        for descriptor in service_ir.event_descriptors
    )


def _has_native_grpc_unary(service_ir: ServiceIR) -> bool:
    return any(
        operation.enabled and operation.grpc_unary is not None
        for operation in service_ir.operations
    )


async def _sleep_seconds(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


__all__ = [
    "AccessControlRoutePublisher",
    "DeferredRoutePublisher",
    "DeploymentResult",
    "KubernetesAPISession",
    "KubernetesManifestDeployer",
    "ManifestDeployer",
    "ProductionActivitySettings",
    "RoutePublisher",
    "create_default_activity_registry",
    "build_streamable_http_tool_invoker",
]
