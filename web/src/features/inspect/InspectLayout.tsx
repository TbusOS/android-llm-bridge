/**
 * InspectLayout — parent of `/inspect/$tabKey` nested routes.
 *
 * Renders:
 *   - Title / sub-line with current device
 *   - SubNav (12 tabs · horizontal scroll on narrow viewports)
 *   - `<Outlet />` for the active tab's component
 *
 * Each tab is mounted by its own child route (see `router.tsx`
 * `inspectTabRoutes`), so this file is now just chrome — there is no
 * 12-branch conditional. Tab keys are validated by the router; a bad
 * `$tabKey` redirects to `/inspect/system` via `inspectTabFallback`.
 *
 * Active-tab state comes from `useParams({ strict: false })`, the same
 * pattern used by `SessionDetailPage`.
 */
import { Outlet, useNavigate, useParams } from "@tanstack/react-router";
import { Suspense } from "react";
import { SubNav } from "../../components/SubNav";
import { type InspectTabKey } from "../../router";
import { useApp } from "../../stores/app";

export function InspectLayout() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const navigate = useNavigate();
  const params = useParams({ strict: false }) as { tabKey?: InspectTabKey };
  const active: InspectTabKey = params.tabKey ?? "system";

  const setTab = (next: InspectTabKey) => {
    navigate({ to: "/inspect/$tabKey", params: { tabKey: next } });
  };

  const tabs = [
    { key: "system" as const, label: lang === "zh" ? "系统信息" : "System Info" },
    { key: "charts" as const, label: lang === "zh" ? "实时图表" : "Charts" },
    { key: "uart" as const, label: lang === "zh" ? "UART 抓取" : "UART" },
    { key: "logcat" as const, label: lang === "zh" ? "Logcat 实时" : "Logcat" },
    { key: "shell" as const, label: lang === "zh" ? "Shell 终端" : "Shell" },
    { key: "screenshot" as const, label: lang === "zh" ? "屏幕截图" : "Screenshot" },
    { key: "ui-dump" as const, label: lang === "zh" ? "UI 树" : "UI Dump" },
    { key: "files" as const, label: lang === "zh" ? "文件" : "Files" },
    { key: "power" as const, label: lang === "zh" ? "电源" : "Power" },
    { key: "log-search" as const, label: lang === "zh" ? "日志搜索" : "Log Search" },
    { key: "diag" as const, label: lang === "zh" ? "诊断" : "Diag" },
    { key: "app" as const, label: lang === "zh" ? "应用" : "App" },
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

      <SubNav<InspectTabKey>
        tabs={tabs}
        active={active}
        onChange={setTab}
        ariaLabel={lang === "zh" ? "Inspect 子模块" : "Inspect sub-nav"}
      />

      {/*
        L-028: Suspense fallback minHeight roughly matches the heavier
        tab bodies (FilesTab / UartTab ~480-540 px) so first-time mount
        doesn't CLS by ~480 px.
      */}
      <Suspense
        fallback={
          <div className="mock-card" style={{ minHeight: 480 }}>
            loading…
          </div>
        }
      >
        <Outlet />
      </Suspense>
    </section>
  );
}
