import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditEvent } from "../../../lib/api";
import { __resetPoolForTests } from "../../../lib/ws";
import { useApp } from "../../../stores/app";
import { renderPage, stubFetch } from "../../../test/renderPage";
import { activeSession, LIVE_IDLE_MS } from "./liveSession";
import { OverviewPage } from "./OverviewPage";

const A = "0123456789ABCDEF";
const B = "FEDCBA9876543210";

const EMPTY = {
  "/devices": { ok: true, transport: "AdbTransport", devices: [] },
  "/agent/status": { v: "1", agents: [], forwarders: { adb: { bound: false, port: 0 }, serial: { bound: false, port: 0, configured: false, com: null, baud: 0 } } },
  "/api/bench/board": { ok: true, table: null, boards: [], active: null, source: "env", problem: null, serial: { com: null, baud: 0, bound: false, readers: 0 } },
  "/api/flash/status": { ok: true, available: false, busy: false, job: null, partitions: [], partitions_problem: "", board: null },
  "/audit": { ok: true, since: "", until: "", events: [] },
  "/api/timeline/captures": { ok: true, data: [] },
  "/playground/backends": { backends: [] },
  "/metrics/summary": { ok: true, since: "", until: "", window_s: 300, session_id: null, tps: null, total_tokens: 0, sample_count: 0 },
  "/tools": { ok: true, count: 0, categories: [], tools: [] },
  "/sessions": { ok: true, sessions: [] },
};

const details = (soc: string, battery: number, temp: number) => ({
  ok: true,
  serial: "",
  transport: "AdbTransport",
  device: {
    model: "Board_X",
    brand: "Example",
    manufacturer: "Example",
    sdk: "34",
    release: "14",
    build_fingerprint: "example/board_x/board_x:14/UP1A/1:userdebug/test-keys",
    abi: "arm64-v8a",
    hardware: soc,
    serialno: "",
    uptime_sec: 3600,
    battery_level: battery,
    storage: {},
    extras: { soc, cpu_cores: 8, cpu_max_khz: 2_200_000, ram_total_kb: 8 * 1048576, ram_avail_kb: 2 * 1048576, display: { size: "1080x2400" }, temp_c: temp },
  },
});

/** Minimal WebSocket that the app's ws client can drive. */
class FakeWS {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static all: FakeWS[] = [];
  readyState = 0;
  binaryType = "blob";
  sent: string[] = [];
  private ls = new Map<string, ((ev: unknown) => void)[]>();
  constructor(public url: string) {
    FakeWS.all.push(this);
  }
  addEventListener(type: string, fn: (ev: unknown) => void) {
    this.ls.set(type, [...(this.ls.get(type) ?? []), fn]);
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
  }
  emit(type: string, ev: unknown = {}) {
    for (const fn of this.ls.get(type) ?? []) fn(ev);
  }
  json(msg: unknown) {
    this.emit("message", { data: JSON.stringify(msg) });
  }
}

/** Open every audit socket, then return the one whose config matches. */
async function auditSocket(includeMetrics: boolean): Promise<FakeWS> {
  await waitFor(() => expect(FakeWS.all.filter((w) => w.url.endsWith("/audit/stream")).length).toBeGreaterThanOrEqual(2));
  await act(async () => {
    for (const w of FakeWS.all) {
      if (w.readyState === 0) {
        w.readyState = 1;
        w.emit("open");
      }
    }
  });
  const ws = FakeWS.all.find((w) => w.url.endsWith("/audit/stream") && w.sent.some((s) => JSON.parse(s).include_metrics === includeMetrics));
  if (!ws) throw new Error("no audit socket");
  return ws;
}

const iso = (agoMs: number) => new Date(Date.now() - agoMs).toISOString();
const ev = (kind: string, agoMs: number, extra: Partial<AuditEvent> = {}): AuditEvent => ({ ts: iso(agoMs), session_id: "20261028-abcdef12", source: "chat", kind, summary: kind, data: null, ts_approx: false, ...extra });

