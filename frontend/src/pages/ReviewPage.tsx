import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, isLeaseLost } from "../api/client";
import { BoxCanvas, labelColor } from "../components/BoxCanvas";
import { RejectDialog } from "../components/RejectDialog";
import { useLeaseHeartbeat } from "../hooks/useLeaseHeartbeat";
import { useWorkQueue } from "../hooks/useWorkQueue";

export const ReviewPage = () => {
  const { batchId = "" } = useParams();
  const [rejecting, setRejecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [counts, setCounts] = useState({ accepted: 0, rejected: 0 });

  const fetchNext = useCallback(() => api.nextForReview(batchId), [batchId]);
  const onLoaded = useCallback(() => {
    setNotice(null);
    setRejecting(false);
    setSelectedId(null);
  }, []);
  const { image, phase, error, claim, markLost, handedOff } = useWorkQueue(fetchNext, onLoaded);

  useLeaseHeartbeat(phase === "ready" ? (image?.id ?? null) : null, api.heartbeat, markLost);

  const decide = useCallback(
    async (decision: "accepted" | "rejected", reason?: string) => {
      if (!image || busy) return;
      setBusy(true);
      try {
        await api.review(image.id, decision, reason);
        handedOff();
        const key = decision === "accepted" ? "accepted" : "rejected";
        setCounts((c) => ({ ...c, [key]: c[key] + 1 }));
        setRejecting(false);
        await claim();
      } catch (e) {
        setRejecting(false);
        if (isLeaseLost(e)) markLost();
        else setNotice(e instanceof Error ? e.message : "Could not save the decision");
      } finally {
        setBusy(false);
      }
    },
    [image, busy, handedOff, claim, markLost],
  );

  // Shortcuts: A accept, R reject.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey || rejecting || phase !== "ready") return;
      if (e.key === "a" || e.key === "A") void decide("accepted");
      else if (e.key === "r" || e.key === "R") setRejecting(true);
      else if (e.key === "n" || e.key === "N") void claim();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide, claim, rejecting, phase]);

  const boxes = image?.annotations ?? [];

  return (
    <div className="workspace">
      <div className="workspace__main">
        <div className="row row--between">
          <Link to={`/batches/${batchId}`}>&larr; Batch</Link>
          <span className="muted" aria-live="polite">
            {counts.accepted} accepted · {counts.rejected} rejected this session
          </span>
        </div>

        {phase === "loading" && <p>Loading the next image to review…</p>}
        {phase === "empty" && (
          <div className="card" role="status">
            <h2>Nothing to review</h2>
            <p>
              No submitted images are waiting for you. Images you annotated yourself are never offered to
              you for review.
            </p>
            <button onClick={() => void claim()}>Check again</button>
          </div>
        )}
        {phase === "error" && (
          <div className="card error" role="alert">
            <p>{error}</p>
            <button onClick={() => void claim()}>Try again</button>
          </div>
        )}
        {phase === "lost" && (
          <div className="card warning" role="alert">
            <h2>This review is no longer yours</h2>
            <p>Your lease expired, so the image went back to the queue. Your decision was not recorded.</p>
            <button onClick={() => void claim()}>Get next image</button>
          </div>
        )}

        {phase === "ready" && image && (
          <>
            {notice && (
              <div className="card error" role="alert">
                {notice}
              </div>
            )}
            {image.review_feedback && (
              <div className="card" data-testid="previous-feedback">
                <strong>Resubmission.</strong> Previously rejected: <em>{image.review_feedback.reason}</em>
              </div>
            )}
            <BoxCanvas
              imageUrl={image.url}
              alt={image.filename}
              boxes={boxes}
              selectedId={selectedId}
              onSelect={setSelectedId}
              readOnly
            />
          </>
        )}
      </div>

      <aside className="workspace__side">
        <h3>{image?.filename ?? "Review"}</h3>
        {image && (
          <p className="muted">
            Submission #{image.last_submission_no} · {boxes.length} box{boxes.length === 1 ? "" : "es"}
          </p>
        )}
        <ul className="box-list" aria-label="Boxes">
          {boxes.map((b) => (
            <li key={b.id} className={b.id === selectedId ? "selected" : ""}>
              <button className="link" onClick={() => setSelectedId(b.id)}>
                <span className="swatch" style={{ background: labelColor(b.label) }} />
                {b.label}
              </button>
            </li>
          ))}
          {phase === "ready" && boxes.length === 0 && <li className="muted">The annotator marked no objects</li>}
        </ul>
        <div className="row">
          <button disabled={phase !== "ready" || busy} onClick={() => void decide("accepted")}>
            Accept (A)
          </button>
          <button className="danger" disabled={phase !== "ready" || busy} onClick={() => setRejecting(true)}>
            Reject (R)
          </button>
        </div>
        <p className="muted small">
          You review the annotator&apos;s work as-is. To request changes, reject with a reason.
        </p>
      </aside>

      {rejecting && (
        <RejectDialog busy={busy} onCancel={() => setRejecting(false)} onConfirm={(r) => void decide("rejected", r)} />
      )}
    </div>
  );
};
