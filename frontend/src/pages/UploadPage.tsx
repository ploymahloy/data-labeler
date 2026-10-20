import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { processUploads, putWithProgress, validateFile } from "../lib/uploader";
import type { UploadItem, UploadStatus } from "../lib/uploader";

const STATUS_TEXT: Record<UploadStatus, string> = {
  queued: "Waiting",
  registering: "Preparing…",
  uploading: "Uploading",
  verifying: "Verifying…",
  ready: "Ready",
  duplicate: "Duplicate (skipped)",
  invalid: "Invalid image",
  error: "Failed",
};

export const UploadPage = () => {
  const { batchId = "" } = useParams();
  const queryClient = useQueryClient();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const counter = useRef(0);

  const addFiles = useCallback((list: FileList | File[]) => {
    const accepted: UploadItem[] = [];
    const problems: string[] = [];
    Array.from(list).forEach((file) => {
      const problem = validateFile(file);
      if (problem) {
        problems.push(problem);
      } else {
        counter.current += 1;
        accepted.push({ key: `${counter.current}-${file.name}`, file, status: "queued", progress: 0 });
      }
    });
    setRejected(problems);
    setItems((prev) => [...prev, ...accepted]);
  }, []);

  const patch = useCallback((key: string, p: Partial<UploadItem>) => {
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...p } : i)));
  }, []);

  const start = useCallback(async () => {
    setRunning(true);
    try {
      // Work on copies: the orchestrator tracks tickets on its own objects and reports via `patch`.
      const work = items.filter((i) => i.status !== "ready" && i.status !== "duplicate" && i.status !== "invalid");
      await processUploads(
        work.map((i) => ({ ...i })),
        {
          requestUploads: (files) => api.requestUploads(batchId, files),
          completeUploads: (ids) => api.completeUploads(batchId, ids),
          put: putWithProgress,
        },
        patch,
      );
      void queryClient.invalidateQueries({ queryKey: ["batch", batchId] });
    } finally {
      setRunning(false);
    }
  }, [items, batchId, patch, queryClient]);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  };

  const summary = useMemo(() => {
    const count = (s: UploadStatus) => items.filter((i) => i.status === s).length;
    return {
      ready: count("ready"),
      duplicate: count("duplicate"),
      invalid: count("invalid"),
      failed: count("error"),
      pending: items.length - count("ready") - count("duplicate") - count("invalid"),
    };
  }, [items]);

  return (
    <div className="page">
      <Link to={`/batches/${batchId}`}>&larr; Batch</Link>
      <h1>Upload images</h1>

      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p>Drag and drop PNG, JPEG or WebP images here, or</p>
        <button onClick={() => inputRef.current?.click()}>Choose files</button>
        <input
          ref={inputRef}
          data-testid="file-input"
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp"
          hidden
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {rejected.length > 0 && (
        <div className="card error" role="alert">
          <strong>Some files were skipped:</strong>
          <ul>
            {rejected.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {items.length > 0 && (
        <>
          <div className="row row--between">
            <span aria-live="polite" data-testid="upload-summary">
              {summary.ready} ready · {summary.duplicate} duplicates · {summary.invalid} invalid ·{" "}
              {summary.failed} failed · {summary.pending} remaining
            </span>
            <div className="row">
              <button onClick={() => void start()} disabled={running || summary.pending === 0}>
                {summary.failed > 0 && !running ? "Retry failed & upload rest" : "Upload"}
              </button>
              <button
                className="secondary"
                disabled={running}
                onClick={() => setItems((prev) => prev.filter((i) => i.status !== "ready" && i.status !== "duplicate"))}
              >
                Clear finished
              </button>
            </div>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>File</th>
                <th>Size</th>
                <th>Status</th>
                <th>Progress</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.key} data-status={i.status}>
                  <td>{i.file.name}</td>
                  <td>{(i.file.size / 1024).toFixed(0)} KB</td>
                  <td>
                    {STATUS_TEXT[i.status]}
                    {i.message && <div className="muted small">{i.message}</div>}
                  </td>
                  <td>
                    <progress max={1} value={i.progress} aria-label={`${i.file.name} progress`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
};
