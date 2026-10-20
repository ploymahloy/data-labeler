import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
_EPS = 1e-6


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class BatchOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    created_by: str
    counts: dict[str, int] = {}


# --- uploads ---------------------------------------------------------------------
class UploadFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str
    size: int = Field(gt=0)

    @field_validator("content_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"content_type must be one of {sorted(ALLOWED_CONTENT_TYPES)}")
        return v


class UploadRequest(BaseModel):
    files: list[UploadFile] = Field(min_length=1)


class UploadTicket(BaseModel):
    image_id: uuid.UUID
    filename: str
    upload_url: str
    headers: dict[str, str]


class UploadResponse(BaseModel):
    tickets: list[UploadTicket]


class CompleteRequest(BaseModel):
    image_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class CompleteResult(BaseModel):
    image_id: uuid.UUID
    result: Literal["ready", "duplicate", "missing", "invalid"]
    duplicate_of: uuid.UUID | None = None
    detail: str | None = None


class CompleteResponse(BaseModel):
    results: list[CompleteResult]


# --- annotations -----------------------------------------------------------------
class AnnotationIn(BaseModel):
    id: uuid.UUID
    label: str = Field(min_length=1, max_length=100)
    x: float
    y: float
    w: float
    h: float

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("label must not be blank")
        return v

    @model_validator(mode="after")
    def _bounds(self) -> "AnnotationIn":
        if self.x < 0 or self.y < 0 or self.w <= 0 or self.h <= 0:
            raise ValueError("box must have non-negative origin and positive size")
        if self.x + self.w > 1 + _EPS or self.y + self.h > 1 + _EPS:
            raise ValueError("box must lie within the image (normalized 0-1 coordinates)")
        return self


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    x: float
    y: float
    w: float
    h: float
    created_by: str


class SaveAnnotationsRequest(BaseModel):
    annotations: list[AnnotationIn]


class SubmitRequest(BaseModel):
    annotations: list[AnnotationIn]


# --- images ----------------------------------------------------------------------
class ReviewFeedback(BaseModel):
    decision: str
    reason: str | None
    reviewer_id: str
    submission_no: int
    created_at: datetime


class ImageDetail(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    filename: str
    url: str
    width: int | None
    height: int | None
    status: str
    lease_kind: str | None
    lease_expires_at: datetime | None
    assigned_to: str | None
    submitted_by: str | None
    rejection_count: int
    last_submission_no: int
    annotations: list[AnnotationOut]
    review_feedback: ReviewFeedback | None = None


class ImageSummary(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    submitted_by: str | None
    rejection_count: int


class LeaseOut(BaseModel):
    image_id: uuid.UUID
    lease_expires_at: datetime


class ReviewRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reason_on_reject(self) -> "ReviewRequest":
        if self.decision == "rejected" and not (self.reason and self.reason.strip()):
            raise ValueError("a reason is required when rejecting")
        return self


class ReviewOut(BaseModel):
    image_id: uuid.UUID
    decision: str
    resulting_status: str


class LabelOut(BaseModel):
    id: uuid.UUID
    name: str


class ReviewerStats(BaseModel):
    reviewer_id: str
    email: str
    accepted: int
    rejected: int


class ReviewStats(BaseModel):
    batch_id: uuid.UUID
    counts: dict[str, int]
    reviewers: list[ReviewerStats]
    rejection_rate: float | None


class DevLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    roles: list[Literal["admin", "labeler", "reviewer"]] = ["labeler"]


class DevLoginResponse(BaseModel):
    token: str
    sub: str
