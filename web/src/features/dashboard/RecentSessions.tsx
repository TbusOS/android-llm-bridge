/**
 * Recent sessions list — sess-card + sess-row grid (24px / 1fr / 90 /
 * 80 / 80).  Status pill at the right (live / ok / err).
 */
import { useApp } from "../../stores/app";
import type { RecentSessionData } from "./types";

interface Props {
  sessions: RecentSessionData[];
}

export function RecentSessions({ sessions }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="sess-card">
      {sessions.map((s) => (
        <div key={s.id} className="sess-row">
          <div className="sess-icon">{s.glyph}</div>
          <div className="sess-msg">
            {lang === "zh" ? s.messageZh : s.message}
          </div>
          <div className="sess-meta">
            {s.turns} {lang === "zh" ? "轮" : "turns"}
          </div>
          <div className="sess-meta">{s.model}</div>
          <div className={`sess-status sess-status--${s.status}`}>
            {labelStatus(s.status, lang)}
          </div>
        </div>
      ))}
    </div>
  );
}

function labelStatus(status: RecentSessionData["status"], lang: string): string {
  if (lang === "zh") {
    return status === "live" ? "进行中" : status === "ok" ? "完成" : "出错";
  }
  return status === "live" ? "live" : status === "ok" ? "done" : "error";
}
