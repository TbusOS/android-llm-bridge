/**
 * File browser pair-of-trees hook (PR-H).
 *
 * Wraps two `useQuery`s — one for the device path, one for the
 * workspace path. Pull/push transfers moved to
 * `useFileTransferStream` (MID-6, 2026-05-06) — that hook talks to
 * the streaming WS endpoints with progress + cancel. Callers
 * invalidate the query keys this module exposes after a successful
 * transfer to refresh the panes.
 */

import { useQuery } from "@tanstack/react-query";

import {
  listDeviceFiles,
  listWorkspaceFiles,
} from "../../lib/api";

const STALE_MS = 10_000;

export function useDeviceFiles(
  serial: string | null | undefined,
  path: string,
) {
  return useQuery({
    queryKey: ["device-files", serial, path],
    enabled: !!serial && !!path,
    staleTime: STALE_MS,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => {
      if (!serial) throw new Error("missing serial");
      return listDeviceFiles(serial, path, signal);
    },
  });
}

export function useWorkspaceFiles(path: string) {
  return useQuery({
    queryKey: ["workspace-files", path],
    enabled: true,
    staleTime: STALE_MS,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => listWorkspaceFiles(path, signal),
  });
}
