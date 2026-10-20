import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { consumeReturnPath, peekReturnPath, rememberReturnPath } from "../auth/returnTo";
import { useCanvasStore } from "../store/canvasStore";
import { stashCanvasDraft, stashDraft, takeDraft } from "./draftStash";

const box = { id: "b", label: "car", x: 0, y: 0, w: 0.5, h: 0.5 };

beforeEach(() => {
  sessionStorage.clear();
  useCanvasStore.getState().load([]);
});
afterEach(() => vi.useRealTimers());

describe("draft stash", () => {
  it("returns stashed boxes once, only for the same image", () => {
    stashDraft("img-1", [box]);
    expect(takeDraft("img-2")).toBeNull();
    expect(takeDraft("img-1")).toEqual([box]);
    expect(takeDraft("img-1")).toBeNull();
  });

  it("drops stale stashes", () => {
    vi.useFakeTimers();
    stashDraft("img-1", [box]);
    vi.setSystemTime(Date.now() + 61 * 60 * 1000);
    expect(takeDraft("img-1")).toBeNull();
  });

  it("only stashes the canvas when there are unsaved edits", () => {
    useCanvasStore.getState().load([box], "img-1");
    stashCanvasDraft();
    expect(takeDraft("img-1")).toBeNull(); // clean

    useCanvasStore.getState().remove("b");
    stashCanvasDraft();
    expect(takeDraft("img-1")).toEqual([]);
  });

  it("restore marks boxes dirty so autosave persists them", () => {
    useCanvasStore.getState().load([], "img-1");
    useCanvasStore.getState().restore([box]);
    const s = useCanvasStore.getState();
    expect(s.boxes).toEqual([box]);
    expect(s.revision).toBeGreaterThan(s.savedRevision);
  });
});

describe("return path", () => {
  it("round-trips once and ignores auth pages", () => {
    rememberReturnPath("/batches/1/label?x=1");
    expect(peekReturnPath()).toBe("/batches/1/label?x=1");
    expect(consumeReturnPath()).toBe("/batches/1/label?x=1");
    expect(consumeReturnPath()).toBe("/");

    rememberReturnPath("/login");
    rememberReturnPath("/auth/callback?code=1");
    expect(consumeReturnPath()).toBe("/");
  });

  it("refuses off-site targets", () => {
    sessionStorage.setItem("labeler.returnTo", JSON.stringify({ path: "//evil.example", at: Date.now() }));
    expect(consumeReturnPath()).toBe("/");
    sessionStorage.setItem("labeler.returnTo", JSON.stringify({ path: "https://evil.example", at: Date.now() }));
    expect(consumeReturnPath()).toBe("/");
  });
});
