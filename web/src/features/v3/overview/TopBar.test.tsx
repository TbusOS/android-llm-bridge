/**
 * Top bar: the chosen device is kept when it drops out of the list, the
 * three ways the device list can be empty read differently, the menu
 * moves with the arrow keys, and the model chip reflects backend health.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "../../../components/v3/TopBar";
import { useApp } from "../../../stores/app";
import { renderPage, stubFetch } from "../../../test/renderPage";

const A = "0123456789ABCDEF";
const B = "FEDCBA9876543210";

const BASE = {
  "/agent/status": { v: "1", agents: [], forwarders: { adb: { bound: false, port: 0 }, serial: { bound: false, port: 0, configured: false, com: null, baud: 0 } } },
  "/api/bench/board": { ok: true, table: null, boards: [], active: null, source: "env", problem: null, serial: { com: null, baud: 0, bound: false, readers: 0 } },
  "/api/flash/status": { ok: true, available: false, busy: false, job: null, partitions: [], partitions_problem: "", board: null },
  "/playground/backends": { backends: [] },
};
const dev = (serial: string, extra: Record<string, string> = {}) => ({ serial, state: "device", model: "Board_X", ...extra });

function routes(extra: Record<string, unknown>) {
  return { ...BASE, ...extra };
}

async function openMenu() {
  const trigger = document.querySelector(".board-switch") as HTMLButtonElement;
  fireEvent.click(trigger);
  return screen.findByRole("menu");
}

describe("TopBar device choice", () => {
  beforeEach(() => useApp.setState({ lang: "en", device: null }));
  afterEach(() => vi.unstubAllGlobals());

  it("picks the first device when nothing was chosen yet", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: true, transport: "AdbTransport", devices: [dev(A), dev(B)] } }))));
    await renderPage(<TopBar />);
    await waitFor(() => expect(useApp.getState().device).toBe(A));
  });

  it("keeps the chosen device when it drops out of the list and shows it as offline", async () => {
    useApp.setState({ device: A });
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: true, transport: "AdbTransport", devices: [dev(B)] } }))));
    await renderPage(<TopBar />);
    // the list arrived with only B in it
    await screen.findByText("offline", { selector: ".board-switch__meta" });
    expect(useApp.getState().device).toBe(A);
    expect(document.querySelector(".board-switch")?.textContent).toContain(A);
    const menu = await openMenu();
    const gone = within(menu).getByText(A).closest("[role='menuitem']") as HTMLElement;
    expect(gone.getAttribute("aria-disabled")).toBe("true");
    expect(gone.textContent).toContain("not in the adb device list");
    expect(gone.textContent).toContain("pick another one below");
    // the user can still switch on purpose
    fireEvent.click(within(menu).getByText(B));
    expect(useApp.getState().device).toBe(B);
  });

  it("shows the real transport, product and model for each device", async () => {
    useApp.setState({ device: A });
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: true, transport: "SshTransport", devices: [dev(A, { product: "board_x" })] } }))));
    await renderPage(<TopBar />);
    const menu = await openMenu();
    await waitFor(() => expect(within(menu).getByText(A).closest("[role='menuitem']")?.textContent).toContain("ssh · device · board_x · Board_X"));
  });
});

describe("TopBar device list problems", () => {
  beforeEach(() => useApp.setState({ lang: "en", device: null }));
  afterEach(() => vi.unstubAllGlobals());

  it("GET /devices failing reads as the service, with a retry that asks again", async () => {
    const base = stubFetch(routes({}));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/devices")) return new Response("boom", { status: 502 });
      return base(input);
    });
    vi.stubGlobal("fetch", fetchMock);
    await renderPage(<TopBar />);
    await waitFor(() => expect(document.querySelector(".chans")?.textContent).toContain("no list"));
    const menu = await openMenu();
    expect(within(menu).getByText("Can't read the device list")).toBeTruthy();
    expect(menu.textContent).toContain("GET /devices failed");
    const before = fetchMock.mock.calls.filter(([u]) => String(u).startsWith("/devices")).length;
    fireEvent.click(within(menu).getByText("Read again"));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([u]) => String(u).startsWith("/devices")).length).toBeGreaterThan(before));
    // retry keeps the menu open so the result shows in place
    expect(screen.getByRole("menu")).toBeTruthy();
  });

  it("a broken transport names the transport and its error, not 'no devices'", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: false, transport: "AdbTransport", devices: [], error: "AdbError: cannot connect to daemon" } }))));
    await renderPage(<TopBar />);
    await waitFor(() => expect(document.querySelector(".chans")?.textContent).toContain("unavailable"));
    const menu = await openMenu();
    expect(within(menu).getByText("adb is not working")).toBeTruthy();
    expect(menu.textContent).toContain("cannot connect to daemon");
    expect(menu.textContent).not.toContain("No devices in adb");
  });

  it("an empty list says what to try next", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: true, transport: "AdbTransport", devices: [] } }))));
    await renderPage(<TopBar />);
    await waitFor(() => expect(document.querySelector(".chans")?.textContent).toContain("no device"));
    const menu = await openMenu();
    expect(within(menu).getByText("No devices in adb")).toBeTruthy();
    expect(menu.textContent).toContain("plug the board into the bench");
    expect(menu.textContent).toContain("adb start-server");
    expect(menu.textContent).toContain("bench tunnel");
  });

  it("says it is still reading before the list arrives", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => (String(input).startsWith("/devices") ? new Promise<Response>(() => {}) : stubFetch(routes({}))(input))));
    await renderPage(<TopBar />);
    const menu = await openMenu();
    expect(within(menu).getByText("Reading the device list…")).toBeTruthy();
  });
});

describe("TopBar menu keyboard", () => {
  beforeEach(() => useApp.setState({ lang: "en", device: B }));
  afterEach(() => vi.unstubAllGlobals());

  it("opens on ArrowDown at the selected row, moves with arrows / Home / End and closes on Escape", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: true, transport: "AdbTransport", devices: [dev(A), dev(B)] } }))));
    await renderPage(<TopBar />);
    const trigger = document.querySelector(".board-switch") as HTMLButtonElement;
    // wait for the list itself, not just the stored choice
    await waitFor(() => expect(document.querySelector(".chans")?.textContent).toContain(B.slice(0, 10)));
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const menu = await screen.findByRole("menu");
    const rowA = within(menu).getByText(A).closest("[role='menuitem']") as HTMLElement;
    const rowB = within(menu).getByText(B).closest("[role='menuitem']") as HTMLElement;
    await waitFor(() => expect(document.activeElement).toBe(rowB));
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rowA); // wraps around
    fireEvent.keyDown(menu, { key: "End" });
    expect(document.activeElement).toBe(rowB);
    fireEvent.keyDown(menu, { key: "Home" });
    expect(document.activeElement).toBe(rowA);
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(document.activeElement).toBe(rowB);
    fireEvent.keyDown(menu, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });
});

describe("TopBar menu focus while rows change", () => {
  beforeEach(() => useApp.setState({ lang: "en", device: B }));
  afterEach(() => vi.unstubAllGlobals());

  it("keeps focus in the menu when the list arrives while it is open", async () => {
    let answer: (r: Response) => void = () => {};
    const base = stubFetch(routes({}));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        String(input).startsWith("/devices") ? new Promise<Response>((r) => { answer = r; }) : base(input),
      ),
    );
    await renderPage(<TopBar />);
    const trigger = document.querySelector(".board-switch") as HTMLButtonElement;
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const menu = await screen.findByRole("menu");
    await waitFor(() => expect(document.activeElement?.textContent).toContain("Reading the device list"));
    await act(async () => {
      answer(new Response(JSON.stringify({ ok: true, transport: "AdbTransport", devices: [dev(A), dev(B)] }), { status: 200 }));
    });
    // the loading row it sat on is gone; focus lands on the selected device, not <body>
    await waitFor(() => expect(document.activeElement).toBe(within(menu).getByText(B).closest("[role='menuitem']")));
  });
});

describe("TopBar model chip", () => {
  beforeEach(() => useApp.setState({ lang: "en", device: null }));
  afterEach(() => vi.unstubAllGlobals());

  const chip = () => Array.from(document.querySelectorAll(".chans .chip")).find((c) => c.querySelector("b")?.textContent === "LLM") as HTMLElement | undefined;

  it("shows the reachable backend and its latency", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch(
          routes({
            "/devices": { ok: true, transport: "AdbTransport", devices: [] },
            "/playground/backends/ollama/health": { name: "ollama", reachable: true, latency_ms: 42, model: "qwen2.5:7b", reason: null },
            "/playground/backends": { backends: [{ name: "ollama", status: "beta", host_compute_type: "remote", supports_tool_calls: true, requires: [], description: "" }] },
          }),
        ),
      ),
    );
    await renderPage(<TopBar />);
    await waitFor(() => expect(chip()?.textContent).toContain("ollama 42 ms"));
    expect(chip()?.querySelector(".dot--ok")).toBeTruthy();
  });

  it("goes red when every probed backend is down, and ignores planned ones", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch(
          routes({
            "/devices": { ok: true, transport: "AdbTransport", devices: [] },
            "/playground/backends/ollama/health": { name: "ollama", reachable: false, latency_ms: null, model: null, reason: "down", error: "connection refused" },
            "/playground/backends": {
              backends: [
                { name: "ollama", status: "beta", host_compute_type: "remote", supports_tool_calls: true, requires: [], description: "" },
                { name: "llama-cpp", status: "planned", host_compute_type: "cpu", supports_tool_calls: false, requires: [], description: "" },
              ],
            },
          }),
        ),
      ),
    );
    await renderPage(<TopBar />);
    await waitFor(() => expect(chip()?.textContent).toContain("down"));
    expect(chip()?.querySelector(".dot--bad")).toBeTruthy();
    expect(chip()?.getAttribute("title")).toContain("llama-cpp · planned");
  });

  it("speaks Chinese", async () => {
    useApp.setState({ lang: "zh" });
    vi.stubGlobal("fetch", vi.fn(stubFetch(routes({ "/devices": { ok: true, transport: "AdbTransport", devices: [] } }))));
    await renderPage(<TopBar />);
    await waitFor(() => expect(Array.from(document.querySelectorAll(".chans b")).map((b) => b.textContent)).toContain("模型"));
    await act(async () => {
      fireEvent.click(document.querySelector(".board-switch") as HTMLElement);
    });
    expect((await screen.findByRole("menu")).textContent).toContain("adb 里没有设备");
  });
});
