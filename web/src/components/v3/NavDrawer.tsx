/**
 * Navigation drawer (expanded), rail (medium) or modal drawer (compact) —
 * the same list, laid out by the shell's data-nav attribute.
 */
import { Link, useRouterState } from "@tanstack/react-router";
import { Fragment } from "react";

import { useT } from "../../lib/i18n";
import { useShell } from "../../stores/shell";
import { Icon } from "./Icon";
import { NAV } from "./nav";

export function isActive(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

export function NavDrawer({ badges }: { badges?: Record<string, number> }) {
  const t = useT();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const setNavOpen = useShell((s) => s.setNavOpen);
  return (
    <nav className="nav" aria-label={t("页面", "Pages")}>
      {NAV.map((g, gi) => (
        <Fragment key={g.en}>
          {gi ? <span className="nav__sep" aria-hidden /> : null}
          <div className="nav__label">{t(g.zh, g.en)}</div>
          {g.items.map((it) => {
            const active = isActive(pathname, it.to);
            const count = badges?.[it.key];
            return (
              <Link key={it.key} to={it.to} className="nav__item" aria-current={active ? "page" : undefined} onClick={() => setNavOpen(false)}>
                <Icon name={it.icon} />
                <span>{t(it.zh, it.en)}</span>
                {count ? <span className="nav__badge">{count}</span> : it.badge === "new" ? <span className="nav__badge nav__badge--new">{t("新", "new")}</span> : null}
              </Link>
            );
          })}
        </Fragment>
      ))}
    </nav>
  );
}
