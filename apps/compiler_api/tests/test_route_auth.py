"""Authentication dependency coverage for compiler API routers."""

from __future__ import annotations

from fastapi.routing import APIRoute

from apps.access_control.security import require_authenticated_caller, require_sse_caller
from apps.compiler_api.routes.artifacts import router as artifacts_router
from apps.compiler_api.routes.compilations import router as compilations_router
from apps.compiler_api.routes.services import router as services_router


def _route(router: object, path: str, method: str) -> APIRoute:
    for candidate in getattr(router, "routes", []):
        if isinstance(candidate, APIRoute) and candidate.path == path and method in candidate.methods:
            return candidate
    raise AssertionError(f"Route {method} {path} was not found.")


def _dependency_calls(route: APIRoute) -> set[object]:
    return {dependency.call for dependency in route.dependant.dependencies}


def test_compilation_routes_require_authenticated_or_sse_callers() -> None:
    protected_paths = [
        ("/api/v1/compilations", "POST", require_authenticated_caller),
        ("/api/v1/compilations", "GET", require_authenticated_caller),
        ("/api/v1/compilations/{job_id}", "GET", require_authenticated_caller),
        ("/api/v1/compilations/{job_id}/retry", "POST", require_authenticated_caller),
        ("/api/v1/compilations/{job_id}/rollback", "POST", require_authenticated_caller),
        ("/api/v1/compilations/{job_id}/events", "GET", require_sse_caller),
    ]

    for path, method, dependency in protected_paths:
        assert dependency in _dependency_calls(_route(compilations_router, path, method))


def test_artifact_routes_require_authenticated_caller() -> None:
    protected_paths = [
        ("/api/v1/artifacts", "POST"),
        ("/api/v1/artifacts/{service_id}/versions", "GET"),
        ("/api/v1/artifacts/{service_id}/versions/{version_number}", "GET"),
        ("/api/v1/artifacts/{service_id}/versions/{version_number}", "PUT"),
        ("/api/v1/artifacts/{service_id}/versions/{version_number}", "DELETE"),
        ("/api/v1/artifacts/{service_id}/versions/{version_number}/activate", "POST"),
        ("/api/v1/artifacts/{service_id}/diff", "GET"),
    ]

    for path, method in protected_paths:
        assert require_authenticated_caller in _dependency_calls(
            _route(artifacts_router, path, method)
        )


def test_service_routes_require_authenticated_caller() -> None:
    protected_paths = [
        ("/api/v1/services", "GET"),
        ("/api/v1/services/{service_id}", "GET"),
    ]

    for path, method in protected_paths:
        assert require_authenticated_caller in _dependency_calls(
            _route(services_router, path, method)
        )
