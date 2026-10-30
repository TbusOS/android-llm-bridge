import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ search: {} as Record<string, string> }));
const clip = vi.hoisted(() => ({ copyText: vi.fn(async (_text: string) => true) }));
vi.mock("../../../lib/clipboard", () => clip);
vi.mock("@tanstack/react-router", () => ({
  useSearch: () => router.search,
  useNavigate: () => vi.fn(),
  Link: ({ children, to, className }: { children: ReactNode; to: string; className?: string }) => (
    <a href={to} className={className}>{children}</a>
  ),
}));

import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";
import { LogsPage } from "./LogsPage";
import { badSpecTokens, LineSplitter, makeMatcher, parseQuery, parseThreadtime } from "./logcatModel";
import { markRegex, parseDmesg } from "./OtherPanels";

const DEVICE = "0123456789ABCDEF";

class FakeWS {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWS[] = [];
  readyState = 0;
  binaryType = "blob";
  sent: string[] = [];
  private ls = new Map<string, ((ev: unknown) => void)[]>();
  constructor(public url: string) {
    FakeWS.instances.push(this);
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
  open() {
    this.readyState = 1;
    this.emit("open");
    this.emit("message", { data: JSON.stringify({ type: "ready" }) });
  }
  text(s: string) {
    const u8 = new TextEncoder().encode(s);
    const buf = new ArrayBuffer(u8.length);
    new Uint8Array(buf).set(u8);
    this.emit("message", { data: buf });
  }
}

const L = (time: string, pid: number, lvl: string, tag: string, msg: string) => `10-28 ${time}  ${pid}  ${pid} ${lvl} ${tag}: ${msg}\n`;
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><LogsPage /></QueryClientProvider>);
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
  vi.stubGlobal("fetch", vi.fn(async () => json({ ok: false }, 404)));
  router.search = {};
  useApp.setState({ device: DEVICE, lang: "en" });
  useShell.setState({ sheetOpen: false, context: null });
});
afterEach(() => vi.unstubAllGlobals());

describe("logcat model", () => {
  it("parses a threadtime line and rejects continuation lines", () => {
    const p = parseThreadtime("10-28 09:31:04.121  1188  1190 E SensorsHal: open failed: Permission denied (13)");
    expect(p).toMatchObject({ pid: "1188", level: "E", tag: "SensorsHal", msg: "open failed: Permission denied (13)" });
    expect(parseThreadtime("    #00 pc 0000000000012a4c  /vendor/lib64/libsensors.so")).toBeNull();
  });

  it("keeps the unfinished tail of a chunk for the next one", () => {
    const s = new LineSplitter();
    const enc = (x: string) => new TextEncoder().encode(x).buffer as ArrayBuffer;
    expect(s.push(enc("abc\ndef"))).toEqual(["abc"]);
    expect(s.push(enc("ghi\n"))).toEqual(["defghi"]);
  });

  it("filters by tag glob, pid and text", () => {
    expect(parseQuery("tag:Sensor* pid:12 hello world")).toEqual({ tags: ["Sensor*"], pids: ["12"], text: "hello world" });
    const e = { id: 1, ts: "x", pid: "12", tid: "12", level: "E" as const, tag: "SensorsHal", msg: "open failed", cont: [] };
    const all = new Set(["V", "D", "I", "W", "E", "F"] as const);
    expect(makeMatcher("tag:Sensor*", all, false).test(e)).toBe(true);
    expect(makeMatcher("tag:Camera*", all, false).test(e)).toBe(false);
    expect(makeMatcher("pid:99", all, false).test(e)).toBe(false);
    expect(makeMatcher("fail", all, false).test(e)).toBe(true);
    expect(makeMatcher("(", all, true).badRegex).toBe(true);
  });

  it("reads kernel lines and grades them", () => {
    const r = parseDmesg("[ 4521.307714] Unable to handle kernel NULL pointer\n[ 1.0] all fine\n");
    expect(r.map((x) => x.level)).toEqual(["F", "I"]);
  });
});

