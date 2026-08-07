/**
 * Flash tab hooks — fastboot state, image upload, and the NDJSON job stream.
 *
 * The job endpoints do not return a JSON object; they stream one JSON object
 * per line, progress first and the verdict last (see `alb/api/flash_route`).
 * So this cannot be a plain `useQuery` — it reads the body with a
 * ReadableStream reader and pushes each line into React state as it lands.
 * That is the whole reason the endpoint streams: during a partition write a
 * silent spinner and a wedged job look identical.
 *
 * The UART side needs no code here at all. The hub already merges the
 * board's console into the job's own timeline (ADR-056 §决定 4), so what
 * arrives on this stream is the job half and the recorded file holds both —
 * the page links to it rather than re-deriving it.
 */
import { useCallback, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

export interface FlashStatus {
  available: boolean;
  busy: boolean;
  job: string;
}

export interface FlashLine {
  /** Monotonic-ish sequence, used only as a React key. */
  seq: number;
  src: "job" | "uart" | "meta";
  t: number;
  text: string;
}

export interface FlashVerdict {
  ok: boolean;
  rc: number;
  code: string;
  error: string;
  stdout: string;
  stderr: string;
  duration_s: number;
  artifacts: string;
}

export function useFlashStatus() {
  return useQuery<FlashStatus>({
    queryKey: ["flash-status"],
    // A bench can gain or lose the capability when the agent reconnects, and
    // the answer is cheap, so poll rather than make the operator guess.
    refetchInterval: 5_000,
    refetchOnWindowFocus: false,
    queryFn: async ({ signal }) => {
      const r = await fetch("/api/flash/status", { signal });
      if (!r.ok) throw new Error(`flash status: HTTP ${r.status}`);
      const body = await r.json();
      return {
        available: !!body.available,
        busy: !!body.busy,
        job: String(body.job ?? ""),
      };
    },
  });
}

/** Upload one file into the hub workspace and return its workspace path. */
export async function uploadImage(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/workspace/files/upload", { method: "POST", body: form });
  if (!r.ok) throw new Error(`upload failed: HTTP ${r.status}`);
  const body = await r.json();
  const path = body?.path ?? body?.data?.path ?? file.name;
  return String(path);
}

function progressText(msg: Record<string, unknown>): string {
  const text = String(msg.text ?? "");
  if (text) return text;
  const done = Number(msg.done ?? 0);
  const total = Number(msg.total ?? 0);
  // total 0 means the agent could not quantify it — show bytes rather than
  // a percentage of nothing.
  if (total > 0) return `${done}/${total} bytes (${Math.floor((done * 100) / total)}%)`;
  return `${done} bytes`;
}

export function useFlashJob() {
  const [lines, setLines] = useState<FlashLine[]>([]);
  const [verdict, setVerdict] = useState<FlashVerdict | null>(null);
  const [running, setRunning] = useState(false);
  const [pct, setPct] = useState(0);
  const seq = useRef(0);

  const push = useCallback((src: FlashLine["src"], t: number, text: string) => {
    seq.current += 1;
    const line: FlashLine = { seq: seq.current, src, t, text };
    // Bounded: a long flash on a chatty console would otherwise grow the
    // DOM without limit. The full record is on disk either way.
    setLines((prev) => (prev.length > 500 ? [...prev.slice(-400), line] : [...prev, line]));
  }, []);

  const run = useCallback(
    async (path: string, body: Record<string, unknown>) => {
      setLines([]);
      setVerdict(null);
      setPct(0);
      setRunning(true);
      try {
        const r = await fetch(`/api/flash/${path}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok || !r.body) {
          push("meta", 0, `HTTP ${r.status} — the hub rejected the request`);
          return;
        }
        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          // Split on newlines but keep the trailing partial: a chunk
          // boundary lands mid-line often enough to matter.
          const parts = buf.split("\n");
          buf = parts.pop() ?? "";
          for (const part of parts) {
            if (!part.trim()) continue;
            let msg: Record<string, unknown>;
            try {
              msg = JSON.parse(part);
            } catch {
              continue; // a half-written line is not worth failing the view over
            }
            if (msg.ev === "progress") {
              const total = Number(msg.total ?? 0);
              if (total > 0) setPct(Math.floor((Number(msg.done ?? 0) * 100) / total));
              push("job", Number(msg.t ?? 0), progressText(msg));
            } else if (msg.ev === "done") {
              setVerdict(msg as unknown as FlashVerdict);
              // Functional update: reading `pct` from the closure here would
              // read whatever it was when `run` was created, not what the
              // last progress line set — the bar would jump backwards.
              setPct((prev) => (msg.ok ? 100 : prev));
            }
          }
        }
      } catch (e) {
        push("meta", 0, e instanceof Error ? e.message : String(e));
      } finally {
        setRunning(false);
      }
    },
    [push],
  );

  return { lines, verdict, running, pct, run };
}
