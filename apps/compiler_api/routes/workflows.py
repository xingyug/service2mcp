"""Review workflow routes served from the compiler API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.security import require_authenticated_caller
from apps.compiler_api.db import get_db_session
from libs.db_models import ReviewWorkflow, ServiceVersion

router = APIRouter(prefix="/api/v1/workflows", tags=["review-workflows"])

VALID_STATES = frozenset({
    "draft", "submitted", "in_review",
    "approved", "rejected", "published", "deployed",
})

VALID_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["submitted"],
    "submitted": ["in_review"],
    "in_review": ["approved", "rejected"],
    "approved": ["published"],
    "rejected": ["draft"],
    "published": ["deployed"],
    "deployed": [],
}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class WorkflowHistoryEntry(BaseModel):
    from_state: str = Field(alias="from")
    to: str
    actor: str
    comment: str | None = None
    timestamp: str

    model_config = {"populate_by_name": True}


class WorkflowResponse(BaseModel):
    id: UUID
    service_id: str
    version_number: int
    tenant: str | None = None
    environment: str | None = None
    state: str
    review_notes: dict[str, Any] | None = None
    history: list[WorkflowHistoryEntry]
    created_at: str
    updated_at: str


class TransitionRequest(BaseModel):
    to: str
    actor: str
    comment: str | None = None


class ReviewNotesUpdate(BaseModel):
    notes: dict[str, str]
    overall_note: str | None = None
    reviewed_operations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(record: ReviewWorkflow) -> WorkflowResponse:
    history_entries = [
        WorkflowHistoryEntry.model_validate(entry) for entry in (record.history or [])
    ]
    return WorkflowResponse(
        id=record.id,
        service_id=record.service_id,
        version_number=record.version_number,
        tenant=record.tenant,
        environment=record.environment,
        state=record.state,
        review_notes=record.review_notes,
        history=history_entries,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


async def _get_or_create(
    session: AsyncSession,
    service_id: str,
    version_number: int,
    *,
    tenant: str | None = None,
    environment: str | None = None,
) -> ReviewWorkflow:
    query = select(ReviewWorkflow).where(
        ReviewWorkflow.service_id == service_id,
        ReviewWorkflow.version_number == version_number,
        ReviewWorkflow.tenant == tenant,
        ReviewWorkflow.environment == environment,
    )
    record = await session.scalar(query)
    if record is not None:
        return record

    record = ReviewWorkflow(
        service_id=service_id,
        version_number=version_number,
        tenant=tenant,
        environment=environment,
        state="draft",
        history=[],
    )
    session.add(record)
    await session.flush()
    return record


async def _require_existing_service_version(
    session: AsyncSession,
    service_id: str,
    version_number: int,
    *,
    tenant: str | None = None,
    environment: str | None = None,
) -> None:
    if version_number < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Version number must be greater than zero.",
        )

    query = select(ServiceVersion.id).where(
        ServiceVersion.service_id == service_id,
        ServiceVersion.version_number == version_number,
        ServiceVersion.tenant == tenant,
        ServiceVersion.environment == environment,
    )
    if await session.scalar(query) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Service version '{service_id}' v{version_number} "
                f"(tenant={tenant!r}, environment={environment!r}) was not found."
            ),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{service_id}/v/{version_number}",
    response_model=WorkflowResponse,
)
async def get_workflow(
    service_id: str,
    version_number: int,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> WorkflowResponse:
    await _require_existing_service_version(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    record = await _get_or_create(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    await session.commit()
    return _to_response(record)


@router.post(
    "/{service_id}/v/{version_number}/transition",
    response_model=WorkflowResponse,
)
async def transition_workflow(
    service_id: str,
    version_number: int,
    payload: TransitionRequest,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> WorkflowResponse:
    await _require_existing_service_version(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    record = await _get_or_create(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )

    allowed = VALID_TRANSITIONS.get(record.state, [])
    if payload.to not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Transition from '{record.state}' to '{payload.to}' "
                f"is not allowed. Valid targets: {allowed}"
            ),
        )

    entry = {
        "from": record.state,
        "to": payload.to,
        "actor": caller.username or caller.subject,
        "comment": payload.comment,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    record.state = payload.to
    record.history = [entry, *(record.history or [])]
    await session.commit()
    await session.refresh(record)
    return _to_response(record)


@router.put(
    "/{service_id}/v/{version_number}/notes",
    response_model=WorkflowResponse,
)
async def save_review_notes(
    service_id: str,
    version_number: int,
    payload: ReviewNotesUpdate,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> WorkflowResponse:
    await _require_existing_service_version(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    record = await _get_or_create(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    record.review_notes = {
        "operation_notes": payload.notes,
        "overall_note": payload.overall_note,
        "reviewed_operations": payload.reviewed_operations,
    }
    await session.commit()
    await session.refresh(record)
    return _to_response(record)


@router.get(
    "/{service_id}/v/{version_number}/history",
    response_model=list[WorkflowHistoryEntry],
)
async def get_workflow_history(
    service_id: str,
    version_number: int,
    tenant: str | None = None,
    environment: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> list[WorkflowHistoryEntry]:
    await _require_existing_service_version(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    record = await _get_or_create(
        session,
        service_id,
        version_number,
        tenant=tenant,
        environment=environment,
    )
    await session.commit()
    return [
        WorkflowHistoryEntry.model_validate(entry) for entry in (record.history or [])
    ]
