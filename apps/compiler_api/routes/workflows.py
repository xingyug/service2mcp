"""Review workflow routes served from the compiler API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compiler_api.db import get_db_session
from libs.db_models import ReviewWorkflow

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
) -> ReviewWorkflow:
    query = select(ReviewWorkflow).where(
        ReviewWorkflow.service_id == service_id,
        ReviewWorkflow.version_number == version_number,
    )
    record = await session.scalar(query)
    if record is not None:
        return record

    record = ReviewWorkflow(
        service_id=service_id,
        version_number=version_number,
        state="draft",
        history=[],
    )
    session.add(record)
    await session.flush()
    return record


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
    session: AsyncSession = Depends(get_db_session),
) -> WorkflowResponse:
    record = await _get_or_create(session, service_id, version_number)
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
    session: AsyncSession = Depends(get_db_session),
) -> WorkflowResponse:
    record = await _get_or_create(session, service_id, version_number)

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
        "actor": payload.actor,
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
    session: AsyncSession = Depends(get_db_session),
) -> WorkflowResponse:
    record = await _get_or_create(session, service_id, version_number)
    record.review_notes = {
        "operation_notes": payload.notes,
        "overall_note": payload.overall_note,
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
    session: AsyncSession = Depends(get_db_session),
) -> list[WorkflowHistoryEntry]:
    record = await _get_or_create(session, service_id, version_number)
    await session.commit()
    return [
        WorkflowHistoryEntry.model_validate(entry) for entry in (record.history or [])
    ]
