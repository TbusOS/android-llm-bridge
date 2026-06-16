/**
 * App tab hooks — 2 queries (app-list / app-info) + 5 mutations
 * (install / start / stop / clear-data / uninstall).
 *
 * Mirrors usePower.ts layering: one hook per query / mutation, exported
 * individually. AppTab sub-components compose them but own their own
 * trigger UI + result rendering so each card stays self-contained.
 *
 * `useAppInstallMutation` and `useAppUninstallMutation` invalidate the
 * `["app-list", device, ...]` cache on success so the package list
 * stays fresh without a manual refresh.
 *
 * The `device` arg accepts `null | undefined` so callers can wire the
 * top-bar device store directly without pre-checking — every queryFn
 * is gated by `enabled` and every mutationFn passes the device along
 * to the API helper, which returns an envelope-b error if no device.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAppInfo,
  fetchAppList,
  postAppClearData,
  postAppInstall,
  postAppStart,
  postAppStop,
  postAppUninstall,
} from "../../lib/api";

export interface AppInstallVars {
  file: File;
  replace: boolean;
  grantRuntime: boolean;
  downgrade: boolean;
}

export function useAppList(
  device: string | null | undefined,
  includeSystem: boolean,
) {
  return useQuery({
    queryKey: ["app-list", device, includeSystem],
    enabled: !!device,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      fetchAppList(device, { include_system: includeSystem }, signal),
  });
}

export function useAppInfo(
  device: string | null | undefined,
  pkg: string | null,
) {
  return useQuery({
    queryKey: ["app-info", device, pkg],
    enabled: !!device && !!pkg,
    staleTime: 60_000,
    queryFn: ({ signal }) => fetchAppInfo(pkg!, device, signal),
  });
}

export function useAppInstallMutation(device: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, replace, grantRuntime, downgrade }: AppInstallVars) =>
      postAppInstall(device, file, {
        replace,
        grant_runtime: grantRuntime,
        downgrade,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["app-list", device] }),
  });
}

export function useAppStartMutation(device: string | null | undefined) {
  return useMutation({
    mutationFn: (componentOrPkg: string) => postAppStart(device, componentOrPkg),
  });
}

export function useAppStopMutation(device: string | null | undefined) {
  return useMutation({
    mutationFn: (pkg: string) => postAppStop(device, pkg),
  });
}

export function useAppClearDataMutation(device: string | null | undefined) {
  return useMutation({
    // allow_dangerous: the armed two-step confirm in the UI is the
    // user's authorisation — same contract as uninstall below.
    mutationFn: (pkg: string) =>
      postAppClearData(device, pkg, { allow_dangerous: true }),
  });
}

export function useAppUninstallMutation(device: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pkg: string) =>
      postAppUninstall(device, pkg, { allow_dangerous: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["app-list", device] }),
  });
}