describe("LogsPage logcat", () => {
  it("asks for a device when none is selected", () => {
    useApp.setState({ device: null });
    renderPage();
    expect(screen.getByText("No device selected")).toBeInTheDocument();
  });

  it("waits for lines, then shows them, hides filtered levels, and expands a row", async () => {
    renderPage();
    expect(screen.getByText("Waiting for logcat")).toBeInTheDocument();
    const ws = FakeWS.instances[0] as FakeWS;
    expect(ws.url).toContain("/logcat/stream");
    act(() => ws.open());
    expect(await screen.findByText("Connected, no lines yet")).toBeInTheDocument();
    act(() => {
      ws.text(L("09:31:04.102", 1123, "D", "SensorService", "debug noise"));
      ws.text(L("09:31:04.121", 1188, "E", "SensorsHal", "open failed: Permission denied (13)"));
      ws.text(L("09:31:04.190", 3011, "F", "libc", "Fatal signal 11"));
      ws.text("    #00 pc 0000000000012a4c  /vendor/lib64/libsensors.so\n");
    });
    expect(await screen.findByText("open failed: Permission denied (13)")).toBeInTheDocument();
    // D is off by default
    expect(screen.queryByText("debug noise")).not.toBeInTheDocument();
    expect(screen.getByText(/Showing 2 of 3 lines · 1 hidden by filters/)).toBeInTheDocument();
    // turn D on
    fireEvent.click(screen.getByRole("button", { name: "D" }));
    expect(await screen.findByText("debug noise")).toBeInTheDocument();
    // expand the fatal row: its continuation lines show with the actions
    fireEvent.click(screen.getByText("Fatal signal 11"));
    expect(screen.getByText(/libsensors\.so/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ask the assistant/ }));
    expect(useShell.getState().context?.detail).toContain("Fatal signal 11");
  });

  it("buffers while paused and shows the lines after resume", async () => {
    renderPage();
    const ws = FakeWS.instances[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text(L("09:31:04.121", 1188, "E", "SensorsHal", "first")));
    await screen.findByText("first");
    fireEvent.click(screen.getByRole("button", { name: /Pause/ }));
    act(() => ws.text(L("09:31:05.000", 1188, "E", "SensorsHal", "second")));
    await screen.findByText(/paused, 1 buffered/);
    expect(screen.queryByText("second")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Resume/ }));
    expect(await screen.findByText("second")).toBeInTheDocument();
  });

  it("shows the stream error with a reconnect button", async () => {
    renderPage();
    const ws = FakeWS.instances[0] as FakeWS;
    act(() => ws.emit("message", { data: JSON.stringify({ type: "closed", reason: "init_failed", error: "adb not found" }) }));
    expect(await screen.findByText(/adb not found/)).toBeInTheDocument();
  });
});

describe("LogsPage other tabs", () => {
  it("reads dmesg and filters to errors", async () => {
    const f = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/log/dmesg")) return json({ ok: true, data: { lines: 2, errors: 1, duration_captured_ms: 5000 } });
      if (url.startsWith("/workspace/files/preview/")) return json({ ok: true, text: "[ 1.000000] boot ok\n[ 2.000000] Unable to handle kernel NULL pointer dereference\n", truncated: false });
      if (url.startsWith("/workspace/files")) return json({ ok: true, entries: [{ name: "2026-10-28T09-00-00-dmesg.txt", is_dir: false, is_link: false, size: 10, mtime_epoch: 1 }] });
      return json({}, 404);
    });
    vi.stubGlobal("fetch", f);
    router.search = { tab: "dmesg" };
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /dmesg/ }));
    expect(await screen.findByText(/Unable to handle kernel/)).toBeInTheDocument();
    // "Errors and warnings" is on: the info line is hidden
    expect(screen.queryByText("boot ok")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Errors and warnings" }));
    expect(await screen.findByText("boot ok")).toBeInTheDocument();
  });

  it("reports a dmesg failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ ok: false, error: { code: "DMESG_FAILED", message: "adb offline" } })));
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /dmesg/ }));
    expect(await screen.findByText(/adb offline/)).toBeInTheDocument();
  });

  it("searches recordings and links hits to the timeline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).startsWith("/api/log/search")) {
          return json({
            ok: true,
            data: {
              pattern: "panic",
              truncated: false,
              match_count: 2,
              matches: [
                { path: "devices/x/timeline/capture-2026-10-28T09-30-58/timeline.jsonl", line_number: 7, content: "Kernel panic - not syncing" },
                { path: "devices/x/logs/2026-10-27T21-03-11-uart.log", line_number: 1882, content: "Kernel panic again" },
              ],
            },
          });
        }
        return json({}, 404);
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Search all recordings/ }));
    expect(screen.getByText("Search everything already recorded")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("search"), { target: { value: "panic" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText((_, el) => el?.classList.contains("list__title") === true && el.textContent === "Kernel panic - not syncing")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Open" })).toHaveLength(1);
  });

  it("shows the empty result and the error result of a search", async () => {
    const f = vi.fn(async () => json({ ok: true, data: { pattern: "zzz", truncated: false, match_count: 0, matches: [] } }));
    vi.stubGlobal("fetch", f);
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Search all recordings/ }));
    fireEvent.change(screen.getByLabelText("search"), { target: { value: "zzz" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("No hits")).toBeInTheDocument();
    f.mockImplementation(async () => json({ ok: false, error: { code: "INVALID_FILTER", message: "bad regex" } }));
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(screen.getByText(/INVALID_FILTER/)).toBeInTheDocument());
  });
});

