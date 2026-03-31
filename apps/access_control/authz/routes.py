"""HTTP routes for the authorization module."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.routes import get_audit_log_service
from apps.access_control.audit.service import AuditLogService
from apps.access_control.authn.models import TokenPrincipalResponse
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
from apps.access_control.security import (
    require_admin_caller,
    require_admin_principal,
    require_authenticated_caller,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/authz", tags=["authz"])


def get_authz_service(session: AsyncSession = Depends(get_db_session)) -> AuthzService:
    """Construct an authz service for the current request."""

    return AuthzService(session)


async def _rollback_and_reconcile_gateway(
    session: AsyncSession,
    gateway_binding: GatewayBindingService,
) -> None:
    await session.rollback()
    try:
        await gateway_binding.reconcile(session)
    except Exception as exc:  # broad-except: route error boundary  # pragma: no cover
        logger.exception("Gateway compensation failed after transaction rollback")
        raise RuntimeError(
            f"Gateway compensation failed after transaction rollback: {exc}"
        ) from exc


@router.post("/policies", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: PolicyCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: AuthzService = Depends(get_authz_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    audit_log: AuditLogService = Depends(get_audit_log_service),
    caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> PolicyResponse:
    require_admin_principal(caller)
    request_payload = payload.model_copy(update={"created_by": caller.subject})
    created = await service.create_policy(request_payload, commit=False)
    try:
        await gateway_binding.sync_policy(created)
    except Exception as exc:  # broad-except: route error boundary
        logger.exception("Gateway sync failed after policy creation")
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after policy creation: {exc}",
        ) from exc
    try:
        await audit_log.append_entry(
            actor=caller.subject,
            action="policy.created",
            resource=created.resource_id,
            detail=created.model_dump(mode="json"),
            commit=False,
        )
        await session.commit()
    except Exception:  # broad-except: route error boundary
        logger.exception("Audit log or commit failed during policy creation")
        await _rollback_and_reconcile_gateway(session, gateway_binding)
        raise
    return created


@router.get("/policies", response_model=PolicyListResponse)
async def list_policies(
    subject_type: str | None = None,
    subject_id: str | None = None,
    resource_id: str | None = None,
    service: AuthzService = Depends(get_authz_service),
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
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
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> PolicyResponse:
    policy = await service.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found.")
    return policy


@router.put("/policies/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: UUID,
    payload: PolicyUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: AuthzService = Depends(get_authz_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    audit_log: AuditLogService = Depends(get_audit_log_service),
    caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> PolicyResponse:
    require_admin_principal(caller)
    updated = await service.update_policy(policy_id, payload, commit=False)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found.")
    try:
        await gateway_binding.sync_policy(updated)
    except Exception as exc:  # broad-except: route error boundary
        logger.exception("Gateway sync failed after policy update")
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after policy update: {exc}",
        ) from exc
    try:
        await audit_log.append_entry(
            actor=caller.subject,
            action="policy.updated",
            resource=updated.resource_id,
            detail=updated.model_dump(mode="json"),
            commit=False,
        )
        await session.commit()
    except Exception:  # broad-except: route error boundary
        logger.exception("Audit log or commit failed during policy update")
        await _rollback_and_reconcile_gateway(session, gateway_binding)
        raise
    return updated


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: AuthzService = Depends(get_authz_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    audit_log: AuditLogService = Depends(get_audit_log_service),
    caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> None:
    require_admin_principal(caller)
    deleted = await service.delete_policy(policy_id, commit=False)
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found.")
    try:
        await gateway_binding.delete_policy(policy_id)
    except Exception as exc:  # broad-except: route error boundary
        logger.exception("Gateway sync failed after policy deletion")
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after policy deletion: {exc}",
        ) from exc
    try:
        await audit_log.append_entry(
            actor=caller.subject,
            action="policy.deleted",
            resource=deleted.resource_id,
            detail={"policy_id": str(policy_id)},
            commit=False,
        )
        await session.commit()
    except Exception:  # broad-except: route error boundary
        logger.exception("Audit log or commit failed during policy deletion")
        await _rollback_and_reconcile_gateway(session, gateway_binding)
        raise


@router.post("/evaluate", response_model=PolicyEvaluationResponse)
async def evaluate_policy(
    payload: PolicyEvaluationRequest,
    service: AuthzService = Depends(get_authz_service),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
    audit_log: AuditLogService = Depends(get_audit_log_service),
) -> PolicyEvaluationResponse:
    result = await service.evaluate(payload)
    await audit_log.append_entry(
        actor=caller.subject,
        action="authz.evaluate",
        resource=payload.resource_id,
        detail={
            "subject_type": payload.subject_type,
            "subject_id": payload.subject_id,
            "action": payload.action,
            "resource_id": payload.resource_id,
            "risk_level": payload.risk_level.value,
            "decision": result.decision,
            "matched_policy_id": (
                str(result.matched_policy_id) if result.matched_policy_id else None
            ),
            "reason": result.reason,
        },
    )
    return result
