import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(enum.StrEnum):
    admin = "admin"
    labeler = "labeler"
    reviewer = "reviewer"


class ImageStatus(enum.StrEnum):
    uploading = "uploading"  # registered, bytes not yet verified in S3
    pending = "pending"
    in_progress = "in_progress"
    submitted = "submitted"  # awaiting review
    in_review = "in_review"
    accepted = "accepted"
    rework = "rework"
    escalated = "escalated"  # rejected too many times; admin queue


class LeaseKind(enum.StrEnum):
    annotate = "annotate"
    review = "review"


class Decision(enum.StrEnum):
    accepted = "accepted"
    rejected = "rejected"


def _in_list(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class User(Base):
    __tablename__ = "users"

    cognito_sub: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    roles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # False = refused by the API immediately, even with a still-valid ID token.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # ID tokens issued before this moment are rejected (401) so clients refresh their groups.
    tokens_valid_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.cognito_sub"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(16), default="open")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed', 'archived')", name="ck_batches_status"),
    )


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    normalized: Mapped[str] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("batch_id", "normalized", name="uq_labels_batch_normalized"),
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    s3_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.cognito_sub"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    status: Mapped[str] = mapped_column(String(16), default=ImageStatus.uploading.value)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.cognito_sub"))
    lease_kind: Mapped[str | None] = mapped_column(String(16))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[str | None] = mapped_column(ForeignKey("users.cognito_sub"))
    last_submission_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejection_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rework_reserved_for: Mapped[str | None] = mapped_column(ForeignKey("users.cognito_sub"))
    rework_reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="image", cascade="all, delete-orphan", order_by="Annotation.created_at"
    )

    __table_args__ = (
        CheckConstraint(_in_list("status", ImageStatus), name="ck_images_status"),
        CheckConstraint(
            "lease_kind IS NULL OR lease_kind IN ('annotate', 'review')",
            name="ck_images_lease_kind",
        ),
        # Leases are all-or-nothing: an assignee always has a kind and an expiry.
        CheckConstraint(
            "(assigned_to IS NULL) = (lease_kind IS NULL) "
            "AND (assigned_to IS NULL) = (lease_expires_at IS NULL)",
            name="ck_images_lease_consistent",
        ),
        # Image-level dedupe: identical bytes cannot appear twice in a batch.
        Index(
            "uq_images_batch_sha256",
            "batch_id",
            "sha256",
            unique=True,
            postgresql_where=text("sha256 IS NOT NULL"),
        ),
        Index("ix_images_batch_status", "batch_id", "status"),
        # Keyset pagination of the streaming export; see migration 0006.
        Index("ix_images_batch_created", "batch_id", "created_at", "id"),
        Index("ix_images_assigned_to", "assigned_to"),
        # Queue-health queries (reaper and /metrics); see migration 0003.
        Index("ix_images_submitted", "id", postgresql_where=text("status = 'submitted'")),
        Index(
            "ix_images_lease_expiry",
            "lease_expires_at",
            postgresql_where=text("assigned_to IS NOT NULL"),
        ),
    )


class ImageSubmission(Base):
    """Every annotator of every attempt; used to keep reviewers independent of authors."""

    __tablename__ = "image_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    submission_no: Mapped[int] = mapped_column(Integer)
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.cognito_sub"))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("image_id", "submission_no", name="uq_image_submissions_attempt"),
        Index("ix_image_submissions_image_user", "image_id", "submitted_by"),
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    submission_no: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.cognito_sub"))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # A submission can only be decided once.
        UniqueConstraint("image_id", "submission_no", name="uq_reviews_attempt"),
        CheckConstraint(_in_list("decision", Decision), name="ck_reviews_decision"),
        CheckConstraint(
            "decision <> 'rejected' OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_reviews_reject_needs_reason",
        ),
        Index("ix_reviews_image_reviewer", "image_id", "reviewer_id"),
    )


class ImageSkip(Base):
    """Skipped images go back to the pool but are never re-offered to the skipper."""

    __tablename__ = "image_skips"

    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.cognito_sub"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Annotation(Base):
    __tablename__ = "annotations"

    # Client-generated UUID so retried saves are idempotent.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), index=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("labels.id"))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    fingerprint: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.cognito_sub"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    image: Mapped[Image] = relationship(back_populates="annotations")
    label: Mapped[Label] = relationship()

    __table_args__ = (
        CheckConstraint(
            "x >= 0 AND y >= 0 AND w > 0 AND h > 0 AND x + w <= 1.000001 AND y + h <= 1.000001",
            name="ck_annotations_bounds",
        ),
        # DB-level backstop for exact duplicates (same label, same rounded box).
        # Deferred so a bulk save may shuffle boxes between positions within one transaction.
        UniqueConstraint(
            "image_id",
            "fingerprint",
            name="uq_annotations_fingerprint",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
