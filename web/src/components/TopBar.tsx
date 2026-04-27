/**
 * 56-px sticky topbar — breadcrumb + active-device picker + ⌘K command
 * search box + right-aligned timestamp.  v2 mockup spec
 * (docs/webui-preview-v2.html).
 *
 * The device picker is the single source of truth for "active device";
 * panels read it via useApp().device.  Real picker dropdown is TODO —
 * for now the chevron is decorative and the value comes from the store.
 */
import { useRouterState } from "@tanstack/react-router";
import { ChevronDown, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useApp } from "../stores/app";

interface CrumbDef {
  /** Path prefix (longest prefix wins). */
  prefix: string;
  en: string;
  zh: string;
}

const DEFAULT_CRUMB: CrumbDef = {
  prefix: "/dashboard",
  en: "Dashboard",
  zh: "总览",
};

const CRUMBS: CrumbDef[] = [
  DEFAULT_CRUMB,
  { prefix: "/chat", en: "Chat", zh: "对话" },
  { prefix: "/terminal", en: "Terminal", zh: "终端" },
  { prefix: "/inspect", en: "Inspect", zh: "检视" },
  { prefix: "/playground", en: "Playground", zh: "调试台" },
  { prefix: "/sessions", en: "Sessions", zh: "会话" },
  { prefix: "/files", en: "Files", zh: "文件" },
  { prefix: "/audit", en: "Audit", zh: "审计" },
];

export function TopBar() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const now = useNow();

  const leaf = CRUMBS.find((c) => pathname.startsWith(c.prefix)) ?? DEFAULT_CRUMB;

  return (
    <header className="topbar" role="banner">
      <span className="crumbs">
        <span className="root">{lang === "zh" ? "工作区" : "Workspace"}</span>
        <span className="sep">/</span>
        <span className="leaf">{lang === "zh" ? leaf.zh : leaf.en}</span>
      </span>

      <span
        className={device ? "device-picker" : "device-picker is-empty"}
        tabIndex={0}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={false}
        aria-label={lang === "zh" ? "当前设备" : "Active device"}
      >
        <span className="dot" />
        <span className="label">
          {device ?? (lang === "zh" ? "未选择设备" : "no device")}
        </span>
        {device ? (
          <span className="meta">adb · usb · 1500000 baud</span>
        ) : null}
        <ChevronDown size={12} aria-hidden={true} />
      </span>

      <span className="cmd-search" tabIndex={0} role="searchbox">
        <Search size={14} aria-hidden={true} />
        <span>
          {lang === "zh"
            ? "命令面板：跑工具 / 问 agent / 跳面板"
            : "Run anything · ask the agent · jump to a panel"}
        </span>
        <kbd>⌘K</kbd>
      </span>

      <span className="topbar-spacer" />

      <span className="topbar-time" aria-live="off">
        {now}
      </span>
    </header>
  );
}

function useNow(): string {
  const [s, setS] = useState(() => formatNow());
  useEffect(() => {
    const t = setInterval(() => setS(formatNow()), 1000);
    return () => clearInterval(t);
  }, []);
  return s;
}

function formatNow(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} · ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
