/**
 * Screenshot history hooks (functional LOW-1).
 *
 * Mirrors useUartCaptures: list / read / trigger triple. Triggering a
 * fresh capture (the existing `captureScreenshot` mutation) invalidates
 * the list cache so the new shot appears at the top of the sidebar.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  captureScreenshot,
  deleteScreenshot,
  fetchScreenshots,
  type ScreenshotResponse,
} from "../../lib/api";

const LIST_KEY = (device: string | null | undefined) =>
  ["screenshots", device ?? null] as const;
const STALE_MS = 10_000;

export function useScreenshots(device?: string | null) {
  return useQuery({
    queryKey: LIST_KEY(device),
    queryFn: ({ signal }) => {
      if (!device) throw new Error("missing device");
      return fetchScreenshots(device, signal);
    },
    enabled: !!device,
    // No auto-poll — screenshots only land when the user triggers one.
    // ui-fluency 2026-05-07 MID-3: explicit window-focus + stale flags
    // (mirror useFileBrowser pattern) so alt-tab back doesn't re-fire
    // the list every time.
    staleTime: STALE_MS,
    refetchOnWindowFocus: false,
  });
}

export function useTriggerScreenshot(
  device?: string | null,
  onSuccess?: (data: ScreenshotResponse) => void,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!device) throw new Error("missing device");
      return captureScreenshot(device);
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: LIST_KEY(device) });
      onSuccess?.(data);
    },
  });
}

export function useDeleteScreenshotMutation(device?: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => {
      if (!device) throw new Error("missing device");
      return deleteScreenshot(device, name);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: LIST_KEY(device) }),
  });
}
