"""Compiled service discovery routes served from the compiler API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compiler_api.db import get_db_session
from apps.compiler_api.dispatcher import CompilationDispatcher, get_compilation_dispatcher
from apps.compiler_api.models import CompilationJobResponse, ServiceListResponse
from apps.compiler_api.repository import ArtifactRegistryRepository, CompilationRepository, ServiceCatalogRepository
from apps.compiler_worker.models import CompilationRequest

router = APIRouter(prefix="/api/v1/services", tags=["services"])


@router.get("", response_model=ServiceListResponse)
async def list_services(
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ServiceListResponse:
    repository = ServiceCatalogRepository(session)
    return await repository.list_services(tenant=tenant, environment=environment)


@router.post("/{service_id}/rebuild", response_model=CompilationJobResponse, status_code=202)
async def rebuild_service(
    service_id: str,
    session: AsyncSession = Depends(get_db_session),
    dispatcher: CompilationDispatcher = Depends(get_compilation_dispatcher),
) -> CompilationJobResponse:
    artifact_repo = ArtifactRegistryRepository(session)
    active = await artifact_repo.get_active_version(service_id)
    if active is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active artifact version for service '{service_id}'.",
        )

    comp_repo = CompilationRepository(session)
    request = CompilationRequest(
        source_url=active.source_url,
        source_hash=active.source_hash,
        service_name=service_id,
        options={
            "rebuild_from_ir": True,
            "skip_to_stage": "generate",
            "ir_json": active.ir_json,
        },
    )
    new_job = await comp_repo.create_job(request)
    request.job_id = new_job.id
    await dispatcher.enqueue(request)
    return new_job
