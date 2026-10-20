import { useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import type { Box, Rect } from "../api/types";
import { HANDLES, isLargeEnough, moveRect, rectFromPoints, resizeRect } from "../lib/geometry";
import type { Handle, Point } from "../lib/geometry";

type Props = {
  imageUrl: string;
  alt?: string;
  boxes: Box[];
  selectedId: string | null;
  invalidIds?: string[];
  readOnly?: boolean;
  /** Dashed outline for a box that has been drawn but not named yet. */
  draft?: Rect | null;
  onSelect?: (id: string | null) => void;
  onCreate?: (rect: Rect) => void;
  onUpdate?: (id: string, rect: Rect) => void;
  onRename?: (id: string) => void;
  /** Overlay content positioned in the canvas (e.g. the label prompt). */
  children?: ReactNode;
};

type Drag =
  | { mode: "draw"; start: Point }
  | { mode: "move"; id: string; start: Point; origin: Rect }
  | { mode: "resize"; id: string; handle: Handle; start: Point; origin: Rect };

const pct = (v: number): string => `${v * 100}%`;

export const labelColor = (label: string): string => {
  let hash = 0;
  for (const ch of label.toLowerCase()) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${hash} 80% 45%)`;
};

const rectStyle = (r: Rect): CSSProperties => ({
  left: pct(r.x),
  top: pct(r.y),
  width: pct(r.w),
  height: pct(r.h),
});

export const BoxCanvas = ({
  imageUrl,
  alt = "Image to label",
  boxes,
  selectedId,
  invalidIds = [],
  readOnly = false,
  draft = null,
  onSelect,
  onCreate,
  onUpdate,
  onRename,
  children,
}: Props) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const [drawing, setDrawing] = useState<Rect | null>(null);

  const toPoint = (e: ReactPointerEvent): Point => {
    const bounds = containerRef.current?.getBoundingClientRect();
    if (!bounds || bounds.width === 0 || bounds.height === 0) return { x: 0, y: 0 };
    return { x: (e.clientX - bounds.left) / bounds.width, y: (e.clientY - bounds.top) / bounds.height };
  };

  const capture = (e: ReactPointerEvent) => {
    containerRef.current?.setPointerCapture?.(e.pointerId);
  };

  const onBackgroundDown = (e: ReactPointerEvent) => {
    if (readOnly || e.button !== 0) return;
    onSelect?.(null);
    capture(e);
    dragRef.current = { mode: "draw", start: toPoint(e) };
    setDrawing(null);
  };

  const onBoxDown = (e: ReactPointerEvent, box: Box) => {
    if (readOnly || e.button !== 0) return;
    e.stopPropagation();
    onSelect?.(box.id);
    capture(e);
    dragRef.current = { mode: "move", id: box.id, start: toPoint(e), origin: box };
  };

  const onHandleDown = (e: ReactPointerEvent, box: Box, handle: Handle) => {
    if (readOnly || e.button !== 0) return;
    e.stopPropagation();
    capture(e);
    dragRef.current = { mode: "resize", id: box.id, handle, start: toPoint(e), origin: box };
  };

  const onMove = (e: ReactPointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const p = toPoint(e);
    if (drag.mode === "draw") {
      setDrawing(rectFromPoints(drag.start, p));
    } else if (drag.mode === "move") {
      onUpdate?.(drag.id, moveRect(drag.origin, p.x - drag.start.x, p.y - drag.start.y));
    } else {
      const { x, y, w, h } = drag.origin;
      onUpdate?.(drag.id, resizeRect({ x, y, w, h }, drag.handle, p.x - drag.start.x, p.y - drag.start.y));
    }
  };

  const finish = (e: ReactPointerEvent) => {
    const drag = dragRef.current;
    dragRef.current = null;
    containerRef.current?.releasePointerCapture?.(e.pointerId);
    if (drag?.mode === "draw") {
      const rect = rectFromPoints(drag.start, toPoint(e));
      setDrawing(null);
      if (isLargeEnough(rect)) onCreate?.(rect);
    }
  };

  return (
    <div
      ref={containerRef}
      className={`canvas${readOnly ? " canvas--readonly" : ""}`}
      data-testid="canvas"
      onPointerDown={onBackgroundDown}
      onPointerMove={onMove}
      onPointerUp={finish}
      onPointerCancel={finish}
    >
      <img className="canvas__image" src={imageUrl} alt={alt} draggable={false} />

      {boxes.map((box) => {
        const selected = box.id === selectedId;
        const invalid = invalidIds.includes(box.id);
        const color = invalid ? "#d00" : labelColor(box.label);
        return (
          <div
            key={box.id}
            data-testid={`box-${box.id}`}
            aria-label={`${box.label} box`}
            className={`box${selected ? " box--selected" : ""}${invalid ? " box--invalid" : ""}`}
            style={{ ...rectStyle(box), borderColor: color }}
            onPointerDown={(e) => onBoxDown(e, box)}
            onDoubleClick={() => !readOnly && onRename?.(box.id)}
          >
            <span className="box__label" style={{ background: color }}>
              {box.label}
            </span>
            {selected &&
              !readOnly &&
              HANDLES.map((h) => (
                <span
                  key={h}
                  data-testid={`handle-${h}`}
                  className={`handle handle--${h}`}
                  onPointerDown={(e) => onHandleDown(e, box, h)}
                />
              ))}
          </div>
        );
      })}

      {(drawing ?? draft) && (
        <div className="box box--draft" data-testid="draft" style={rectStyle((drawing ?? draft) as Rect)} />
      )}
      {children}
    </div>
  );
};
