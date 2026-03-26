"""Compilation pipeline worker exports with lazy loading."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CompilationActivities",
    "ActivityRegistry",
    "CompilationContext",
    "CompilationExecutor",
    "CompilationEventRecord",
    "CompilationEventType",
    "CompilationJobRecord",
    "CompilationJobStore",
    "CompilationObservability",
    "CompilationRequest",
    "CompilationResult",
    "CompilationStage",
    "CompilationStatus",
    "CompilationWorkflow",
    "CompilationWorkflowError",
    "CallbackCompilationExecutor",
    "AccessControlRoutePublisher",
    "DEFAULT_STAGE_DEFINITIONS",
    "DeferredRoutePublisher",
    "DeploymentResult",
    "KubernetesAPISession",
    "KubernetesManifestDeployer",
    "ManifestDeployer",
    "ProductionActivitySettings",
    "RetryPolicy",
    "RoutePublisher",
    "SQLAlchemyCompilationJobStore",
    "StageDefinition",
    "StageExecutionResult",
    "WorkflowCompilationExecutor",
    "build_streamable_http_tool_invoker",
    "create_default_activity_registry",
]

_EXPORT_MODULES: dict[str, tuple[str, str]] = {
    "CompilationActivities": (
        "apps.compiler_worker.workflows.compile_workflow",
        "CompilationActivities",
    ),
    "ActivityRegistry": ("apps.compiler_worker.activities", "ActivityRegistry"),
    "CompilationContext": ("apps.compiler_worker.models", "CompilationContext"),
    "CompilationExecutor": ("apps.compiler_worker.executor", "CompilationExecutor"),
    "CompilationEventRecord": ("apps.compiler_worker.models", "CompilationEventRecord"),
    "CompilationEventType": ("apps.compiler_worker.models", "CompilationEventType"),
    "CompilationJobRecord": ("apps.compiler_worker.models", "CompilationJobRecord"),
    "CompilationJobStore": (
        "apps.compiler_worker.workflows.compile_workflow",
        "CompilationJobStore",
    ),
    "CompilationObservability": (
        "apps.compiler_worker.observability",
        "CompilationObservability",
    ),
    "CompilationRequest": ("apps.compiler_worker.models", "CompilationRequest"),
    "CompilationResult": ("apps.compiler_worker.models", "CompilationResult"),
    "CompilationStage": ("apps.compiler_worker.models", "CompilationStage"),
    "CompilationStatus": ("apps.compiler_worker.models", "CompilationStatus"),
    "CompilationWorkflow": (
        "apps.compiler_worker.workflows.compile_workflow",
        "CompilationWorkflow",
    ),
    "CompilationWorkflowError": (
        "apps.compiler_worker.workflows.compile_workflow",
        "CompilationWorkflowError",
    ),
    "CallbackCompilationExecutor": (
        "apps.compiler_worker.executor",
        "CallbackCompilationExecutor",
    ),
    "AccessControlRoutePublisher": (
        "apps.compiler_worker.activities",
        "AccessControlRoutePublisher",
    ),
    "DEFAULT_STAGE_DEFINITIONS": (
        "apps.compiler_worker.workflows.compile_workflow",
        "DEFAULT_STAGE_DEFINITIONS",
    ),
    "DeferredRoutePublisher": ("apps.compiler_worker.activities", "DeferredRoutePublisher"),
    "DeploymentResult": ("apps.compiler_worker.activities", "DeploymentResult"),
    "KubernetesAPISession": ("apps.compiler_worker.activities", "KubernetesAPISession"),
    "KubernetesManifestDeployer": (
        "apps.compiler_worker.activities",
        "KubernetesManifestDeployer",
    ),
    "ManifestDeployer": ("apps.compiler_worker.activities", "ManifestDeployer"),
    "ProductionActivitySettings": (
        "apps.compiler_worker.activities",
        "ProductionActivitySettings",
    ),
    "RetryPolicy": ("apps.compiler_worker.models", "RetryPolicy"),
    "RoutePublisher": ("apps.compiler_worker.activities", "RoutePublisher"),
    "SQLAlchemyCompilationJobStore": (
        "apps.compiler_worker.repository",
        "SQLAlchemyCompilationJobStore",
    ),
    "StageDefinition": ("apps.compiler_worker.models", "StageDefinition"),
    "StageExecutionResult": ("apps.compiler_worker.models", "StageExecutionResult"),
    "WorkflowCompilationExecutor": (
        "apps.compiler_worker.executor",
        "WorkflowCompilationExecutor",
    ),
    "build_streamable_http_tool_invoker": (
        "apps.compiler_worker.activities",
        "build_streamable_http_tool_invoker",
    ),
    "create_default_activity_registry": (
        "apps.compiler_worker.activities",
        "create_default_activity_registry",
    ),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORT_MODULES[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name)
    return getattr(module, attr_name)
