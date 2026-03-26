"""Compiled service discovery routes served from the compiler API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compiler_api.db import get_db_session
from apps.compiler_api.models import ServiceListResponse
from apps.compiler_api.repository import ServiceCatalogRepository

router = APIRouter(prefix="/api/v1/services", tags=["services"])


@router.get("", response_model=ServiceListResponse)
async def list_services(
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ServiceListResponse:
    repository = ServiceCatalogRepository(session)
    return await repository.list_services(tenant=tenant, environment=environment)
