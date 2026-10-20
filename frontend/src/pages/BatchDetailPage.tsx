import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ImageStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { hasRole } from "../auth/jwt";

const STATUS_LABELS: [ImageStatus, string][] = [
  ["pending", "Waiting for annotation"],
  ["in_progress", "Being annotated"],
  ["submitted", "Waiting for review"],
  ["in_review", "Being reviewed"],
  ["rework", "Needs rework"],
  ["escalated", "Escalated to admin"],
  ["accepted", "Accepted"],
];

const downloadJson = (data: unknown, filename: string) => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};

export const BatchDetailPage = () => {
  const { batchId = "" } = useParams();
  const { identity } = useAuth();
  const queryClient = useQueryClient();
  const [includeUnreviewed, setIncludeUnreviewed] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const canLabel = hasRole(identity, "labeler");
  const canReview = hasRole(identity, "reviewer");
  const isAdmin = hasRole(identity, "admin") && identity?.roles.includes("admin");

  const batch = useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => api.getBatch(batchId),
    refetchInterval: 10_000,
  });
  const stats = useQuery({
    queryKey: ["review-stats", batchId],
    queryFn: () => api.reviewStats(batchId),
    enabled: canReview,
  });
  const escalated = useQuery({
    queryKey: ["escalated", batchId],
    queryFn: () => api.escalated(batchId),
    enabled: !!isAdmin,
  });
  const requeue = useMutation({
    mutationFn: (imageId: string) => api.requeue(imageId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["escalated", batchId] });
      await queryClient.invalidateQueries({ queryKey: ["batch", batchId] });
    },
  });

  const onExport = async () => {
    setExportError(null);
    try {
      downloadJson(await api.exportCoco(batchId, includeUnreviewed), `${batch.data?.name ?? "batch"}-coco.json`);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed");
    }
  };

  if (batch.isLoading) return <div className="page">Loading…</div>;
  if (batch.isError || !batch.data) {
    return (
      <div className="page error" role="alert">
        {batch.error?.message ?? "Batch not found"}
      </div>
    );
  }

  return (
    <div className="page">
      <Link to="/">&larr; All batches</Link>
      <h1>{batch.data.name}</h1>

      <div className="row">
        {canLabel && <Link className="button" to={`/batches/${batchId}/upload`}>Upload images</Link>}
        {canLabel && <Link className="button" to={`/batches/${batchId}/label`}>Start labeling</Link>}
        {canReview && <Link className="button" to={`/batches/${batchId}/review`}>Start reviewing</Link>}
      </div>

      <h2>Progress</h2>
      <table className="table" data-testid="status-table">
        <tbody>
          {STATUS_LABELS.map(([status, label]) => (
            <tr key={status}>
              <td>{label}</td>
              <td data-testid={`count-${status}`}>{batch.data.counts[status] ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {canReview && (
        <>
          <h2>Review statistics</h2>
          {stats.data && (
            <>
              <p className="muted">
                Rejection rate:{" "}
                {stats.data.rejection_rate === null ? "n/a" : `${Math.round(stats.data.rejection_rate * 100)}%`}
              </p>
              <table className="table" data-testid="review-stats">
                <thead>
                  <tr>
                    <th>Reviewer</th>
                    <th>Accepted</th>
                    <th>Rejected</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.data.reviewers.map((r) => (
                    <tr key={r.reviewer_id}>
                      <td>{r.email}</td>
                      <td>{r.accepted}</td>
                      <td>{r.rejected}</td>
                    </tr>
                  ))}
                  {stats.data.reviewers.length === 0 && (
                    <tr>
                      <td colSpan={3} className="muted">
                        No reviews yet
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}

          <h2>Export</h2>
          <div className="row">
            <label>
              <input
                type="checkbox"
                checked={includeUnreviewed}
                onChange={(e) => setIncludeUnreviewed(e.target.checked)}
              />{" "}
              Include images not yet reviewed
            </label>
            <button onClick={() => void onExport()}>Download COCO JSON</button>
          </div>
          {exportError && (
            <p className="error" role="alert">
              {exportError}
            </p>
          )}
        </>
      )}

      {isAdmin && (
        <>
          <h2>Escalated images</h2>
          {escalated.data && escalated.data.length === 0 && <p className="muted">None.</p>}
          <ul>
            {escalated.data?.map((img) => (
              <li key={img.id} className="row">
                <span>
                  {img.filename} · rejected {img.rejection_count}×
                </span>
                <button onClick={() => requeue.mutate(img.id)} disabled={requeue.isPending}>
                  Return to annotation queue
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
};
