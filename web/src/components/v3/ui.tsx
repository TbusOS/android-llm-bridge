/**
 * Small v3 building blocks shared by every page: snackbar, inline
 * confirmation, menu, sparkline, status dot. Class names match the design
 * mockups (docs/webui-preview-v3*.html) one to one.
 */
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { create } from "zustand";

import { Icon } from "./Icon";

/* ---------------- snackbar ---------------- */

interface SnackState {
  text: string | null;
  seq: number;
  show: (text: string) => void;
  clear: () => void;
}

export const useSnack = create<SnackState>((set) => ({
  text: null,
  seq: 0,
  show: (text) => set((s) => ({ text, seq: s.seq + 1 })),
  clear: () => set({ text: null }),
}));

/** Fire-and-forget toast: `toast(t("已复制", "Copied"))`. */
export function toast(text: string): void {
  useSnack.getState().show(text);
}

export function SnackbarHost() {
  const text = useSnack((s) => s.text);
  const seq = useSnack((s) => s.seq);
  const clear = useSnack((s) => s.clear);
  useEffect(() => {
    if (!text) return;
    const id = window.setTimeout(clear, 3200);
    return () => window.clearTimeout(id);
  }, [text, seq, clear]);
  if (!text) return null;
  return (
    <div className="snackbar" role="status">
      {text}
    </div>
  );
}

/* ---------------- inline confirmation ---------------- */

export interface ConfirmProps {
  open: boolean;
  message: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  /** "danger" for actions that change the board (default). */
  tone?: "danger" | "primary";
}

/**
 * Two-step confirmation drawn in the page, not a browser dialog: the
 * first click (elsewhere) opens it, this button commits. Escape cancels.
 */
export function Confirm({ open, message, confirmLabel, cancelLabel, onConfirm, onCancel, busy, tone = "danger" }: ConfirmProps) {
  const goRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    goRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="confirm" role="alert">
      <Icon name="alert" />
      <p>{message}</p>
      <div className="confirm__actions">
        <button
          ref={goRef}
          type="button"
          className={`btn btn--sm ${tone === "danger" ? "btn--danger" : "btn--filled"}`}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : null}
          {confirmLabel}
        </button>
        <button type="button" className="btn btn--text btn--sm" onClick={onCancel}>
          {cancelLabel}
        </button>
      </div>
    </div>
  );
}

/* ---------------- menu ---------------- */

export interface MenuItem {
  key: string;
  label: ReactNode;
  icon?: string;
  desc?: ReactNode;
  trail?: ReactNode;
  disabled?: boolean;
  onSelect: () => void;
}

export interface MenuProps {
  trigger: (props: { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu"; "aria-controls": string }) => ReactNode;
  items: (MenuItem | "sep")[];
  align?: "start" | "end";
  footer?: ReactNode;
}

export function Menu({ trigger, items, align = "start", footer }: MenuProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return (
    <span className="menu-anchor" ref={ref}>
      {trigger({ onClick: () => setOpen((o) => !o), "aria-expanded": open, "aria-haspopup": "menu", "aria-controls": id })}
      {open ? (
        <div className={`menu${align === "end" ? " menu--end" : ""}`} id={id} role="menu">
          {items.map((it, i) =>
            it === "sep" ? (
              <div className="menu__sep" key={`sep-${i}`} />
            ) : (
              <button
                key={it.key}
                type="button"
                className="menu__item"
                role="menuitem"
                disabled={it.disabled}
                onClick={() => {
                  close();
                  it.onSelect();
                }}
              >
                {it.icon ? <Icon name={it.icon} /> : <span />}
                <span>{it.label}</span>
                {it.trail ?? <span />}
                {it.desc ? <span className="menu__desc">{it.desc}</span> : null}
              </button>
            ),
          )}
          {footer}
        </div>
      ) : null}
    </span>
  );
}

/* ---------------- sparkline ---------------- */

export interface SparkProps {
  lines: readonly (readonly number[])[];
  lo: number;
  hi: number;
  small?: boolean;
  className?: string;
  label?: string;
  height?: number;
}

/** Area sparkline with a faint grid and an emphasised last point. */
export function Spark({ lines, lo, hi, small, className, label, height }: SparkProps) {
  const w = 200;
  const h = height ?? (small ? 24 : 44);
  const pad = 3;
  const span = hi - lo || 1;
  const first = lines[0] ?? [];
  if (first.length < 2) {
    return <svg className={`spark${small ? " spark--sm" : ""} ${className ?? ""}`} viewBox={`0 0 ${w} ${h}`} aria-hidden />;
  }
  const toPath = (data: readonly number[]) =>
    data
      .map((v, i) => {
        const x = (i / (data.length - 1)) * w;
        const y = pad + (1 - (Math.min(hi, Math.max(lo, v)) - lo) / span) * (h - 2 * pad);
        return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join("");
  const last = first[first.length - 1] ?? lo;
  const cy = pad + (1 - (Math.min(hi, Math.max(lo, last)) - lo) / span) * (h - 2 * pad);
  return (
    <svg
      className={`spark${small ? " spark--sm" : ""} ${className ?? ""}`}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {small
        ? null
        : [0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1={0} x2={w} y1={h * f} y2={h * f} className="grid-line" vectorEffect="non-scaling-stroke" />
          ))}
      {lines.map((data, i) => {
        const d = toPath(data);
        return i === 0 ? (
          <g key={i}>
            <path d={`${d}L${w} ${h}L0 ${h}Z`} fill="currentColor" fillOpacity={0.12} stroke="none" />
            <path d={d} fill="none" stroke="currentColor" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
          </g>
        ) : (
          <path key={i} d={d} fill="none" className="line2" strokeWidth={1.6} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        );
      })}
      <circle cx={w - 1} cy={cy.toFixed(1)} r={small ? 2.4 : 3} fill="currentColor" />
    </svg>
  );
}

/* ---------------- status ---------------- */

export type Tone = "ok" | "warn" | "bad" | "idle" | "busy" | "live";

export function Dot({ tone = "idle", inline }: { tone?: Tone; inline?: boolean }) {
  const cls = tone === "idle" ? "dot" : `dot dot--${tone}`;
  return <span className={cls} style={inline ? { display: "inline-block", marginRight: 6 } : undefined} />;
}

export function Pill({ tone, children, title }: { tone?: "ok" | "warn" | "bad" | "info"; children: ReactNode; title?: string }) {
  return (
    <span className={tone ? `pill pill--${tone}` : "pill"} title={title}>
      {children}
    </span>
  );
}

export function CardHead({ title, sub, actions, level = 2 }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; level?: 2 | 3 }) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className="card__head">
      <H>{title}</H>
      {sub ? <span className="card__sub">{sub}</span> : null}
      {actions ? <div className="card__actions">{actions}</div> : null}
    </div>
  );
}

export function PageHead({ eyebrow, title, desc, actions }: { eyebrow?: ReactNode; title: ReactNode; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        {eyebrow ? <div className="page-head__eyebrow">{eyebrow}</div> : null}
        <h1>{title}</h1>
        {desc ? <p>{desc}</p> : null}
      </div>
      {actions ? <div className="page-head__actions">{actions}</div> : null}
    </div>
  );
}

export function Empty({ icon = "info", title, children, action }: { icon?: string; title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} />
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  );
}

export function ErrorBanner({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="banner banner--error" role="alert">
      <Icon name="error" />
      <p>{children}</p>
      {action ? <div className="banner__actions">{action}</div> : <span />}
    </div>
  );
}