/* ---------------- restored from the v2 tabs ---------------- */

const wsAt = (path: string) => FakeWS.instances.filter((w) => w.url.endsWith(path));
const firstFrame = (ws: FakeWS) => JSON.parse(ws.sent[0] ?? "null") as Record<string, unknown> | null;
const many = (n: number, msg: string) => Array.from({ length: n }, (_, i) => L("09:31:05.000", 2000, "E", "Flood", `${msg} ${i}`)).join("");

describe("logcat: clear, stop / start, board filter, pause", () => {
  it("Clear empties the view and keeps the stream running", async () => {
    renderPage();
    const ws = wsAt("/logcat/stream")[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text(L("09:31:04.121", 1188, "E", "SensorsHal", "before clear")));
    await screen.findByText("before clear");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(await screen.findByText("Connected, no lines yet")).toBeInTheDocument();
    expect(screen.queryByText("before clear")).not.toBeInTheDocument();
    act(() => ws.text(L("09:31:05.000", 1188, "E", "SensorsHal", "after clear")));
    expect(await screen.findByText("after clear")).toBeInTheDocument();
    expect(wsAt("/logcat/stream")).toHaveLength(1);
  });

  it("Stop closes the stream and Start reopens it from an empty view", async () => {
    renderPage();
    const ws = wsAt("/logcat/stream")[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text(L("09:31:04.121", 1188, "E", "SensorsHal", "kept while stopped")));
    await screen.findByText("kept while stopped");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(ws.readyState).toBe(3);
    expect(ws.sent).toContain(JSON.stringify({ type: "close" }));
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    expect(screen.getByText("kept while stopped")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    const again = wsAt("/logcat/stream");
    expect(again).toHaveLength(2);
    // the board replays its ring buffer on a new stream: old rows are dropped first
    expect(screen.queryByText("kept while stopped")).not.toBeInTheDocument();
    act(() => (again[1] as FakeWS).open());
    expect(firstFrame(again[1] as FakeWS)).toEqual({ device: DEVICE });
  });

  it("picking a buffer reopens the stream with logcat -b, and the default sends none", async () => {
    renderPage();
    const first = wsAt("/logcat/stream")[0] as FakeWS;
    act(() => first.open());
    expect(firstFrame(first)).not.toHaveProperty("buffers");
    act(() => first.text(L("09:31:04.121", 1188, "E", "SensorsHal", "from main")));
    await screen.findByText("from main");
    fireEvent.change(screen.getByLabelText("Buffer"), { target: { value: "events" } });
    const second = wsAt("/logcat/stream")[1] as FakeWS;
    act(() => second.open());
    expect(firstFrame(second)).toMatchObject({ buffers: ["events"] });
    expect(screen.queryByText("from main")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Buffer"), { target: { value: "" } });
    const third = wsAt("/logcat/stream")[2] as FakeWS;
    act(() => third.open());
    expect(firstFrame(third)).not.toHaveProperty("buffers");
  });

  it("sends the board filter to the stream and refuses a malformed one", async () => {
    renderPage();
    const ws = wsAt("/logcat/stream")[0] as FakeWS;
    act(() => ws.open());
    expect(firstFrame(ws)).toEqual({ device: DEVICE });
    const input = screen.getByLabelText("Filter on the board");
    const apply = screen.getByRole("button", { name: "Apply" });
    fireEvent.change(input, { target: { value: "SensorsHal" } });
    expect(apply).toBeDisabled();
    expect(screen.getByText(/Board filter not valid: SensorsHal/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "SensorsHal:V  *:S" } });
    fireEvent.click(apply);
    const next = wsAt("/logcat/stream")[1] as FakeWS;
    expect(next).toBeDefined();
    act(() => next.open());
    expect(firstFrame(next)).toEqual({ device: DEVICE, filter: "SensorsHal:V *:S" });
    expect(screen.getByText("Board filter SensorsHal:V *:S")).toBeInTheDocument();
    // a later Start keeps using it
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    const third = wsAt("/logcat/stream")[2] as FakeWS;
    act(() => third.open());
    expect(firstFrame(third)).toMatchObject({ filter: "SensorsHal:V *:S" });
  });

  it("Pause keeps the rows on screen even after the buffer overflows", async () => {
    renderPage();
    const ws = wsAt("/logcat/stream")[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text(L("09:31:04.121", 1188, "E", "SensorsHal", "row being read")));
    await screen.findByText("row being read");
    fireEvent.click(screen.getByRole("button", { name: /Pause/ }));
    act(() => ws.text(many(6000, "flood")));
    // wait for the render that follows the flood, not the one from the click
    await screen.findByText(/paused, [1-9]\d* buffered/);
    expect(screen.getByText("row being read")).toBeInTheDocument();
    expect(screen.getByText(/paused, 5000 buffered, 1000 more pushed out of the buffer/)).toBeInTheDocument();
    expect(screen.queryByText("flood 5999")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Resume/ }));
    expect(await screen.findByText("flood 5999")).toBeInTheDocument();
  });

  it("offers clear and reconnect after a stream error", async () => {
    renderPage();
    const ws = wsAt("/logcat/stream")[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text(L("09:31:04.121", 1188, "E", "SensorsHal", "line before the error")));
    await screen.findByText("line before the error");
    act(() => ws.emit("message", { data: JSON.stringify({ type: "closed", reason: "stream_error", error: "adb went away" }) }));
    fireEvent.click(await screen.findByRole("button", { name: "Clear and reconnect" }));
    expect(wsAt("/logcat/stream")).toHaveLength(2);
    expect(screen.queryByText("line before the error")).not.toBeInTheDocument();
  });

  it("checks filter tokens the way the server does", () => {
    expect(badSpecTokens("*:E")).toEqual([]);
    expect(badSpecTokens("ActivityManager:I MyApp:v *:S")).toEqual([]);
    expect(badSpecTokens("MyApp *:X a:b:E")).toEqual(["MyApp", "*:X", "a:b:E"]);
  });
});