describe("OverviewPage", () => {
  beforeEach(() => {
    useApp.setState({ lang: "en", device: null });
    FakeWS.all = [];
    vi.stubGlobal("WebSocket", FakeWS);
  });
  afterEach(() => {
    __resetPoolForTests();
    vi.unstubAllGlobals();
  });

  it("explains what to do when adb lists no device", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("No device yet")).toBeTruthy();
    expect(screen.getByText("Check the bench")).toBeTruthy();
    expect(screen.getByText("Board state")).toBeTruthy();
    expect(screen.getByText("Connections")).toBeTruthy();
    expect(screen.getByText(/bench tunnel is down/)).toBeTruthy();
  });

  it("shows every channel as missing and offers a first boot recording", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("no agent connected")).toBeTruthy();
    expect(await screen.findByText("No boot recorded yet")).toBeTruthy();
    expect(await screen.findByText("Nothing in the last 4 hours")).toBeTruthy();
  });

  it("speaks Chinese when the app language is Chinese", async () => {
    useApp.setState({ lang: "zh", device: null });
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("还没有设备")).toBeTruthy();
    expect(screen.getByText("板子状态")).toBeTruthy();
    expect(screen.getByText("服务概况")).toBeTruthy();
    expect(screen.getByText("模型后端")).toBeTruthy();
  });

  it("asks before rebooting into the bootloader", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    (await screen.findByText("Stop in bootloader")).click();
    expect((await screen.findByRole("alert")).textContent).toContain("bootloader countdown");
  });
});

describe("OverviewPage device list problems", () => {
  beforeEach(() => {
    useApp.setState({ lang: "en", device: null });
    FakeWS.all = [];
    vi.stubGlobal("WebSocket", FakeWS);
  });
  afterEach(() => {
    __resetPoolForTests();
    vi.unstubAllGlobals();
  });

  it("GET /devices failing, a broken transport and an empty list each read differently", async () => {
    const base = stubFetch(EMPTY);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => (String(input) === "/devices" ? new Response("x", { status: 502 }) : base(input))));
    const one = await renderPage(<OverviewPage />);
    expect(await screen.findByText("Can't read the device list")).toBeTruthy();
    expect(screen.getByText(/GET \/devices failed/)).toBeTruthy();
    expect(screen.queryByText("No device yet")).toBeNull();
    one.unmount();

    vi.stubGlobal("fetch", vi.fn(stubFetch({ ...EMPTY, "/devices": { ok: false, transport: "AdbTransport", devices: [], error: "AdbError: cannot connect to daemon" } })));
    const two = await renderPage(<OverviewPage />);
    expect(await screen.findByText("adb is not working")).toBeTruthy();
    expect(screen.getByText(/cannot connect to daemon/)).toBeTruthy();
    expect(screen.queryByText("No device yet")).toBeNull();
    two.unmount();

    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("No device yet")).toBeTruthy();
    expect(screen.queryByText("adb is not working")).toBeNull();
  });

  it("offers a retry that reads the list again", async () => {
    const base = stubFetch(EMPTY);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => (String(input) === "/devices" ? new Response("x", { status: 502 }) : base(input)));
    vi.stubGlobal("fetch", fetchMock);
    await renderPage(<OverviewPage />);
    await screen.findByText("Can't read the device list");
    const count = () => fetchMock.mock.calls.filter(([u]) => String(u) === "/devices").length;
    const before = count();
    fireEvent.click(screen.getAllByText("Read again")[0] as HTMLElement);
    await waitFor(() => expect(count()).toBeGreaterThan(before));
  });

  it("does not call the chosen board offline before the list has arrived", async () => {
    useApp.setState({ device: A });
    const base = stubFetch(EMPTY);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => (String(input) === "/devices" ? new Promise<Response>(() => {}) : base(input))));
    await renderPage(<OverviewPage />);
    const card = (await screen.findByText("Board state")).closest("article") as HTMLElement;
    expect(within(card).getByText("reading")).toBeTruthy();
    expect(card.textContent).not.toContain("not in the adb device list");
    expect(card.textContent).not.toContain("offline");
  });

  it("keeps the chosen board when it drops out and offers the one that is there", async () => {
    useApp.setState({ device: A });
    vi.stubGlobal("fetch", vi.fn(stubFetch({ ...EMPTY, "/devices": { ok: true, transport: "AdbTransport", devices: [{ serial: B, state: "device", model: "Board_X" }] }, [`/devices/${B}/details`]: details("soc-x1", 55, 38.5) })));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText(/is not in the adb device list/)).toBeTruthy();
    expect(useApp.getState().device).toBe(A);
    await waitFor(() => expect(document.querySelector(".devs")).toBeTruthy());
    const strip = document.querySelector(".devs") as HTMLElement;
    expect(within(strip).getByText(A).closest(".dev--gone")).toBeTruthy();
    fireEvent.click(within(strip).getByText(B));
    expect(useApp.getState().device).toBe(B);
  });
});

