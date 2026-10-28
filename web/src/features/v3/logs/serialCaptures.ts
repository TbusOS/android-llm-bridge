/**
 * Serial capture history for the Logs page.
 *
 * POST /uart/capture writes `<ts>-uart.log` under the workspace: into
 * `devices/<serial>/logs/` when an adb device was given, into `logs/` when
 * none was (a board in the bootloader, or with adb dead). Both folders are
 * listed together here, each entry tagged with the device it was filed
 * under, so a boot captured before adb came up does not vanish from the
 * list the moment the top bar picks the device.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchUartCaptures, readUartCapture, type UartCaptureSummary } from "../../../lib/api";

export interface SerialCapture extends UartCaptureSummary {
  /** The `device` the capture was filed under; null = no adb device. */
  owner: string | null;
}

export const capturesKey = (device: string | null) => ["uart-captures", device] as const;

export async function listSerialCaptures(device: string | null, signal?: AbortSignal): Promise<SerialCapture[]> {
  const [own, loose] = await Promise.all([device ? fetchUartCaptures(device, signal) : Promise.resolve(null), fetchUartCaptures(null, signal)]);
  const out: SerialCapture[] = [
    ...(own?.captures ?? []).map((c) => ({ ...c, owner: device })),
    ...(loose.captures ?? []).map((c) => ({ ...c, owner: null })),
  ];
  out.sort((a, b) => b.mtime - a.mtime);
  return out;
}

export function useSerialCaptures(device: string | null) {
  return useQuery({
    queryKey: capturesKey(device),
    queryFn: ({ signal }) => listSerialCaptures(device, signal),
    // Captures only appear when someone presses Capture; no polling.
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
}

export function useSerialCaptureText(cap: SerialCapture | null) {
  return useQuery({
    queryKey: ["uart-capture", cap?.owner ?? null, cap?.name ?? null],
    enabled: !!cap,
    queryFn: ({ signal }) => {
      if (!cap) throw new Error("no capture selected");
      return readUartCapture(cap.name, cap.owner, signal);
    },
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
}

/** Text -> lines, without the empty one a trailing newline leaves. */
export function textLines(text: string): string[] {
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}
