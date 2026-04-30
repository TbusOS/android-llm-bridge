/**
 * Placeholder data for the Dashboard.  Mirrors the values in
 * docs/webui-preview-v2.html.  Each section should be replaced by a
 * real fetcher (GET /devices, /tunnels, /sessions, /metrics) once the
 * backend exposes those endpoints — see project_status.md "下一步".
 */
import type {
  BackendCardData,
  DeviceCardData,
  KpiCardData,
  LiveSessionData,
  QuickActionData,
  RecentSessionData,
  TimelineEventData,
} from "./types";

export const MOCK_LIVE: LiveSessionData = {
  active: true,
  deviceId: "rk-board-7c",
  deviceTransport: "uart",
  turn: 3,
  elapsedHuman: "1m 42s",
  elapsedHumanZh: "1m 42s",
  prompt:
    '"Find what\'s draining the battery on this device — list the top 5 wakelocks and any wakeup sources from the last 30 min."',
  promptZh: '"查一下这台设备是什么在耗电 —— 列最近 30 分钟前 5 个 wakelock 和唤醒源。"',
  tools: [
    { name: "alb_dumpsys_battery", args: '{ since: "30m" }', state: "done", elapsedSec: 1.2 },
    { name: "alb_top", args: '{ sort: "cpu", n: 10 }', state: "done", elapsedSec: 0.8 },
    {
      name: "alb_dumpsys_power",
      args: '{ section: "wakelocks" }',
      state: "running",
      elapsedSec: 2.4,
    },
  ],
  tps: 42,
  totalTokens: 1247,
  modelName: "qwen2.5:7b",
  tpsSpark: [28, 26, 22, 24, 18, 20, 14, 18, 12, 15, 10, 13, 9, 12, 8],
};

export const MOCK_KPIS: KpiCardData[] = [
  {
    label: "Devices",
    labelZh: "设备",
    value: "3",
    unit: "/ 4",
    delta: { sign: "up", text: "▲ 1" },
    deltaText: "vs yesterday",
    deltaTextZh: "较昨日",
  },
  {
    label: "Sessions",
    labelZh: "会话",
    value: "2",
    deltaText: "1 live · 1 idle",
    deltaTextZh: "1 进行 · 1 空闲",
  },
  {
    label: "MCP tools",
    labelZh: "MCP 工具",
    value: "21",
    deltaText: "3 need HITL",
    deltaTextZh: "3 个需 HITL",
  },
  {
    label: "LLM throughput",
    labelZh: "LLM 吞吐",
    value: "38",
    unit: "tok/s",
    delta: { sign: "up", text: "▲ 4%" },
    deltaText: "5 min avg",
    deltaTextZh: "5 分钟均值",
  },
];

export const MOCK_DEVICES: DeviceCardData[] = [
  {
    id: "aosp-emu-01",
    name: "aosp-emu-01",
    modelLine: "Pixel 8 · Android 14",
    transport: "adb-usb",
    transportLabel: "adb usb",
    status: "online",
    cpu: 42,
    cpuTrend: [18, 15, 13, 15, 11, 8, 11, 6, 8, 5, 3],
    cpuColor: "blue",
    tempC: 46,
    tempTrend: [12, 11, 11, 10, 9, 8],
    tempColor: "green",
  },
  {
    id: "rk-board-7c",
    name: "rk-board-7c",
    modelLine: "ARM board · Debian",
    transport: "uart",
    transportLabel: "uart 1.5M",
    status: "warn",
    cpu: 88,
    cpuTrend: [12, 9, 6, 8, 4, 5, 2, 3, 2, 1, 2],
    cpuColor: "orange",
    tempC: 63,
    tempTrend: [14, 12, 10, 8, 7, 5],
    tempColor: "orange",
  },
  {
    id: "qa-tablet",
    name: "qa-tablet",
    modelLine: "Tablet · Android 14",
    transport: "adb-wifi",
    transportLabel: "adb wifi",
    status: "online",
    cpu: 12,
    cpuTrend: [18, 17, 16, 17, 15, 16],
    cpuColor: "blue",
    tempC: 38,
    tempTrend: [15, 15, 14, 14, 13, 13],
    tempColor: "green",
  },
  {
    id: "vbox-stable",
    name: "vbox-stable",
    modelLine: "VM · Android 13",
    transport: "adb-tcp",
    transportLabel: "adb tcp",
    status: "online",
    cpu: 28,
    cpuTrend: [16, 14, 15, 12, 13, 11],
    cpuColor: "blue",
    tempC: 41,
    tempTrend: [17, 16, 16, 16, 15, 15],
    tempColor: "green",
  },
  {
    id: "build-bot-09",
    name: "build-bot-09",
    modelLine: "",
    transport: "ssh",
    transportLabel: "ssh",
    status: "offline",
    cpu: null,
    cpuTrend: [],
    cpuColor: "blue",
    tempC: null,
    tempTrend: [],
    tempColor: "blue",
    offlineNote: "unreachable · 14 min ago",
  },
];

