import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { Rect } from "../api/types";
import { cleanLabel } from "../lib/geometry";

type Props = {
  anchor: Rect;
  initial?: string;
  suggestions: string[];
  onConfirm: (label: string) => void;
  onCancel: () => void;
};

/** Floating "name this entity" input with autocomplete from labels already used in the batch. */
export const LabelPrompt = ({ anchor, initial = "", suggestions, onConfirm, onCancel }: Props) => {
  const [value, setValue] = useState(initial);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const confirm = () => {
    const label = cleanLabel(value);
    if (label) onConfirm(label);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation(); // page-level shortcuts must not fire while typing
    if (e.key === "Enter") {
      e.preventDefault();
      confirm();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };

  const top = Math.min(anchor.y + anchor.h, 0.9);
  const left = Math.min(anchor.x, 0.7);

  return (
    <form
      className="label-prompt"
      style={{ left: `${left * 100}%`, top: `${top * 100}%` }}
      onPointerDown={(e) => e.stopPropagation()}
      onSubmit={(e) => {
        e.preventDefault();
        confirm();
      }}
    >
      <input
        ref={inputRef}
        aria-label="Entity name"
        placeholder="Name this entity"
        list={listId}
        value={value}
        maxLength={100}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <datalist id={listId}>
        {suggestions.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
      <button type="submit" disabled={!cleanLabel(value)}>
        Add
      </button>
      <button type="button" className="secondary" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
};
