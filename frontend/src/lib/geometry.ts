import type { Box, Rect } from "../api/types";

export const MIN_SIZE = 0.01;
export const DUPLICATE_IOU = 0.9;

export type Point = { x: number; y: number };
export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

export const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

export const rectFromPoints = (a: Point, b: Point): Rect => {
  const x1 = clamp01(Math.min(a.x, b.x));
  const y1 = clamp01(Math.min(a.y, b.y));
  const x2 = clamp01(Math.max(a.x, b.x));
  const y2 = clamp01(Math.max(a.y, b.y));
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
};

export const isLargeEnough = (r: Rect): boolean => r.w >= MIN_SIZE && r.h >= MIN_SIZE;

/** Moves a rect, keeping it fully inside the image. */
export const moveRect = (r: Rect, dx: number, dy: number): Rect => ({
  x: Math.min(1 - r.w, Math.max(0, r.x + dx)),
  y: Math.min(1 - r.h, Math.max(0, r.y + dy)),
  w: r.w,
  h: r.h,
});

/** Drags one handle of a rect by (dx, dy); the opposite edge stays fixed. */
export const resizeRect = (r: Rect, handle: Handle, dx: number, dy: number): Rect => {
  let left = r.x;
  let right = r.x + r.w;
  let top = r.y;
  let bottom = r.y + r.h;
  if (handle.includes("w")) left = clamp01(left + dx);
  if (handle.includes("e")) right = clamp01(right + dx);
  if (handle.includes("n")) top = clamp01(top + dy);
  if (handle.includes("s")) bottom = clamp01(bottom + dy);

  // Allow dragging past the opposite edge (flip), then enforce a minimum size.
  let x1 = Math.min(left, right);
  let x2 = Math.max(left, right);
  let y1 = Math.min(top, bottom);
  let y2 = Math.max(top, bottom);
  if (x2 - x1 < MIN_SIZE) {
    if (handle.includes("w")) x1 = Math.max(0, x2 - MIN_SIZE);
    else x2 = Math.min(1, x1 + MIN_SIZE);
    if (x2 - x1 < MIN_SIZE) x1 = x2 - MIN_SIZE;
  }
  if (y2 - y1 < MIN_SIZE) {
    if (handle.includes("n")) y1 = Math.max(0, y2 - MIN_SIZE);
    else y2 = Math.min(1, y1 + MIN_SIZE);
    if (y2 - y1 < MIN_SIZE) y1 = y2 - MIN_SIZE;
  }
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
};

export const iou = (a: Rect, b: Rect): number => {
  const ix = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const iy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  const inter = ix * iy;
  const union = a.w * a.h + b.w * b.h - inter;
  return union > 0 ? inter / union : 0;
};

export const normalizeLabel = (name: string): string => name.trim().split(/\s+/).join(" ").toLowerCase();

export const cleanLabel = (name: string): string => name.trim().split(/\s+/).join(" ");

/** Pairs of same-label boxes that overlap enough for the server to call them duplicates. */
export const findDuplicatePairs = (boxes: Box[], threshold = DUPLICATE_IOU): [string, string][] => {
  const pairs: [string, string][] = [];
  boxes.forEach((a, i) => {
    boxes.slice(i + 1).forEach((b) => {
      if (normalizeLabel(a.label) === normalizeLabel(b.label) && iou(a, b) > threshold) {
        pairs.push([a.id, b.id]);
      }
    });
  });
  return pairs;
};

export const containsPoint = (r: Rect, p: Point): boolean =>
  p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h;
