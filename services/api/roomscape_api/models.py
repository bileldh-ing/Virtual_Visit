"""Persistent data model. All timestamps are timezone-aware UTC."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> uuid.UUID:
    return uuid.uuid4()


TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email_verified_at: Mapped[datetime | None] = mapped_column(TS)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled | deleted
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class EmailToken(Base):
    __tablename__ = "email_tokens"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # verify | reset | invite
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    is_personal: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int | None] = mapped_column(Integer)
    default_share_visibility: Mapped[str] = mapped_column(String(20), default="UNLISTED")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # OWNER | ADMIN | EDITOR | VIEWER
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    user: Mapped[User] = relationship(lazy="joined")


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(TS)
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_org_status_updated", "org_id", "status", "updated_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    address_private: Mapped[str] = mapped_column(String(300), default="")  # never exposed publicly
    property_type: Mapped[str] = mapped_column(String(40), default="apartment")
    rooms_estimate: Mapped[int | None] = mapped_column(Integer)
    listing_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | archived
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | deleting | deleted
    capture_checklist: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cover_upload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    is_public_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    public_consent_at: Mapped[datetime | None] = mapped_column(TS)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (UniqueConstraint("project_id", "sha256", name="uq_upload_project_sha"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20), default="PHOTO")  # PHOTO | FLOOR_PLAN
    display_name: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(400))
    web_key: Mapped[str | None] = mapped_column(String(400))
    thumb_key: Mapped[str | None] = mapped_column(String(400))
    mime: Mapped[str] = mapped_column(String(40))
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    is_panorama: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="stored")  # stored | deleted
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class ImageQualityResult(Base):
    __tablename__ = "image_quality_results"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    upload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), unique=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    issues: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class CaptureReport(Base):
    __tablename__ = "capture_reports"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30))  # PENDING | READY | READY_WITH_WARNINGS | ...
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class ReconstructionJob(Base):
    __tablename__ = "reconstruction_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_job_idempotency"),
        Index("ix_jobs_state_created", "state", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(30))  # PHOTOGRAMMETRY | PANORAMA_TOUR | GAUSSIAN_SPLAT
    state: Mapped[str] = mapped_column(String(40), default="CREATED")
    idempotency_key: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_upload_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    enqueued_at: Mapped[datetime | None] = mapped_column(TS)
    next_attempt_at: Mapped[datetime | None] = mapped_column(TS)
    stage_started_at: Mapped[datetime | None] = mapped_column(TS)
    error_category: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(500))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # 2.0 engines: requested engine (auto | marble_world | depth_room | colmap_sparse | panorama_tour | ...),
    # the engine that produced the result, durable provider state (operation ids) and the fallback record.
    engine: Mapped[str | None] = mapped_column(String(40))
    engine_used: Mapped[str | None] = mapped_column(String(40))
    provider_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fallback: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cost_estimate: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "number"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconstruction_jobs.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(120))
    worker_version: Mapped[str] = mapped_column(String(40), default="")
    engine: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="RUNNING")  # RUNNING | SUCCEEDED | FAILED | LOST | CANCELLED
    lease_expires_at: Mapped[datetime] = mapped_column(TS)
    heartbeat_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    error_category: Mapped[str | None] = mapped_column(String(40))
    error_detail: Mapped[str | None] = mapped_column(Text)  # redacted operator detail


class JobStageEvent(Base):
    __tablename__ = "job_stage_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconstruction_jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_attempts.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(40))
    event: Mapped[str] = mapped_column(String(20))  # started | completed | skipped | failed | state
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class UploadManifest(Base):
    __tablename__ = "upload_manifests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconstruction_jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_attempts.id", ondelete="CASCADE"))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconstruction_jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_attempts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(400))
    mime: Mapped[str] = mapped_column(String(60))
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    public_safe: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class ArtifactValidationResult(Base):
    __tablename__ = "artifact_validation_results"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class RoomLabel(Base):
    __tablename__ = "room_labels"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    cover_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    dimensions_note: Mapped[str] = mapped_column(String(120), default="")  # owner-entered only


class TourNode(Base):
    __tablename__ = "tour_nodes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reconstruction_jobs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))  # VIEWPOINT (registered photo) | PANORAMA
    ref: Mapped[str] = mapped_column(String(200))  # image name / panorama file in the artifact set
    upload_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("uploads.id", ondelete="SET NULL"))
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("room_labels.id", ondelete="SET NULL"))
    label: Mapped[str] = mapped_column(String(120), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="reconstruction")  # reconstruction | owner


class TourEdge(Base):
    __tablename__ = "tour_edges"
    __table_args__ = (UniqueConstraint("from_node_id", "to_node_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    from_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tour_nodes.id", ondelete="CASCADE"))
    to_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tour_nodes.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(80), default="")
    yaw: Mapped[float | None] = mapped_column(Float)
    pitch: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Hotspot(Base):
    __tablename__ = "hotspots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tour_nodes.id", ondelete="CASCADE"))
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("room_labels.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(30), default="feature")
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000), default="")
    position: Mapped[dict[str, Any]] = mapped_column(JSON)  # {"xyz":[..]} scene coords or {"yaw":..,"pitch":..}
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ShareLink(Base):
    __tablename__ = "share_links"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(8))
    visibility: Mapped[str] = mapped_column(String(20))  # UNLISTED | RESTRICTED
    password_hash: Mapped[str | None] = mapped_column(String(255))
    allow_embed: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_origins: Mapped[list[Any]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ShareAccessEvent(Base):
    __tablename__ = "share_access_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    share_link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("share_links.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(20))  # granted | denied_revoked | denied_expired | denied_password
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)  # no IP / UA stored (privacy)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    plan_code: Mapped[str] = mapped_column(String(20), default="FREE")
    plan_version: Mapped[str] = mapped_column(String(20), default="2026-10")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | past_due | canceled | expired
    provider: Mapped[str] = mapped_column(String(20), default="none")  # none | test | stripe
    provider_customer_id: Mapped[str | None] = mapped_column(String(120))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120))
    current_period_end: Mapped[datetime | None] = mapped_column(TS)
    last_event_at: Mapped[datetime | None] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class UsageLedger(Base):
    """Append-only usage ledger. Rows are never updated or deleted by the app."""

    __tablename__ = "usage_ledger"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # reconstruction_minutes | storage_bytes_snapshot | ...
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reconstruction_jobs.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class BillingEvent(Base):
    __tablename__ = "billing_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(20))
    provider_event_id: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(80))
    event_created: Mapped[datetime | None] = mapped_column(TS)
    outcome: Mapped[str] = mapped_column(String(40))
    processed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_org_created", "org_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(320), default="")
    kind: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    delivered_via: Mapped[str] = mapped_column(String(20), default="outbox")  # outbox | smtp | failed
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(TS)


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))  # project | account
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | in_progress | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    keys_deleted: Mapped[int] = mapped_column(Integer, default=0)
    verified_empty: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class AbuseReport(Base):
    __tablename__ = "abuse_reports"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    share_token_prefix: Mapped[str] = mapped_column(String(8), default="")
    reason: Mapped[str] = mapped_column(String(40))  # people | documents | security | other
    details: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
