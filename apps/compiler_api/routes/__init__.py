"""Compiler API route modules."""

from apps.compiler_api.routes.artifacts import router as artifact_registry_router
from apps.compiler_api.routes.compilations import router as compilations_router
from apps.compiler_api.routes.services import router as services_router

__all__ = ["artifact_registry_router", "compilations_router", "services_router"]
