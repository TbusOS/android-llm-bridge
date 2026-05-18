/**
 * Inspect — per-device drill-in module. Eight sub-tabs:
 *   System Info / Charts / UART / Logcat / Shell / Screenshot /
 *   UI Dump / Files.
 *
 * SubNav state syncs with `?tab=<key>` in the URL so that:
 *   - Dashboard QuickActionRow / external links can deep-link straight
 *     to a tab (e.g. `/inspect?tab=logcat`);
 *   - browser back/forward navigates between tabs;
 *   - on a clean `/inspect` URL we default to `system`.
 *
 * Eventually each tab should become a nested route under `/inspect/...`
 * (DEBT: SubNav-as-routes refactor) but the `?tab=` query is enough for
 * the wire-up of the four QuickActions on the Dashboard.
 */
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Suspense, lazy } from "react";
import { SubNav } from "../../components/SubNav";
import { useApp } from "../../stores/app";

// Lazy-load each tab so the heavy ones (UART/Shell pull xterm.js =
// ~80 KB gzip, Charts pulls Sparkline + chart styles) only land when
// the user opens that tab. Keeps first-paint of /inspect lean.
const SystemInfoTab = lazy(() =>
  import("./SystemInfoTab").then((m) => ({ default: m.SystemInfoTab })),
);
const ChartsTab = lazy(() =>
  import("./ChartsTab").then((m) => ({ default: m.ChartsTab })),
);
const UartTab = lazy(() =>
  import("./UartTab").then((m) => ({ default: m.UartTab })),
);
const LogcatTab = lazy(() =>
  import("./LogcatTab").then((m) => ({ default: m.LogcatTab })),
);
const ShellTab = lazy(() =>
  import("./ShellTab").then((m) => ({ default: m.ShellTab })),
);
const ScreenshotTab = lazy(() =>
  import("./ScreenshotTab").then((m) => ({ default: m.ScreenshotTab })),
);
const UiDumpTab = lazy(() =>
  import("./UiDumpTab").then((m) => ({ default: m.UiDumpTab })),
);
const FilesTab = lazy(() =>
  import("./FilesTab").then((m) => ({ default: m.FilesTab })),
);
const PowerTab = lazy(() =>
  import("./PowerTab").then((m) => ({ default: m.PowerTab })),
);
const LogSearchTab = lazy(() =>
  import("./LogSearchTab").then((m) => ({ default: m.LogSearchTab })),
);
const DiagTab = lazy(() =>
  import("./DiagTab").then((m) => ({ default: m.DiagTab })),
);

type TabKey =
  | "system"
  | "charts"
  | "uart"
  | "logcat"
  | "shell"
  | "screenshot"
  | "ui-dump"
  | "files"
  | "power"
  | "log-search"
  | "diag";

export function InspectPage() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  // `strict: false` lets us read the search param without coupling to
  // the route id (avoids a circular import with router.tsx). The
  // route's validateSearch has already narrowed `tab` to a known key
  // or stripped it.
  const search = useSearch({ strict: false }) as { tab?: TabKey };
  const navigate = useNavigate();
  const tab: TabKey = search.tab ?? "system";
  const setTab = (next: TabKey) => {
    navigate({
      to: "/inspect",
      search: next === "system" ? {} : { tab: next },
    });
  };

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
    { key: "power" as TabKey, label: lang === "zh" ? "电源" : "Power" },
    {
      key: "log-search" as TabKey,
      label: lang === "zh" ? "日志搜索" : "Log Search",
    },
    { key: "diag" as TabKey, label: lang === "zh" ? "诊断" : "Diag" },
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

      {/*
        L-028: Suspense fallback minHeight must roughly match the
        lazy children's actual rendered height (FilesTab/UartTab
        have min-height: 480-540px). Without this, first-time tab
        switch would CLS by ~480px.
      */}
      <Suspense
        fallback={
          <div className="mock-card" style={{ minHeight: 480 }}>
            loading…
          </div>
        }
      >
        {tab === "system" ? <SystemInfoTab /> : null}
        {tab === "charts" ? <ChartsTab /> : null}
        {tab === "uart" ? <UartTab /> : null}
        {tab === "logcat" ? <LogcatTab /> : null}
        {tab === "shell" ? <ShellTab /> : null}
        {tab === "screenshot" ? <ScreenshotTab /> : null}
        {tab === "ui-dump" ? <UiDumpTab /> : null}
        {tab === "files" ? <FilesTab /> : null}
        {tab === "power" ? <PowerTab /> : null}
        {tab === "log-search" ? <LogSearchTab /> : null}
        {tab === "diag" ? <DiagTab /> : null}
      </Suspense>
    </section>
  );
}
