/**
 * File browser pair-of-trees hook (PR-H).
 *
 * Wraps two `useQuery`s — one for the device path, one for the
 * workspace path — and exposes mutations for pull / push.
 *
 * Listings are cached briefly (10s) so toggling between tabs doesn't
 * re-run `ls` every time, but mutations invalidate both sides so the
 * UI always reflects the latest state after a transfer.
 */

import { useCallback } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  type PullResponse,
  type PushResponse,
  listDeviceFiles,
  listWorkspaceFiles,
  pullDeviceFile,
  pushDeviceFile,
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

export interface PullArgs {
  serial: string;
  remote: string;
  /** Workspace-relative; if omitted, server lands under pulls/. */
  local?: string;
}

export interface PushArgs {
  serial: string;
  local: string;
  remote: string;
  force?: boolean;
}

/** Mutations bundled in one hook so callers get the same query-cache
 * invalidation behaviour for both pull and push. */
export function useFileTransfers() {
  const qc = useQueryClient();

  const invalidate = useCallback(
    (serial: string) => {
      qc.invalidateQueries({ queryKey: ["device-files", serial] });
      qc.invalidateQueries({ queryKey: ["workspace-files"] });
    },
    [qc],
  );

  const pullMutation = useMutation<PullResponse, Error, PullArgs>({
    mutationFn: ({ serial, remote, local }) =>
      pullDeviceFile(serial, remote, local),
    onSuccess: (_data, vars) => invalidate(vars.serial),
  });

  const pushMutation = useMutation<PushResponse, Error, PushArgs>({
    mutationFn: ({ serial, local, remote, force }) =>
      pushDeviceFile(serial, local, remote, { force }),
    onSuccess: (data, vars) => {
      // Confirm-required responses don't actually transfer — skip
      // invalidation so the UI doesn't refetch over a no-op.
      if (data.requires_confirm) return;
      invalidate(vars.serial);
    },
  });

  return { pullMutation, pushMutation };
}
