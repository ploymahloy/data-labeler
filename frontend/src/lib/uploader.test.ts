import { describe, expect, it } from "vitest";
import type { CompleteResult, UploadTicket } from "../api/types";
import { chunk, processUploads, runWithConcurrency, validateFile } from "./uploader";
import type { UploadDeps, UploadItem } from "./uploader";

const file = (name: string, type = "image/png", size = 10) =>
  new File([new Uint8Array(size)], name, { type });

const item = (name: string): UploadItem => ({
  key: name,
  file: file(name),
  status: "queued",
  progress: 0,
});

const apply = (items: UploadItem[]) => (key: string, patch: Partial<UploadItem>) => {
  const target = items.find((i) => i.key === key);
  if (target) Object.assign(target, patch);
};

type Behavior = {
  failPutsFor?: Record<string, number>; // filename -> number of failing attempts
  results?: Record<string, CompleteResult["result"]>;
};

/** An in-memory implementation of the upload protocol (same contract as the real API + S3). */
const fakeServer = (behavior: Behavior = {}) => {
  const log = { registered: [] as string[], puts: [] as string[], completed: [] as string[] };
  const attempts: Record<string, number> = {};
  const deps: UploadDeps = {
    sleep: async () => undefined,
    requestUploads: async (files) => {
      log.registered.push(...files.map((f) => f.filename));
      return {
        tickets: files.map<UploadTicket>((f) => ({
          image_id: `id-${f.filename}`,
          filename: f.filename,
          upload_url: `https://s3.test/${f.filename}`,
          headers: { "Content-Type": f.content_type },
        })),
      };
    },
    put: async (ticket, _file, onProgress) => {
      const name = ticket.filename;
      attempts[name] = (attempts[name] ?? 0) + 1;
      onProgress(0.5);
      if (attempts[name]! <= (behavior.failPutsFor?.[name] ?? 0)) throw new Error("boom");
      log.puts.push(name);
    },
    completeUploads: async (ids) => {
      log.completed.push(...ids);
      return {
        results: ids.map<CompleteResult>((id) => ({
          image_id: id,
          result: behavior.results?.[id.replace("id-", "")] ?? "ready",
          duplicate_of: null,
          detail: null,
        })),
      };
    },
  };
  return { deps, log };
};

describe("helpers", () => {
  it("chunks arrays", () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });

  it("limits concurrency", async () => {
    let active = 0;
    let peak = 0;
    await runWithConcurrency([1, 2, 3, 4, 5, 6, 7, 8], 3, async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 5));
      active -= 1;
    });
    expect(peak).toBe(3);
  });

  it("validates file type, size and emptiness", () => {
    expect(validateFile(file("a.png"))).toBeNull();
    expect(validateFile(file("a.gif", "image/gif"))).toMatch(/only PNG/);
    expect(validateFile({ name: "big.png", type: "image/png", size: 30 * 1024 * 1024 })).toMatch(/larger/);
    expect(validateFile(file("e.png", "image/png", 0))).toMatch(/empty/);
  });
});

describe("processUploads", () => {
  it("uploads everything and marks results from the server", async () => {
    const items = ["a.png", "b.png", "c.png"].map(item);
    const { deps, log } = fakeServer({ results: { "b.png": "duplicate", "c.png": "invalid" } });
    await processUploads(items, deps, apply(items));
    expect(items.map((i) => i.status)).toEqual(["ready", "duplicate", "invalid"]);
    expect(log.puts.sort()).toEqual(["a.png", "b.png", "c.png"]);
    expect(items[0]?.progress).toBe(1);
  });

  it("retries transient PUT failures automatically", async () => {
    const items = [item("a.png")];
    const { deps, log } = fakeServer({ failPutsFor: { "a.png": 2 } });
    await processUploads(items, deps, apply(items));
    expect(items[0]?.status).toBe("ready");
    expect(log.puts).toEqual(["a.png"]);
    expect(log.registered).toEqual(["a.png"]); // one registration, ticket reused across retries
  });

  it("reports persistent failures and succeeds on a manual retry without redoing finished files", async () => {
    const items = ["a.png", "b.png"].map(item);
    const { deps, log } = fakeServer({ failPutsFor: { "b.png": 3 } });
    await processUploads(items, deps, apply(items));
    expect(items.map((i) => i.status)).toEqual(["ready", "error"]);

    await processUploads(items, deps, apply(items));
    expect(items.map((i) => i.status)).toEqual(["ready", "ready"]);
    expect(log.puts.filter((n) => n === "a.png")).toHaveLength(1);
  });

  it("treats a missing object as a retryable error", async () => {
    const items = [item("a.png")];
    const { deps } = fakeServer({ results: { "a.png": "missing" } });
    await processUploads(items, deps, apply(items));
    expect(items[0]?.status).toBe("error");
  });

  it("processes in chunks", async () => {
    const items = Array.from({ length: 5 }, (_, i) => item(`f${i}.png`));
    const { deps, log } = fakeServer();
    await processUploads(items, deps, apply(items), { chunkSize: 2 });
    expect(items.every((i) => i.status === "ready")).toBe(true);
    expect(log.completed).toHaveLength(5);
  });
});
