"""Pydantic models for the authorization module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from libs.ir.models import RiskLevel


class PolicyCreateRequest(BaseModel):
    """Request payload for creating an authorization policy."""

    subject_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    action_pattern: str = Field(min_length=1)
    risk_threshold: RiskLevel = RiskLevel.safe
    decision: str = Field(pattern="^(allow|deny|require_approval)$")
    created_by: str | None = None


class PolicyUpdateRequest(BaseModel):
    """Request payload for updating an authorization policy."""

    resource_id: str | None = None
    action_pattern: str | None = None
    risk_threshold: RiskLevel | None = None
    decision: str | None = Field(default=None, pattern="^(allow|deny|require_approval)$")


class PolicyResponse(BaseModel):
    """Serialized policy rule."""

    id: UUID
    subject_type: str
    subject_id: str
    resource_id: str
    action_pattern: str
    risk_threshold: RiskLevel
    decision: str
    created_by: str | None = None
    created_at: datetime


class PolicyListResponse(BaseModel):
    """List of policy rules."""

    items: list[PolicyResponse]


class PolicyEvaluationRequest(BaseModel):
    """Authorization evaluation input."""

    subject_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: RiskLevel


class PolicyEvaluationResponse(BaseModel):
    """Authorization evaluation output."""

    decision: str
    matched_policy_id: UUID | None = None
    reason: str
