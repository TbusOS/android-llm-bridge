/**
 * ⌘K / Ctrl+K: jump to a page by name in either language, or by the
 * words it is known for ("logcat", "截屏", "fastboot").
 *
 * ARIA combobox pattern: focus stays in the input; arrow keys move the
 * active option (aria-activedescendant), Enter opens it, Escape closes.
 */
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useMemo, useState } from "react";

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
  const listId = useId();
  const hits = useMemo(() => matchItems(q), [q]);

  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
    }
  }, [open]);

  if (!open) return null;
  const go = (it: NavItem | undefined) => {
    if (!it) return;
    setOpen(false);
    void navigate({ to: it.to });
  };
  const optionId = (i: number) => `${listId}-opt-${i}`;
  return (
    <>
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: pointer-only scrim; Escape in the input closes the dialog */}
      <div className="scrim" onClick={() => setOpen(false)} aria-hidden />
      <div className="palette" role="dialog" aria-modal="true" aria-label={t("搜索页面", "Search pages")}>
        <label className="input--search palette__input">
          <Icon name="search" size="sm" />
          <input
            // biome-ignore lint/a11y/noAutofocus: a search dialog opened by the user puts focus in its field
            autoFocus
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={hits.length ? optionId(sel) : undefined}
            aria-autocomplete="list"
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
        {hits.length === 0 ? (
          <p className="palette__none">{t("没有匹配的页面", "No matching page")}</p>
        ) : (
          <ul className="list palette__list" id={listId} role="listbox" aria-label={t("页面", "Pages")}>
            {hits.map((it, i) => (
              // Options are picked with the arrow keys + Enter in the input
              // (aria-activedescendant); the mouse handlers mirror that.
              // biome-ignore lint/a11y/useKeyWithClickEvents: keyboard selection lives on the combobox input
              <li
                key={it.key}
                id={optionId(i)}
                role="option"
                aria-selected={i === sel}
                className="menu__item"
                onMouseEnter={() => setSel(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(it)}
              >
                <Icon name={it.icon} />
                <span>{t(it.zh, it.en)}</span>
                <span className="mono t-muted">{it.to}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