/**
 * Dev fixture only — production uses `useBackends` (closes DEBT-002 as
 * of 2026-04-29). Kept here so storybook / visual regression / future
 * vitest snapshots have stable seed data without hitting alb-api.
 */
export const MOCK_BACKENDS: BackendCardData[] = [
  { name: "ollama", model: "qwen2.5:7b", status: "up" },
  { name: "openai-compat", model: "lm-studio · gemma3", status: "unconfigured" },
];

export const MOCK_SESSIONS: RecentSessionData[] = [
  {
    id: "s1",
    glyph: "A",
    message: '"Find what\'s draining the battery on rk-board-7c"',
    messageZh: '"查一下 rk-board-7c 是什么在耗电"',
    turns: 12,
    model: "qwen2.5",
    status: "live",
  },
  {
    id: "s2",
    glyph: "A",
    message: '"Pull the last logcat error window from aosp-emu-01"',
    messageZh: '"把 aosp-emu-01 最近一次 logcat 错误段拉出来"',
    turns: 4,
    model: "qwen2.5",
    status: "ok",
  },
  {
    id: "s3",
    glyph: "A",
    message: '"Bench start-up time on the new build"',
    messageZh: '"测一下新版本的启动耗时"',
    turns: 7,
    model: "qwen2.5",
    status: "ok",
  },
  {
    id: "s4",
    glyph: "A",
    message: '"Why does setprop persist.foo trigger HITL?"',
    messageZh: '"为什么 setprop persist.foo 会触发 HITL？"',
    turns: 2,
    model: "qwen2.5",
    status: "err",
  },
];

export const MOCK_TIMELINE: TimelineEventData[] = [
  {
    time: "16:42:08",
    dot: "ok",
    text: "tool call <code>alb_logcat</code> completed · 618 lines · <em>aosp-emu-01</em>",
    textZh: "工具 <code>alb_logcat</code> 完成 · 618 行 · <em>aosp-emu-01</em>",
  },
  {
    time: "16:41:47",
    dot: "orange",
    text: "HITL approved · <code>rm /data/cache/*.tmp</code> on <em>aosp-emu-01</em>",
    textZh: "HITL 放行 · <code>rm /data/cache/*.tmp</code> · <em>aosp-emu-01</em>",
  },
  {
    time: "16:39:12",
    dot: "err",
    text: "build-bot-09 ssh tunnel dropped · <em>retrying in 30 s</em>",
    textZh: "build-bot-09 ssh 隧道断开 · <em>30 秒后重试</em>",
  },
  {
    time: "16:35:22",
    dot: "ok",
    text: "screenshot saved · <code>workspace/aosp-emu-01/2026-04-24T16-35-22.png</code>",
    textZh: "截图已保存 · <code>workspace/aosp-emu-01/2026-04-24T16-35-22.png</code>",
  },
  {
    time: "16:33:02",
    dot: "ok",
    text: "session <code>20260424-c4e2…</code> resumed · <em>3 turns added</em>",
    textZh: "会话 <code>20260424-c4e2…</code> 续接 · <em>新增 3 轮</em>",
  },
];

export const MOCK_QUICK_ACTIONS: QuickActionData[] = [
  {
    key: "new-chat",
    title: "New chat",
    titleZh: "新建 Chat",
    sub: "agent loop · tools enabled",
    subZh: "Agent 模式 · 工具已开",
  },
  {
    key: "open-terminal",
    title: "Open terminal",
    titleZh: "打开终端",
    sub: "PTY · adb shell",
    subZh: "PTY · adb shell",
  },
  {
    key: "tail-logcat",
    title: "Tail logcat",
    titleZh: "实时 logcat",
    sub: "level E · since boot",
    subZh: "level E · 自开机",
  },
  {
    key: "screenshot",
    title: "Take screenshot",
    titleZh: "抓屏",
    sub: "save to workspace",
    subZh: "保存到 workspace",
  },
];
