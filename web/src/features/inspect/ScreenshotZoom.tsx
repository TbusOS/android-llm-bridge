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

  // Esc closes; trap focus inside wrapper while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    wrapRef.current?.focus();
    // Lock page scroll while modal is open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
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
        type="button"
        className="screenshot-zoom__close"
        onClick={onClose}
        aria-label={lang === "zh" ? "关闭" : "Close"}
      >
        <X size={20} />
      </button>
      <div className="screenshot-zoom__hint" role="status">
        {lang === "zh"
          ? "滚轮缩放 · 拖动平移 · 双击复位 · Esc 关闭"
          : "scroll = zoom · drag = pan · dbl-click = reset · Esc = close"}
        {scale !== 1 && <> · {Math.round(scale * 100)}%</>}
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
