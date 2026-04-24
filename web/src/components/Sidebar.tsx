import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  BarChart3,
  Cpu,
  MessageSquare,
  Sliders,
  SquareTerminal,
  Wrench,
} from "lucide-react";

interface NavItem {
  to: string;
  label: string;
  labelZh: string;
  icon: typeof Activity;
  hint?: string;
}

const NAV: NavItem[] = [
  { to: "/devices", label: "Devices", labelZh: "设备", icon: Wrench },
  { to: "/chat", label: "Chat", labelZh: "对话", icon: MessageSquare },
  { to: "/terminal", label: "Terminal", labelZh: "终端", icon: SquareTerminal },
  { to: "/playground", label: "Playground", labelZh: "模型调试", icon: Sliders },
  { to: "/system", label: "System", labelZh: "系统信息", icon: Cpu },
  { to: "/charts", label: "Charts", labelZh: "实时图表", icon: BarChart3 },
];

export function Sidebar({ lang }: { lang: "en" | "zh" }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  return (
    <nav
      aria-label="Primary"
      style={{
        position: "sticky",
        top: 0,
        alignSelf: "start",
        width: 220,
        height: "100vh",
        padding: "var(--space-5) var(--space-3)",
        borderRight: "1px solid var(--anth-light-gray)",
        background: "var(--anth-bg)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-heading)",
          fontWeight: 600,
          fontSize: 18,
          padding: "0 var(--space-3)",
          marginBottom: "var(--space-4)",
        }}
      >
        alb
        <span
          style={{
            display: "block",
            fontSize: 11,
            fontWeight: 400,
            color: "var(--anth-text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            marginTop: 2,
          }}
        >
          android-llm-bridge
        </span>
      </div>

      {NAV.map((item) => {
        const active = pathname.startsWith(item.to);
        const Icon = item.icon;
        return (
          <Link
            key={item.to}
            to={item.to}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-3)",
              padding: "8px var(--space-3)",
              borderRadius: "var(--radius-sm)",
              fontFamily: "var(--font-heading)",
              fontSize: 14,
              textDecoration: "none",
              color: active ? "var(--anth-orange)" : "var(--anth-text)",
              background: active ? "rgba(217,119,87,0.10)" : "transparent",
            }}
          >
            <Icon size={16} aria-hidden="true" />
            {lang === "zh" ? item.labelZh : item.label}
          </Link>
        );
      })}

      <div style={{ marginTop: "auto", padding: "0 var(--space-3)" }}>
        <a
          href="../"
          style={{
            fontSize: 12,
            color: "var(--anth-text-secondary)",
            textDecoration: "none",
          }}
        >
          ← docs home
        </a>
      </div>
    </nav>
  );
}
