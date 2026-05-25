/**
 * Device transport / status formatting utilities.
 *
 * Pure functions, no React. Shared by the raw `useDevices` hook
 * (lib/hooks/) and any feature that wants to render a device pill
 * without pulling in dashboard's `DeviceCardData` view-model
 * (DevicePicker is the current second consumer).
 *
 * The `Transport` / `DeviceStatus` unions live here (and `features/
 * dashboard/types.ts` re-exports them) so the lib layer doesn't
 * reverse-depend on a feature.
 */
export type DeviceStatus = "online" | "warn" | "offline";
export type Transport = "adb-usb" | "adb-wifi" | "adb-tcp" | "uart" | "ssh";

/** Map a server-side Transport class name (e.g. "AdbUsbTransport") to
 *  the UI's Transport union. Unknown / null falls back to "adb-usb"
 *  so the strip still renders something. */
export function transportFromName(name: string | null | undefined): Transport {
  if (!name) return "adb-usb";
  if (name.includes("Ssh")) return "ssh";
  if (name.includes("Serial")) return "uart";
  return "adb-usb";
}

export function transportLabel(t: Transport): string {
  switch (t) {
    case "adb-usb":
      return "adb";
    case "adb-wifi":
      return "adb wifi";
    case "adb-tcp":
      return "adb tcp";
    case "uart":
      return "uart";
    case "ssh":
      return "ssh";
  }
}

export function statusFrom(state: string): DeviceStatus {
  if (state === "device") return "online";
  if (state === "offline" || state === "unauthorized") return "offline";
  return "warn";
}
