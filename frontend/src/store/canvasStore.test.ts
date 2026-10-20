import { beforeEach, describe, expect, it } from "vitest";
import { isDirty, useCanvasStore } from "./canvasStore";

const box = (id: string) => ({ id, label: "car", x: 0.1, y: 0.1, w: 0.2, h: 0.2 });

describe("canvas store", () => {
  beforeEach(() => useCanvasStore.getState().load([]));

  it("tracks dirtiness by revision so concurrent edits are not marked saved", () => {
    const s = useCanvasStore.getState();
    s.add(box("a"));
    const savedAt = useCanvasStore.getState().revision;
    s.rename("a", "truck"); // edited while a save for `savedAt` was in flight
    s.markSaved(savedAt);
    expect(isDirty(useCanvasStore.getState())).toBe(true);
    s.markSaved(useCanvasStore.getState().revision);
    expect(isDirty(useCanvasStore.getState())).toBe(false);
  });

  it("selects new boxes and clears selection when the box is removed", () => {
    const s = useCanvasStore.getState();
    s.add(box("a"));
    expect(useCanvasStore.getState().selectedId).toBe("a");
    s.remove("a");
    expect(useCanvasStore.getState().selectedId).toBeNull();
    expect(useCanvasStore.getState().boxes).toEqual([]);
  });

  it("loading server boxes starts clean", () => {
    useCanvasStore.getState().load([box("a"), box("b")]);
    expect(isDirty(useCanvasStore.getState())).toBe(false);
    expect(useCanvasStore.getState().boxes).toHaveLength(2);
  });

  it("editing clears server duplicate markers", () => {
    const s = useCanvasStore.getState();
    s.load([box("a"), box("b")]);
    s.setInvalid(["a", "b"]);
    s.remove("b");
    expect(useCanvasStore.getState().invalidIds).toEqual([]);
  });
});
