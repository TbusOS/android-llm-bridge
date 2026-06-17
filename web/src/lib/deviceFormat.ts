/**
 * Device transport / status formatting utilities.
 *
 * Pure functions, no React. Shared by the raw `useDevices` hook
 * (lib/hooks/) and any feature that wants to render a device pill
 * without pulling in dashboard's `DeviceCardData` view-model
 * (DevicePicker is the current second consumer).
 *
 * AL-2: `Transport` / `DeviceStatus` unions moved to `lib/types.ts`
 * so this file can be pure functions only.
 */
import type { Transport, DeviceStatus } from "./types";

export type { Transport, DeviceStatus };

/** Map a server-side Transport class name (e.g. "AdbUsbTransport") to
 *  the UI's Transport union. Unknown / null falls back to "adb-usb"
 *  so the strip still renders something. */
export function transportFromName(name: string | null | undefined): Transport {
  if (!name) return "adb-usb";
  if (name.includes("Ssh")) return "ssh";
  if (name.includes("Serial")) return "uart";
  // AR9-6: HybridTransport used to fall through to adb-usb (silent
  // mislabel) — map it to its own pill.
  if (name.includes("Hybrid")) return "hybrid";
  return "adb-usb";
}

export function transportLabel(t: Transport): string {
  switch (t) {
    case "adb-usb":
      return "adb";
    case "uart":
      return "uart";
    case "ssh":
      return "ssh";
    case "hybrid":
      return "hybrid";
  }
}

export function statusFrom(state: string): DeviceStatus {
  if (state === "device") return "online";
  if (state === "offline" || state === "unauthorized") return "offline";
  return "warn";
}
