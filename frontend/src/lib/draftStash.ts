import type { Box } from "../api/types";
import { isDirty, useCanvasStore } from "../store/canvasStore";

const KEY = "labeler.draftStash";
const MAX_AGE_MS = 60 * 60 * 1000;

type Stash = { imageId: string; boxes: Box[]; at: number };

export const stashDraft = (imageId: string, boxes: Box[]): void => {
  const stash: Stash = { imageId, boxes, at: Date.now() };
  sessionStorage.setItem(KEY, JSON.stringify(stash));
};

/** Unsaved boxes for `imageId`, if any were stashed before a forced sign-out. Single use. */
export const takeDraft = (imageId: string): Box[] | null => {
  const raw = sessionStorage.getItem(KEY);
  if (!raw) return null;
  try {
    const stash = JSON.parse(raw) as Stash;
    if (Date.now() - stash.at > MAX_AGE_MS) {
      sessionStorage.removeItem(KEY);
      return null;
    }
    if (stash.imageId !== imageId) return null; // keep it: the user may get that image back
    sessionStorage.removeItem(KEY);
    return stash.boxes;
  } catch {
    sessionStorage.removeItem(KEY);
    return null;
  }
};

/** Called right before a forced sign-out so edits not yet autosaved survive re-login. */
export const stashCanvasDraft = (): void => {
  const state = useCanvasStore.getState();
  if (state.imageId && isDirty(state)) stashDraft(state.imageId, state.boxes);
};
