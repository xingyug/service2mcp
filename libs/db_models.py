"""SQLAlchemy ORM models for all control plane database tables.

Defines the schema for:
- compiler: compilation_jobs
- compiler: compilation_events
- registry: service_versions, artifact_records
- auth: users, pats, policies, audit_log

All tables use UUID primary keys and UTC timestamps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


# ── Compiler Schema ───────────────────────────────────────────────────────


class CompilationJob(Base):
    """A compilation pipeline job."""

    __tablename__ = "compilation_jobs"
    __table_args__ = (
        Index("ix_compilation_jobs_status", "status"),
        Index("ix_compilation_jobs_created_at", "created_at"),
        {"schema": "compiler"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )
    events: Mapped[list[CompilationEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CompilationEvent(Base):
    """An ordered event emitted during compilation workflow execution."""

    __tablename__ = "compilation_events"
    __table_args__ = (
        Index("ix_compilation_events_job_id", "job_id"),
        Index("ix_compilation_events_created_at", "created_at"),
        UniqueConstraint("job_id", "sequence_number", name="uq_compilation_event_sequence"),
        {"schema": "compiler"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiler.compilation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[CompilationJob] = relationship(back_populates="events")


# ── Registry Schema ──────────────────────────────────────────────────────


class ServiceVersion(Base):
    """A versioned compilation artifact for a service."""

    __tablename__ = "service_versions"
    __table_args__ = (
        Index("ix_service_versions_service_id", "service_id"),
        Index("ix_service_versions_active", "service_id", "is_active"),
        Index(
            "uq_service_versions_one_active",
            "service_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        UniqueConstraint("service_id", "version_number", name="uq_service_version"),
        {"schema": "registry"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ir_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_ir_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    compiler_version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    deployment_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    route_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tenant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    artifacts: Mapped[list[ArtifactRecord]] = relationship(
        back_populates="service_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ArtifactRecord(Base):
    """A stored artifact (image digest, manifest hash, etc.) for a service version."""

    __tablename__ = "artifact_records"
    __table_args__ = (
        Index("ix_artifact_records_version", "service_version_id"),
        {"schema": "registry"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registry.service_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )  # "image", "manifest", "ir"
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    service_version: Mapped[ServiceVersion] = relationship(back_populates="artifacts")


# ── Auth Schema ───────────────────────────────────────────────────────────


class User(Base):
    """A platform user."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_username", "username", unique=True),
        {"schema": "auth"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ldap_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pats: Mapped[list[PersonalAccessToken]] = relationship(back_populates="user")


class PersonalAccessToken(Base):
    """A Personal Access Token for API authentication."""

    __tablename__ = "pats"
    __table_args__ = (
        Index("ix_pats_user_id", "user_id"),
        Index("ix_pats_token_hash", "token_hash", unique=True),
        {"schema": "auth"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="pats")


class Policy(Base):
    """An authorization policy rule."""

    __tablename__ = "policies"
    __table_args__ = (
        Index("ix_policies_subject", "subject_type", "subject_id"),
        Index("ix_policies_resource", "resource_id"),
        {"schema": "auth"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "user", "group"
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)  # service_id or "*"
    action_pattern: Mapped[str] = mapped_column(String(255), nullable=False, default="*")
    risk_threshold: Mapped[str] = mapped_column(String(32), nullable=False, default="safe")
    decision: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="deny",
    )  # allow, deny, require_approval
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Append-only audit log for all privileged operations."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor", "actor"),
        Index("ix_audit_log_timestamp", "timestamp"),
        Index("ix_audit_log_action", "action"),
        {"schema": "auth"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
