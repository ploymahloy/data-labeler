export type Role = "admin" | "labeler" | "reviewer";

export type ImageStatus =
  | "uploading"
  | "pending"
  | "in_progress"
  | "submitted"
  | "in_review"
  | "accepted"
  | "rework"
  | "escalated";

export type Batch = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  created_by: string;
  archived_at: string | null;
  counts: Partial<Record<ImageStatus, number>>;
};

export type BatchDeleteProgress = {
  done: boolean;
  objects_deleted: number;
  images_deleted: number;
};

/** Normalized (0-1) bounding box with an entity name. `id` is client-generated. */
export type Box = {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
};

export type Rect = Pick<Box, "x" | "y" | "w" | "h">;

export type ReviewFeedback = {
  decision: string;
  reason: string | null;
  reviewer_id: string;
  submission_no: number;
  created_at: string;
};

export type ImageDetail = {
  id: string;
  batch_id: string;
  filename: string;
  url: string;
  width: number | null;
  height: number | null;
  status: ImageStatus;
  lease_kind: "annotate" | "review" | null;
  lease_expires_at: string | null;
  assigned_to: string | null;
  submitted_by: string | null;
  rejection_count: number;
  last_submission_no: number;
  annotations: (Box & { created_by: string })[];
  review_feedback: ReviewFeedback | null;
};

export type ImageSummary = {
  id: string;
  filename: string;
  status: ImageStatus;
  submitted_by: string | null;
  rejection_count: number;
};

export type UploadTicket = {
  image_id: string;
  filename: string;
  upload_url: string;
  headers: Record<string, string>;
};

export type CompleteResult = {
  image_id: string;
  result: "ready" | "duplicate" | "missing" | "invalid";
  duplicate_of: string | null;
  detail: string | null;
};

export type Label = { id: string; name: string };

export type ReviewerStats = {
  reviewer_id: string;
  email: string;
  accepted: number;
  rejected: number;
};

export type ReviewStats = {
  batch_id: string;
  counts: Partial<Record<ImageStatus, number>>;
  reviewers: ReviewerStats[];
  rejection_rate: number | null;
};

export type ReviewResult = {
  image_id: string;
  decision: "accepted" | "rejected";
  resulting_status: ImageStatus;
};

export type AdminUser = {
  sub: string;
  email: string;
  enabled: boolean;
  status: string;
  roles: Role[];
  created_at: string | null;
};

export type AdminUsersPage = {
  users: AdminUser[];
  next_token: string | null;
};
