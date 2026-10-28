/**
 * Router (v3). One top-level route per page in the navigation map
 * (components/v3/nav.ts); the v2 /inspect/<tab> and /terminal addresses
 * redirect to their new homes so old links and bookmarks keep working.
 *
 * Cross-repo invariant (DEBT-014, 2026-04-29): every `path:` value
 * below MUST NOT contain `.` in any segment. The backend SPA fallback
 * (`src/alb/api/ui_static.py SPAStaticFiles`) uses presence of `.` in
 * the last path segment to distinguish "missing asset" from "SPA
 * route", so a route like `/app/v2.0/foo` would be misclassified as
 * an asset 404. See `.claude/knowledge/architecture.md` 关键不变量.
 */
import { createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { lazy } from "react";

import { INSPECT_REDIRECTS, inspectTarget } from "./components/v3/nav";
import { AuditPage } from "./features/audit/AuditPage";
import { ConnectionsPage } from "./features/connections/ConnectionsPage";
import { DoctorPage } from "./features/doctor/DoctorPage";
import { PlaygroundPage } from "./features/playground/PlaygroundPage";
import { SessionDetailPage } from "./features/session/SessionDetailPage";
import { SessionsListPage } from "./features/session/SessionsListPage";
import { LegacyPage } from "./features/v3/LegacyPage";
import { OverviewPage } from "./features/v3/overview/OverviewPage";
import { AppShell } from "./layouts/AppShell";

/** v2 Inspect tab keys — kept for the redirects and older imports. */
export type InspectTabKey =
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
  | "diag"
  | "app"
  | "flash";
export const INSPECT_TAB_KEYS = Object.keys(INSPECT_REDIRECTS) as InspectTabKey[];

const lazyNamed = <K extends string>(load: () => Promise<Record<K, React.ComponentType>>, name: K) =>
  lazy(() => load().then((m) => ({ default: m[name] })));

const ConsolePage = lazyNamed(() => import("./features/v3/console/ConsolePage"), "ConsolePage");
const LogsPage = lazyNamed(() => import("./features/v3/logs/LogsPage"), "LogsPage");
const TimelinePage = lazyNamed(() => import("./features/v3/timeline/TimelinePage"), "TimelinePage");
const FlashPage = lazyNamed(() => import("./features/v3/flash/FlashPage"), "FlashPage");
const BenchPage = lazyNamed(() => import("./features/v3/bench/BenchPage"), "BenchPage");
const ChatPage3 = lazyNamed(() => import("./features/v3/chat/ChatPage3"), "ChatPage3");
const ProblemsPage = lazyNamed(() => import("./features/v3/problems/ProblemsPage"), "ProblemsPage");
const ScreenPage = lazyNamed(() => import("./features/v3/screen/ScreenPage"), "ScreenPage");

const ChartsTab = lazyNamed(() => import("./features/inspect/ChartsTab"), "ChartsTab");
const FilesTab = lazyNamed(() => import("./features/inspect/FilesTab"), "FilesTab");
const AppTab = lazyNamed(() => import("./features/inspect/AppTab"), "AppTab");
const SystemInfoTab = lazyNamed(() => import("./features/inspect/SystemInfoTab"), "SystemInfoTab");
const PowerTab = lazyNamed(() => import("./features/inspect/PowerTab"), "PowerTab");
const BoardConfigCard = lazyNamed(() => import("./features/inspect/BoardConfigCard"), "BoardConfigCard");

const rootRoute = createRootRoute({ component: AppShell });

const page = (path: string, C: React.ComponentType, validateSearch?: (s: Record<string, unknown>) => Record<string, unknown>) =>
  createRoute({ getParentRoute: () => rootRoute, path, component: () => <C />, ...(validateSearch ? { validateSearch } : {}) });

const legacy = (path: string, zh: string, en: string, descZh: string, descEn: string, C: React.ComponentType, needsDevice = true) =>
  page(path, () => (
    <LegacyPage zh={zh} en={en} descZh={descZh} descEn={descEn} needsDevice={needsDevice}>
      <C />
    </LegacyPage>
  ));

const redirectRoute = (path: string, to: string) =>
  createRoute({
    getParentRoute: () => rootRoute,
    path,
    beforeLoad: () => {
      throw redirect({ to, replace: true });
    },
  });

const routeTree = rootRoute.addChildren([
  redirectRoute("/", "/dashboard"),
  page("/dashboard", OverviewPage),
  page("/console", ConsolePage),
  page("/logs", LogsPage, (s) => ({ tab: typeof s.tab === "string" ? s.tab : undefined })),
  page("/timeline", TimelinePage, (s) => ({
    record: s.record === "boot" ? "boot" : undefined,
    name: typeof s.name === "string" ? s.name : undefined,
  })),
  legacy("/perf", "性能", "Performance", "CPU、内存、温度、网络、磁盘的实时曲线。", "Live CPU, memory, temperature, network and disk.", ChartsTab),
  legacy("/files", "文件", "Files", "板子上的文件和工作区的文件,推送和拉取。", "Files on the board and in the workspace; push and pull.", FilesTab),
  legacy("/apps", "应用", "Apps", "安装、启动、停止、清数据和卸载应用。", "Install, start, stop, clear and uninstall apps.", AppTab),
  page("/screen", ScreenPage, (s) => ({ tab: s.tab === "shot" || s.tab === "tree" ? s.tab : undefined })),
  page("/problems", ProblemsPage),
  legacy("/info", "系统信息", "System info", "属性、分区、挂载和块设备。", "Properties, partitions, mounts and block devices.", SystemInfoTab),
  legacy("/power", "电源", "Power", "电池、休眠唤醒测试和重启。", "Battery, sleep / wake tests and reboot.", PowerTab),
  page("/flash", FlashPage),
  legacy("/board-config", "配置分区", "Board config", "从已开机的板子读回配置分区,解析成 KEY=VALUE。", "Read the config partition back from a booted board, parsed as KEY=VALUE.", BoardConfigCard),
  page("/bench", BenchPage),
  page("/chat", ChatPage3),
  page("/playground", PlaygroundPage),
  page("/sessions", SessionsListPage),
  createRoute({ getParentRoute: () => rootRoute, path: "/sessions/$sessionId", component: SessionDetailPage }),
  page("/doctor", DoctorPage),
  page("/audit", AuditPage),
  page("/connections", ConnectionsPage),
  redirectRoute("/terminal", "/console"),
  // v2 bookmarks: `/inspect?tab=<key>` and `/inspect/<key>` both land on
  // the page that took that tab over, on the matching tab of that page.
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/inspect",
    validateSearch: (s: Record<string, unknown>) => ({ tab: typeof s.tab === "string" ? s.tab : undefined }),
    beforeLoad: ({ search }) => {
      const { to, search: next } = inspectTarget(search.tab);
      throw redirect({ to, search: next as never, replace: true });
    },
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/inspect/$tabKey",
    beforeLoad: ({ params }) => {
      const { to, search: next } = inspectTarget(params.tabKey);
      throw redirect({ to, search: next as never, replace: true });
    },
  }),
]);

/** The route tree, exported so tests can drive the redirects on a memory history. */
export { routeTree };

const RAW_BASE = import.meta.env.BASE_URL || "/";
const BASEPATH = RAW_BASE === "/" ? "" : RAW_BASE.replace(/\/$/, "");

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  basepath: BASEPATH || undefined,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
