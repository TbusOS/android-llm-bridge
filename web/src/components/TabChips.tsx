/**
 * Sticky pill-tab strip below AppNav — selects the active route.
 * Style mirrors the user-approved mockup (.tab-chip + .is-active).
 */
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
import { useApp } from "../stores/app";

interface NavItem {
  to: string;
  label: string;
  labelZh: string;
  icon: typeof Activity;
}

const NAV: NavItem[] = [
  { to: "/devices", label: "Devices", labelZh: "设备", icon: Wrench },
  { to: "/chat", label: "Chat", labelZh: "对话", icon: MessageSquare },
  { to: "/terminal", label: "Terminal", labelZh: "终端", icon: SquareTerminal },
  { to: "/playground", label: "Playground", labelZh: "模型调试", icon: Sliders },
  { to: "/system", label: "System", labelZh: "系统信息", icon: Cpu },
  { to: "/charts", label: "Charts", labelZh: "实时图表", icon: BarChart3 },
];

export function TabChips() {
  const lang = useApp((s) => s.lang);
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className="tab-chips" role="navigation" aria-label="Modules">
      <div className="tab-chips-inner">
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = pathname.endsWith(item.to);
          return (
            <Link
              key={item.to}
              to={item.to}
              className={active ? "tab-chip is-active" : "tab-chip"}
            >
              <Icon size={14} aria-hidden="true" />
              {lang === "zh" ? item.labelZh : item.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
