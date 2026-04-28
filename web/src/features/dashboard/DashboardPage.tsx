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
import type { Lang } from "../../stores/app";
import { useApp } from "../../stores/app";
import { ActivityTimeline } from "./ActivityTimeline";
import { DeviceStripCompact } from "./DeviceStripCompact";
import { KpiStrip } from "./KpiStrip";
import { LiveSessionCard } from "./LiveSessionCard";
import { LlmBackendCards } from "./LlmBackendCards";
import { QuickActionRow } from "./QuickActionRow";
import { RecentSessions } from "./RecentSessions";
import { useAuditStream } from "./useAuditStream";
import { useDevices } from "./useDevices";
import { useRecentSessions } from "./useSessions";
import {
  MOCK_BACKENDS,
  MOCK_LIVE,
  MOCK_QUICK_ACTIONS,
} from "./mockData";
import type { KpiCardData } from "./types";

export function DashboardPage() {
  const lang = useApp((s) => s.lang);
  const setDevice = useApp((s) => s.setDevice);
  const recent = useRecentSessions();
  const devices = useDevices();
  const audit = useAuditStream();
  const kpis = buildKpis(devices, recent, lang);

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
        <KpiStrip items={kpis} />
      </div>

      {/* === Devices compact strip === */}
      <div className="group-head">
        <h2>{lang === "zh" ? "设备" : "Devices"}</h2>
        <span className="meta">{deviceMeta(devices, lang)}</span>
        <span className="right">
          <a className="link-arrow" href="#all-devices">
            {lang === "zh" ? "所有设备" : "All devices"}
          </a>
        </span>
      </div>
      {devices.isLoading ? (
        <div className="dev-strip-state">
          {lang === "zh" ? "加载设备中…" : "Loading devices…"}
        </div>
      ) : devices.isError ? (
        <div className="dev-strip-state dev-strip-state--err">
          {lang === "zh"
            ? "无法获取设备列表（GET /devices 失败）"
            : "Couldn't load devices (GET /devices failed)"}
        </div>
      ) : devices.backendError ? (
        <div className="dev-strip-state dev-strip-state--err">
          {lang === "zh"
            ? `传输层不可用 · ${devices.backendError}`
            : `Transport unavailable · ${devices.backendError}`}
        </div>
      ) : devices.devices.length === 0 ? (
        <div className="dev-strip-state">
          {lang === "zh"
            ? `当前 transport：${devices.transportName ?? "—"} · 无设备`
            : `Active transport: ${devices.transportName ?? "—"} · no devices`}
        </div>
      ) : (
        <DeviceStripCompact devices={devices.devices} onSelect={setDevice} />
      )}

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
          {recent.isLoading ? (
            <div className="sess-card sess-card--state">
              {lang === "zh" ? "加载中…" : "Loading…"}
            </div>
          ) : recent.isError ? (
            <div className="sess-card sess-card--state sess-card--err">
              {lang === "zh"
                ? "无法获取会话列表（GET /sessions 失败）"
                : "Couldn't load sessions (GET /sessions failed)"}
            </div>
          ) : recent.sessions.length === 0 ? (
            <div className="sess-card sess-card--state">
              {lang === "zh"
                ? "尚无会话 · 进入 Chat 开一段试试"
                : "No sessions yet · open Chat to start one"}
            </div>
          ) : (
            <RecentSessions sessions={recent.sessions.slice(0, 5)} />
          )}
        </section>
      </div>

      {/* === Activity timeline === */}
      <div className="group-head">
        <h2>{lang === "zh" ? "最近动作" : "Recent activity"}</h2>
        <span className="meta">{auditMeta(audit, lang)}</span>
        <span className="right">
          <button
            type="button"
            className="link-arrow"
            onClick={audit.paused ? audit.resume : audit.pause}
            disabled={audit.status !== "open"}
            style={{ background: "none", border: "none", cursor: "pointer" }}
          >
            {audit.paused
              ? lang === "zh" ? "恢复实时" : "Resume"
              : lang === "zh" ? "暂停实时" : "Pause"}
          </button>
        </span>
      </div>
      {audit.status === "connecting" ? (
        <div className="dev-strip-state">
          {lang === "zh" ? "连接事件流…" : "Connecting to event stream…"}
        </div>
      ) : audit.status === "error" || audit.status === "closed" ? (
        <div className="dev-strip-state dev-strip-state--err">
          {lang === "zh"
            ? "事件流断开 · 自动重连中…"
            : "Event stream disconnected · reconnecting…"}
        </div>
      ) : audit.events.length === 0 ? (
        <div className="dev-strip-state">
          {lang === "zh"
            ? "近 30 分钟没有事件 · 跑个 chat 或 terminal 试试"
            : "No events in the last 30 minutes · run a chat or terminal"}
        </div>
      ) : (
        <ActivityTimeline events={audit.events} />
      )}

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

/** Compose the 4-tile KPI strip: 2 from real data + 2 still mocked. */
function buildKpis(
  devices: ReturnType<typeof useDevices>,
  recent: ReturnType<typeof useRecentSessions>,
  lang: Lang,
): KpiCardData[] {
  const total = devices.devices.length;
  const online = devices.devices.filter((d) => d.status === "online").length;
  const sessions = recent.sessions.length;
  const live = recent.sessions.filter((s) => s.status === "live").length;
  return [
    {
      label: "Devices",
      labelZh: "设备",
      value: devices.isLoading ? "—" : String(online),
      unit: total ? `/ ${total}` : undefined,
      deltaText: devices.transportName ?? "",
      deltaTextZh: devices.transportName ?? "",
    },
    {
      label: "Sessions",
      labelZh: "会话",
      value: recent.isLoading ? "—" : String(sessions),
      deltaText: live
        ? `${live} live`
        : sessions
          ? "all idle"
          : "—",
      deltaTextZh: live
        ? `${live} 进行中`
        : sessions
          ? "全部空闲"
          : "—",
    },
    {
      label: "MCP tools",
      labelZh: "MCP 工具",
      value: "21",
      deltaText: lang === "zh" ? "见 alb_describe" : "see alb_describe",
      deltaTextZh: "见 alb_describe",
    },
    {
      label: "LLM throughput",
      labelZh: "LLM 吞吐",
      value: "—",
      unit: "tok/s",
      deltaText: "待 /metrics",
      deltaTextZh: "待 /metrics",
    },
  ];
}

function auditMeta(
  vm: ReturnType<typeof useAuditStream>,
  lang: Lang,
): string {
  const count = vm.events.length;
  const live = vm.status === "open" && !vm.paused;
  const liveLabel = vm.paused
    ? (lang === "zh" ? "已暂停" : "paused")
    : vm.status === "open"
      ? (lang === "zh" ? "实时" : "live")
      : (lang === "zh" ? "连接中" : "connecting");
  if (lang === "zh") {
    return `近 30 分钟 · ${count} 条 · ${liveLabel}${live ? " ●" : ""}`;
  }
  return `last 30 minutes · ${count} events · ${liveLabel}${live ? " ●" : ""}`;
}

function deviceMeta(
  vm: ReturnType<typeof useDevices>,
  lang: Lang,
): string {
  if (vm.isLoading) return lang === "zh" ? "加载中…" : "Loading…";
  if (vm.isError) return lang === "zh" ? "请求失败" : "request failed";
  const total = vm.devices.length;
  const online = vm.devices.filter((d) => d.status === "online").length;
  const offline = vm.devices.filter((d) => d.status === "offline").length;
  if (total === 0) {
    return lang === "zh" ? "无设备 · 检查 transport" : "no devices · check transport";
  }
  return lang === "zh"
    ? `${online} 在线 · ${offline} 不可达 · ${vm.transportName ?? "—"}`
    : `${online} online · ${offline} offline · ${vm.transportName ?? "—"}`;
}
