/**
 * useFlashJob — NDJSON stream consumption (ADR-056).
 *
 * The endpoint streams one JSON object per line, progress first and the
 * verdict last. The specs here lock the properties a partition write
 * depends on: a chunk boundary landing mid-line must not lose or corrupt a
 * line, and the terminal frame must always reach the caller — during a
 * flash, "no verdict" and "failed" are different facts about the device.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useFlashJob } from "./useFlash";

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const body = {
    getReader() {
      return {
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
      };
    },
  };
  return { ok: true, status: 200, body } as unknown as Response;
}

function mockFetch(resp: Response) {
  const spy = vi.fn().mockResolvedValue(resp);
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useFlashJob", () => {
  it("renders progress lines then the verdict", async () => {
    mockFetch(
      streamOf([
        '{"ev":"progress","phase":"transfer","done":5,"total":10,"t":0.1}\n',
        '{"ev":"progress","phase":"flash","text":"writing \'boot\'","t":0.5}\n',
        '{"ev":"done","ok":true,"rc":0,"duration_s":1.2,"artifacts":"/ws/flash/x"}\n',
      ]),
    );
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("flash", { partition: "boot", image: "x.bin" });
    });
    await waitFor(() => expect(result.current.verdict).not.toBeNull());
    expect(result.current.lines.map((l) => l.text)).toEqual([
      "5/10 bytes (50%)",
      "writing 'boot'",
    ]);
    expect(result.current.verdict?.ok).toBe(true);
    expect(result.current.verdict?.artifacts).toBe("/ws/flash/x");
    expect(result.current.running).toBe(false);
  });

  it("reassembles a line split across chunk boundaries", async () => {
    // The single most likely way to silently lose the verdict: a chunk that
    // ends in the middle of the last line.
    mockFetch(
      streamOf(['{"ev":"done","ok":tr', 'ue,"rc":0,"duration_s":0.4,"artifacts":""}\n']),
    );
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("devices", {});
    });
    await waitFor(() => expect(result.current.verdict?.ok).toBe(true));
  });

  it("keeps the bar where it was on a failed job", async () => {
    mockFetch(
      streamOf([
        '{"ev":"progress","phase":"transfer","done":3,"total":10,"t":0.1}\n',
        '{"ev":"done","ok":false,"rc":1,"code":"FLASH_FAILED","error":"boom","duration_s":0.9}\n',
      ]),
    );
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("flash", {});
    });
    await waitFor(() => expect(result.current.verdict?.ok).toBe(false));
    // Not 100, not 0 — jumping either way would misreport how far it got.
    expect(result.current.pct).toBe(30);
    expect(result.current.verdict?.code).toBe("FLASH_FAILED");
  });

  it("shows bytes rather than a percentage of nothing", async () => {
    mockFetch(
      streamOf([
        '{"ev":"progress","phase":"flash","done":42,"total":0,"t":0.2}\n',
        '{"ev":"done","ok":true,"rc":0,"duration_s":0.3}\n',
      ]),
    );
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("flash", {});
    });
    await waitFor(() => expect(result.current.verdict).not.toBeNull());
    expect(result.current.lines[0]?.text).toBe("42 bytes");
    expect(result.current.pct).toBe(100);
  });

  it("skips a malformed line instead of abandoning the stream", async () => {
    mockFetch(
      streamOf([
        "not json at all\n",
        '{"ev":"done","ok":true,"rc":0,"duration_s":0.1}\n',
      ]),
    );
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("devices", {});
    });
    await waitFor(() => expect(result.current.verdict?.ok).toBe(true));
  });

  it("reports a rejected request instead of hanging", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 400, body: null } as unknown as Response),
    );
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("flash", {});
    });
    expect(result.current.running).toBe(false);
    expect(result.current.lines[0]?.text).toContain("400");
  });

  it("clears the previous job's output when a new one starts", async () => {
    mockFetch(streamOf(['{"ev":"done","ok":true,"rc":0,"duration_s":0.1}\n']));
    const { result } = renderHook(() => useFlashJob());
    await act(async () => {
      await result.current.run("devices", {});
    });
    await waitFor(() => expect(result.current.verdict).not.toBeNull());

    mockFetch(streamOf(['{"ev":"progress","phase":"flash","text":"second","t":0.1}\n']));
    await act(async () => {
      await result.current.run("devices", {});
    });
    // A stale verdict left on screen would describe the wrong job.
    expect(result.current.verdict).toBeNull();
    expect(result.current.lines.map((l) => l.text)).toEqual(["second"]);
  });
});
