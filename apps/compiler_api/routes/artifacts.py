"""Artifact registry routes served from the compiler API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compiler_api.db import get_db_session
from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_api.route_publisher import ArtifactRoutePublisher, get_route_publisher
from libs.registry_client.models import (
    ArtifactDiffResponse,
    ArtifactVersionCreate,
    ArtifactVersionListResponse,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifact-registry"])


def _not_found(service_id: str, version_number: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Service version {service_id}:{version_number} was not found.",
    )


def _version_only_route_config(route_config: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(route_config, dict):
        return None
    if not isinstance(route_config.get("version_route"), dict):
        return None
    version_only = dict(route_config)
    version_only["default_route"] = None
    return version_only


@router.post("", response_model=ArtifactVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact_version(
    payload: ArtifactVersionCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ArtifactVersionResponse:
    repository = ArtifactRegistryRepository(session)
    try:
        return await repository.create_version(payload)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service version {payload.service_id}:{payload.version_number} already exists.",
        ) from exc


@router.get("/{service_id}/versions", response_model=ArtifactVersionListResponse)
async def list_artifact_versions(
    service_id: str,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ArtifactVersionListResponse:
    repository = ArtifactRegistryRepository(session)
    return await repository.list_versions(service_id, tenant=tenant, environment=environment)


@router.get("/{service_id}/versions/{version_number}", response_model=ArtifactVersionResponse)
async def get_artifact_version(
    service_id: str,
    version_number: int,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ArtifactVersionResponse:
    repository = ArtifactRegistryRepository(session)
    version = await repository.get_version(
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    if version is None:
        raise _not_found(service_id, version_number)
    return version


@router.put("/{service_id}/versions/{version_number}", response_model=ArtifactVersionResponse)
async def update_artifact_version(
    service_id: str,
    version_number: int,
    payload: ArtifactVersionUpdate,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ArtifactVersionResponse:
    repository = ArtifactRegistryRepository(session)
    version = await repository.update_version(
        service_id,
        version_number,
        payload,
        tenant=tenant,
        environment=environment,
    )
    if version is None:
        raise _not_found(service_id, version_number)
    return version


@router.delete("/{service_id}/versions/{version_number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact_version(
    service_id: str,
    version_number: int,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    route_publisher: ArtifactRoutePublisher = Depends(get_route_publisher),
) -> Response:
    repository = ArtifactRegistryRepository(session)
    deleted_version = await repository.get_version(
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    if deleted_version is None:
        raise _not_found(service_id, version_number)
    deleted = await repository.delete_version(
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
        commit=False,
    )
    if not deleted:
        raise _not_found(service_id, version_number)
    replacement = None
    if deleted_version.is_active:
        replacement = await repository.get_active_version(
            service_id, tenant=tenant, environment=environment,
        )
    try:
        version_only_route = _version_only_route_config(deleted_version.route_config)
        if deleted_version.is_active:
            if replacement is not None and isinstance(replacement.route_config, dict):
                if version_only_route is not None:
                    await route_publisher.delete(version_only_route)
                await route_publisher.sync(replacement.route_config)
            elif isinstance(deleted_version.route_config, dict):
                await route_publisher.delete(deleted_version.route_config)
        elif version_only_route is not None:
            await route_publisher.delete(version_only_route)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Route synchronization failed after deleting "
                f"{service_id}:{version_number}: {exc}"
            ),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{service_id}/versions/{version_number}/activate",
    response_model=ArtifactVersionResponse,
)
async def activate_artifact_version(
    service_id: str,
    version_number: int,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    route_publisher: ArtifactRoutePublisher = Depends(get_route_publisher),
) -> ArtifactVersionResponse:
    repository = ArtifactRegistryRepository(session)
    version = await repository.activate_version(
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
        commit=False,
    )
    if version is None:
        raise _not_found(service_id, version_number)
    try:
        if isinstance(version.route_config, dict):
            await route_publisher.sync(version.route_config)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Route synchronization failed after activating "
                f"{service_id}:{version_number}: {exc}"
            ),
        ) from exc
    return version


@router.get("/{service_id}/diff", response_model=ArtifactDiffResponse)
async def diff_artifact_versions(
    service_id: str,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ArtifactDiffResponse:
    repository = ArtifactRegistryRepository(session)
    diff = await repository.diff_versions(
        service_id,
        from_version=from_version,
        to_version=to_version,
        tenant=tenant,
        environment=environment,
    )
    if diff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Unable to diff versions {service_id}:{from_version} and "
                f"{service_id}:{to_version} with the provided filters."
            ),
        )
    return diff
