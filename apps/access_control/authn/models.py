"""Pydantic models for the authentication module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TokenValidationRequest(BaseModel):
    """Request payload for token validation."""

    token: str = Field(min_length=1, max_length=4096)


class TokenPrincipalResponse(BaseModel):
    """Validated token subject."""

    subject: str
    username: str | None = None
    token_type: str
    claims: dict[str, object]


class PATCreateRequest(BaseModel):
    """Request payload for creating a personal access token."""

    username: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)


class PATResponse(BaseModel):
    """Serialized PAT metadata."""

    id: UUID
    username: str
    name: str
    created_at: datetime
    revoked_at: datetime | None = None


class PATCreateResponse(PATResponse):
    """PAT creation response with the plaintext token."""

    token: str


class PATListResponse(BaseModel):
    """List of PATs for a user."""

    items: list[PATResponse]
    total: int = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1)
