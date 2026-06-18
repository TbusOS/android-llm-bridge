/**
 * Static quick-action labels for the Dashboard QuickActionRow.
 *
 * All MOCK_LIVE / MOCK_KPIS / MOCK_DEVICES / MOCK_BACKENDS / MOCK_SESSIONS
 * fixtures have been removed — every dashboard surface now reads from a
 * real fetcher (useDevices / useBackends / useAuditStream / useSessions /
 * useLiveSession / metrics stream). Only the quick-action labels remain
 * here because they're a static manifest mapping → known routes, not
 * data.
 */
import type { QuickActionData } from "./types";

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
  {
    key: "doctor",
    title: "Health check",
    titleZh: "环境检查",
    sub: "six-layer doctor probe",
    subZh: "六层 doctor 探测",
  },
  {
    key: "connections",
    title: "Connection Center",
    titleZh: "连接中心",
    sub: "remote agents · forwarders",
    subZh: "远程 agent · 转发器",
  },
];
