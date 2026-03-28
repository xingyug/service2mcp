"""HTTP routes for the authorization module."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.routes import get_audit_log_service
from apps.access_control.audit.service import AuditLogService
from apps.access_control.authz.models import (
    PolicyCreateRequest,
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    PolicyListResponse,
    PolicyResponse,
    PolicyUpdateRequest,
)
from apps.access_control.authz.service import AuthzService
from apps.access_control.db import get_db_session
from apps.access_control.gateway_binding.service import (
    GatewayBindingService,
    get_gateway_binding_service,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/authz", tags=["authz"])


def get_authz_service(session: AsyncSession = Depends(get_db_session)) -> AuthzService:
    """Construct an authz service for the current request."""

    return AuthzService(session)


@router.post("/policies", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: PolicyCreateRequest,
    service: AuthzService = Depends(get_authz_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    audit_log: AuditLogService = Depends(get_audit_log_service),
) -> PolicyResponse:
    created = await service.create_policy(payload)
    try:
        await gateway_binding.sync_policy(created)
    except Exception:
        _logger.warning("Gateway sync failed after policy creation %s", created.id, exc_info=True)
    await audit_log.append_entry(
        actor=payload.created_by or "system",
        action="policy.created",
        resource=created.resource_id,
        detail=created.model_dump(mode="json"),
    )
    return created


@router.get("/policies", response_model=PolicyListResponse)
async def list_policies(
    subject_type: str | None = None,
    subject_id: str | None = None,
    resource_id: str | None = None,
    service: AuthzService = Depends(get_authz_service),
) -> PolicyListResponse:
    return PolicyListResponse(
        items=await service.list_policies(
            subject_type=subject_type,
            subject_id=subject_id,
            resource_id=resource_id,
        )
    )


@router.get("/policies/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: UUID,
    service: AuthzService = Depends(get_authz_service),
) -> PolicyResponse:
    policy = await service.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found.")
    return policy


@router.put("/policies/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: UUID,
    payload: PolicyUpdateRequest,
    service: AuthzService = Depends(get_authz_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    audit_log: AuditLogService = Depends(get_audit_log_service),
) -> PolicyResponse:
    updated = await service.update_policy(policy_id, payload)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found.")
    try:
        await gateway_binding.sync_policy(updated)
    except Exception:
        _logger.warning("Gateway sync failed after policy update %s", policy_id, exc_info=True)
    await audit_log.append_entry(
        actor=payload.created_by or "system",
        action="policy.updated",
        resource=updated.resource_id,
        detail=updated.model_dump(mode="json"),
    )
    return updated


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: UUID,
    service: AuthzService = Depends(get_authz_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    audit_log: AuditLogService = Depends(get_audit_log_service),
) -> None:
    deleted = await service.delete_policy(policy_id)
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found.")
    try:
        await gateway_binding.delete_policy(policy_id)
    except Exception:
        _logger.warning("Gateway delete failed after policy deletion %s", policy_id, exc_info=True)
    await audit_log.append_entry(
        actor="system",
        action="policy.deleted",
        resource=deleted.resource_id,
        detail={"policy_id": str(policy_id)},
    )


@router.post("/evaluate", response_model=PolicyEvaluationResponse)
async def evaluate_policy(
    payload: PolicyEvaluationRequest,
    service: AuthzService = Depends(get_authz_service),
) -> PolicyEvaluationResponse:
    return await service.evaluate(payload)
