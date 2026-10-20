import { describe, expect, it } from "vitest";
import type { Box } from "../api/types";
import {
  MIN_SIZE,
  cleanLabel,
  findDuplicatePairs,
  iou,
  isLargeEnough,
  moveRect,
  normalizeLabel,
  rectFromPoints,
  resizeRect,
} from "./geometry";

const box = (id: string, label: string, x: number, y: number, w: number, h: number): Box => ({
  id,
  label,
  x,
  y,
  w,
  h,
});

describe("rectFromPoints", () => {
  it("normalizes drag direction", () => {
    expect(rectFromPoints({ x: 0.6, y: 0.7 }, { x: 0.2, y: 0.1 })).toEqual({
      x: 0.2,
      y: 0.1,
      w: expect.closeTo(0.4, 10),
      h: expect.closeTo(0.6, 10),
    });
  });

  it("clamps to the image", () => {
    const r = rectFromPoints({ x: -0.5, y: 0.5 }, { x: 1.5, y: 2 });
    expect(r).toEqual({ x: 0, y: 0.5, w: 1, h: 0.5 });
  });
});

describe("moveRect", () => {
  it("cannot leave the image", () => {
    const r = { x: 0.5, y: 0.5, w: 0.4, h: 0.4 };
    expect(moveRect(r, 0.5, 0.5)).toEqual({ x: 0.6, y: 0.6, w: 0.4, h: 0.4 });
    expect(moveRect(r, -2, -2)).toEqual({ x: 0, y: 0, w: 0.4, h: 0.4 });
  });
});

describe("resizeRect", () => {
  const r = { x: 0.2, y: 0.2, w: 0.4, h: 0.4 };

  it("moves only the dragged edges", () => {
    const out = resizeRect(r, "se", 0.1, 0.2);
    expect(out.x).toBeCloseTo(0.2);
    expect(out.y).toBeCloseTo(0.2);
    expect(out.w).toBeCloseTo(0.5);
    expect(out.h).toBeCloseTo(0.6);
  });

  it("keeps the opposite corner fixed when dragging nw", () => {
    const out = resizeRect(r, "nw", -0.1, -0.1);
    expect(out.x).toBeCloseTo(0.1);
    expect(out.y).toBeCloseTo(0.1);
    expect(out.x + out.w).toBeCloseTo(0.6);
    expect(out.y + out.h).toBeCloseTo(0.6);
  });

  it("supports flipping past the opposite edge", () => {
    const out = resizeRect(r, "e", -0.6, 0);
    expect(out.x).toBeCloseTo(0.0);
    expect(out.w).toBeCloseTo(0.2);
  });

  it("enforces the minimum size", () => {
    const out = resizeRect(r, "e", -0.4, 0);
    expect(out.w).toBeGreaterThanOrEqual(MIN_SIZE - 1e-12);
    expect(isLargeEnough(out)).toBe(true);
  });

  it("never leaves the image", () => {
    const out = resizeRect(r, "se", 5, 5);
    expect(out.x + out.w).toBeLessThanOrEqual(1 + 1e-12);
    expect(out.y + out.h).toBeLessThanOrEqual(1 + 1e-12);
  });
});

describe("iou and duplicate detection", () => {
  it("is 1 for identical boxes and 0 for disjoint ones", () => {
    const a = { x: 0.1, y: 0.1, w: 0.3, h: 0.3 };
    expect(iou(a, a)).toBeCloseTo(1);
    expect(iou(a, { x: 0.6, y: 0.6, w: 0.2, h: 0.2 })).toBe(0);
  });

  it("flags near-identical boxes with the same normalized label", () => {
    const boxes = [
      box("a", "Car", 0.1, 0.1, 0.4, 0.4),
      box("b", " car ", 0.11, 0.1, 0.4, 0.4),
      box("c", "dog", 0.1, 0.1, 0.4, 0.4),
      box("d", "car", 0.6, 0.6, 0.2, 0.2),
    ];
    expect(findDuplicatePairs(boxes)).toEqual([["a", "b"]]);
  });
});

describe("label normalization", () => {
  it("collapses whitespace and case for comparison but keeps display casing", () => {
    expect(normalizeLabel("  Traffic   Light ")).toBe("traffic light");
    expect(cleanLabel("  Traffic   Light ")).toBe("Traffic Light");
  });
});
