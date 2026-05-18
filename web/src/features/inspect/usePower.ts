/**
 * Power tab hooks — battery query + reboot/sleep-wake mutations.
 *
 * Battery is cheap and read-only, so we keep it in useQuery with a
 * short staleTime. Reboot and sleep-wake are intentionally NOT exposed
 * through useMutation here — `PowerTab` owns the trigger logic + result
 * state inline so each action card stays self-contained.
 */
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  fetchBattery,
  postReboot,
  postSleepWake,
  type RebootRequest,
  type SleepWakeRequest,
} from "../../lib/api";

export function useBattery(device: string | null | undefined) {
  return useQuery({
    queryKey: ["battery", device],
    enabled: !!device,
    staleTime: 15_000,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => fetchBattery(device, signal),
  });
}

export function useRebootMutation(device: string | null | undefined) {
  return useMutation({
    mutationFn: (body: RebootRequest) => postReboot(device, body),
  });
}

export function useSleepWakeMutation(device: string | null | undefined) {
  return useMutation({
    mutationFn: (body: SleepWakeRequest) => postSleepWake(device, body),
  });
}
