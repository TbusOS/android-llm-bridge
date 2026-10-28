import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ search: {} as Record<string, string> }));
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
import { LineSplitter, makeMatcher, parseQuery, parseThreadtime } from "./logcatModel";
import { parseDmesg } from "./OtherPanels";

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
    expect(await screen.findByText("Kernel panic - not syncing")).toBeInTheDocument();
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
