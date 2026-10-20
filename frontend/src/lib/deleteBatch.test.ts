import { describe, expect, it, vi } from "vitest";
import { makeApiError } from "../api/client";
import type { BatchDeleteProgress } from "../api/types";
import { deleteBatchFully } from "./deleteBatch";

const progress = (objects: number, images: number, done: boolean): BatchDeleteProgress => ({
  done,
  objects_deleted: objects,
  images_deleted: images,
});

describe("deleteBatchFully", () => {
  it("keeps calling until the server says done and sums the progress", async () => {
    const steps = [progress(1000, 0, false), progress(500, 0, false), progress(0, 1200, false), progress(0, 300, true)];
    const step = vi.fn(async () => steps.shift() as BatchDeleteProgress);
    const seen: number[] = [];

    const totals = await deleteBatchFully("b1", (t) => seen.push(t.objects + t.images), step);

    expect(step).toHaveBeenCalledTimes(4);
    expect(totals).toEqual({ objects: 1500, images: 1500 });
    expect(seen).toEqual([1000, 1500, 2700, 3000]);
  });

  it("propagates errors such as the archive cooldown so the caller can resume later", async () => {
    const step = vi
      .fn<(id: string) => Promise<BatchDeleteProgress>>()
      .mockResolvedValueOnce(progress(10, 0, false))
      .mockRejectedValueOnce(makeApiError(409, "archive_cooldown", "wait", { retry_after_seconds: 30 }));

    await expect(deleteBatchFully("b1", undefined, step)).rejects.toMatchObject({
      code: "archive_cooldown",
      detail: { retry_after_seconds: 30 },
    });
  });

  it("gives up instead of looping forever", async () => {
    const step = async () => progress(0, 0, false);
    await expect(deleteBatchFully("b1", undefined, step, 3)).rejects.toThrow(/did not finish/);
  });
});
