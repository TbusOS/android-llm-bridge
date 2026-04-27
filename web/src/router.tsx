/**
 * Router assembly — code-based TanStack Router setup with the
 * RootLayout wrapping every route.  File-based routing via the
 * @tanstack/router-plugin is a future polish; for now the explicit
 * route tree is easy to read.
 */
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { ChatPage } from "./features/chat/ChatPage";
import { RootLayout } from "./layouts/RootLayout";
import { StubPage } from "./routes/stub";

const rootRoute = createRootRoute({ component: RootLayout });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/devices" });
  },
});

const devicesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/devices",
  component: () => (
    <StubPage
      title="Devices"
      titleZh="设备"
      summary="Lists every connected device (adb USB / adb Wi-Fi / UART bridge) with tunnel state and Windows probe feed."
      summaryZh="列出所有连上的设备（USB adb / Wi-Fi adb / UART 桥），显示隧道状态和 Windows 探针数据。"
      consumes={["GET /devices", "GET /tunnels", "WS /devices/live"]}
    />
  ),
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

const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  component: () => (
    <StubPage
      title="System Info"
      titleZh="系统信息"
      summary="11 structured panels covering OS / kernel / CPU / GPU / memory / storage / network / battery / security / display / packages / processes."
      summaryZh="11 个结构化面板 —— 系统 / 内核 / CPU / GPU / 内存 / 存储 / 网络 / 电池 / 安全 / 显示 / 应用 / 进程。"
      consumes={["GET /devices/{id}/info", "alb info all (WIP REST)"]}
    />
  ),
});

const chartsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/charts",
  component: () => (
    <StubPage
      title="Real-time Charts"
      titleZh="实时图表"
      summary="1 Hz telemetry — CPU / memory / temp / disk / network / GPU — via µPlot with a sliding 60 s window."
      summaryZh="1 Hz 实时 —— CPU / 内存 / 温度 / 磁盘 / 网络 / GPU —— µPlot 渲染 60 秒滑窗。"
      consumes={["WS /metrics/stream"]}
    />
  ),
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  devicesRoute,
  chatRoute,
  terminalRoute,
  playgroundRoute,
  systemRoute,
  chartsRoute,
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
