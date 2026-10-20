import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Box, Rect } from "../api/types";
import { BoxCanvas } from "./BoxCanvas";

// jsdom has no layout; give the canvas a 1000x500 box at the origin so pointer math is real.
const original = Element.prototype.getBoundingClientRect;
beforeEach(() => {
  Element.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500, x: 0, y: 0 }) as DOMRect;
});
afterEach(() => {
  Element.prototype.getBoundingClientRect = original;
});

const pointer = (el: Element, type: string, x: number, y: number, pointerId = 1) =>
  fireEvent(
    el,
    Object.assign(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 }), {
      pointerId,
    }),
  );

const car: Box = { id: "car-1", label: "car", x: 0.2, y: 0.2, w: 0.4, h: 0.4 };

describe("BoxCanvas", () => {
  it("creates a normalized box when the user drags on the image", () => {
    const created: Rect[] = [];
    render(
      <BoxCanvas imageUrl="x.png" boxes={[]} selectedId={null} onCreate={(r) => created.push(r)} />,
    );
    const canvas = screen.getByTestId("canvas");
    pointer(canvas, "pointerdown", 100, 50);
    pointer(canvas, "pointermove", 600, 300);
    pointer(canvas, "pointerup", 600, 300);
    expect(created).toHaveLength(1);
    expect(created[0]?.x).toBeCloseTo(0.1);
    expect(created[0]?.y).toBeCloseTo(0.1);
    expect(created[0]?.w).toBeCloseTo(0.5);
    expect(created[0]?.h).toBeCloseTo(0.5);
  });

  it("ignores accidental clicks and tiny drags", () => {
    const created: Rect[] = [];
    render(<BoxCanvas imageUrl="x.png" boxes={[]} selectedId={null} onCreate={(r) => created.push(r)} />);
    const canvas = screen.getByTestId("canvas");
    pointer(canvas, "pointerdown", 100, 100);
    pointer(canvas, "pointerup", 102, 101);
    expect(created).toEqual([]);
  });

  it("moves a box by dragging it and keeps it inside the image", () => {
    const updates: Rect[] = [];
    render(
      <BoxCanvas
        imageUrl="x.png"
        boxes={[car]}
        selectedId="car-1"
        onUpdate={(_, r) => updates.push(r)}
        onSelect={() => undefined}
      />,
    );
    const box = screen.getByTestId("box-car-1");
    pointer(box, "pointerdown", 400, 150);
    pointer(screen.getByTestId("canvas"), "pointermove", 500, 200);
    pointer(screen.getByTestId("canvas"), "pointerup", 500, 200);
    const last = updates.at(-1);
    expect(last?.x).toBeCloseTo(0.3);
    expect(last?.y).toBeCloseTo(0.3);
    expect(last?.w).toBeCloseTo(0.4);

    pointer(box, "pointerdown", 400, 150);
    pointer(screen.getByTestId("canvas"), "pointermove", 5000, 5000);
    const clamped = updates.at(-1);
    expect((clamped?.x ?? 0) + (clamped?.w ?? 0)).toBeLessThanOrEqual(1 + 1e-9);
    expect((clamped?.y ?? 0) + (clamped?.h ?? 0)).toBeLessThanOrEqual(1 + 1e-9);
  });

  it("resizes from a corner handle", () => {
    const updates: Rect[] = [];
    render(
      <BoxCanvas
        imageUrl="x.png"
        boxes={[car]}
        selectedId="car-1"
        onUpdate={(_, r) => updates.push(r)}
      />,
    );
    pointer(screen.getByTestId("handle-se"), "pointerdown", 600, 300);
    pointer(screen.getByTestId("canvas"), "pointermove", 700, 350);
    const last = updates.at(-1);
    expect(last?.x).toBeCloseTo(0.2);
    expect(last?.w).toBeCloseTo(0.5);
    expect(last?.h).toBeCloseTo(0.5);
  });

  it("is inert when read-only (review mode): no drawing, no handles", () => {
    const created: Rect[] = [];
    const updates: Rect[] = [];
    render(
      <BoxCanvas
        imageUrl="x.png"
        boxes={[car]}
        selectedId="car-1"
        readOnly
        onCreate={(r) => created.push(r)}
        onUpdate={(_, r) => updates.push(r)}
      />,
    );
    expect(screen.queryByTestId("handle-se")).toBeNull();
    const canvas = screen.getByTestId("canvas");
    pointer(canvas, "pointerdown", 100, 50);
    pointer(canvas, "pointermove", 600, 300);
    pointer(canvas, "pointerup", 600, 300);
    pointer(screen.getByTestId("box-car-1"), "pointerdown", 400, 150);
    pointer(canvas, "pointermove", 500, 250);
    expect(created).toEqual([]);
    expect(updates).toEqual([]);
    expect(screen.getByText("car")).toBeInTheDocument();
  });

  it("marks server-flagged duplicates", () => {
    render(<BoxCanvas imageUrl="x.png" boxes={[car]} selectedId={null} invalidIds={["car-1"]} />);
    expect(screen.getByTestId("box-car-1")).toHaveClass("box--invalid");
  });

  describe("touch", () => {
    it("ignores a second finger while one is drawing", () => {
      const created: Rect[] = [];
      render(<BoxCanvas imageUrl="x.png" boxes={[]} selectedId={null} onCreate={(r) => created.push(r)} />);
      const canvas = screen.getByTestId("canvas");
      pointer(canvas, "pointerdown", 100, 50, 1);
      pointer(canvas, "pointerdown", 900, 450, 2); // second finger lands: must not restart the drag
      pointer(canvas, "pointermove", 900, 450, 2); // ...and its moves are not ours
      pointer(canvas, "pointerup", 900, 450, 2);
      expect(created).toEqual([]); // the second finger lifting did not finish the first drag
      pointer(canvas, "pointermove", 600, 300, 1);
      pointer(canvas, "pointerup", 600, 300, 1);
      expect(created).toHaveLength(1);
      expect(created[0]?.x).toBeCloseTo(0.1);
      expect(created[0]?.w).toBeCloseTo(0.5);
    });

    it("discards a drawing when the browser cancels the gesture", () => {
      const created: Rect[] = [];
      render(<BoxCanvas imageUrl="x.png" boxes={[]} selectedId={null} onCreate={(r) => created.push(r)} />);
      const canvas = screen.getByTestId("canvas");
      pointer(canvas, "pointerdown", 100, 50);
      pointer(canvas, "pointermove", 600, 300);
      expect(screen.getByTestId("draft")).toBeInTheDocument();
      pointer(canvas, "pointercancel", 600, 300);
      expect(created).toEqual([]);
      expect(screen.queryByTestId("draft")).toBeNull();
    });

    it("puts a box back where it was when a move is cancelled", () => {
      const updates: Rect[] = [];
      render(
        <BoxCanvas
          imageUrl="x.png"
          boxes={[car]}
          selectedId="car-1"
          onUpdate={(_, r) => updates.push(r)}
          onSelect={() => undefined}
        />,
      );
      const canvas = screen.getByTestId("canvas");
      pointer(screen.getByTestId("box-car-1"), "pointerdown", 400, 150);
      pointer(canvas, "pointermove", 700, 400);
      pointer(canvas, "pointercancel", 700, 400);
      expect(updates.at(-1)).toEqual({ x: 0.2, y: 0.2, w: 0.4, h: 0.4 });
      // and the cancelled drag is over: further moves do nothing
      const count = updates.length;
      pointer(canvas, "pointermove", 100, 100);
      expect(updates).toHaveLength(count);
    });
  });
});
