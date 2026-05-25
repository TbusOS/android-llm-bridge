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
export type Transport = "adb-usb" | "adb-wifi" | "adb-tcp" | "uart" | "ssh";
