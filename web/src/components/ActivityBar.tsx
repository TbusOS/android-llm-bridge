/**
 * Left activity bar (64 px) — primary navigation.  Lifted from
 * docs/webui-preview-v2.html. 8 module icons + bottom status block
 * (LLM health pill, theme cycle, language toggle, divider, settings).
 */
import { Link, useRouterState } from "@tanstack/react-router";
import {
  Files,
  LayoutDashboard,
  Languages,
  type LucideIcon,
  MessageSquare,
  Moon,
  Search,
  Settings,
  Shield,
  SlidersHorizontal,
  SquareTerminal,
  Sun,
  SunMoon,
  Timer,
} from "lucide-react";
import { useApp } from "../stores/app";

interface NavItem {
  to: string;
  label: string;
  labelZh: string;
  icon: LucideIcon;
  /** Optional badge — shows in top-right of icon when > 0. */
  badge?: number;
}

const NAV: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", labelZh: "总览", icon: LayoutDashboard },
  { to: "/chat", label: "Chat", labelZh: "对话", icon: MessageSquare },
  { to: "/terminal", label: "Terminal", labelZh: "终端", icon: SquareTerminal },
  { to: "/inspect", label: "Inspect", labelZh: "检视", icon: Search },
  { to: "/playground", label: "Playground", labelZh: "调试台", icon: SlidersHorizontal },
  { to: "/sessions", label: "Sessions", labelZh: "会话", icon: Timer },
  { to: "/files", label: "Files", labelZh: "文件", icon: Files },
  { to: "/audit", label: "Audit", labelZh: "审计", icon: Shield },
];

export function ActivityBar() {
  const lang = useApp((s) => s.lang);
  const theme = useApp((s) => s.theme);
  const setTheme = useApp((s) => s.setTheme);
  const setLang = useApp((s) => s.setLang);
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  const nextTheme =
    theme === "light" ? "dark" : theme === "dark" ? "auto" : "light";

  return (
    <aside className="activity-bar" aria-label="Primary navigation">
      <Link to="/dashboard" className="ab-logo" aria-label="alb home">
        alb
      </Link>

      {NAV.map((item) => {
        const Icon = item.icon;
        const active = pathname === item.to || pathname.startsWith(item.to + "/");
        return (
          <Link
            key={item.to}
            to={item.to}
            className={active ? "ab-item is-active" : "ab-item"}
            aria-label={lang === "zh" ? item.labelZh : item.label}
            aria-current={active ? "page" : undefined}
            title={lang === "zh" ? item.labelZh : item.label}
          >
            <Icon size={20} aria-hidden={true} />
            {item.badge && item.badge > 0 ? (
              <span className="badge">{item.badge}</span>
            ) : null}
          </Link>
        );
      })}

      <span className="ab-spacer" />

      <span
        className="ab-status"
        title={lang === "zh" ? "LLM 后端在线" : "LLM backend up"}
      >
        <span className="swatch" />
        LLM
      </span>

      <button
        type="button"
        className="ab-item"
        aria-label={`theme: ${theme}`}
        onClick={() => setTheme(nextTheme)}
        title={`theme: ${theme}`}
      >
        {theme === "dark" ? (
          <Moon size={20} aria-hidden={true} />
        ) : theme === "auto" ? (
          <SunMoon size={20} aria-hidden={true} />
        ) : (
          <Sun size={20} aria-hidden={true} />
        )}
      </button>

      <button
        type="button"
        className="ab-item"
        aria-label={lang === "zh" ? "Switch to English" : "切换到中文"}
        onClick={() => setLang(lang === "zh" ? "en" : "zh")}
      >
        <Languages size={20} aria-hidden={true} />
      </button>

      <span className="ab-divider" aria-hidden={true} />

      <button
        type="button"
        className="ab-item"
        aria-label={lang === "zh" ? "设置" : "Settings"}
        title={lang === "zh" ? "设置" : "Settings"}
      >
        <Settings size={20} aria-hidden={true} />
      </button>
    </aside>
  );
}
