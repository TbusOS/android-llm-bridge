/**
 * Module-internal sub-nav — thin underline-active style, distinct from
 * the global ActivityBar (which uses solid orange pills).  Used by
 * Inspect (System Info / Charts / Screenshot / UI Dump / Files) and
 * later by Playground (sampling / metrics / examples).
 *
 * Active tab gets a 2px orange border-bottom.  Lifted from
 * docs/webui-preview-v2.html .subnav.
 */
import type { ReactNode } from "react";

export interface SubNavTab<T extends string> {
  key: T;
  /** Visible label.  Pass a fragment if you need EN/ZH conditionals. */
  label: ReactNode;
  /** Disabled tabs render greyed and ignore clicks. */
  disabled?: boolean;
}

interface Props<T extends string> {
  tabs: SubNavTab<T>[];
  active: T;
  onChange: (key: T) => void;
  ariaLabel?: string;
}

export function SubNav<T extends string>({
  tabs,
  active,
  onChange,
  ariaLabel,
}: Props<T>) {
  return (
    <nav className="subnav" aria-label={ariaLabel} role="tablist">
      {tabs.map((tab) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={tab.disabled}
            className={isActive ? "is-active" : undefined}
            onClick={() => {
              if (!tab.disabled) onChange(tab.key);
            }}
            style={
              tab.disabled
                ? { opacity: 0.55, cursor: "not-allowed" }
                : undefined
            }
          >
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}
