import { getIdToken } from "../auth/sessionStore";
import { config } from "../config";
import type {
  Batch,
  Box,
  CompleteResult,
  ImageDetail,
  ImageSummary,
  Label,
  ReviewResult,
  ReviewStats,
  UploadTicket,
} from "./types";

export type ApiError = Error & {
  name: "ApiError";
  status: number;
  code: string;
  detail: Record<string, unknown>;
};

export const makeApiError = (
  status: number,
  code: string,
  message: string,
  detail: Record<string, unknown> = {},
): ApiError => Object.assign(new Error(message), { name: "ApiError" as const, status, code, detail });

export const isApiError = (e: unknown): e is ApiError =>
  e instanceof Error && e.name === "ApiError";

/** True when the server says we no longer hold the lease on this image. */
export const isLeaseLost = (e: unknown): boolean =>
  isApiError(e) && e.status === 409 && e.code === "lease_lost";

export const isUnauthorized = (e: unknown): boolean => isApiError(e) && e.status === 401;

let onUnauthorized: () => void = () => undefined;

/** Wired up by the AuthProvider so the API layer stays free of React. */
export const setUnauthorizedHandler = (handler: () => void): void => {
  onUnauthorized = handler;
};

const parseError = async (res: Response): Promise<ApiError> => {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // not JSON
  }
  const raw = (body as { detail?: unknown } | null)?.detail;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const d = raw as Record<string, unknown>;
    return makeApiError(res.status, String(d.code ?? "error"), String(d.message ?? res.statusText), d);
  }
  if (Array.isArray(raw)) {
    // FastAPI validation errors
    const msg = raw.map((r: { msg?: string }) => r.msg ?? "invalid").join("; ");
    return makeApiError(res.status, "validation_error", msg, { errors: raw });
  }
  return makeApiError(res.status, "error", typeof raw === "string" ? raw : res.statusText);
};

type RequestOptions = { method?: string; body?: unknown; signal?: AbortSignal };

export const request = async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
  const token = getIdToken();
  const res = await fetch(`${config.apiUrl}${path}`, {
    method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
    headers: {
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });
  if (res.status === 401) onUnauthorized();
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
};

const toWire = (boxes: Box[]) =>
  boxes.map(({ id, label, x, y, w, h }) => ({ id, label, x, y, w, h }));

export const api = {
  devLogin: (email: string, roles: string[]) =>
    request<{ token: string; sub: string }>("/dev/login", { body: { email, roles } }),

  listBatches: () => request<Batch[]>("/batches"),
  getBatch: (id: string) => request<Batch>(`/batches/${id}`),
  createBatch: (name: string) => request<Batch>("/batches", { body: { name } }),
  listLabels: (batchId: string) => request<Label[]>(`/batches/${batchId}/labels`),

  requestUploads: (
    batchId: string,
    files: { filename: string; content_type: string; size: number }[],
  ) => request<{ tickets: UploadTicket[] }>(`/batches/${batchId}/uploads`, { body: { files } }),
  completeUploads: (batchId: string, imageIds: string[]) =>
    request<{ results: CompleteResult[] }>(`/batches/${batchId}/uploads/complete`, {
      body: { image_ids: imageIds },
    }),

  /** Returns null when the queue is empty (HTTP 204). */
  nextImage: (batchId: string) =>
    request<ImageDetail | undefined>(`/batches/${batchId}/images/next`, { method: "POST", body: {} }).then(
      (r) => r ?? null,
    ),
  nextForReview: (batchId: string) =>
    request<ImageDetail | undefined>(`/batches/${batchId}/reviews/next`, { method: "POST", body: {} }).then(
      (r) => r ?? null,
    ),
  getImage: (id: string) => request<ImageDetail>(`/images/${id}`),
  heartbeat: (id: string) =>
    request<{ image_id: string; lease_expires_at: string }>(`/images/${id}/heartbeat`, {
      method: "POST",
      body: {},
    }),
  release: (id: string) => request<void>(`/images/${id}/release`, { method: "POST", body: {} }),
  skip: (id: string) => request<void>(`/images/${id}/skip`, { method: "POST", body: {} }),
  saveDraft: (id: string, boxes: Box[]) =>
    request<ImageDetail>(`/images/${id}/annotations`, {
      method: "PUT",
      body: { annotations: toWire(boxes) },
    }),
  submit: (id: string, boxes: Box[]) =>
    request<ImageDetail>(`/images/${id}/submit`, { body: { annotations: toWire(boxes) } }),
  review: (id: string, decision: "accepted" | "rejected", reason?: string) =>
    request<ReviewResult>(`/images/${id}/review`, { body: { decision, reason } }),

  reviewStats: (batchId: string) => request<ReviewStats>(`/batches/${batchId}/review-stats`),
  escalated: (batchId: string) => request<ImageSummary[]>(`/batches/${batchId}/escalated`),
  requeue: (id: string) => request<void>(`/images/${id}/requeue`, { method: "POST", body: {} }),
  exportCoco: (batchId: string, includeUnreviewed: boolean) =>
    request<unknown>(`/batches/${batchId}/export?include_unreviewed=${includeUnreviewed}`),
};
