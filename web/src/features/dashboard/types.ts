/**
 * Dashboard placeholder types.  Same shape as the future API responses
 * (GET /devices, GET /tunnels, GET /sessions, GET /metrics) so that
 * swapping mock data for real fetchers is a one-line change inside
 * each card.
 */

export type DeviceStatus = "online" | "warn" | "offline";
export type Transport = "adb-usb" | "adb-wifi" | "adb-tcp" | "uart" | "ssh";

export interface DeviceCardData {
  id: string;
  name: string;
  modelLine: string; // "Pixel 8 · Android 14"
  transport: Transport;
  transportLabel: string; // "adb usb", "uart 1.5M", etc.
  status: DeviceStatus;
  cpu: number | null; // 0..100, null when offline
  cpuTrend: number[]; // 0..100 series, last few samples
  cpuColor: "blue" | "green" | "orange";
  tempC: number | null; // °C, null when offline
  tempTrend: number[];
  tempColor: "blue" | "green" | "orange";
  /** Last-seen freeform note shown when offline. */
  offlineNote?: string;
}

export interface KpiCardData {
  label: string;
  labelZh: string;
  value: string;
  unit?: string;
  delta?: { sign: "up" | "down"; text: string };
  deltaText?: string;
  deltaTextZh?: string;
}

export interface LiveToolCallData {
  name: string;
  args: string; // pretty-printed JSON snippet
  state: "done" | "running" | "err";
  elapsedSec: number;
}

export interface LiveSessionData {
  active: boolean;
  deviceId: string;
  deviceTransport: string; // "uart"
  turn: number;
  elapsedHuman: string;
  elapsedHumanZh: string;
  prompt: string;
  promptZh: string;
  tools: LiveToolCallData[];
  tps: number;
  totalTokens: number;
  modelName: string;
  /** y-coordinates 0..36 for the throughput sparkline (60 s window). */
  tpsSpark: number[];
}

/** Static identity for a backend card.
 *
 * Runtime data (latency / probe status / model presence / errors)
 * lives on `BackendRuntimeState` in `useBackends.ts`, indexed by
 * `name`. Keeping the two shapes parallel lets the static manifest
 * (60 s refetch) stay stable while the per-backend health probe
 * (15 s refetch) churns independently.
 */
export interface BackendCardData {
  name: string;
  /** Headline subtitle — usually the configured model tag, falling
   * back to the registry description for planned backends. */
  model: string;
  /** Card layout mode. `up` shows the runtime stat row; `unconfigured`
   * shows the planned/registered placeholder. (`paused` reserved for
   * a future "explicitly disabled" state.) */
  status: "up" | "paused" | "unconfigured";
}

export interface RecentSessionData {
  id: string;
  glyph: string; // "A" / "T" / etc
  message: string;
  messageZh: string;
  turns: number;
  model: string;
  status: "live" | "ok" | "err";
}

export interface TimelineEventData {
  time: string; // "16:42:08"
  dot: "ok" | "orange" | "err" | "muted";
  text: string;
  textZh: string;
}

export interface QuickActionData {
  key: string;
  title: string;
  titleZh: string;
  sub: string;
  subZh: string;
}
