/**
 * Router assembly — code-based TanStack Router setup with the
 * RootLayout wrapping every route.  v2 module map (8 entries on the
 * activity bar): Dashboard / Chat / Terminal / Inspect / Playground /
 * Sessions / Files / Audit.  Only Dashboard and Chat are real today;
 * the rest render StubPage.
 */
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { ChatPage } from "./features/chat/ChatPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { RootLayout } from "./layouts/RootLayout";
import { StubPage } from "./routes/stub";

const rootRoute = createRootRoute({ component: RootLayout });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/dashboard" });
  },
});

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/dashboard",
  component: DashboardPage,
});

const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/chat",
  component: ChatPage,
});

const terminalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/terminal",
  component: () => (
    <StubPage
      title="Terminal"
      titleZh="Terminal 终端"
      summary="Interactive adb / serial shell via xterm.js with HITL command guard and optional read-only mode."
      summaryZh="xterm.js 直通 adb / 串口 shell；危险命令 HITL 拦截；可切只读模式。"
      consumes={["WS /terminal/ws"]}
    />
  ),
});

const inspectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/inspect",
  component: () => (
    <StubPage
      title="Inspect"
      titleZh="Inspect 检视"
      summary="Per-device drill-in: System info (11 panels), 1 Hz charts (CPU / mem / temp / disk / net / GPU), screenshots, UI dump, file browser."
      summaryZh="单设备深挖：系统信息 11 面板、1 Hz 实时图表、抓屏、UI 树、文件浏览。模块内子导航。"
      consumes={[
        "GET /devices/{id}/info",
        "WS /metrics/stream",
        "POST /devices/{id}/screenshot",
      ]}
    />
  ),
});

const playgroundRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/playground",
  component: () => (
    <StubPage
      title="Model Playground"
      titleZh="模型调试台"
      summary="Raw LLM chat bypassing the agent loop, with every sampling knob and live metrics."
      summaryZh="绕过 agent loop 的直聊 —— 所有采样参数可调、实时 tokens/s 指标。"
      consumes={[
        "GET /playground/backends",
        "GET /playground/backends/{backend}/models",
        "POST /playground/chat",
        "WS /playground/chat/ws",
      ]}
    />
  ),
});

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sessions",
  component: () => (
    <StubPage
      title="Sessions"
      titleZh="会话历史"
      summary="Browse / search / replay every JSONL agent session.  Resume an old turn, fork a session, export to share."
      summaryZh="所有 JSONL agent session 的浏览 / 搜索 / 回放。可续接旧轮、fork、导出。"
      consumes={["GET /sessions", "GET /sessions/{id}", "POST /sessions/{id}/resume"]}
    />
  ),
});

const filesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/files",
  component: () => (
    <StubPage
      title="Files"
      titleZh="文件浏览"
      summary="Push / pull / rsync between host and device with HITL on path changes outside /sdcard."
      summaryZh="主机 ↔ 设备 文件 push / pull / rsync；非 /sdcard 路径走 HITL。"
      consumes={["GET /devices/{id}/fs", "POST /devices/{id}/push", "POST /devices/{id}/pull"]}
    />
  ),
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: () => (
    <StubPage
      title="Audit"
      titleZh="审计日志"
      summary="Append-only log of every tool call, HITL decision, and session boundary.  Filter by device / actor / verdict."
      summaryZh="所有工具调用、HITL 决策、session 边界的 append-only 日志；按设备 / 操作者 / 结果筛。"
      consumes={["GET /audit", "WS /audit/stream"]}
    />
  ),
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  dashboardRoute,
  chatRoute,
  terminalRoute,
  inspectRoute,
  playgroundRoute,
  sessionsRoute,
  filesRoute,
  auditRoute,
]);

// Strip the deployment base (e.g. `/app/` in dev + alb-api mount, or
// `/android-llm-bridge/app/` on GitHub Pages) before matching routes,
// so route definitions stay deployment-agnostic.
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
