/**
 * Quick actions — auto-fit (220px min) grid of qa-card buttons.  Each
 * card has a 36-px rounded square icon (orange on subtle bg) + title +
 * sub-line.  Routes to the matching module.
 */
import { Link } from "@tanstack/react-router";
import {
  Camera,
  type LucideIcon,
  MessageSquare,
  ScrollText,
  SquareTerminal,
} from "lucide-react";
import { useApp } from "../../stores/app";
import type { QuickActionData } from "./types";

const ICONS: Record<string, { Icon: LucideIcon; to: string }> = {
  "new-chat": { Icon: MessageSquare, to: "/chat" },
  "open-terminal": { Icon: SquareTerminal, to: "/terminal" },
  "tail-logcat": { Icon: ScrollText, to: "/inspect" },
  screenshot: { Icon: Camera, to: "/inspect" },
};

interface Props {
  actions: QuickActionData[];
}

export function QuickActionRow({ actions }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="qa-row">
      {actions.map((a) => {
        const def = ICONS[a.key] ?? { Icon: MessageSquare, to: "/chat" };
        const Icon = def.Icon;
        return (
          <Link key={a.key} to={def.to} className="qa-card">
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
