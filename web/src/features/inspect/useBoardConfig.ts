/**
 * Board config partition — find it by shape, read it back.
 *
 * This is the readback the flash path does not perform. `Writing 'x' OKAY`
 * is fastboot reporting that it believes it wrote; nothing verifies the
 * bytes. Here they are.
 *
 * Detection is by CONTENT (the partition whose head parses as KEY="VALUE"),
 * never by name: the by-name label differs per product, so a hard-coded or
 * hand-typed name silently finds nothing on the next board — the same defect
 * that made the flash partition picker offer four names this bench refuses.
 */
import { useMutation, useQuery } from "@tanstack/react-query";

export interface ConfigCandidate {
  name: string;
  node: string;
  lines: number;
}

export interface ConfigEntry {
  key: string;
  value: string;
}

export interface ConfigRead {
  device: string;
  node: string;
  size_bytes: number;
  read_bytes: number;
  /** False = these bytes are not KEY="VALUE". Render `raw`, never an empty
   *  table: "no keys" and "wrong partition" are indistinguishable in one, and
   *  the second is far more likely. */
  parsed: boolean;
  entries: ConfigEntry[];
  raw: string;
}

async function envelope<T>(url: string): Promise<T> {
  const r = await fetch(url);
  const body = await r.json();
  if (!r.ok || body?.ok === false) {
    throw new Error(body?.error?.message || body?.error?.code || `HTTP ${r.status}`);
  }
  return body.data as T;
}

export function useConfigScan(enabled: boolean) {
  return useQuery<{ candidates: ConfigCandidate[]; hint: string }>({
    queryKey: ["board-config-scan"],
    enabled,
    // A scan walks every partition on the device. Cheap (~2 s measured) but
    // not free, and the answer only changes when the board does — so it is
    // explicitly triggered, never polled.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
    queryFn: () => envelope("/api/board-config/scan"),
  });
}

export function useConfigRead() {
  return useMutation<ConfigRead, Error, { name: string }>({
    mutationFn: ({ name }) =>
      envelope<ConfigRead>(`/api/board-config/read?name=${encodeURIComponent(name)}`),
  });
}
