"""Compiled service discovery routes served from the compiler API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.security import require_authenticated_caller, require_scope_access
from apps.compiler_api.db import get_db_session
from apps.compiler_api.models import (
    DashboardSummaryResponse,
    ServiceListResponse,
    ServiceSummaryResponse,
)
from apps.compiler_api.repository import (
    ArtifactRegistryRepository,
    MalformedServiceVersionError,
    ServiceCatalogRepository,
)

router = APIRouter(prefix="/api/v1/services", tags=["services"])


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
)
async def get_dashboard_summary(
    tenant: str | None = None,
    environment: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> DashboardSummaryResponse:
    """Aggregate dashboard summary for the operator web UI."""
    require_scope_access(caller, tenant=tenant, environment=environment)
    repository = ServiceCatalogRepository(session)
    return await repository.get_dashboard_summary(
        tenant=tenant,
        environment=environment,
        recent_limit=limit,
    )


@router.get(
    "",
    response_model=ServiceListResponse,
)
async def list_services(
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> ServiceListResponse:
    require_scope_access(caller, tenant=tenant, environment=environment)
    repository = ServiceCatalogRepository(session)
    return await repository.list_services(tenant=tenant, environment=environment)


@router.get(
    "/{service_id}",
    response_model=ServiceSummaryResponse,
)
async def get_service(
    service_id: str,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> ServiceSummaryResponse:
    require_scope_access(caller, tenant=tenant, environment=environment)
    repository = ServiceCatalogRepository(session)
    try:
        service = await repository.get_service(
            service_id,
            tenant=tenant,
            environment=environment,
        )
    except MalformedServiceVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_id} was not found.",
        )
    return service


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_service(
    service_id: str,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> None:
    """Delete all versions of a service from the registry."""
    require_scope_access(caller, tenant=tenant, environment=environment)
    repository = ArtifactRegistryRepository(session)
    deleted = await repository.delete_service(
        service_id, tenant=tenant, environment=environment
    )
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_id} was not found.",
        )
