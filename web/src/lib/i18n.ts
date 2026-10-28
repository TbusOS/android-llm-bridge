/**
 * Bilingual copy for the v3 UI. Chinese and English are both first-class:
 * every string is written in both at the call site, `t(zh, en)` picks one
 * by the language in the app store, and the document's `lang` attribute
 * drives the typography differences (CJK tracking / line height in
 * styles/v3/tokens.css).
 */
import { useCallback } from "react";

import { type Lang, useApp } from "../stores/app";

export type T = (zh: string, en: string) => string;

export function pick(lang: Lang, zh: string, en: string): string {
  return lang === "zh" ? zh : en;
}

/** `const t = useT(); t("总览", "Overview")` */
export function useT(): T {
  const lang = useApp((s) => s.lang);
  return useCallback((zh: string, en: string) => pick(lang, zh, en), [lang]);
}

/** Relative time in the current language: "3 s ago" / "3 秒前". */
export function ago(lang: Lang, seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return lang === "zh" ? `${s} 秒前` : `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return lang === "zh" ? `${m} 分钟前` : `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return lang === "zh" ? `${h} 小时前` : `${h} h ago`;
  const d = Math.round(h / 24);
  return lang === "zh" ? `${d} 天前` : `${d} d ago`;
}

/** Duration such as "5 d 3 h" / "5 天 3 小时". */
export function duration(lang: Lang, seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (lang === "zh") {
    if (d) return `${d} 天 ${h} 小时`;
    if (h) return `${h} 小时 ${m} 分`;
    return `${m} 分 ${s % 60} 秒`;
  }
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} m`;
  return `${m} m ${s % 60} s`;
}
