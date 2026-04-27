/**
 * Dashboard — the v2 home page.
 *
 * Container mirrors docs/webui-preview-v2.html section by section:
 *   1. h-title + h-sub
 *   2. .hero-row (LiveSessionCard + KpiStrip)
 *   3. .group-head + .dev-strip (DeviceStripCompact)
 *   4. 2-column row (LlmBackendCards | RecentSessions)
 *   5. .group-head + .timeline (ActivityTimeline)
 *   6. .group-head + .qa-row (QuickActionRow)
 *
 * Data is currently mock; each section's `data` prop will swap to a
 * real fetcher once /devices /tunnels /sessions /metrics ship.
 */
import { useApp } from "../../stores/app";
import { ActivityTimeline } from "./ActivityTimeline";
import { DeviceStripCompact } from "./DeviceStripCompact";
import { KpiStrip } from "./KpiStrip";
import { LiveSessionCard } from "./LiveSessionCard";
import { LlmBackendCards } from "./LlmBackendCards";
import { QuickActionRow } from "./QuickActionRow";
import { RecentSessions } from "./RecentSessions";
import {
  MOCK_BACKENDS,
  MOCK_DEVICES,
  MOCK_KPIS,
  MOCK_LIVE,
  MOCK_QUICK_ACTIONS,
  MOCK_SESSIONS,
  MOCK_TIMELINE,
} from "./mockData";

export function DashboardPage() {
  const lang = useApp((s) => s.lang);
  const setDevice = useApp((s) => s.setDevice);

  return (
    <section>
      <h1 className="h-title">{lang === "zh" ? "总览" : "Dashboard"}</h1>
      <p className="h-sub">
        {lang === "zh"
          ? "当前所有设备、本地 LLM、最近 session、刚发生过的事 —— 一屏看完。"
          : "Live status across every connected device, the local LLM, recent agent sessions, and what just happened."}
      </p>

      {/* === Hero: live session + KPI 2x2 === */}
      <div className="hero-row">
        <LiveSessionCard data={MOCK_LIVE} />
        <KpiStrip items={MOCK_KPIS} />
      </div>

      {/* === Devices compact strip === */}
      <div className="group-head">
        <h2>{lang === "zh" ? "设备" : "Devices"}</h2>
        <span className="meta">
          {lang === "zh"
            ? "3 在线 · 1 不可达 · 点卡片进入设备页"
            : "3 online · 1 unreachable · click to drill in"}
        </span>
        <span className="right">
          <a className="link-arrow" href="#all-devices">
            {lang === "zh" ? "所有设备" : "All devices"}
          </a>
        </span>
      </div>
      <DeviceStripCompact devices={MOCK_DEVICES} onSelect={setDevice} />

      {/* === LLM backends + Recent sessions side-by-side === */}
      <div className="dash-2col">
        <section>
          <div className="group-head" style={{ marginTop: 0 }}>
            <h2>{lang === "zh" ? "LLM 后端" : "LLM backends"}</h2>
            <span className="meta">
              {lang === "zh"
                ? "1 本地 · 1 暂停 · 2 未配置"
                : "1 local · 1 paused · 2 unconfigured"}
            </span>
          </div>
          <LlmBackendCards backends={MOCK_BACKENDS} />
        </section>

        <section>
          <div className="group-head" style={{ marginTop: 0 }}>
            <h2>{lang === "zh" ? "近期会话" : "Recent sessions"}</h2>
            <span className="right">
              <a className="link-arrow" href="#sessions">
                {lang === "zh" ? "查看全部" : "Open Sessions"}
              </a>
            </span>
          </div>
          <RecentSessions sessions={MOCK_SESSIONS} />
        </section>
      </div>

      {/* === Activity timeline === */}
      <div className="group-head">
        <h2>{lang === "zh" ? "最近动作" : "Recent activity"}</h2>
        <span className="meta">
          {lang === "zh"
            ? "近 30 分钟 · agent / 终端 / 工具调用"
            : "last 30 minutes · agent + terminal + tools"}
        </span>
        <span className="right">
          <a className="link-arrow" href="#audit">
            {lang === "zh" ? "打开审计" : "Open Audit"}
          </a>
        </span>
      </div>
      <ActivityTimeline events={MOCK_TIMELINE} />

      {/* === Quick actions === */}
      <div className="group-head">
        <h2>{lang === "zh" ? "快捷操作" : "Quick actions"}</h2>
        <span className="meta">
          {lang === "zh"
            ? "对当前选中设备的一键操作"
            : "one-click verbs against the active device"}
        </span>
      </div>
      <QuickActionRow actions={MOCK_QUICK_ACTIONS} />
    </section>
  );
}
