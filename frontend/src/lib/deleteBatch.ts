import { api } from "../api/client";
import type { BatchDeleteProgress } from "../api/types";

export type DeleteTotals = { objects: number; images: number };

/**
 * Deleting a big batch takes several bounded server calls. Keep calling until the server
 * reports `done`, reporting running totals. Errors (for example the post-archive cooldown)
 * propagate to the caller; calling again later resumes where it stopped.
 */
export const deleteBatchFully = async (
  batchId: string,
  onProgress: (totals: DeleteTotals) => void = () => undefined,
  step: (id: string) => Promise<BatchDeleteProgress> = api.deleteBatch,
  maxCalls = 10_000,
): Promise<DeleteTotals> => {
  const totals: DeleteTotals = { objects: 0, images: 0 };
  for (let call = 0; call < maxCalls; call += 1) {
    const progress = await step(batchId);
    totals.objects += progress.objects_deleted;
    totals.images += progress.images_deleted;
    onProgress({ ...totals });
    if (progress.done) return totals;
  }
  throw new Error("Delete did not finish; try again to resume.");
};
