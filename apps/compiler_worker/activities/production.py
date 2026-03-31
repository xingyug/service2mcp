"""Default production activity handlers for the compilation workflow."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.access_control.authn.service import build_service_jwt
from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_worker.activities.pipeline import ActivityRegistry
from apps.compiler_worker.models import (
    CompilationContext,
    CompilationStage,
    StageExecutionResult,
)
from apps.compiler_worker.workflows.rollback_workflow import (
    RollbackDeployer,
    RollbackPublisher,
    RollbackValidator,
    RollbackVersionStore,
    RollbackWorkflow,
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
from libs.generator import (
    GeneratedManifestSet,
    GenericManifestConfig,
    generate_generic_manifests,
)
from libs.generator.codegen_mode import CodegenManifestConfig, generate_codegen_manifests
from libs.ir import ServiceIR, serialize_ir
from libs.ir.models import (
    AuthConfig,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationType,
    Operation,
    Param,
    SqlOperationType,
)
from libs.registry_client.models import (
    ArtifactRecordPayload,
    ArtifactVersionCreate,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)
from libs.sample_placeholders import (
    PATH_PLACEHOLDER_ID_SAMPLE,
    PATH_PLACEHOLDER_INT_SAMPLE,
    PATH_PLACEHOLDER_NUMBER_SAMPLE,
    PATH_PLACEHOLDER_STRING_SAMPLE,
)
from libs.validator import PostDeployValidator, PreDeployValidator

_logger = logging.getLogger(__name__)

ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
RuntimeHttpClientFactory = Callable[[str], httpx.AsyncClient]
ToolInvokerFactory = Callable[[str], ToolInvoker]

_DEFAULT_RUNTIME_IMAGE = "tool-compiler/mcp-runtime:latest"
_DEFAULT_IMAGE_PULL_POLICY = "IfNotPresent"
_DEFERRED_ROUTE_PUBLISH_MODE = "deferred"
_DEFAULT_PROXY_TIMEOUT_SECONDS = 10.0
_DEFAULT_ROUTE_PUBLISH_TIMEOUT_SECONDS = 10.0
_DEFAULT_RUNTIME_STARTUP_TIMEOUT_SECONDS = 10.0
_DEFAULT_RUNTIME_STARTUP_POLL_SECONDS = 1.0
_PATH_PLACEHOLDER_PATTERN = re.compile(r"{([^{}]+)}")


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("Non-numeric env var %s=%r, using default %s", key, raw, default)
        return default


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
    route_publish_mode: str | None = None
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
        route_publish_mode = (os.getenv("ROUTE_PUBLISH_MODE") or "").strip() or None
        return cls(
            runtime_image=runtime_image,
            namespace=namespace,
            image_pull_policy=os.getenv(
                "MCP_RUNTIME_IMAGE_PULL_POLICY",
                _DEFAULT_IMAGE_PULL_POLICY,
            ),
            route_publish_mode=route_publish_mode,
            access_control_url=os.getenv("ACCESS_CONTROL_URL"),
            proxy_timeout_seconds=_float_env(
                "COMPILER_PROXY_TIMEOUT_SECONDS", _DEFAULT_PROXY_TIMEOUT_SECONDS
            ),
            route_publish_timeout_seconds=_float_env(
                "COMPILER_ROUTE_PUBLISH_TIMEOUT_SECONDS",
                _DEFAULT_ROUTE_PUBLISH_TIMEOUT_SECONDS,
            ),
            runtime_startup_timeout_seconds=_float_env(
                "COMPILER_RUNTIME_STARTUP_TIMEOUT_SECONDS",
                _DEFAULT_RUNTIME_STARTUP_TIMEOUT_SECONDS,
            ),
            runtime_startup_poll_seconds=_float_env(
                "COMPILER_RUNTIME_STARTUP_POLL_SECONDS",
                _DEFAULT_RUNTIME_STARTUP_POLL_SECONDS,
            ),
        )


def _resolve_route_publisher(
    resolved_settings: ProductionActivitySettings,
    route_publisher: RoutePublisher | None,
) -> RoutePublisher:
    if route_publisher is not None:
        return route_publisher
    mode = (resolved_settings.route_publish_mode or "").strip()
    if not mode:
        raise RuntimeError(
            "ROUTE_PUBLISH_MODE must be explicitly set to a supported publisher mode."
        )
    if mode == _DEFERRED_ROUTE_PUBLISH_MODE:
        return DeferredRoutePublisher(mode=_DEFERRED_ROUTE_PUBLISH_MODE)
    if mode == "access-control":
        if not resolved_settings.access_control_url:
            raise RuntimeError("ROUTE_PUBLISH_MODE=access-control requires ACCESS_CONTROL_URL.")
        return AccessControlRoutePublisher(
            base_url=resolved_settings.access_control_url,
            timeout_seconds=resolved_settings.route_publish_timeout_seconds,
        )
    raise RuntimeError(f"Unsupported ROUTE_PUBLISH_MODE: {mode}.")


def _resolve_manifest_deployer_factory(
    resolved_settings: ProductionActivitySettings,
    deployer: ManifestDeployer | None,
) -> Callable[[], ManifestDeployer]:
    if deployer is not None:

        def provided_deployer_factory() -> ManifestDeployer:
            return deployer

        return provided_deployer_factory

    def default_deployer_factory() -> ManifestDeployer:
        return KubernetesManifestDeployer(
            api=KubernetesAPISession.from_in_cluster(namespace=resolved_settings.namespace)
        )

    return default_deployer_factory


def _resolve_runtime_http_client_factory(
    resolved_settings: ProductionActivitySettings,
    runtime_http_client_factory: RuntimeHttpClientFactory | None,
) -> RuntimeHttpClientFactory:
    if runtime_http_client_factory is not None:
        return runtime_http_client_factory

    def default_runtime_http_client_factory(base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            follow_redirects=True,
            timeout=resolved_settings.proxy_timeout_seconds,
        )

    return default_runtime_http_client_factory


def _resolve_tool_invoker_factory(
    resolved_runtime_http_client_factory: RuntimeHttpClientFactory,
    tool_invoker_factory: ToolInvokerFactory | None,
) -> ToolInvokerFactory:
    if tool_invoker_factory is not None:
        return tool_invoker_factory

    def default_tool_invoker_factory(base_url: str) -> ToolInvoker:
        return build_streamable_http_tool_invoker(
            base_url,
            http_client_factory=resolved_runtime_http_client_factory,
        )

    return default_tool_invoker_factory


@dataclass
class ArtifactRegistryRollbackStore(RollbackVersionStore):
    """Rollback store backed by the artifact registry repository."""

    session_factory: async_sessionmaker[AsyncSession]

    async def get_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        async with self.session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.get_version(
                service_id,
                version_number,
                tenant=tenant,
                environment=environment,
            )

    async def get_active_version(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        async with self.session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.get_active_version(
                service_id,
                tenant=tenant,
                environment=environment,
            )

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        async with self.session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.update_version(
                service_id,
                version_number,
                payload,
                tenant=tenant,
                environment=environment,
            )

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        async with self.session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.activate_version(
                service_id,
                version_number,
                tenant=tenant,
                environment=environment,
            )


def _generate_manifest_set_for_version(
    version: ArtifactVersionResponse,
    *,
    settings: ProductionActivitySettings,
    request_options: Mapping[str, Any],
) -> GeneratedManifestSet:
    service_ir = ServiceIR.model_validate(version.ir_json)
    runtime_mode = request_options.get("runtime_mode", "generic")
    if runtime_mode == "codegen":
        return generate_codegen_manifests(
            service_ir,
            config=CodegenManifestConfig(
                runtime_image=settings.runtime_image,
                service_id=version.service_id,
                version_number=version.version_number,
                namespace=settings.namespace,
                image_pull_policy=settings.image_pull_policy,
            ),
        )
    return generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image=settings.runtime_image,
            service_id=version.service_id,
            version_number=version.version_number,
            namespace=settings.namespace,
            image_pull_policy=settings.image_pull_policy,
        ),
    )


@dataclass
class GeneratedManifestRollbackDeployer(RollbackDeployer):
    """Rollback deployer that regenerates and reapplies runtime manifests."""

    settings: ProductionActivitySettings
    request_options: Mapping[str, Any]
    deployer_factory: Callable[[], ManifestDeployer]
    _latest_result: DeploymentResult | None = field(default=None, init=False, repr=False)

    async def apply_version(self, version: ArtifactVersionResponse) -> str:
        manifest_set = _generate_manifest_set_for_version(
            version,
            settings=self.settings,
            request_options=self.request_options,
        )
        deployment_result = await self.deployer_factory().deploy(manifest_set)
        self._latest_result = deployment_result
        return deployment_result.deployment_revision

    async def wait_for_rollout(self, deployment_revision: str) -> None:
        if (
            self._latest_result is None
            or self._latest_result.deployment_revision != deployment_revision
        ):
            raise RuntimeError(
                f"Rollback deployment revision {deployment_revision}"
                " is not available for validation."
            )

    @property
    def runtime_base_url(self) -> str | None:
        if self._latest_result is None:
            return None
        return self._latest_result.runtime_base_url


@dataclass(frozen=True)
class VersionRouteRollbackPublisher(RollbackPublisher):
    """Rollback publisher that reuses the configured route publisher."""

    route_publisher: RoutePublisher

    async def publish(self, version: ArtifactVersionResponse) -> dict[str, Any] | None:
        route_config = version.route_config
        if not isinstance(route_config, dict):
            raise RuntimeError(
                f"Rollback target {version.service_id}"
                f" v{version.version_number} is missing route_config."
            )
        return await self.route_publisher.publish(route_config)


@dataclass(frozen=True)
class RuntimeRollbackValidator(RollbackValidator):
    """Rollback validator that reuses the production post-deploy checks."""

    settings: ProductionActivitySettings
    request_options: Mapping[str, Any]
    runtime_http_client_factory: RuntimeHttpClientFactory
    tool_invoker_factory: ToolInvokerFactory
    deployer: GeneratedManifestRollbackDeployer

    async def validate(self, version: ArtifactVersionResponse) -> dict[str, Any]:
        runtime_base_url = self.deployer.runtime_base_url
        if runtime_base_url is None:
            raise RuntimeError(
                f"Rollback target {version.service_id}"
                f" v{version.version_number} has no deployed runtime URL."
            )

        service_ir = ServiceIR.model_validate(version.ir_json)
        await _wait_for_runtime_http_ready(
            runtime_base_url,
            client_factory=self.runtime_http_client_factory,
            timeout_seconds=self.settings.runtime_startup_timeout_seconds,
            poll_seconds=self.settings.runtime_startup_poll_seconds,
        )
        sample_invocations = _build_sample_invocations(service_ir)
        sample_invocations.update(_sample_invocation_overrides(self.request_options))
        preferred_smoke_tool_ids = _preferred_smoke_tool_ids(self.request_options)
        client = self.runtime_http_client_factory(runtime_base_url)
        try:
            async with PostDeployValidator(
                client=client,
                tool_invoker=self.tool_invoker_factory(runtime_base_url),
            ) as validator:
                report = await validator.validate(
                    runtime_base_url,
                    service_ir,
                    sample_invocations=sample_invocations,
                    preferred_smoke_tool_ids=preferred_smoke_tool_ids,
                )
        finally:
            await client.aclose()

        return report.model_dump(mode="json")


def create_default_rollback_workflow(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    request_options: Mapping[str, Any] | None = None,
    settings: ProductionActivitySettings | None = None,
    deployer: ManifestDeployer | None = None,
    route_publisher: RoutePublisher | None = None,
    runtime_http_client_factory: RuntimeHttpClientFactory | None = None,
    tool_invoker_factory: ToolInvokerFactory | None = None,
) -> RollbackWorkflow:
    """Build the default production rollback workflow runtime."""

    resolved_settings = settings or ProductionActivitySettings.from_env()
    resolved_request_options = dict(request_options or {})
    resolved_route_publisher = _resolve_route_publisher(resolved_settings, route_publisher)
    deployer_factory = _resolve_manifest_deployer_factory(resolved_settings, deployer)
    resolved_runtime_http_client_factory = _resolve_runtime_http_client_factory(
        resolved_settings,
        runtime_http_client_factory,
    )
    resolved_tool_invoker_factory = _resolve_tool_invoker_factory(
        resolved_runtime_http_client_factory,
        tool_invoker_factory,
    )
    rollback_deployer = GeneratedManifestRollbackDeployer(
        settings=resolved_settings,
        request_options=resolved_request_options,
        deployer_factory=deployer_factory,
    )
    return RollbackWorkflow(
        store=ArtifactRegistryRollbackStore(session_factory),
        deployer=rollback_deployer,
        validator=RuntimeRollbackValidator(
            settings=resolved_settings,
            request_options=resolved_request_options,
            runtime_http_client_factory=resolved_runtime_http_client_factory,
            tool_invoker_factory=resolved_tool_invoker_factory,
            deployer=rollback_deployer,
        ),
        publisher=VersionRouteRollbackPublisher(route_publisher=resolved_route_publisher),
    )


@dataclass(frozen=True)
class DeferredRoutePublisher:
    """Default route publisher that records route metadata without touching a gateway."""

    mode: str = _DEFERRED_ROUTE_PUBLISH_MODE

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
    auth_token: str | None = None

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
        if publication is not None and not isinstance(publication, dict):
            raise RuntimeError("Access control route rollback publication must be an object.")
        previous_routes: dict[str, dict[str, Any]] = {}
        if isinstance(publication, dict):
            raw_previous_routes = publication.get("previous_routes", {})
            if not isinstance(raw_previous_routes, dict):
                raise RuntimeError(
                    "Access control route rollback previous_routes must be an object."
                )
            previous_routes = cast(dict[str, dict[str, Any]], raw_previous_routes)
        await self._post(
            "/api/v1/gateway-binding/service-routes/rollback",
            route_config=route_config,
            previous_routes=previous_routes,
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
                headers=self._headers,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(
                    f"Access control route publisher returned non-JSON response: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Access control route publisher returned a non-object response.")
            return cast(dict[str, Any], payload)
        finally:
            if owns_client:
                await client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        token = self.auth_token or build_service_jwt()
        return {"Authorization": f"Bearer {token}"}


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
        created_manifests: list[tuple[str, str, str]] = []
        try:
            await self._apply_manifest("configmaps", "v1", manifest_set.config_map)
            created_manifests.append(
                ("configmaps", "v1", str(manifest_set.config_map["metadata"]["name"]))
            )
            deployment_response = await self._apply_manifest(
                "deployments",
                "apps/v1",
                manifest_set.deployment,
            )
            created_manifests.append(
                ("deployments", "apps/v1", str(manifest_set.deployment["metadata"]["name"]))
            )
            await self._apply_manifest("services", "v1", manifest_set.service)
            created_manifests.append(
                ("services", "v1", str(manifest_set.service["metadata"]["name"]))
            )
            await self._apply_manifest(
                "networkpolicies",
                "networking.k8s.io/v1",
                manifest_set.network_policy,
            )
            created_manifests.append(
                (
                    "networkpolicies",
                    "networking.k8s.io/v1",
                    str(manifest_set.network_policy["metadata"]["name"]),
                )
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
        except Exception:
            cleanup_errors = await self._delete_manifests_best_effort(created_manifests)
            if cleanup_errors:
                raise RuntimeError(
                    "Kubernetes deployment cleanup failed after partial apply: "
                    + "; ".join(cleanup_errors)
                )
            raise
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
            cleanup_errors = await self._delete_manifests_best_effort(
                [
                    (
                        "configmaps",
                        "v1",
                        str(manifest_set.config_map["metadata"]["name"]),
                    ),
                    (
                        "deployments",
                        "apps/v1",
                        str(manifest_set.deployment["metadata"]["name"]),
                    ),
                    (
                        "services",
                        "v1",
                        str(manifest_set.service["metadata"]["name"]),
                    ),
                    (
                        "networkpolicies",
                        "networking.k8s.io/v1",
                        str(manifest_set.network_policy["metadata"]["name"]),
                    ),
                ]
            )
            if cleanup_errors:
                raise RuntimeError(
                    "Kubernetes rollback left undeleted resources: " + "; ".join(cleanup_errors)
                )
        finally:
            if self.owns_api_client:
                await self.api.aclose()

    async def _delete_manifests_best_effort(
        self,
        manifests: list[tuple[str, str, str]],
    ) -> list[str]:
        errors: list[str] = []
        for plural, api_version, name in reversed(manifests):
            try:
                await self._delete_manifest(plural, api_version, name)
            except Exception as exc:
                errors.append(f"{plural}/{name}: {exc}")
        return errors

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
        if response.is_error:
            raise RuntimeError(
                f"K8s apply failed for {plural}/{name}: "
                f"{response.status_code} {_summarize_k8s_error(response)}"
            )
        return self._response_json_object(response)

    async def _delete_manifest(self, plural: str, api_version: str, name: str) -> None:
        named_path, _ = self._resource_paths(plural, api_version, name)
        response = await self.api.client.delete(named_path)
        if response.status_code not in {200, 202, 404}:
            response.raise_for_status()

    async def _wait_for_rollout(self, deployment_name: str, *, expected_replicas: int) -> int:
        named_path, _ = self._resource_paths("deployments", "apps/v1", deployment_name)
        timeout_seconds = self.rollout_timeout_seconds
        poll_seconds = self.rollout_poll_seconds
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                break
            response = await self.api.client.get(named_path)
            response.raise_for_status()
            deployment = self._response_json_object(response)
            metadata = cast(dict[str, Any], deployment.get("metadata", {}))
            status = cast(dict[str, Any], deployment.get("status", {}))
            raw_updated_replicas = status.get("updatedReplicas")
            try:
                observed_generation = int(status.get("observedGeneration", 0) or 0)
                generation = int(metadata.get("generation", 0) or 0)
                available_replicas = int(status.get("availableReplicas", 0) or 0)
                updated_replicas = (
                    int(raw_updated_replicas or 0) if raw_updated_replicas is not None else None
                )
            except (ValueError, TypeError):
                observed_generation = generation = available_replicas = 0
                updated_replicas = 0 if raw_updated_replicas is not None else None
            if (
                observed_generation >= generation
                and available_replicas >= expected_replicas
                and (updated_replicas is None or updated_replicas >= expected_replicas)
            ):
                return observed_generation
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await _sleep_seconds(min(poll_seconds, remaining))
        raise RuntimeError(
            f"Timed out waiting for Kubernetes rollout of deployment {deployment_name}."
        )

    @staticmethod
    def _response_json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"K8s API returned non-JSON response: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("K8s API returned non-object response.")
        return cast(dict[str, Any], payload)

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
    resolved_route_publisher = route_publisher
    deployer_factory = _resolve_manifest_deployer_factory(resolved_settings, deployer)
    resolved_runtime_http_client_factory = _resolve_runtime_http_client_factory(
        resolved_settings,
        runtime_http_client_factory,
    )
    resolved_tool_invoker_factory = _resolve_tool_invoker_factory(
        resolved_runtime_http_client_factory,
        tool_invoker_factory,
    )

    def _resolved_route_publisher() -> RoutePublisher:
        nonlocal resolved_route_publisher
        if resolved_route_publisher is None:
            resolved_route_publisher = _resolve_route_publisher(resolved_settings, None)
        return resolved_route_publisher

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
        extractors = _build_extractors(source)
        try:
            extractor = _resolve_extractor(context, source, extractors)
            service_ir = extractor.extract(source)
            service_ir = _apply_auth_override(service_ir, context.request.options)
            service_ir = _apply_scope_override(service_ir, context.request.options)
            if context.request.service_name:
                service_ir = service_ir.model_copy(
                    update={"service_name": context.request.service_name}
                )
            service_id = (
                context.request.service_id
                or context.request.service_name
                or service_ir.service_name
            )
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
        if not _enhancement_enabled(context.request.options):
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
        runtime_mode = context.request.options.get("runtime_mode", "generic")
        if runtime_mode == "codegen":
            manifest_set = generate_codegen_manifests(
                service_ir,
                config=CodegenManifestConfig(
                    runtime_image=resolved_settings.runtime_image,
                    service_id=str(context.payload["service_id"]),
                    version_number=int(context.payload["version_number"]),
                    namespace=resolved_settings.namespace,
                    image_pull_policy=resolved_settings.image_pull_policy,
                ),
            )
        else:
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
        options = context.request.options
        await _wait_for_runtime_http_ready(
            runtime_base_url,
            client_factory=resolved_runtime_http_client_factory,
            timeout_seconds=resolved_settings.runtime_startup_timeout_seconds,
            poll_seconds=resolved_settings.runtime_startup_poll_seconds,
        )
        sample_invocations = _build_sample_invocations(service_ir)
        sample_invocations.update(_sample_invocation_overrides(options))
        preferred_smoke_tool_ids = _preferred_smoke_tool_ids(options)
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
                    preferred_smoke_tool_ids=preferred_smoke_tool_ids,
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
        publication = await _resolved_route_publisher().publish(route_config)
        publication_mode = (
            publication.get("mode")
            if isinstance(publication, dict) and isinstance(publication.get("mode"), str)
            else resolved_settings.route_publish_mode
        )
        return _stage_result(
            context_updates={"route_publication": publication},
            event_detail={
                "route_id": route_config.get("default_route", {}).get("route_id"),
                "publication_mode": publication_mode,
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
        try:
            manifest_set = _deserialize_manifest_set(manifest_payload)
            deployment = _deserialize_deployment_result(deployment_payload)
        except RuntimeError as exc:
            _logger.warning("deploy_rollback skipped: %s", exc)
            return
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
        publication = rollback_payload.get("publication")
        if publication is not None and not isinstance(publication, dict):
            _logger.warning(
                "route_rollback skipped: publication is %s, not dict",
                type(publication).__name__,
            )
            return
        publication = cast(dict[str, Any] | None, publication)
        await _resolved_route_publisher().rollback(route_config, publication)
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
    protocol_hint = options.get("force_protocol")
    if not isinstance(protocol_hint, str) or not protocol_hint:
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


def _apply_auth_override(service_ir: ServiceIR, options: Mapping[str, Any]) -> ServiceIR:
    raw_auth = options.get("auth")
    if not isinstance(raw_auth, Mapping):
        raw_auth = options.get("auth_config")
    if not isinstance(raw_auth, Mapping):
        return service_ir

    auth_payload = _normalize_auth_override(dict(raw_auth))
    if not auth_payload:
        return service_ir

    auth = AuthConfig.model_validate(auth_payload)
    return service_ir.model_copy(update={"auth": auth})


def _apply_scope_override(service_ir: ServiceIR, options: Mapping[str, Any]) -> ServiceIR:
    updates: dict[str, str] = {}

    tenant = options.get("tenant")
    if isinstance(tenant, str) and tenant.strip():
        updates["tenant"] = tenant.strip()

    environment = options.get("environment")
    if isinstance(environment, str) and environment.strip():
        updates["environment"] = environment.strip()

    if not updates:
        return service_ir
    return service_ir.model_copy(update=updates)


def _normalize_auth_override(auth_payload: dict[str, Any]) -> dict[str, Any]:
    auth_type = auth_payload.get("type")

    compile_time_secret_ref = auth_payload.get("compile_time_secret_ref")
    if (
        isinstance(compile_time_secret_ref, str)
        and compile_time_secret_ref
        and not isinstance(auth_payload.get("runtime_secret_ref"), str)
    ):
        auth_payload["runtime_secret_ref"] = compile_time_secret_ref

    if auth_type == "basic":
        username = auth_payload.pop("username", None)
        password_secret_ref = auth_payload.pop("password_secret_ref", None)
        if isinstance(username, str) and username:
            auth_payload["basic_username"] = username
        if isinstance(password_secret_ref, str) and password_secret_ref:
            auth_payload["basic_password_ref"] = password_secret_ref

    if auth_type == "api_key":
        header_name = auth_payload.pop("header_name", None)
        if isinstance(header_name, str) and header_name and "api_key_param" not in auth_payload:
            auth_payload["api_key_param"] = header_name
        auth_payload.setdefault("api_key_location", "header")

    if auth_type == "oauth2" and not isinstance(auth_payload.get("oauth2"), Mapping):
        token_url = auth_payload.pop("token_url", None)
        client_id = auth_payload.pop("client_id", None)
        client_id_ref = auth_payload.pop("client_id_ref", None)
        client_secret_ref = auth_payload.pop("client_secret_ref", None)
        oauth2_payload: dict[str, Any] = {}
        if isinstance(token_url, str) and token_url:
            oauth2_payload["token_url"] = token_url
        if isinstance(client_id, str) and client_id:
            oauth2_payload["client_id"] = client_id
        if isinstance(client_id_ref, str) and client_id_ref:
            oauth2_payload["client_id_ref"] = client_id_ref
        if isinstance(client_secret_ref, str) and client_secret_ref:
            oauth2_payload["client_secret_ref"] = client_secret_ref
        if oauth2_payload:
            auth_payload["oauth2"] = oauth2_payload

    return auth_payload


def _preferred_smoke_tool_ids(options: Mapping[str, Any]) -> tuple[str, ...]:
    raw_preferred_tool_ids = options.get("preferred_smoke_tool_ids")
    if not isinstance(raw_preferred_tool_ids, (list, tuple)):
        return ()
    return tuple(
        tool_id for tool_id in raw_preferred_tool_ids if isinstance(tool_id, str) and tool_id
    )


def _sample_invocation_overrides(options: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_overrides = options.get("sample_invocation_overrides")
    if not isinstance(raw_overrides, Mapping):
        return {}

    overrides: dict[str, dict[str, Any]] = {}
    for tool_name, arguments in raw_overrides.items():
        if not isinstance(tool_name, str) or not isinstance(arguments, Mapping):
            continue
        overrides[tool_name] = dict(arguments)
    return overrides


def _build_extractors(source: SourceConfig | None = None) -> list[ExtractorProtocol]:
    rest_llm_client = None
    if source is not None and _truthy_hint(source.hints.get("llm_seed_mutation")):
        enhancer_config = EnhancerConfig.from_env()
        rest_llm_client = create_llm_client(enhancer_config)
    return [
        OpenAPIExtractor(),
        GraphQLExtractor(),
        GrpcProtoExtractor(),
        SOAPWSDLExtractor(),
        ODataExtractor(),
        SCIMExtractor(),
        JsonRpcExtractor(),
        SQLExtractor(),
        RESTExtractor(llm_client=rest_llm_client),
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


def _truthy_hint(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


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


def _enhancement_enabled(options: Mapping[str, Any] | None = None) -> bool:
    if isinstance(options, Mapping) and options.get("skip_enhancement") is True:
        return False
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
        config_map=_required_payload_object(payload, "config_map", "Manifest set"),
        deployment=_required_payload_object(payload, "deployment", "Manifest set"),
        service=_required_payload_object(payload, "service", "Manifest set"),
        network_policy=_required_payload_object(payload, "network_policy", "Manifest set"),
        route_config=_required_payload_object(payload, "route_config", "Manifest set"),
        yaml=_required_payload_string(payload, "yaml", "Manifest set"),
    )


def _deserialize_deployment_result(payload: dict[str, Any]) -> DeploymentResult:
    return DeploymentResult(
        deployment_revision=_required_payload_string(
            payload, "deployment_revision", "Deployment rollback"
        ),
        runtime_base_url=_required_payload_string(
            payload, "runtime_base_url", "Deployment rollback"
        ),
        manifest_storage_path=_required_payload_string(
            payload, "manifest_storage_path", "Deployment rollback"
        ),
    )


def _required_payload_object(
    payload: dict[str, Any],
    field_name: str,
    payload_name: str,
) -> dict[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, dict):
        raise RuntimeError(f"{payload_name} field '{field_name}' must be an object.")
    return value


def _required_payload_string(
    payload: dict[str, Any],
    field_name: str,
    payload_name: str,
) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise RuntimeError(f"{payload_name} field '{field_name}' must be a string.")
    return value


def _manifest_set_from_context(context: CompilationContext) -> GeneratedManifestSet:
    serialized = context.payload.get("generated_manifest_set")
    if not isinstance(serialized, dict):
        raise RuntimeError("Generated manifest set missing from workflow context.")
    return _deserialize_manifest_set(serialized)


def _sample_value(
    param: Param,
    *,
    service_ir: ServiceIR | None = None,
    operation: Operation | None = None,
) -> Any:
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
    path_param_names = {
        match.group(1) for match in _PATH_PLACEHOLDER_PATTERN.finditer(operation.path or "")
    }
    # Keep smoke requests conservative for HTTP/SOAP-style protocols. Real APIs
    # often reject arbitrary placeholders for optional filters/sorts/search params,
    # but path placeholders must still be populated so runtime URL resolution works.
    return {
        param.name: (
            _sample_path_value(param)
            if param.name in path_param_names
            else _sample_value(param, service_ir=service_ir, operation=operation)
        )
        for param in operation.params
        if param.required or param.default is not None or param.name in path_param_names
    }


def _sample_path_value(param: Param) -> Any:
    if param.default is not None:
        return param.default
    lowered_name = param.name.lower()
    if param.type == "integer":
        return PATH_PLACEHOLDER_INT_SAMPLE
    if param.type == "number":
        return PATH_PLACEHOLDER_NUMBER_SAMPLE
    if param.type == "array":
        return [PATH_PLACEHOLDER_STRING_SAMPLE]
    if param.type == "object":
        return {"name": PATH_PLACEHOLDER_STRING_SAMPLE}
    if lowered_name.endswith("id"):
        return PATH_PLACEHOLDER_ID_SAMPLE
    return PATH_PLACEHOLDER_STRING_SAMPLE


def _sample_grpc_arguments(operation: Operation) -> dict[str, Any]:
    arguments = {
        param.name: _sample_value(param, operation=operation)
        for param in operation.params
        if param.required or param.default is not None
    }
    for param in operation.params:
        if param.name in arguments:
            continue
        if param.type in {"array", "object"}:
            continue
        if _is_safe_optional_grpc_sample_param(param):
            arguments[param.name] = _sample_value(param, operation=operation)
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
        return {param.name: _sample_value(param, operation=operation) for param in operation.params}

    arguments: dict[str, Any] = {}
    for param in operation.params:
        if param.required or param.default is not None:
            arguments[param.name] = _sample_value(param, operation=operation)

    if arguments:
        return arguments

    if operation.graphql.operation_type is GraphQLOperationType.query:
        return {}

    return {param.name: _sample_value(param, operation=operation) for param in operation.params}


def _sample_sql_arguments(operation: Operation) -> dict[str, Any]:
    if operation.sql is None:
        return {param.name: _sample_value(param, operation=operation) for param in operation.params}

    if operation.sql.action is SqlOperationType.query:
        arguments: dict[str, Any] = {}
        for param in operation.params:
            if param.name == "limit":
                arguments[param.name] = param.default if param.default is not None else 1
                continue
            if param.required:
                arguments[param.name] = _sample_value(param, operation=operation)
        return arguments

    if operation.sql.action is SqlOperationType.update:
        arguments = {
            param.name: _sample_value(param, operation=operation)
            for param in operation.params
            if param.required
        }
        for param in operation.params:
            if param.name in arguments:
                continue
            if param.name not in operation.sql.updatable_columns:
                continue
            arguments[param.name] = _sample_value(param, operation=operation)
            break
        return arguments

    return {
        param.name: _sample_value(param, operation=operation)
        for param in operation.params
        if param.required
    }


def _validation_failure_message(prefix: str, report: Any) -> str:
    failed_results = [
        f"{result.stage}: {result.details}" for result in report.results if not result.passed
    ]
    if not failed_results:
        return prefix
    return f"{prefix}: {'; '.join(failed_results)}"


def _summarize_k8s_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = response.text

    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
        return str(payload)
    if isinstance(payload, str) and payload:
        return payload
    return response.reason_phrase


async def _wait_for_runtime_http_ready(
    runtime_base_url: str,
    *,
    client_factory: RuntimeHttpClientFactory,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    health_url = f"{runtime_base_url.rstrip('/')}/healthz"
    ready_url = f"{runtime_base_url.rstrip('/')}/readyz"
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: str | None = None

    while True:
        if asyncio.get_running_loop().time() >= deadline:
            break
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

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        sleep_seconds = min(poll_seconds, remaining)
        if sleep_seconds <= 0:
            break
        await _sleep_seconds(sleep_seconds)

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
    "ArtifactRegistryRollbackStore",
    "DeferredRoutePublisher",
    "DeploymentResult",
    "GeneratedManifestRollbackDeployer",
    "KubernetesAPISession",
    "KubernetesManifestDeployer",
    "ManifestDeployer",
    "ProductionActivitySettings",
    "RuntimeRollbackValidator",
    "RoutePublisher",
    "VersionRouteRollbackPublisher",
    "create_default_activity_registry",
    "create_default_rollback_workflow",
    "build_streamable_http_tool_invoker",
]
