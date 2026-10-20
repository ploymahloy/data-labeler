import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, isApiError, isLeaseLost } from "../api/client";
import type { ImageDetail, Rect } from "../api/types";
import { BoxCanvas, labelColor } from "../components/BoxCanvas";
import { LabelPrompt } from "../components/LabelPrompt";
import { AUTOSAVE_DELAY_MS } from "../config";
import { useLeaseHeartbeat } from "../hooks/useLeaseHeartbeat";
import { useWorkQueue } from "../hooks/useWorkQueue";
import { takeDraft } from "../lib/draftStash";
import { findDuplicatePairs, normalizeLabel } from "../lib/geometry";
import { isDirty, useCanvasStore } from "../store/canvasStore";

type SaveState = "idle" | "saving" | "saved" | "error";

const DUPLICATE_MESSAGE =
  "Two boxes with the same name overlap almost completely. Remove or rename one of the highlighted boxes.";

export const LabelPage = () => {
  const { batchId = "" } = useParams();
  const queryClient = useQueryClient();

  const boxes = useCanvasStore((s) => s.boxes);
  const selectedId = useCanvasStore((s) => s.selectedId);
  const invalidIds = useCanvasStore((s) => s.invalidIds);
  const dirty = useCanvasStore(isDirty);
  const revision = useCanvasStore((s) => s.revision);
  const { load, restore, add, update, rename, remove, select, setInvalid, markSaved } = useCanvasStore.getState();

  const [draft, setDraft] = useState<Rect | null>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [doneCount, setDoneCount] = useState(0);

  const fetchNext = useCallback(() => api.nextImage(batchId), [batchId]);
  const onLoaded = useCallback(
    (img: ImageDetail) => {
      load(img.annotations, img.id);
      setDraft(null);
      setRenameId(null);
      setNotice(null);
      setSaveState("idle");
      // Unsaved edits stashed when the session expired. Claiming returns this same image only
      // while our lease is still live, so a different image simply never matches the stash.
      const stashed = takeDraft(img.id);
      if (stashed) {
        restore(stashed);
        setNotice("Restored your unsaved changes from before you were signed out.");
      }
    },
    [load, restore],
  );
  const { image, phase, error, claim, markLost, handedOff } = useWorkQueue(fetchNext, onLoaded);

  const labels = useQuery({
    queryKey: ["labels", batchId],
    queryFn: () => api.listLabels(batchId),
  });
  const suggestions = useMemo(() => {
    const seen = new Map<string, string>();
    [...(labels.data?.map((l) => l.name) ?? []), ...boxes.map((b) => b.label)].forEach((name) => {
      seen.set(normalizeLabel(name), seen.get(normalizeLabel(name)) ?? name);
    });
    return [...seen.values()].sort((a, b) => a.localeCompare(b));
  }, [labels.data, boxes]);

  useLeaseHeartbeat(phase === "ready" ? (image?.id ?? null) : null, api.heartbeat, markLost);

  const handleServerError = useCallback(
    (e: unknown) => {
      if (isLeaseLost(e)) {
        markLost();
        return;
      }
      if (isApiError(e) && e.code === "duplicate_annotations") {
        const pairs = (e.detail.pairs as string[][] | undefined) ?? [];
        setInvalid(pairs.flat());
        setNotice(DUPLICATE_MESSAGE);
        return;
      }
      setNotice(e instanceof Error ? e.message : "Something went wrong");
    },
    [markLost, setInvalid],
  );

  // Debounced draft autosave. Retries are idempotent (client-generated box ids).
  useEffect(() => {
    if (!image || phase !== "ready" || !dirty) return undefined;
    const timer = window.setTimeout(async () => {
      const { boxes: current, revision: rev } = useCanvasStore.getState();
      setSaveState("saving");
      try {
        await api.saveDraft(image.id, current);
        markSaved(rev);
        setSaveState("saved");
      } catch (e) {
        setSaveState("error");
        handleServerError(e);
      }
    }, AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [revision, dirty, image, phase, markSaved, handleServerError]);

  const submit = useCallback(async () => {
    if (!image || busy || phase !== "ready") return;
    const current = useCanvasStore.getState().boxes;
    const dupes = findDuplicatePairs(current);
    if (dupes.length > 0) {
      setInvalid(dupes.flat());
      setNotice(DUPLICATE_MESSAGE);
      return;
    }
    if (current.length === 0 && !window.confirm("Submit this image with no labeled objects?")) return;
    setBusy(true);
    try {
      await api.submit(image.id, current);
      handedOff();
      setDoneCount((n) => n + 1);
      void queryClient.invalidateQueries({ queryKey: ["labels", batchId] });
      await claim();
    } catch (e) {
      handleServerError(e);
    } finally {
      setBusy(false);
    }
  }, [image, busy, phase, setInvalid, handedOff, queryClient, batchId, claim, handleServerError]);

  const skip = useCallback(async () => {
    if (!image || busy || phase !== "ready") return;
    setBusy(true);
    try {
      await api.skip(image.id);
      handedOff();
      await claim();
    } catch (e) {
      handleServerError(e);
    } finally {
      setBusy(false);
    }
  }, [image, busy, phase, handedOff, claim, handleServerError]);

  // Keyboard shortcuts: N next, S skip, Enter submit, Del remove, Esc cancel/deselect.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Enter") {
        e.preventDefault();
        void submit();
      } else if (e.key === "s" || e.key === "S") {
        void skip();
      } else if (e.key === "n" || e.key === "N") {
        void claim();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        const id = useCanvasStore.getState().selectedId;
        if (id) remove(id);
      } else if (e.key === "Escape") {
        setDraft(null);
        setRenameId(null);
        select(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submit, skip, claim, remove, select]);

  const renaming = renameId ? boxes.find((b) => b.id === renameId) : undefined;

  return (
    <div className="workspace">
      <div className="workspace__main">
        <div className="row row--between">
          <Link to={`/batches/${batchId}`}>&larr; Batch</Link>
          <span className="muted" aria-live="polite">
            {doneCount} submitted this session
            {saveState === "saving" && " · saving…"}
            {saveState === "saved" && " · draft saved"}
            {saveState === "error" && " · draft not saved"}
          </span>
        </div>

        {phase === "loading" && <p>Loading the next image…</p>}
        {phase === "empty" && (
          <div className="card" role="status">
            <h2>All caught up</h2>
            <p>There are no more images waiting to be labeled in this batch.</p>
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
            <h2>This image is no longer yours</h2>
            <p>
              Your lease expired or the image was reassigned, so your latest changes were not
              submitted. Press N or use the button to get the next image.
            </p>
            <button onClick={() => void claim()}>Get next image</button>
          </div>
        )}

        {phase === "ready" && image && (
          <>
            {image.review_feedback && (
              <div className="card warning" role="alert" data-testid="rework-banner">
                <strong>Needs rework.</strong> A reviewer rejected the previous submission:
                <blockquote>{image.review_feedback.reason}</blockquote>
              </div>
            )}
            {notice && (
              <div className="card error" role="alert">
                {notice}
              </div>
            )}
            <BoxCanvas
              imageUrl={image.url}
              alt={image.filename}
              boxes={boxes}
              selectedId={selectedId}
              invalidIds={invalidIds}
              draft={draft}
              onSelect={select}
              onCreate={(rect) => {
                setRenameId(null);
                setDraft(rect);
              }}
              onUpdate={update}
              onRename={(id) => {
                setDraft(null);
                setRenameId(id);
              }}
            >
              {draft && (
                <LabelPrompt
                  anchor={draft}
                  suggestions={suggestions}
                  onCancel={() => setDraft(null)}
                  onConfirm={(label) => {
                    add({ id: crypto.randomUUID(), label, ...draft });
                    setDraft(null);
                  }}
                />
              )}
              {renaming && (
                <LabelPrompt
                  anchor={renaming}
                  initial={renaming.label}
                  suggestions={suggestions}
                  onCancel={() => setRenameId(null)}
                  onConfirm={(label) => {
                    rename(renaming.id, label);
                    setRenameId(null);
                  }}
                />
              )}
            </BoxCanvas>
          </>
        )}
      </div>

      <aside className="workspace__side">
        <h3>{image?.filename ?? "Labeling"}</h3>
        <p className="muted">Drag on the image to draw a box, then name it. Double-click a box (or use ✎) to rename it.</p>
        <ul className="box-list" aria-label="Boxes">
          {boxes.map((b) => (
            <li key={b.id} className={b.id === selectedId ? "selected" : ""}>
              <button className="link" onClick={() => select(b.id)}>
                <span className="swatch" style={{ background: labelColor(b.label) }} />
                {b.label}
              </button>
              <button
                className="icon"
                aria-label={`Rename ${b.label} box`}
                onClick={() => {
                  select(b.id);
                  setDraft(null);
                  setRenameId(b.id);
                }}
              >
                ✎
              </button>
              <button
                className="icon"
                aria-label={`Delete ${b.label} box`}
                onClick={() => remove(b.id)}
              >
                ×
              </button>
            </li>
          ))}
          {boxes.length === 0 && <li className="muted">No boxes yet</li>}
        </ul>
        <div className="row">
          <button disabled={phase !== "ready" || busy} onClick={() => void submit()}>
            Submit (Enter)
          </button>
          <button className="secondary" disabled={phase !== "ready" || busy} onClick={() => void skip()}>
            Skip (S)
          </button>
        </div>
        <p className="muted small">
          Shortcuts: <kbd>Enter</kbd> submit · <kbd>S</kbd> skip · <kbd>N</kbd> next · <kbd>Del</kbd> remove ·
          double-click a box to rename
        </p>
      </aside>
    </div>
  );
};