describe("OverviewPage devices strip", () => {
  beforeEach(() => {
    useApp.setState({ lang: "en", device: A });
    FakeWS.all = [];
    vi.stubGlobal("WebSocket", FakeWS);
  });
  afterEach(() => {
    __resetPoolForTests();
    vi.unstubAllGlobals();
  });

  const TWO = {
    ...EMPTY,
    "/devices": { ok: true, transport: "AdbTransport", devices: [{ serial: A, state: "device", model: "Board_X" }, { serial: B, state: "device", model: "Board_X" }] },
    [`/devices/${A}/details`]: details("soc-x1", 87, 41.2),
    [`/devices/${B}/details`]: details("soc-y2", 40, 52.0),
  };

  it("shows one tile per device with SoC, memory, battery and temperature, and switches on click", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(TWO)));
    await renderPage(<OverviewPage />);
    await waitFor(() => expect(document.querySelectorAll(".devs .dev").length).toBe(2));
    const strip = document.querySelector(".devs") as HTMLElement;
    await waitFor(() => expect(strip.textContent).toContain("soc-y2"));
    expect(strip.textContent).toContain("soc-x1");
    expect(strip.textContent).toContain("75% · 8.0 GiB");
    expect(strip.textContent).toContain("87%");
    expect(strip.textContent).toContain("52.0 °C");
    expect(screen.getByText("2 online · 0 not reachable · via adb")).toBeTruthy();
    const tileB = within(strip).getByText(B).closest("button") as HTMLButtonElement;
    expect(tileB.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(tileB);
    expect(useApp.getState().device).toBe(B);
  });

  it("hides the strip when there is only the one chosen device", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ ...TWO, "/devices": { ok: true, transport: "AdbTransport", devices: [{ serial: A, state: "device", model: "Board_X" }] } })));
    await renderPage(<OverviewPage />);
    await screen.findByText("Board state");
    await waitFor(() => expect(screen.getByText(/Android 14/)).toBeTruthy());
    expect(document.querySelector(".devs")).toBeNull();
  });
});

