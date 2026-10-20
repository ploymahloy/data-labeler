import { useState } from "react";

type Props = {
  busy?: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
};

/** Rejecting work always requires a reason the annotator will see. */
export const RejectDialog = ({ busy = false, onConfirm, onCancel }: Props) => {
  const [reason, setReason] = useState("");
  const trimmed = reason.trim();

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Reject image">
      <form
        className="modal__body"
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed) onConfirm(trimmed);
        }}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Escape") onCancel();
        }}
      >
        <h3>Reject this image</h3>
        <p className="muted">Tell the annotator what to fix. They will see this message.</p>
        <textarea
          aria-label="Reason for rejection"
          autoFocus
          rows={4}
          maxLength={2000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. The truck on the right is missing a box"
        />
        <div className="row">
          <button type="submit" className="danger" disabled={!trimmed || busy}>
            Reject
          </button>
          <button type="button" className="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
};
