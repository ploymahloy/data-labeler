import { create } from "zustand";
import type { Box, Rect } from "../api/types";

export type CanvasState = {
  boxes: Box[];
  selectedId: string | null;
  /** Box ids the server rejected as duplicates; highlighted in the UI. */
  invalidIds: string[];
  /** Incremented on every edit so async saves can tell whether newer edits happened meanwhile. */
  revision: number;
  savedRevision: number;
  load: (boxes: Box[]) => void;
  add: (box: Box) => void;
  update: (id: string, rect: Rect) => void;
  rename: (id: string, label: string) => void;
  remove: (id: string) => void;
  select: (id: string | null) => void;
  setInvalid: (ids: string[]) => void;
  markSaved: (revision: number) => void;
};

export const useCanvasStore = create<CanvasState>()((set) => ({
  boxes: [],
  selectedId: null,
  invalidIds: [],
  revision: 0,
  savedRevision: 0,

  load: (boxes) => set({ boxes, selectedId: null, invalidIds: [], revision: 0, savedRevision: 0 }),

  add: (box) =>
    set((s) => ({ boxes: [...s.boxes, box], selectedId: box.id, revision: s.revision + 1, invalidIds: [] })),

  update: (id, rect) =>
    set((s) => ({
      boxes: s.boxes.map((b) => (b.id === id ? { ...b, ...rect } : b)),
      revision: s.revision + 1,
      invalidIds: [],
    })),

  rename: (id, label) =>
    set((s) => ({
      boxes: s.boxes.map((b) => (b.id === id ? { ...b, label } : b)),
      revision: s.revision + 1,
      invalidIds: [],
    })),

  remove: (id) =>
    set((s) => ({
      boxes: s.boxes.filter((b) => b.id !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
      revision: s.revision + 1,
      invalidIds: [],
    })),

  select: (id) => set({ selectedId: id }),

  setInvalid: (ids) => set({ invalidIds: ids }),

  markSaved: (revision) => set((s) => ({ savedRevision: Math.max(s.savedRevision, revision) })),
}));

export const isDirty = (s: Pick<CanvasState, "revision" | "savedRevision">): boolean =>
  s.revision > s.savedRevision;