describe("OverviewPage assistant and service cards", () => {
  beforeEach(() => {
    useApp.setState({ lang: "en", device: null });
    FakeWS.all = [];
    vi.stubGlobal("WebSocket", FakeWS);
  });
  afterEach(() => {
    __resetPoolForTests();
    vi.unstubAllGlobals();
  });

  it("fills the four summary tiles from /metrics/summary, /tools, /sessions and /devices", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch({
          ...EMPTY,
          "/devices": { ok: true, transport: "AdbTransport", devices: [{ serial: A, state: "device" }, { serial: B, state: "offline" }] },
          "/metrics/summary": { ok: true, since: "", until: "", window_s: 300, session_id: null, tps: { mean: 12.34, p50: 12, p95: 20, max: 22, min: 3 }, total_tokens: 900, sample_count: 61 },
          "/tools": { ok: true, count: 42, categories: [{ name: "shell", count: 30 }, { name: "logs", count: 12 }], tools: [] },
          "/sessions": {
            ok: true,
            sessions: [
              { session_id: "s1", created: null, backend: "ollama", model: "m", device: null, turns: 3, last_event_ts: iso(5_000) },
              { session_id: "s2", created: null, backend: "ollama", model: "m", device: null, turns: 1, last_event_ts: iso(3_600_000) },
            ],
          },
        }),
      ),
    );
    await renderPage(<OverviewPage />);
    const tile = (k: string) => document.querySelector(`[data-kpi="${k}"]`) as HTMLElement;
    await waitFor(() => expect(tile("tps").textContent).toContain("12.3tok/s"));
    expect(tile("tps").textContent).toContain("5-min average · 61 samples");
    await waitFor(() => expect(tile("tools").textContent).toContain("42"));
    expect(tile("tools").textContent).toContain("2 groups");
    await waitFor(() => expect(tile("sessions").textContent).toContain("1 running"));
    expect(tile("sessions").querySelector(".metric__value")?.textContent).toBe("2");
    expect(tile("devices").querySelector(".metric__value")?.textContent).toBe("1/ 2");
  });

  it("shows '—', not 0, when the counts can't be read", async () => {
    const base = stubFetch(EMPTY);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => (/^\/(tools|metrics\/summary)/.test(String(input)) ? new Response("x", { status: 500 }) : base(input))));
    await renderPage(<OverviewPage />);
    const tile = (k: string) => document.querySelector(`[data-kpi="${k}"]`) as HTMLElement;
    await waitFor(() => expect(tile("tools").textContent).toContain("couldn't read"));
    expect(tile("tools").querySelector(".metric__value")?.textContent).toBe("—");
    await waitFor(() => expect(tile("tps").textContent).toContain("couldn't read"));
  });

  it("lists LLM backends with health and latency", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch({
          ...EMPTY,
          "/playground/backends/ollama/health": { name: "ollama", reachable: true, latency_ms: 42, model: "qwen2.5:7b", reason: null },
          "/playground/backends/openai-compat/health": { name: "openai-compat", reachable: false, latency_ms: null, model: null, reason: "down", error: "connection refused" },
          "/playground/backends": {
            backends: [
              { name: "ollama", status: "beta", host_compute_type: "remote", supports_tool_calls: true, requires: [], description: "" },
              { name: "openai-compat", status: "beta", host_compute_type: "remote", supports_tool_calls: true, requires: [], description: "" },
            ],
          },
        }),
      ),
    );
    await renderPage(<OverviewPage />);
    const card = (await screen.findByText("LLM backends")).closest("article") as HTMLElement;
    await waitFor(() => expect(card.textContent).toContain("42 ms"));
    expect(card.textContent).toContain("qwen2.5:7b");
    await waitFor(() => expect(card.textContent).toContain("connection refused"));
    expect(card.textContent).toContain("down · down");
    expect(card.textContent).toContain("1 reachable · 2 registered");
  });

  it("has quick actions for screenshot, health check and connections", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    const href = async (name: string) => ((await screen.findByText(name)).closest("a") as HTMLAnchorElement).getAttribute("href");
    expect(await href("Take a screenshot")).toBe("/screen");
    expect(await href("Health check")).toBe("/doctor");
    expect(await href("Connection center")).toBe("/connections");
  });

  it("streams recent activity and pauses it", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    const ws = await auditSocket(false);
    expect(JSON.parse(ws.sent[0] as string)).toEqual({ minutes: 240, include_metrics: false });
    await act(async () => {
      ws.json({ type: "snapshot", since: "", until: "", events: [ev("user", 2_000, { source: "terminal", summary: "getprop ro.build.type" })] });
    });
    expect(await screen.findByText("getprop ro.build.type")).toBeTruthy();
    await act(async () => {
      ws.json({ type: "event", data: ev("user", 0, { source: "terminal", summary: "dmesg | tail" }) });
    });
    expect(await screen.findByText("dmesg | tail")).toBeTruthy();
    fireEvent.click(screen.getByText("Pause"));
    expect(ws.sent.map((s) => JSON.parse(s))).toContainEqual({ type: "control", action: "pause" });
    await act(async () => {
      ws.json({ type: "control_ack", action: "pause", paused: true });
    });
    expect(screen.getByText("Resume")).toBeTruthy();
    expect(screen.getByText("paused")).toBeTruthy();
  });

  it("shows the running agent session with tool progress and tokens per second, and marks a dropped stream", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("No agent session is running.")).toBeTruthy();
    const ws = await auditSocket(true);
    await act(async () => {
      ws.json({
        type: "snapshot",
        since: "",
        until: "",
        events: [
          ev("tps_sample", 1_000, { data: { rate_per_s: 17, total_tokens: 340, backend: "ollama" } }),
          ev("tool_call_start", 4_000, { data: { id: "t2", name: "alb_logcat" } }),
          ev("tool_call_end", 6_000, { data: { id: "t1", ok: true } }),
          ev("tool_call_start", 8_000, { data: { id: "t1", name: "alb_dmesg" } }),
          ev("user", 9_000, { summary: "Why did the board reboot?" }),
        ],
      });
    });
    expect(await screen.findByText("Why did the board reboot?")).toBeTruthy();
    const card = screen.getByText("Agent session").closest("article") as HTMLElement;
    expect(card.querySelector(".live__tool--done")?.textContent).toContain("alb_dmesg");
    expect(card.querySelector(".live__tool--running")?.textContent).toContain("alb_logcat");
    expect(card.textContent).toContain("17tok/s now");
    expect(card.textContent).toContain("ollama");
    expect(card.textContent).toContain("340 tok");
    expect(card.textContent).toContain("turn 1");
    await act(async () => {
      ws.readyState = 3;
      ws.emit("close", { code: 1006, reason: "" });
    });
    expect(within(card).getByText("stream dropped")).toBeTruthy();
  });
});

describe("live session reducer", () => {
  it("picks the unfinished session and ignores finished, failed and long-silent ones", () => {
    const now = Date.now();
    const s = (id: string, kind: string, agoMs: number, extra: Partial<AuditEvent> = {}) => ev(kind, agoMs, { session_id: id, ...extra });
    // newest first, as the stream keeps them
    const events = [
      s("done", "done", 1_000, { data: { model: "m", usage: { total_tokens: 10 } } }),
      s("live", "user", 2_000, { summary: "hello" }),
      s("failed", "error", 3_000),
      s("done", "user", 4_000),
      s("failed", "user", 5_000),
      s("ghost", "user", LIVE_IDLE_MS + 60_000),
      s("other", "user", 500, { source: "terminal" }),
    ];
    const live = activeSession(events, now);
    expect(live?.sessionId).toBe("live");
    expect(live?.prompt).toBe("hello");
    expect(activeSession(events.filter((e) => e.session_id !== "live"), now)).toBeNull();
  });
});
