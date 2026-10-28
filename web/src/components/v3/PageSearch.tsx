/**
 * ⌘K / Ctrl+K: jump to a page by name in either language, or by the
 * words it is known for ("logcat", "截屏", "fastboot").
 */
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import { useT } from "../../lib/i18n";
import { useShell } from "../../stores/shell";
import { Icon } from "./Icon";
import { ALL_ITEMS, type NavItem } from "./nav";

export function matchItems(query: string, items: NavItem[] = ALL_ITEMS): NavItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((it) => `${it.zh} ${it.en} ${it.key} ${it.keywords ?? ""}`.toLowerCase().includes(q));
}

export function PageSearch() {
  const t = useT();
  const open = useShell((s) => s.searchOpen);
  const setOpen = useShell((s) => s.setSearchOpen);
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const hits = useMemo(() => matchItems(q), [q]);

  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  if (!open) return null;
  const go = (it: NavItem | undefined) => {
    if (!it) return;
    setOpen(false);
    void navigate({ to: it.to });
  };
  return (
    <>
      <div className="scrim" onClick={() => setOpen(false)} aria-hidden />
      <div className="palette" role="dialog" aria-modal="true" aria-label={t("搜索页面", "Search pages")}>
        <label className="input--search palette__input">
          <Icon name="search" size="sm" />
          <input
            ref={input}
            value={q}
            placeholder={t("输入页面名或关键词,比如 logcat、截屏、fastboot", "Type a page or a word, e.g. logcat, screenshot, fastboot")}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSel((s) => Math.min(hits.length - 1, s + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSel((s) => Math.max(0, s - 1));
              } else if (e.key === "Enter") {
                go(hits[sel]);
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
          />
        </label>
        <ul className="list palette__list" role="listbox">
          {hits.length === 0 ? (
            <li className="palette__none">{t("没有匹配的页面", "No matching page")}</li>
          ) : (
            hits.map((it, i) => (
              <li key={it.key} role="option" aria-selected={i === sel}>
                <button type="button" className="menu__item" onMouseEnter={() => setSel(i)} onClick={() => go(it)}>
                  <Icon name={it.icon} />
                  <span>{t(it.zh, it.en)}</span>
                  <span className="mono t-muted">{it.to}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </>
  );
}