/** Serves the serial tab's REST calls; `calls` records method + url. */
function serialFetch(over: { captures?: Record<string, unknown[]>; text?: string } = {}) {
  const calls: string[] = [];
  const caps = over.captures ?? {
    "": [{ name: "2026-10-27T21-03-11-uart.log", size_bytes: 2048, mtime: Date.now() / 1000 - 120 }],
  };
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url}`);
    if (url.startsWith("/uart/capture?") && method === "POST") {
      return json({ ok: true, duration: 5, lines: 42, errors: 3, filename: "2026-10-28T09-00-00-uart.log", path: "/ws/logs/2026-10-28T09-00-00-uart.log" });
    }
    if (url.startsWith("/uart/captures/") && method === "DELETE") return json({ ok: true, name: "x" });
    if (url.startsWith("/uart/captures/")) return json({ ok: true, name: "x", size_bytes: 40, text: over.text ?? "U-Boot SPL board init\nStarting kernel ...\n" });
    if (url.startsWith("/uart/captures")) {
      const dev = new URL(url, "http://x").searchParams.get("device") ?? "";
      return json({ ok: true, device: dev || null, captures: caps[dev] ?? [] });
    }
    return json({}, 404);
  });
  vi.stubGlobal("fetch", f);
  return calls;
}

describe("serial tab", () => {
  it("connects with no adb device selected", async () => {
    serialFetch();
    useApp.setState({ device: null });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    const ws = wsAt("/uart/stream")[0] as FakeWS;
    expect(ws).toBeDefined();
    act(() => ws.open());
    expect(firstFrame(ws)).toEqual({});
    expect(screen.queryByText("No device selected")).not.toBeInTheDocument();
    act(() => ws.text("U-Boot SPL board init\n"));
    expect(await screen.findByText(/U-Boot SPL board init/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Capture 60 s/ })).toBeEnabled();
  });

  it("keeps the stream and its lines when an adb device shows up mid-boot", async () => {
    serialFetch();
    useApp.setState({ device: null });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    const ws = wsAt("/uart/stream")[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text("Starting kernel ...\n"));
    await screen.findByText(/Starting kernel/);
    act(() => useApp.setState({ device: DEVICE }));
    act(() => ws.text("init: second stage\n"));
    expect(await screen.findByText(/init: second stage/)).toBeInTheDocument();
    expect(screen.getByText(/Starting kernel/)).toBeInTheDocument();
    expect(wsAt("/uart/stream")).toHaveLength(1);
  });

  it("keeps up to 5000 lines and can clear, copy and open them large", async () => {
    serialFetch();
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    const ws = wsAt("/uart/stream")[0] as FakeWS;
    act(() => ws.open());
    act(() => ws.text(`${Array.from({ length: 600 }, (_, i) => `boot line ${i}`).join("\n")}\n`));
    await waitFor(() => expect(document.querySelector("pre.term")?.textContent).toContain("boot line 599"));
    // the old view kept 500 lines, so the first 100 were gone by now
    expect(document.querySelector("pre.term")?.textContent?.startsWith("boot line 0\n")).toBe(true);
    expect(screen.getByText("Last 600 lines, up to 5,000 kept")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy all" }));
    await waitFor(() => expect(clip.copyText).toHaveBeenCalled());
    const copied = clip.copyText.mock.calls.at(-1)?.[0] ?? "";
    expect(copied.startsWith("boot line 0\n")).toBe(true);
    expect(copied.endsWith("boot line 599")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Open large/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Serial output");
    expect(dialog).toHaveTextContent("600 lines");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(await screen.findByText("Connected, nothing on the serial line yet")).toBeInTheDocument();
  });

  it("captures for the chosen length, keeps the result, opens and deletes captures", async () => {
    const calls = serialFetch();
    useApp.setState({ device: null });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    expect(await screen.findByText("2026-10-27T21-03-11-uart.log")).toBeInTheDocument();
    expect(calls).toContain("GET /uart/captures");

    fireEvent.change(screen.getByLabelText(/Capture length/), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Capture 5 s" }));
    expect(await screen.findByText("Captured 2026-10-28T09-00-00-uart.log: 42 lines, 3 with error keywords.")).toBeInTheDocument();
    expect(calls).toContain("POST /uart/capture?duration=5");

    fireEvent.click(screen.getByRole("button", { name: "Open 2026-10-27T21-03-11-uart.log" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog).toHaveTextContent("Starting kernel ..."));
    expect(calls).toContain("GET /uart/captures/2026-10-27T21-03-11-uart.log");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    fireEvent.click(screen.getByRole("button", { name: "Delete 2026-10-27T21-03-11-uart.log" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Delete 2026-10-27T21-03-11-uart.log?");
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls).toContain("DELETE /uart/captures/2026-10-27T21-03-11-uart.log"));
    // the result of the capture is still on screen
    expect(screen.getByText(/Captured 2026-10-28T09-00-00-uart\.log/)).toBeInTheDocument();
  });

  it("cancel leaves the capture alone", async () => {
    const calls = serialFetch();
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete 2026-10-27T21-03-11-uart.log" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
  });

  it("lists captures filed under the device and those without one", async () => {
    const calls = serialFetch({
      captures: {
        [DEVICE]: [{ name: "2026-10-28T10-00-00-uart.log", size_bytes: 10, mtime: Date.now() / 1000 - 10 }],
        "": [{ name: "2026-10-28T08-00-00-uart.log", size_bytes: 10, mtime: Date.now() / 1000 - 3600 }],
      },
    });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    expect(await screen.findByText("2026-10-28T10-00-00-uart.log")).toBeInTheDocument();
    expect(screen.getByText("2026-10-28T08-00-00-uart.log")).toBeInTheDocument();
    expect(calls).toEqual(expect.arrayContaining([`GET /uart/captures?device=${DEVICE}`, "GET /uart/captures"]));
    fireEvent.click(screen.getByRole("button", { name: "Open 2026-10-28T10-00-00-uart.log" }));
    await waitFor(() => expect(calls).toContain(`GET /uart/captures/2026-10-28T10-00-00-uart.log?device=${DEVICE}`));
  });

  it("shows a failed capture until it is dismissed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/uart/capture?") && init?.method === "POST") return json({ ok: false, duration: 60, error: "serial port busy" });
        if (url.startsWith("/uart/captures")) return json({ ok: true, device: null, captures: [] });
        return json({}, 404);
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Serial/ }));
    fireEvent.click(screen.getByRole("button", { name: "Capture 60 s" }));
    expect(await screen.findByText(/serial port busy/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/serial port busy/)).not.toBeInTheDocument();
  });
});

describe("search tab", () => {
  const hits = {
    ok: true,
    timing_ms: 12,
    data: {
      pattern: "panic|oops|fatal",
      truncated: true,
      match_count: 3,
      matches: [
        { path: "devices/x/logs/a-dmesg.txt", line_number: 3, content: "Kernel panic - not syncing: fatal exception" },
        { path: "devices/x/logs/a-dmesg.txt", line_number: 9, content: "oops again" },
        { path: "devices/x/logs/b-uart.log", line_number: 1, content: "nothing to mark here" },
      ],
    },
  };

  it("starts from panic|oops|fatal, marks the hits, and reports files, time and the limit", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return json(hits);
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Search all recordings/ }));
    expect(screen.getByLabelText("search")).toHaveValue("panic|oops|fatal");
    fireEvent.change(screen.getByLabelText("most hits to list"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText(/3 hits in 2 files · 12 ms; only the first 500 are listed/)).toBeInTheDocument();
    const q = new URL(urls[0] as string, "http://x").searchParams;
    expect(q.get("pattern")).toBe("panic|oops|fatal");
    expect(q.get("max")).toBe("500");
    const marks = Array.from(document.querySelectorAll(".log-search-hit mark")).map((m) => m.textContent);
    expect(marks).toEqual(["panic", "fatal", "oops"]);
  });

  it("collects dmesg and runs the search again", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        if (url.startsWith("/api/log/dmesg")) return json({ ok: true, data: { lines: 120, errors: 2, duration_captured_ms: 5000 } });
        return json(hits);
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Search all recordings/ }));
    fireEvent.click(screen.getByRole("button", { name: /Collect dmesg and search/ }));
    expect(await screen.findByText("dmesg collected: 120 lines, 2 with error keywords.")).toBeInTheDocument();
    await waitFor(() => expect(urls.filter((u) => u.startsWith("/api/log/search"))).toHaveLength(1));
    expect(urls[0]).toBe(`/api/log/dmesg?device=${DEVICE}`);
  });

  it("marks regex hits and leaves text alone when the browser cannot compile the pattern", () => {
    render(<p data-testid="m">{markRegex("a1b22c", "\\d+")}</p>);
    expect(Array.from(screen.getByTestId("m").querySelectorAll("mark")).map((m) => m.textContent)).toEqual(["1", "22"]);
    expect(markRegex("abc", "(?<")).toBe("abc");
    expect(markRegex("abc", "^")).toBe("abc");
  });
});
