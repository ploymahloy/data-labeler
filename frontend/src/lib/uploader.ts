import type { CompleteResult, UploadTicket } from "../api/types";

export type UploadStatus =
  | "queued"
  | "registering"
  | "uploading"
  | "verifying"
  | "ready"
  | "duplicate"
  | "invalid"
  | "error";

export type UploadItem = {
  key: string;
  file: File;
  status: UploadStatus;
  /** 0-1 byte progress of the PUT to S3. */
  progress: number;
  message?: string;
  imageId?: string;
  ticket?: UploadTicket;
};

export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const MAX_BYTES = 25 * 1024 * 1024;

export const FINAL_STATUSES: UploadStatus[] = ["ready", "duplicate", "invalid"];

export const chunk = <T>(items: T[], size: number): T[][] => {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
};

/** Runs `worker` over `items` with at most `limit` in flight. Never rejects; workers handle errors. */
export const runWithConcurrency = async <T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> => {
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const item = items[next];
      next += 1;
      if (item !== undefined) await worker(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
};

/** Returns a user-facing problem with the file, or null if it can be uploaded. */
export const validateFile = (file: Pick<File, "type" | "size" | "name">): string | null => {
  if (!ACCEPTED_TYPES.includes(file.type)) return `${file.name}: only PNG, JPEG and WebP images are supported`;
  if (file.size > MAX_BYTES) return `${file.name}: larger than ${MAX_BYTES / 1024 / 1024} MB`;
  if (file.size === 0) return `${file.name}: file is empty`;
  return null;
};

export type UploadDeps = {
  requestUploads: (
    files: { filename: string; content_type: string; size: number }[],
  ) => Promise<{ tickets: UploadTicket[] }>;
  completeUploads: (imageIds: string[]) => Promise<{ results: CompleteResult[] }>;
  put: (ticket: UploadTicket, file: File, onProgress: (fraction: number) => void) => Promise<void>;
  sleep?: (ms: number) => Promise<void>;
};

type Options = {
  concurrency?: number;
  chunkSize?: number;
  attempts?: number;
};

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const messageOf = (e: unknown): string => (e instanceof Error ? e.message : "Upload failed");

/**
 * Registers, uploads (in parallel, with retries) and verifies the given items.
 * `update` is called with a patch whenever an item changes. Items already final are skipped,
 * so calling this again retries only failures.
 */
export const processUploads = async (
  items: UploadItem[],
  deps: UploadDeps,
  update: (key: string, patch: Partial<UploadItem>) => void,
  { concurrency = 4, chunkSize = 50, attempts = 3 }: Options = {},
): Promise<void> => {
  const sleep = deps.sleep ?? defaultSleep;
  const todo = items.filter((i) => !FINAL_STATUSES.includes(i.status));

  for (const group of chunk(todo, chunkSize)) {
    // 1. Register (only items without a usable ticket).
    const needTicket = group.filter((i) => !i.ticket);
    if (needTicket.length > 0) {
      needTicket.forEach((i) => update(i.key, { status: "registering", message: undefined }));
      try {
        const { tickets } = await deps.requestUploads(
          needTicket.map((i) => ({
            filename: i.file.name,
            content_type: i.file.type,
            size: i.file.size,
          })),
        );
        needTicket.forEach((item, idx) => {
          const ticket = tickets[idx];
          if (ticket) {
            item.ticket = ticket;
            item.imageId = ticket.image_id;
            update(item.key, { ticket, imageId: ticket.image_id });
          }
        });
      } catch (e) {
        needTicket.forEach((i) => update(i.key, { status: "error", message: messageOf(e) }));
      }
    }

    // 2. Upload bytes straight to S3.
    const ready = group.filter((i) => i.ticket);
    const uploaded: UploadItem[] = [];
    await runWithConcurrency(ready, concurrency, async (item) => {
      const ticket = item.ticket as UploadTicket;
      update(item.key, { status: "uploading", progress: 0, message: undefined });
      for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try {
          await deps.put(ticket, item.file, (p) => update(item.key, { progress: p }));
          update(item.key, { status: "verifying", progress: 1 });
          uploaded.push(item);
          return;
        } catch (e) {
          const expired = (e as { status?: number }).status === 403;
          if (expired || attempt === attempts) {
            item.ticket = undefined; // next retry re-registers
            update(item.key, { status: "error", ticket: undefined, message: messageOf(e) });
            return;
          }
          await sleep(500 * attempt);
        }
      }
    });

    // 3. Ask the server to verify bytes, hash and de-duplicate.
    if (uploaded.length > 0) {
      try {
        const { results } = await deps.completeUploads(uploaded.map((i) => i.imageId as string));
        const byId = new Map(results.map((r) => [r.image_id, r]));
        uploaded.forEach((item) => {
          const r = byId.get(item.imageId as string);
          if (!r || r.result === "missing") {
            update(item.key, { status: "error", message: "The upload did not arrive; try again" });
          } else if (r.result === "ready") {
            update(item.key, { status: "ready", progress: 1 });
          } else if (r.result === "duplicate") {
            update(item.key, { status: "duplicate", message: "Identical image already in this batch" });
          } else {
            update(item.key, { status: "invalid", message: r.detail ?? "Not a valid image" });
          }
        });
      } catch (e) {
        uploaded.forEach((i) => update(i.key, { status: "error", message: messageOf(e) }));
      }
    }
  }
};

/** Browser PUT with progress (fetch cannot report upload progress). */
export const putWithProgress = (
  ticket: UploadTicket,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<void> =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", ticket.upload_url);
    Object.entries(ticket.headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(Object.assign(new Error(`Upload failed (${xhr.status})`), { status: xhr.status }));
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(file);
  });
