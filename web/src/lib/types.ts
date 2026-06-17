/**
 * Cross-cutting domain unions — used by lib utilities AND multiple
 * features. Put union types here so format / utility files can
 * import them without depending on a specific feature.
 *
 * Rule of thumb:
 *   - Atomic enum / union that mirrors a server-side concept → here
 *   - Composite view-model interfaces (DeviceCardData, KpiCardData
 *     etc.) → stay in the owning feature's types.ts
 *
 * `features/dashboard/types.ts` re-exports these for backward
 * compatibility with existing dashboard imports.
 */
export type DeviceStatus = "online" | "warn" | "offline";
// AR9-6: mirrors the transports the backend can actually emit
// (Adb / Ssh / Serial / Hybrid via type(t).__name__). The old
// adb-wifi / adb-tcp members had no producer — the backend can't
// distinguish adb-usb from wifi/tcp (all AdbTransport) — so they were
// dropped; `hybrid` was added (was being silently mislabeled adb-usb).
export type Transport = "adb-usb" | "uart" | "ssh" | "hybrid";
