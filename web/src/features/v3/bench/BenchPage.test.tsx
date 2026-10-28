/**
 * Bench (v3) against a mocked hub: board table and switching (including the
 * busy-serial force step), USB rows by driver state, agent card, log tail,
 * operation table, and the offline / unconfigured states.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApp } from "../../../stores/app";
import { BenchPage } from "./BenchPage";

const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });
const ndjson = (events: object[]) =>
  new Response(events.map((e) => JSON.stringify(e)).join("\n") + "\n", { headers: { "content-type": "application/x-ndjson" } });

const board = (over: object = {}) => ({
  ok: true, table: "/x/boards.toml", active: "board-a", source: "file", problem: null,
  boards: [
    { name: "board-a", serial_com: "COM7", serial_baud: 115200, note: "" },
    { name: "board-b", serial_com: "COM9", serial_baud: 1500000, note: "lab shelf" },
  ],
  serial: { com: "COM7", baud: 115200, bound: true, readers: 1 },
  ...over,
});

const op = (name: string, over: object = {}) => ({ name, group: "host", summary: `${name} summary`, params: [], needs: "", image: false, mutates: false, exclusive: false, timeout_s: 30, ...over });
const OPS = {
  ok: true, agent: { id: "a1", name: "bench-01", build: "aaaaaaaaaaaa111" }, bundle_build: "bbbbbbbbbbbb222", reported: true,
  ops: [
    op("flash", { group: "fastboot", needs: "fastboot-device", image: true, mutates: true, timeout_s: 900 }),
    op("usb-list"),
    op("log-tail", { params: [{ name: "source", kind: "enum", choices: ["agent", "setupapi"], required: true }, { name: "lines", kind: "int" }, { name: "grep", kind: "text" }] }),
  ],
};
const AGENT = { v: "1", agents: [{ agent_id: "a1", name: "bench-01", version: 7, caps: [], current: true, adb_devices: ["0123456789ABCDEF"], com_ports: [{ name: "COM7" }] }], forwarders: { adb: { bound: true, port: 1 }, serial: { bound: true, port: 2, configured: true, com: "COM7", baud: 115200 } } };

const dev = (over: object) => ({ mode: "other", what: "", driver: "ok", hint: "", vid: "0000", pid: "0000", serial: "", windows_id: "", name: "", ...over });
const USB = {
  ok: true, ev: "done",
  data: {
    devices: [
      dev({ mode: "fastboot", driver: "missing", vid: "18d1", pid: "d00d", serial: "0123456789ABCDEF", hint: "install the WinUSB driver there" }),
      dev({ mode: "adb", vid: "18d1", pid: "4ee7", serial: "89ABCDEF01234567" }),
      dev({ mode: "qualcomm-edl", driver: "problem", vid: "05c6", pid: "9008", windows_id: "5&2a3b", hint: "code 28" }),
      dev({ name: "USB Root Hub", vid: "1d6b", pid: "0002" }),
      dev({ name: "Keyboard", vid: "046d", pid: "c31c" }),
    ],
    boards: [
      dev({ mode: "fastboot", driver: "missing", vid: "18d1", pid: "d00d", serial: "0123456789ABCDEF", hint: "install the WinUSB driver there" }),
      dev({ mode: "adb", vid: "18d1", pid: "4ee7", serial: "89ABCDEF01234567" }),
      dev({ mode: "qualcomm-edl", driver: "problem", vid: "05c6", pid: "9008", windows_id: "5&2a3b", hint: "code 28" }),
    ],
  },
};

interface World { board?: object; agent?: object; ops?: object; usb?: Response | object; onBoardPost?: (b: { name: string; force: boolean }) => object; extra?: (url: string, body: unknown) => Response | undefined }
let calls: { url: string; body: unknown }[] = [];

function mock(w: World = {}) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, body });
      const e = w.extra?.(url, body);
      if (e) return e;
      if (url === "/api/bench/board" && init?.method === "POST") return json(w.onBoardPost?.(body) ?? { ...board(), ok: true });
      if (url.startsWith("/api/bench/board")) return json(w.board ?? board());
      if (url.startsWith("/agent/status")) return json(w.agent ?? AGENT);
      if (url === "/api/bench/ops") return json(w.ops ?? OPS);
      if (url === "/api/bench/ops/usb-list") return w.usb instanceof Response ? w.usb : ndjson([w.usb ?? USB]);
      if (url === "/api/bench/ops/log-tail") return ndjson([{ ev: "done", ok: true, stdout: "09:12 job 41: start\n09:13 job 41: fastboot exit 1: FAILED (remote: nope)", data: { source: "agent", lines: 2, truncated: false } }]);
      if (url.startsWith("/api/flash/status")) return json({ ok: true, available: true, busy: false, job: null, partitions: [], partitions_problem: "", board: "board-a" });
      return json({ detail: `unmocked ${url}` }, 404);
    }),
  );
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BenchPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => useApp.setState({ lang: "en" }));
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Bench page", () => {
  it("lists boards, marks the current one and switches the other", async () => {
    mock();
    renderPage();
    const cell = await screen.findByText("board-b");
    const rowB = cell.closest("tr") as HTMLElement;
    expect(within(rowB).getByText("COM9 · 1500000")).toBeInTheDocument();
    expect(within(rowB).getByText("lab shelf")).toBeInTheDocument();
    const rowA = screen.getByText("board-a").closest("tr") as HTMLElement;
    expect(rowA.getAttribute("aria-selected")).toBe("true");
    expect((within(rowA).getByRole("button", { name: "Current" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(rowB).getByRole("button", { name: "Switch here" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/bench/board" && JSON.stringify(c.body) === JSON.stringify({ name: "board-b", force: false }))).toBe(true));
  });

  it("serial busy: nothing is forced until the user confirms", async () => {
    let n = 0;
    mock({
      onBoardPost: (b) => {
        n += 1;
        return b.force ? { ...board(), ok: true } : { ok: false, code: "BENCH_SERIAL_BUSY", error: "2 reader(s) on the serial console" };
      },
    });
    renderPage();
    const rowB = (await screen.findByText("board-b")).closest("tr") as HTMLElement;
    fireEvent.click(within(rowB).getByRole("button", { name: "Switch here" }));
    const alert = await screen.findByText(/cuts them off/);
    expect(alert.textContent).toContain("board-b");
    expect(n).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Force switch" }));
    await waitFor(() => expect(n).toBe(2));
    const posts = calls.filter((c) => c.url === "/api/bench/board" && c.body);
    expect(posts.map((p) => (p.body as { force: boolean }).force)).toEqual([false, true]);
    await waitFor(() => expect(screen.queryByText(/cuts them off/)).toBeNull());
  });

  it("no board table configured: says how to set it up", async () => {
    mock({ board: board({ table: null, boards: [], active: null }) });
    renderPage();
    expect(await screen.findByText("This hub has no board table")).toBeInTheDocument();
  });

  it("a broken board table shows the hub's reason", async () => {
    mock({ board: board({ boards: [], problem: "BENCH_BOARDS_INVALID: line 3 is wrong" }) });
    renderPage();
    expect(await screen.findByText(/BENCH_BOARDS_INVALID: line 3 is wrong/)).toBeInTheDocument();
  });

  it("USB rows: a missing driver is a red row with its hint, a driver problem is a warning, other devices are folded", async () => {
    mock();
    renderPage();
    const fb = (await screen.findByText("fastboot", { selector: ".usb__mode" })).closest(".usb__row") as HTMLElement;
    expect(fb.className).toContain("usb__row--bad");
    expect(within(fb).getByText("no driver")).toBeInTheDocument();
    expect(within(fb).getByText("install the WinUSB driver there")).toBeInTheDocument();
    expect(fb.textContent).toContain("0123456789ABCDEF");
    const adb = screen.getByText("adb", { selector: ".usb__mode" }).closest(".usb__row") as HTMLElement;
    expect(adb.className).not.toContain("--bad");
    expect(within(adb).getByText("driver ok")).toBeInTheDocument();
    const edl = screen.getByText("download (EDL)").closest(".usb__row") as HTMLElement;
    expect(within(edl).getByText("driver problem")).toBeInTheDocument();
    expect(edl.textContent).toContain("Windows-made id");
    expect(screen.getByText(/5 devices/)).toBeInTheDocument();
    expect(screen.queryByText("Keyboard")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 2 other devices" }));
    expect(screen.getByText("Keyboard")).toBeInTheDocument();
  });

  it("USB listing failure (agent gone) is an error, not an empty list", async () => {
    mock({ usb: { ev: "done", ok: false, error: { code: "AGENT_OFFLINE", message: "no bench agent is connected" } } });
    renderPage();
    expect(await screen.findByText(/AGENT_OFFLINE: no bench agent is connected/)).toBeInTheDocument();
  });

  it("agent offline: shows it, update is disabled and remote start is offered", async () => {
    mock({ agent: { ...AGENT, agents: [] }, ops: { ...OPS, agent: null, ops: [] } });
    renderPage();
    expect(await screen.findByText("The bench agent is not connected", { selector: "h3" })).toBeInTheDocument();
    expect((screen.getByRole("button", { name: "Update agent" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Start remotely" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText("The operation table is empty")).toBeInTheDocument();
  });

  it("agent card shows the version gap, and updating asks first then streams the result", async () => {
    mock({
      extra: (url) =>
        url === "/api/bench/agent-update"
          ? ndjson([{ ev: "progress", phase: "send", done: 1, total: 2, text: "sending 12 files" }, { ev: "done", ok: true, data: { outcome: "updated" } }])
          : undefined,
    });
    renderPage();
    expect(await screen.findByText("bench is behind")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Update" }));
    expect(calls.some((c) => c.url === "/api/bench/agent-update")).toBe(false);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("rolls back");
    fireEvent.click(within(alert).getByRole("button", { name: "Update" }));
    expect(await screen.findByText(/updated and reconnected/)).toBeInTheDocument();
    expect(screen.getByText("sending 12 files")).toBeInTheDocument();
  });

  it("a failed agent update shows its code", async () => {
    mock({ extra: (url) => (url === "/api/bench/agent-update" ? ndjson([{ ev: "done", ok: false, error: { code: "AGENT_UPDATE_ROLLED_BACK", message: "the old version is back" } }]) : undefined) });
    renderPage();
    await screen.findByText("bench is behind");
    fireEvent.click(screen.getByRole("button", { name: "Update" }));
    fireEvent.click(within(await screen.findByRole("alert")).getByRole("button", { name: "Update" }));
    expect(await screen.findByText("AGENT_UPDATE_ROLLED_BACK")).toBeInTheDocument();
  });

  it("bench logs call log-tail with the source, lines and keyword, and mark error lines", async () => {
    mock();
    renderPage();
    await waitFor(() => expect(calls.some((c) => c.url === "/api/bench/ops/log-tail")).toBe(true));
    expect(calls.find((c) => c.url === "/api/bench/ops/log-tail")?.body).toEqual({ params: { source: "agent", lines: 200, grep: "" } });
    const bad = await screen.findByText(/fastboot exit 1: FAILED/);
    expect(bad.className).toBe("e");
    fireEvent.change(screen.getByLabelText("keyword"), { target: { value: "FAILED" } });
    fireEvent.submit(screen.getByLabelText("keyword").closest("form") as HTMLFormElement);
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/bench/ops/log-tail").pop()?.body).toEqual({ params: { source: "agent", lines: 200, grep: "FAILED" } }));
    fireEvent.click(screen.getByRole("button", { name: "USB driver log" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/bench/ops/log-tail").pop()?.body).toMatchObject({ params: { source: "setupapi" } }));
  });

  it("operation table lists the agent's operations with timeouts", async () => {
    mock();
    renderPage();
    const flashRow = (await screen.findByText("flash", { selector: "td" })).closest("tr") as HTMLElement;
    expect(within(flashRow).getByText("15 min")).toBeInTheDocument();
    expect(within(flashRow).getAllByText("✓").length).toBeGreaterThanOrEqual(3);
  });
});
