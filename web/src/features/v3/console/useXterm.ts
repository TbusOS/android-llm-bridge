/**
 * xterm.js wiring shared by the adb and serial panes of Console: mount
 * once, fit to the card, follow the v3 terminal colours (read from the
 * --alb-color-terminal-* variables, refreshed when the theme changes),
 * and expose clear / copy / transcript helpers.
 *
 * SECURITY: device bytes go straight into term.write(). Keep xterm on
 * its default options; do NOT enable `allowProposedApi` (OSC 52 clipboard
 * write) or a link handler. Same rule as features/inspect/ShellTab.tsx.
 */
import { FitAddon } from "@xterm/addon-fit";
import { type ITheme, Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { type MutableRefObject, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { copyText as writeClipboard } from "../../../lib/clipboard";
import { useApp } from "../../../stores/app";

const COLOR_VARS: [keyof ITheme, string][] = [
  ["background", "--alb-color-terminal-bg"],
  ["foreground", "--alb-color-terminal-fg"],
  ["cursor", "--alb-color-terminal-fg"],
  ["brightBlack", "--alb-color-terminal-muted"],
  ["green", "--alb-color-terminal-prompt"],
  ["blue", "--alb-color-terminal-accent"],
  ["yellow", "--alb-color-terminal-warn"],
  ["red", "--alb-color-terminal-error"],
];

/** Terminal colours as the page sees them right now. Missing variable = key left out. */
export function readTermTheme(): ITheme {
  const out: ITheme = {};
  if (typeof document === "undefined") return out;
  const cs = getComputedStyle(document.documentElement);
  for (const [key, name] of COLOR_VARS) {
    const v = cs.getPropertyValue(name).trim();
    if (v) (out as Record<string, string>)[key] = v;
  }
  return out;
}

function readMonoFont(): string {
  if (typeof document === "undefined") return "monospace";
  const v = getComputedStyle(document.documentElement).getPropertyValue("--md-ref-typeface-mono").trim();
  return v ? `${v}, monospace` : "monospace";
}

/** Re-reads the theme whenever the page theme changes (attribute, system scheme, app setting). */
export function useTermTheme(): ITheme {
  const appTheme = useApp((s) => s.theme);
  const [theme, setTheme] = useState<ITheme>(() => readTermTheme());
  useEffect(() => {
    const refresh = () => setTheme(readTermTheme());
    refresh();
    const mo = typeof MutationObserver !== "undefined" ? new MutationObserver(refresh) : null;
    mo?.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    const mq = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    mq?.addEventListener?.("change", refresh);
    return () => {
      mo?.disconnect();
      mq?.removeEventListener?.("change", refresh);
    };
  }, [appTheme]);
  return theme;
}

/** Text of the whole buffer (scrollback included), wrapped lines re-joined. */
export function bufferText(term: Terminal | null): string {
  const buf = term?.buffer?.active;
  if (!buf) return "";
  const lines: string[] = [];
  for (let i = 0; i < buf.length; i++) {
    const line = buf.getLine(i);
    if (!line) continue;
    const s = line.translateToString(true);
    if (line.isWrapped && lines.length) lines[lines.length - 1] += s;
    else lines.push(s);
  }
  while (lines.length && !lines[lines.length - 1]?.trim()) lines.pop();
  return lines.join("\n");
}

/** Prompt on the cursor line, e.g. `board_x:/ #`; null when the line does not end in # or $. */
export function promptOf(term: Terminal | null): string | null {
  const buf = term?.buffer?.active;
  if (!buf) return null;
  const line = buf.getLine(buf.baseY + buf.cursorY)?.translateToString(true).trimEnd();
  if (!line || line.length > 80) return null;
  return /[#$]$/.test(line) ? line : null;
}

export interface UseXtermOptions {
  convertEol: boolean;
  cursorBlink: boolean;
  /** Keystrokes typed into the terminal. */
  onData: (data: string) => void;
  /** Called with the new size after the terminal is fitted. */
  onResize?: (rows: number, cols: number) => void;
  /** Hidden panes cannot be measured; refit when this turns true. */
  active: boolean;
}

export interface XtermApi {
  hostRef: RefObject<HTMLDivElement>;
  termRef: MutableRefObject<Terminal | null>;
  write: (chunk: Uint8Array | string) => void;
  clear: () => void;
  focus: () => void;
  size: () => { rows: number; cols: number };
  /** Selected text, or the whole buffer when nothing is selected. */
  copyText: () => string;
  transcript: () => string;
}

export function useXterm(opts: UseXtermOptions): XtermApi {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const dataRef = useRef(opts.onData);
  const resizeRef = useRef(opts.onResize);
  dataRef.current = opts.onData;
  resizeRef.current = opts.onResize;
  const theme = useTermTheme();
  const themeRef = useRef(theme);
  themeRef.current = theme;

  const refit = useCallback(() => {
    const term = termRef.current;
    const fit = fitRef.current;
    const host = hostRef.current;
    if (!term || !fit || !host) return;
    // A hidden pane measures NaN and fit() ignores it; the active-effect below refits on show.
    try {
      fit.fit();
      if (term.rows > 0 && term.cols > 0) resizeRef.current?.(term.rows, term.cols);
    } catch {
      /* container not laid out yet */
    }
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const term = new Terminal({
      fontFamily: readMonoFont(),
      fontSize: 13,
      lineHeight: 1.25,
      cursorBlink: opts.cursorBlink,
      convertEol: opts.convertEol,
      scrollback: 5000,
      theme: themeRef.current,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;
    fitRef.current = fit;
    refit();
    const sub = term.onData((d) => dataRef.current(d));
    let raf = 0;
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(refit);
    };
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(schedule) : null;
    ro?.observe(host);
    window.addEventListener("resize", schedule);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", schedule);
      ro?.disconnect();
      sub.dispose();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
    // The terminal is created once; options that can change later go through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const term = termRef.current;
    if (term?.options) term.options.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!opts.active) return;
    const id = requestAnimationFrame(refit);
    return () => cancelAnimationFrame(id);
  }, [opts.active, refit]);

  const write = useCallback((chunk: Uint8Array | string) => termRef.current?.write(chunk), []);
  const clear = useCallback(() => termRef.current?.clear(), []);
  const focus = useCallback(() => termRef.current?.focus(), []);
  const size = useCallback(() => ({ rows: termRef.current?.rows ?? 30, cols: termRef.current?.cols ?? 100 }), []);
  const copyText = useCallback(() => {
    const term = termRef.current;
    return (term?.hasSelection?.() ? term.getSelection() : "") || bufferText(term);
  }, []);
  const transcript = useCallback(() => bufferText(termRef.current), []);
  // One stable object: panes list it in effect dependencies.
  return useMemo(() => ({ hostRef, termRef, write, clear, focus, size, copyText, transcript }), [write, clear, focus, size, copyText, transcript]);
}

/** Save text as a file; returns false when the browser cannot. */
export function downloadText(name: string, text: string): boolean {
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return true;
  } catch {
    return false;
  }
}

export async function copyToClipboard(text: string): Promise<boolean> {
  return writeClipboard(text);
}
