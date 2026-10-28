/**
 * Console (v3): both terminals reuse the existing session hooks, so the
 * tests drive them through a fake WebSocket and a fake xterm. Covers
 * connect, typing / output, saved commands (incl. storage failure and the
 * confirm for board-changing ones), serial break-in, the adb actions with
 * their plain-words result codes, and the empty states.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const term = vi.hoisted(() => ({ instances: [] as any[] }));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    rows = 24;
    cols = 80;
    options: Record<string, unknown> = {};
    written: unknown[] = [];
    cleared = 0;
    cb: ((d: string) => void) | null = null;
    buffer = { active: { length: 2, baseY: 0, cursorY: 1, getLine: (i: number) => ({ isWrapped: false, translateToString: () => (i === 0 ? "first line" : "board_x:/ #") }) } };
    constructor(public opts: Record<string, unknown>) {
      term.instances.push(this);
    }
    loadAddon() {}
    open() {}
    onData(cb: (d: string) => void) {
      this.cb = cb;
      return { dispose() {} };
    }
    write(c: unknown) {
      this.written.push(c);
    }
    clear() {
      this.cleared += 1;
    }
    focus() {}
    dispose() {}
    hasSelection() {
      return false;
    }
    getSelection() {
      return "";
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));

import { SnackbarHost } from "../../../components/v3/ui";
import { useApp } from "../../../stores/app";
import { ConsolePage } from "./ConsolePage";

class FakeWs {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
  static all: FakeWs[] = [];
  readyState = 1;
  binaryType = "";
  sent: unknown[] = [];
  listeners: Record<string, ((ev: any) => void)[]> = {};
  constructor(public url: string) {
    FakeWs.all.push(this);
    queueMicrotask(() => this.emit("open", {}));
  }
  addEventListener(n: string, cb: (ev: any) => void) {
    (this.listeners[n] ??= []).push(cb);
  }
  emit(n: string, ev: any) {
    for (const cb of this.listeners[n] ?? []) cb(ev);
  }
  send(d: unknown) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
  }
}

const DEV = "0123456789ABCDEF";

function mockFetch(over: Record<string, () => Response | Promise<Response>> = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      for (const [k, fn] of Object.entries(over)) if (url.includes(k)) return fn();
      if (url.includes("/api/bench/board")) {
        return Response.json({ ok: true, boards: [], active: null, serial: { com: "COM7", baud: 115200, bound: true, readers: 2 } });
      }
      if (url.includes("/audit")) return Response.json({ ok: true, since: "", until: "", events: [] });
      return Response.json({ ok: false }, { status: 404 });
    }),
  );
  return calls;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConsolePage />
      <SnackbarHost />
    </QueryClientProvider>,
  );
}

const adbWs = () => FakeWs.all.find((w) => w.url.endsWith("/terminal/ws"))!;
const uartWs = () => FakeWs.all.find((w) => w.url.endsWith("/uart/stream"))!;

async function connectAdb() {
  await waitFor(() => expect(adbWs()).toBeTruthy());
  await waitFor(() => expect(adbWs().sent.length).toBeGreaterThan(0));
  await act(async () => adbWs().emit("message", { data: JSON.stringify({ type: "ready", device: DEV, transport: "adb", session_id: "s1" }) }));
}

beforeEach(() => {
  term.instances.length = 0;
  FakeWs.all.length = 0;
  vi.stubGlobal("WebSocket", FakeWs);
  useApp.setState({ lang: "zh", device: DEV });
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Console", () => {
  it("opens an adb shell for the selected device and shows it connected with the prompt", async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(adbWs()).toBeTruthy());
    await waitFor(() => expect(adbWs().sent.length).toBeGreaterThan(0));
    expect(JSON.parse(adbWs().sent[0] as string)).toMatchObject({ device: DEV, read_only: false });
    await connectAdb();
    // output arrives -> written to xterm, prompt read from the cursor line
    await act(async () => adbWs().emit("message", { data: new ArrayBuffer(2) }));
    expect(term.instances[0].written.length).toBe(1);
    await waitFor(() => expect(screen.getByText(/已连接 · root · board_x:\/ #/)).toBeInTheDocument());
    // keystrokes go out as bytes
    act(() => term.instances[0].cb("ls\r"));
    expect(Array.from(adbWs().sent.at(-1) as Uint8Array)).toEqual([108, 115, 13]);
  });

  it("serial tab connects with write on and shows com / baud / readers", async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(uartWs()).toBeTruthy());
    await waitFor(() => expect(uartWs().sent.length).toBeGreaterThan(0));
    expect(JSON.parse(uartWs().sent[0] as string)).toMatchObject({ device: DEV, write: true });
    await act(async () => uartWs().emit("message", { data: JSON.stringify({ type: "ready", transport: "serial", write: true }) }));
    fireEvent.click(screen.getByRole("tab", { name: /串口/ }));
    await waitFor(() => expect(screen.getByText(/COM7 · 115200 baud · 2 个读者/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "发送 Ctrl+C" }));
    expect(Array.from(uartWs().sent.at(-1) as Uint8Array)).toEqual([3]);
  });

  it("Stop in bootloader asks first, then streams the result into the terminal", async () => {
    const calls = mockFetch({
      "/api/serial/break-in": () =>
        new Response('{"ev":"progress","phase":"ready","reboot":"adb"}\n{"ev":"done","ok":true}\n', { headers: { "content-type": "application/x-ndjson" } }),
    });
    renderPage();
    await waitFor(() => expect(uartWs().sent.length).toBeGreaterThan(0));
    await act(async () => uartWs().emit("message", { data: JSON.stringify({ type: "ready", write: true }) }));
    fireEvent.click(screen.getByRole("tab", { name: /串口/ }));
    fireEvent.click(screen.getByRole("button", { name: /停在 bootloader/ }));
    expect(calls.some((c) => c.includes("break-in"))).toBe(false); // nothing before the confirm
    fireEvent.click(screen.getByRole("button", { name: "重启并停下" }));
    await waitFor(() => expect(calls.some((c) => c.startsWith("POST") && c.includes("/api/serial/break-in"))).toBe(true));
    await waitFor(() => expect(term.instances.some((x) => x.written.some((w: unknown) => String(w).includes("已停在 bootloader 提示符")))).toBe(true));
  });

  it("saved commands: read-only ones run at once, board-changing ones need the confirm", async () => {
    mockFetch();
    renderPage();
    await connectAdb();
    fireEvent.click(screen.getByRole("button", { name: "运行 dumpsys battery" }));
    expect(new TextDecoder().decode(adbWs().sent.at(-1) as Uint8Array)).toBe("dumpsys battery\r");
    const before = adbWs().sent.length;
    fireEvent.click(screen.getByRole("button", { name: "运行 setenforce 0" }));
    expect(adbWs().sent.length).toBe(before); // held back
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("会改动板子");
    fireEvent.click(within(alert).getByRole("button", { name: "运行" }));
    expect(new TextDecoder().decode(adbWs().sent.at(-1) as Uint8Array)).toBe("setenforce 0\r");
  });

  it("saved commands are kept in localStorage and survive a failing storage", async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    fireEvent.change(screen.getByLabelText("命令"), { target: { value: "uname -a" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByText("uname -a")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("alb.console.snippets") as string).map((s: { cmd: string }) => s.cmd)).toContain("uname -a");
  });

  it("does not crash when localStorage throws", async () => {
    mockFetch();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderPage();
    expect(screen.getByText("getprop ro.build.fingerprint")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除 dumpsys battery" }));
    expect(screen.queryByText("dumpsys battery")).toBeNull();
  });

  it("running without a connection says so instead of silently dropping the command", async () => {
    mockFetch();
    useApp.setState({ device: null });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "运行 dumpsys battery" }));
    expect(await screen.findByText(/没有连上/)).toBeInTheDocument();
    expect(screen.getByText(/没有选中的设备,adb shell 连不上/)).toBeInTheDocument();
  });

  it("opens on the serial tab when adb has no device to talk to", async () => {
    mockFetch({ "/devices": () => Response.json({ ok: true, transport: "AdbTransport", devices: [] }) });
    useApp.setState({ device: null });
    renderPage();
    await waitFor(() => {
      const selected = screen.getAllByRole("tab").find((el) => el.getAttribute("aria-selected") === "true");
      expect(selected?.textContent).toMatch(/串口/);
    });
  });

  it("lists recent terminal commands from the audit log", async () => {
    mockFetch({
      "/audit": () =>
        Response.json({
          ok: true,
          since: "",
          until: "",
          events: [
            { ts: "2026-10-28T01:02:03Z", session_id: "term-1", source: "terminal", kind: "command", summary: "$ ls /data", data: { line: "ls /data" } },
            { ts: "2026-10-28T01:03:03Z", session_id: "chat-1", source: "chat", kind: "user", summary: "hello" },
          ],
        }),
    });
    renderPage();
    const card = (await screen.findByText("最近命令")).closest("article") as HTMLElement;
    await waitFor(() => expect(within(card).getByText("ls /data")).toBeInTheDocument());
    expect(within(card).queryByText("hello")).toBeNull();
  });

  it("shows an error with retry when the audit log cannot be read", async () => {
    mockFetch({ "/audit": () => new Response("x", { status: 500 }) });
    renderPage();
    expect(await screen.findByText(/读不到操作记录/)).toBeInTheDocument();
  });

  it("adb unroot with no effect is explained in plain words, after a confirm", async () => {
    const calls = mockFetch({
      "/api/adb/unroot": () => Response.json({ ok: false, error: { code: "ADB_UNROOT_NO_EFFECT", message: "came back as uid 0" } }),
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "adb unroot" }));
    expect(calls.some((c) => c.includes("/api/adb/unroot"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "回到普通用户" }));
    await waitFor(() => expect(screen.getByText("ADB_UNROOT_NO_EFFECT")).toBeInTheDocument());
    expect(screen.getByText(/不管 adb unroot 怎么要求都保持 root/)).toBeInTheDocument();
  });

  it("adb root success is reported", async () => {
    mockFetch({ "/api/adb/root": () => Response.json({ ok: true, data: {} }) });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "adb root" }));
    fireEvent.click(screen.getByRole("button", { name: "切到 root" }));
    expect(await screen.findByText("adb root 成功。")).toBeInTheDocument();
  });

  it("new terminal menu opens another one; closing the last shows the empty state", async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /新建终端/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /串口控制台/ }));
    expect(screen.getAllByRole("tab").length).toBe(3);
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "关闭这个终端" }));
    expect(screen.getByText("没有打开的终端")).toBeInTheDocument();
  });

  it("reads terminal colours from the CSS variables", async () => {
    mockFetch();
    document.documentElement.style.setProperty("--alb-color-terminal-bg", "rgb(1, 2, 3)");
    renderPage();
    await waitFor(() => expect(term.instances.length).toBeGreaterThan(0));
    expect((term.instances[0].opts.theme as { background?: string }).background).toBe("rgb(1, 2, 3)");
    document.documentElement.style.removeProperty("--alb-color-terminal-bg");
  });

  it("English labels when the language is English", async () => {
    mockFetch();
    useApp.setState({ lang: "en" });
    renderPage();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Console");
    expect(screen.getByText("Saved commands")).toBeInTheDocument();
  });
});
