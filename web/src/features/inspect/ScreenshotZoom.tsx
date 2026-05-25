/**
 * ScreenshotZoom — fullscreen modal for zooming into a screenshot.
 *
 * Opens when ScreenshotTab calls into it; closes via Esc, backdrop
 * click, or the close button. Inputs:
 *   - mouse wheel: scale ±10% per tick (clamped 0.25× ~ 8×)
 *   - drag: pan when zoom > 1
 *   - double-click: reset to fit
 *
 * No external library — react state + a single transform on the <img>.
 * Performance: pinch-zoom on touch devices is browser-native (we don't
 * override default touch behaviour beyond preventDefault on wheel so
 * scroll doesn't bleed to background).
 */
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

const MIN_SCALE = 0.25;
const MAX_SCALE = 8;
const WHEEL_STEP = 0.1;

interface Props {
  src: string;
  alt: string;
  onClose: () => void;
  lang: "zh" | "en";
}

export function ScreenshotZoom({ src, alt, onClose, lang }: Props) {
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [dragging, setDragging] = useState(false);
  const dragOriginRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  // Element that had focus BEFORE the modal opened — we restore focus
  // here on unmount so keyboard users return to whatever opened the
  // zoom (the screenshot thumbnail button, typically) instead of
  // landing on <body> and having to Tab-cycle from page top.
  const previousActiveRef = useRef<HTMLElement | null>(null);

  // Modal a11y lifecycle:
  //   - Esc closes (was already here pre-AI-7).
  //   - Save previously-focused element, focus the close button so
  //     Enter / Space immediately closes (5/25 ui-f MID-6).
  //   - Tab / Shift+Tab is trapped: there is only one focusable
  //     element inside the modal (close button), so any Tab keypress
  //     forces focus back to it instead of escaping to the page
  //     behind the backdrop.
  //   - Restore focus on unmount (5/25 ui-f MID-6).
  useEffect(() => {
    previousActiveRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "Tab") {
        // Single-focusable-element trap — pin focus to close button.
        e.preventDefault();
        closeBtnRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    closeBtnRef.current?.focus();

    // Lock page scroll while modal is open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previousActiveRef.current?.focus?.();
    };
  }, [onClose]);

  // React JSX `onWheel` is registered as a PASSIVE listener — calling
  // `e.preventDefault()` inside silently fails (Chrome warns) and the
  // page behind the modal scrolls while the user zooms. Attach the
  // wheel handler imperatively with `{ passive: false }` so the
  // preventDefault actually fires.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const dir = e.deltaY > 0 ? -1 : 1;
      setScale((s) => {
        const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, s + dir * WHEEL_STEP));
        if (next <= 1) {
          setTx(0);
          setTy(0);
        }
        return next;
      });
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  const onMouseDown = (e: React.MouseEvent) => {
    if (scale <= 1) return;
    setDragging(true);
    dragOriginRef.current = { x: e.clientX, y: e.clientY, tx, ty };
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragging || !dragOriginRef.current) return;
    const o = dragOriginRef.current;
    setTx(o.tx + (e.clientX - o.x));
    setTy(o.ty + (e.clientY - o.y));
  };

  const stopDrag = () => {
    setDragging(false);
    dragOriginRef.current = null;
  };

  const onDoubleClick = () => {
    setScale(1);
    setTx(0);
    setTy(0);
  };

  const onBackdropClick = (e: React.MouseEvent) => {
    // Only close when click landed on the backdrop itself, not bubbled
    // from the image / controls.
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div
      className="screenshot-zoom"
      role="dialog"
      aria-modal="true"
      aria-label={lang === "zh" ? "查看截图" : "Zoom screenshot"}
      ref={wrapRef}
      tabIndex={-1}
      onClick={onBackdropClick}
    >
      <button
        ref={closeBtnRef}
        type="button"
        className="screenshot-zoom__close"
        onClick={onClose}
        aria-label={lang === "zh" ? "关闭" : "Close"}
      >
        <X size={20} />
      </button>
      {/* 5/25 ui-f MID-3: pre-AI-7 the zoom % shared this region's
       *   `role="status"` so every wheel tick triggered a polite SR
       *   announcement ("…100%, 110%, 120%, 130%…"). The hint text
       *   itself is static and doesn't need a live region; the % is a
       *   visual decoration only (aria-hidden so AT skips it). */}
      <div className="screenshot-zoom__hint">
        <span>
          {lang === "zh"
            ? "滚轮缩放 · 拖动平移 · 双击复位 · Esc 关闭"
            : "scroll = zoom · drag = pan · dbl-click = reset · Esc = close"}
        </span>
        {scale !== 1 && (
          <span aria-hidden="true"> · {Math.round(scale * 100)}%</span>
        )}
      </div>
      <img
        src={src}
        alt={alt}
        className="screenshot-zoom__img"
        style={{
          transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
          cursor: scale > 1 ? (dragging ? "grabbing" : "grab") : "zoom-in",
          transition: dragging ? "none" : "transform 80ms linear",
        }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={stopDrag}
        onMouseLeave={stopDrag}
        onDoubleClick={onDoubleClick}
        draggable={false}
      />
    </div>
  );
}
