/**
 * Inspect — per-device drill-in module.  Five sub-tabs:
 *   System Info / Charts / Screenshot / UI Dump / Files.
 *
 * SubNav state lives in component-local useState (not in the URL or
 * Zustand) — Inspect is always entered from the activity bar so a
 * sub-tab doesn't deserve a route segment yet.  When Screenshot / UI
 * Dump / Files ship with deep links, they should each become nested
 * routes under /inspect/...
 */
import { useState } from "react";
import { SubNav } from "../../components/SubNav";
import { useApp } from "../../stores/app";
import { ChartsTab } from "./ChartsTab";
import { LogcatTab } from "./LogcatTab";
import { ShellTab } from "./ShellTab";
import { SystemInfoTab } from "./SystemInfoTab";
import { UartTab } from "./UartTab";

type TabKey =
  | "system"
  | "charts"
  | "uart"
  | "logcat"
  | "shell"
  | "screenshot"
  | "ui-dump"
  | "files";

export function InspectPage() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [tab, setTab] = useState<TabKey>("system");

  const tabs = [
    {
      key: "system" as TabKey,
      label: lang === "zh" ? "系统信息" : "System Info",
    },
    {
      key: "charts" as TabKey,
      label: lang === "zh" ? "实时图表" : "Charts",
    },
    {
      key: "uart" as TabKey,
      label: lang === "zh" ? "UART 抓取" : "UART",
    },
    {
      key: "logcat" as TabKey,
      label: lang === "zh" ? "Logcat 实时" : "Logcat",
    },
    {
      key: "shell" as TabKey,
      label: lang === "zh" ? "Shell 终端" : "Shell",
    },
    {
      key: "screenshot" as TabKey,
      label: lang === "zh" ? "屏幕截图" : "Screenshot",
    },
    { key: "ui-dump" as TabKey, label: lang === "zh" ? "UI 树" : "UI Dump" },
    { key: "files" as TabKey, label: lang === "zh" ? "文件" : "Files" },
  ];

  return (
    <section>
      <h1 className="h-title">{lang === "zh" ? "Inspect 检视" : "Inspect"}</h1>
      <p className="h-sub">
        {device ? (
          lang === "zh" ? (
            <>
              当前设备：<code>{device}</code> · 系统信息 / 1 Hz 图表 / 抓屏 /
              UI 树 / 文件
            </>
          ) : (
            <>
              Active device: <code>{device}</code> · system info, 1 Hz charts,
              screenshots, UI dump, files.
            </>
          )
        ) : lang === "zh" ? (
          "未选择设备 —— 顶栏的设备选择器选一个，再回这里查看。"
        ) : (
          "No device selected — pick one from the top-bar device picker, then come back."
        )}
      </p>

      <SubNav<TabKey>
        tabs={tabs}
        active={tab}
        onChange={setTab}
        ariaLabel={lang === "zh" ? "Inspect 子模块" : "Inspect sub-nav"}
      />

      {tab === "system" ? <SystemInfoTab /> : null}
      {tab === "charts" ? <ChartsTab /> : null}
      {tab === "uart" ? <UartTab /> : null}
      {tab === "logcat" ? <LogcatTab /> : null}
      {tab === "shell" ? <ShellTab /> : null}
      {tab === "screenshot" ? <PendingTab kind="screenshot" /> : null}
      {tab === "ui-dump" ? <PendingTab kind="ui-dump" /> : null}
      {tab === "files" ? <PendingTab kind="files" /> : null}
    </section>
  );
}

function PendingTab({ kind }: { kind: "screenshot" | "ui-dump" | "files" }) {
  const lang = useApp((s) => s.lang);
  const en = {
    screenshot: {
      title: "Screenshot",
      sub: "On-demand framebuffer capture — saves to workspace/<device>/, served back inline.  Endpoint: POST /devices/{id}/screenshot.",
    },
    "ui-dump": {
      title: "UI Dump",
      sub: "uiautomator dump — view-tree explorer with bounds overlay on a freshly-captured screenshot.  Endpoint: POST /devices/{id}/ui-dump.",
    },
    files: {
      title: "Files",
      sub: "Browse, push, pull, rsync.  HITL gates non-/sdcard writes.  See the Files top-level module too — this tab is the device-scoped lite version.",
    },
  } as const;
  const zh = {
    screenshot: {
      title: "屏幕截图",
      sub: "按需 framebuffer 抓屏，存到 workspace/<device>/，回传内联展示。端点：POST /devices/{id}/screenshot。",
    },
    "ui-dump": {
      title: "UI 树",
      sub: "uiautomator dump —— view-tree 浏览，新抓的截图上叠 bounds 高亮。端点：POST /devices/{id}/ui-dump。",
    },
    files: {
      title: "文件",
      sub: "浏览 / push / pull / rsync。非 /sdcard 路径走 HITL。和顶层 Files 模块共用底层；这个 tab 是当前设备视角的精简版。",
    },
  } as const;
  const t = lang === "zh" ? zh[kind] : en[kind];

  return (
    <div className="mock-card">
      <div className="section-head">
        <h1 style={{ fontSize: 22 }}>{t.title}</h1>
        <span className="status-pill status-pill--plan">
          {lang === "zh" ? "待实现" : "Planned"}
        </span>
      </div>
      <p className="section-sub" style={{ marginBottom: 0 }}>
        {t.sub}
      </p>
    </div>
  );
}
