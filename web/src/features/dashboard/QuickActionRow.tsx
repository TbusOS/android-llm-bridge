/**
 * Quick actions — auto-fit (220px min) grid of qa-card buttons.  Each
 * card has a 36-px rounded square icon (orange on subtle bg) + title +
 * sub-line.  Routes to the matching module.
 *
 * Inspect-tab actions deep-link to `/inspect/<tabKey>` (nested routes,
 * see `router.tsx` `inspectTabRoutes`) so a tap on "Tail Logcat" lands
 * the user on the Logcat tab, not the default System Info.
 */
import { Link } from "@tanstack/react-router";
import {
  Camera,
  HeartPulse,
  type LucideIcon,
  MessageSquare,
  ScrollText,
  SquareTerminal,
} from "lucide-react";
import type { InspectTabKey } from "../../router";
import { useApp } from "../../stores/app";
import type { QuickActionData } from "./types";

type ActionTarget =
  | { kind: "plain"; to: string }
  | { kind: "inspect"; tab: InspectTabKey };

const ACTIONS: Record<string, { Icon: LucideIcon; target: ActionTarget }> = {
  "new-chat": { Icon: MessageSquare, target: { kind: "plain", to: "/chat" } },
  "open-terminal": {
    Icon: SquareTerminal,
    target: { kind: "inspect", tab: "shell" },
  },
  "tail-logcat": {
    Icon: ScrollText,
    target: { kind: "inspect", tab: "logcat" },
  },
  screenshot: {
    Icon: Camera,
    target: { kind: "inspect", tab: "screenshot" },
  },
  doctor: { Icon: HeartPulse, target: { kind: "plain", to: "/doctor" } },
};

const FALLBACK = ACTIONS["new-chat"]!;

interface Props {
  actions: QuickActionData[];
}

export function QuickActionRow({ actions }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="qa-row">
      {actions.map((a) => {
        const def = ACTIONS[a.key] ?? FALLBACK;
        const Icon = def.Icon;
        const linkProps =
          def.target.kind === "inspect"
            ? ({
                to: "/inspect/$tabKey",
                params: { tabKey: def.target.tab },
              } as const)
            : ({ to: def.target.to } as const);
        return (
          <Link key={a.key} {...linkProps} className="qa-card">
            <span className="qa-icon">
              <Icon size={18} aria-hidden={true} />
            </span>
            <span className="qa-text">
              <span className="qa-title">
                {lang === "zh" ? a.titleZh : a.title}
              </span>
              <span className="qa-sub">{lang === "zh" ? a.subZh : a.sub}</span>
            </span>
          </Link>
        );
      })}
    </div>
  );
}
