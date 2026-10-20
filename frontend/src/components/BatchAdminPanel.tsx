import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, isApiError } from "../api/client";
import type { Batch } from "../api/types";
import { deleteBatchFully } from "../lib/deleteBatch";
import type { DeleteTotals } from "../lib/deleteBatch";

type Props = {
  batch: Batch;
  /** Called after archive/unarchive so the page can refetch. */
  onChanged: () => void;
  /** Called once the batch no longer exists. */
  onDeleted: () => void;
};

const describeError = (e: unknown): string => {
  if (isApiError(e) && e.code === "archive_cooldown") {
    const wait = Number(e.detail.retry_after_seconds ?? 0);
    return `Upload links issued before archiving may still be valid. Try again in ${wait > 0 ? `${wait} seconds` : "a moment"}.`;
  }
  return e instanceof Error ? e.message : "Something went wrong";
};

/** Admin-only controls: archive (reversible), unarchive, and permanent delete (archived only). */
export const BatchAdminPanel = ({ batch, onChanged, onDeleted }: Props) => {
  const [confirming, setConfirming] = useState<"archive" | "delete" | null>(null);
  const [typedName, setTypedName] = useState("");
  const [progress, setProgress] = useState<DeleteTotals | null>(null);
  const archived = batch.status === "archived";

  const archive = useMutation({
    mutationFn: () => api.archiveBatch(batch.id),
    onSuccess: () => {
      setConfirming(null);
      onChanged();
    },
  });
  const unarchive = useMutation({
    mutationFn: () => api.unarchiveBatch(batch.id),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: () => deleteBatchFully(batch.id, setProgress),
    onSuccess: onDeleted,
  });

  const error = archive.error ?? unarchive.error ?? remove.error;
  const busy = archive.isPending || unarchive.isPending || remove.isPending;

  return (
    <section aria-labelledby="batch-admin-heading">
      <h2 id="batch-admin-heading">Batch administration</h2>

      {!archived && confirming !== "archive" && (
        <button className="secondary" onClick={() => setConfirming("archive")} disabled={busy}>
          Archive batch
        </button>
      )}
      {!archived && confirming === "archive" && (
        <div className="card warning" role="alertdialog" aria-label="Confirm archive">
          <p>
            Archiving stops all work in this batch: active leases return to their queues, and nobody can claim or upload
            until you unarchive it. Exports keep working.
          </p>
          <div className="row">
            <button onClick={() => archive.mutate()} disabled={busy}>
              Confirm archive
            </button>
            <button className="secondary" onClick={() => setConfirming(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {archived && (
        <>
          <p className="muted">
            This batch is archived{batch.archived_at ? ` (${new Date(batch.archived_at).toLocaleString()})` : ""}. Export
            your data before deleting it.
          </p>
          <div className="row">
            <button onClick={() => unarchive.mutate()} disabled={busy}>
              Unarchive
            </button>
            {confirming !== "delete" && (
              <button className="danger" onClick={() => setConfirming("delete")} disabled={busy}>
                Delete permanently…
              </button>
            )}
          </div>
          {confirming === "delete" && (
            <div className="card warning" role="alertdialog" aria-label="Confirm delete">
              <p>
                This permanently deletes every image, annotation and review in <strong>{batch.name}</strong>. It cannot
                be undone.
              </p>
              <label>
                Type the batch name to confirm{" "}
                <input value={typedName} onChange={(e) => setTypedName(e.target.value)} aria-label="Batch name" />
              </label>
              <div className="row">
                <button
                  className="danger"
                  onClick={() => remove.mutate()}
                  disabled={busy || typedName !== batch.name}
                >
                  Delete forever
                </button>
                <button className="secondary" onClick={() => setConfirming(null)} disabled={busy}>
                  Cancel
                </button>
              </div>
              {progress && (
                <p role="status" data-testid="delete-progress">
                  Removed {progress.objects} files and {progress.images} images…
                </p>
              )}
            </div>
          )}
        </>
      )}

      {error && (
        <p className="error" role="alert">
          {describeError(error)}
        </p>
      )}
    </section>
  );
};
