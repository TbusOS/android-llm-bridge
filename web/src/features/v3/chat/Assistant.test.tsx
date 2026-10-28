/**
 * The shared conversation (components/v3/Assistant): what came back from the
 * old chat page. Files a turn produced, retry, Esc stops a reply (and only
 * then keeps the shell from closing the sheet), full tool arguments / output
 * with the large view, session id, stopped vs failed, the growing composer
 * and focus coming back. The conversation hook is faked.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const chat = vi.hoisted(() => ({
  state: { turns: [] as unknown[], sessionId: null as string | null, isStreaming: false },
  send: vi.fn(),
  reset: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
}));
const clip = vi.hoisted(() => ({ copyText: vi.fn(async (_: string) => true) }));

vi.mock("../../chat/useChatStream", () => ({
  useChatStream: () => ({ ...chat.state, send: chat.send, reset: chat.reset, cancel: chat.cancel, retry: chat.retry }),
}));
vi.mock("../../../lib/clipboard", () => clip);

import { AssistantProvider, COMPOSER_MAX_PX, Composer, Conversation } from "../../../components/v3/Assistant";
import { SnackbarHost, useSnack } from "../../../components/v3/ui";
import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";

const ROOT = "/srv/alb-ws";

function mockFetch(root: string | null = ROOT) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/workspace/files?path=")) {
        return root ? Response.json({ ok: true, path: "", absolute_path: root, entries: [] }) : new Response("x", { status: 500 });
      }
      if (url.startsWith("/workspace/files/preview/devices/board-a/logs/logcat-1.txt")) {
        return Response.json({ ok: true, path: "x", size_bytes: 30, is_binary: false, truncated: false, text: "first line\nE fatal thing\n", encoding: "utf-8" });
      }
      return Response.json({ ok: false }, { status: 404 });
    }),
  );
  return calls;
}

let qc: QueryClient;

function tree() {
  return (
    <QueryClientProvider client={qc}>
      <AssistantProvider>
        <Conversation />
        <Composer />
        <SnackbarHost />
      </AssistantProvider>
    </QueryClientProvider>
  );
}

const user = (id: string, content: string) => ({ id, role: "user", content, toolCalls: [], artifacts: [], status: "done" });
const ai = (id: string, over: object) => ({ id, role: "assistant", content: "", toolCalls: [], artifacts: [], status: "done", ...over });

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  chat.state = { turns: [], sessionId: null, isStreaming: false };
  for (const f of [chat.send, chat.reset, chat.cancel, chat.retry, clip.copyText]) f.mockClear();
  useApp.setState({ lang: "zh", device: "board-a", backend: "ollama", model: null });
  useShell.setState({ source: "host", agentMode: true, context: null, sheetOpen: false });
  useSnack.setState({ text: null }); // a toast from the previous test is also role=status
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("files a turn produced", () => {
  it("shows each file; workspace files open or download, others offer their path", async () => {
    chat.state.turns = [
      user("u1", "grab logs and a screenshot"),
      ai("a1", {
        content: "Saved both.",
        artifacts: [`${ROOT}/devices/board-a/logs/logcat-1.txt`, `${ROOT}/devices/board-a/screenshots/s1.png`, "/tmp/elsewhere/report.zip"],
      }),
    ];
    const calls = mockFetch();
    render(tree());
    expect(screen.getByText("产出文件")).toBeInTheDocument();
    // full path on hover
    expect(screen.getByText("report.zip").closest(".chat__file")?.getAttribute("title")).toBe("/tmp/elsewhere/report.zip");

    const dl = await screen.findByRole("link", { name: "下载 logcat-1.txt" });
    expect(dl.getAttribute("href")).toBe("/workspace/files/download/devices/board-a/logs/logcat-1.txt");
    const shot = screen.getByRole("link", { name: "打开 s1.png" });
    expect(shot.getAttribute("href")).toBe("/devices/board-a/screenshots/s1.png");
    expect(shot.getAttribute("target")).toBe("_blank");
    // outside the workspace: no download, the path can be copied
    expect(screen.queryByRole("link", { name: "下载 report.zip" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "复制 report.zip 的路径" }));
    await waitFor(() => expect(clip.copyText).toHaveBeenCalledWith("/tmp/elsewhere/report.zip"));

    // a text file opens in the log dialog
    fireEvent.click(screen.getByRole("button", { name: "打开 logcat-1.txt" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("E fatal thing")).toBeInTheDocument();
    expect(within(dialog).getByText("devices/board-a/logs/logcat-1.txt")).toBeInTheDocument();
    expect(calls.some((u) => u === "/workspace/files/preview/devices/board-a/logs/logcat-1.txt?max_bytes=1048576")).toBe(true);
  });

  it("when the workspace root cannot be read, files are listed without links", async () => {
    chat.state.turns = [user("u1", "q"), ai("a1", { content: "ok", artifacts: [`${ROOT}/devices/board-a/logs/logcat-1.txt`] })];
    mockFetch(null);
    render(tree());
    expect(await screen.findByRole("button", { name: "复制 logcat-1.txt 的路径" })).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("retry", () => {
  it("a failed or stopped last reply offers retry; a finished or running one does not", () => {
    mockFetch();
    chat.state.turns = [user("u1", "q"), ai("a1", { status: "error", error: { code: "CONNECTION_LOST", message: "socket closed" } })];
    const r = render(tree());
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(chat.retry).toHaveBeenCalledTimes(1);

    chat.state.turns = [user("u1", "q"), ai("a1", { status: "cancelled", content: "half", error: { code: "CANCELLED", message: "x" } })];
    r.rerender(tree());
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();

    chat.state.isStreaming = true;
    r.rerender(tree());
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();

    chat.state = { turns: [user("u1", "q"), ai("a1", { content: "fine" })], sessionId: null, isStreaming: false };
    r.rerender(tree());
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("only the last turn offers it", () => {
    mockFetch();
    chat.state.turns = [
      user("u1", "q1"),
      ai("a1", { status: "error", error: { code: "E1", message: "old" } }),
      user("u2", "q2"),
      ai("a2", { status: "error", error: { code: "E2", message: "new" } }),
    ];
    render(tree());
    expect(screen.getAllByRole("button", { name: "重试" }).length).toBe(1);
  });
});

describe("Esc", () => {
  // Same listener as layouts/AppShell: Esc on the window closes the sheet.
  function shellEsc() {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") useShell.getState().closeSheet();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }

  it("stops a reply being generated and keeps the sheet open", () => {
    mockFetch();
    chat.state = { turns: [user("u1", "q"), ai("a1", { status: "streaming", content: "par" })], sessionId: null, isStreaming: true };
    useShell.setState({ sheetOpen: true });
    const off = shellEsc();
    render(tree());
    fireEvent.keyDown(screen.getByLabelText("消息"), { key: "Escape" });
    expect(chat.cancel).toHaveBeenCalledTimes(1);
    expect(useShell.getState().sheetOpen).toBe(true);
    // also from the Stop button, which has focus after a click
    fireEvent.keyDown(screen.getByRole("button", { name: "停止" }), { key: "Escape" });
    expect(chat.cancel).toHaveBeenCalledTimes(2);
    expect(useShell.getState().sheetOpen).toBe(true);
    off();
  });

  it("falls through to the shell when nothing is generating", () => {
    mockFetch();
    useShell.setState({ sheetOpen: true });
    const off = shellEsc();
    render(tree());
    fireEvent.keyDown(screen.getByLabelText("消息"), { key: "Escape" });
    expect(chat.cancel).not.toHaveBeenCalled();
    expect(useShell.getState().sheetOpen).toBe(false);
    off();
  });

  it("does nothing while an input method is composing", () => {
    mockFetch();
    chat.state.isStreaming = true;
    render(tree());
    fireEvent.keyDown(screen.getByLabelText("消息"), { key: "Escape", isComposing: true });
    expect(chat.cancel).not.toHaveBeenCalled();
  });
});

describe("tool calls", () => {
  const LOG = Array.from({ length: 3000 }, (_, i) => `line ${i}`).join("\n");

  it("compact by default; expand shows the full arguments and the whole output; JSON and copy", async () => {
    mockFetch();
    chat.state.turns = [
      user("u1", "tail it"),
      ai("a1", {
        content: "done",
        toolCalls: [{ id: "c1", name: "alb_log_tail", arguments: { path: "/srv/alb-ws/devices/board-a/logs/x.txt", lines: 3000, opts: { from_line: 1 } }, result: { ok: true, data: LOG, artifacts: [] }, status: "done", startedAt: 0, endedAt: 1200 }],
      }),
    ];
    render(tree());
    const head = screen.getByText(/^alb_log_tail /);
    expect(head.textContent).toContain("lines=3000");
    expect(screen.getByText("1.2 s")).toBeInTheDocument();
    expect(screen.getByText("line 0")).toBeInTheDocument(); // first output line, collapsed
    expect(screen.queryByLabelText("alb_log_tail 的输出")).toBeNull();

    const toggle = screen.getByRole("button", { name: /参数和输出/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByLabelText("alb_log_tail 的参数").textContent).toContain('"opts": {\n    "from_line": 1\n  }');
    const out = screen.getByLabelText("alb_log_tail 的输出");
    expect(out.textContent?.length).toBe(LOG.length); // nothing cut (the old v3 card stopped at 4000 chars)
    expect(out.textContent).toContain("line 2999");
    expect(screen.getByText(/输出 · 3000 行/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    expect(screen.getByLabelText("alb_log_tail 的输出").textContent).toContain('"ok": true');
    fireEvent.click(screen.getByRole("button", { name: "文本" }));

    fireEvent.click(screen.getByRole("button", { name: "复制输出" }));
    await waitFor(() => expect(clip.copyText).toHaveBeenCalledWith(LOG));
    expect(await screen.findByText("已复制输出。")).toBeInTheDocument();
  });

  it("Open large shows the output as numbered lines in the log dialog; Esc closes only the dialog", () => {
    mockFetch();
    chat.state.turns = [user("u1", "q"), ai("a1", { content: "x", toolCalls: [{ id: "c1", name: "alb_shell", arguments: { cmd: "dmesg" }, result: { ok: true, data: { stdout: LOG, stderr: "", exit_code: 0 } }, status: "done", startedAt: 0, endedAt: 10 }] })];
    useShell.setState({ sheetOpen: true });
    render(tree());
    fireEvent.click(screen.getByRole("button", { name: /放大查看/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("3000 行")).toBeInTheDocument();
    expect(within(dialog).getByText("line 2999")).toBeInTheDocument();
    expect(within(dialog).getByText("cmd=dmesg")).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a failed call is marked with its error code; a running call has no output yet", () => {
    mockFetch();
    chat.state = {
      turns: [
        user("u1", "q"),
        ai("a1", {
          status: "streaming",
          toolCalls: [
            { id: "c1", name: "alb_devinfo", arguments: {}, result: { ok: false, error: { code: "DEVICE_NOT_FOUND", message: "no such device" } }, status: "done", startedAt: 0, endedAt: 5 },
            { id: "c2", name: "alb_logcat", arguments: { duration: 5 }, status: "running", startedAt: 0 },
          ],
        }),
      ],
      sessionId: null,
      isStreaming: true,
    };
    render(tree());
    expect(screen.getByText("DEVICE_NOT_FOUND: no such device")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    const toggles = screen.getAllByRole("button", { name: /^参数/ });
    expect(toggles.map((b) => b.textContent)).toEqual(["参数和输出", "参数"]);
    expect(screen.getAllByRole("button", { name: /放大查看/ }).length).toBe(1);
  });
});

describe("small things", () => {
  it("shows the session id, short, with the full id on hover", () => {
    mockFetch();
    chat.state.sessionId = "20261028-aaaa1111bbbb";
    render(tree());
    const meta = screen.getByText(/会话 20261028…bbbb/);
    expect(meta.getAttribute("title")).toBe("会话 20261028-aaaa1111bbbb");
  });

  it("a stopped reply looks different from a failed one", () => {
    mockFetch();
    chat.state.turns = [
      user("u1", "q1"),
      ai("a1", { status: "cancelled", content: "partial answer", error: { code: "CANCELLED", message: "已取消" } }),
      user("u2", "q2"),
      ai("a2", { status: "error", error: { code: "BACKEND_UNREACHABLE", message: "refused", suggestion: "start the server" } }),
    ];
    render(tree());
    const stopped = screen.getByRole("status");
    expect(stopped.className).not.toContain("banner--error");
    expect(stopped.textContent).toContain("已停止,这次回答没有说完。");
    expect(stopped.textContent).not.toContain("CANCELLED");
    expect(screen.getByText("partial answer")).toBeInTheDocument();
    const failed = screen.getByRole("alert");
    expect(failed.className).toContain("banner--error");
    expect(failed.textContent).toContain("BACKEND_UNREACHABLE");
    expect(failed.textContent).toContain("提示:start the server");
  });

  it("the composer grows with its text up to a limit", () => {
    mockFetch();
    let h = 0;
    const spy = vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(() => h);
    render(tree());
    const box = screen.getByLabelText("消息") as HTMLTextAreaElement;
    h = 96;
    fireEvent.change(box, { target: { value: "one\ntwo\nthree" } });
    expect(box.style.height).toBe("96px");
    h = 900;
    fireEvent.change(box, { target: { value: "x\n".repeat(60) } });
    expect(box.style.height).toBe(`${COMPOSER_MAX_PX}px`);
    h = 0; // hidden: back to the rows height
    fireEvent.change(box, { target: { value: "" } });
    expect(box.style.height).toBe("");
    spy.mockRestore();
  });

  it("focus comes back to the composer when a reply finishes, unless the user moved to another field", () => {
    mockFetch();
    chat.state.isStreaming = true;
    const r = render(
      <>
        {tree()}
        <input aria-label="elsewhere" />
      </>,
    );
    const box = screen.getByLabelText("消息");
    (document.activeElement as HTMLElement | null)?.blur();
    chat.state.isStreaming = false;
    r.rerender(
      <>
        {tree()}
        <input aria-label="elsewhere" />
      </>,
    );
    expect(document.activeElement).toBe(box);

    chat.state.isStreaming = true;
    r.rerender(
      <>
        {tree()}
        <input aria-label="elsewhere" />
      </>,
    );
    screen.getByLabelText("elsewhere").focus();
    chat.state.isStreaming = false;
    r.rerender(
      <>
        {tree()}
        <input aria-label="elsewhere" />
      </>,
    );
    expect(document.activeElement).toBe(screen.getByLabelText("elsewhere"));
  });

  it("sending keeps focus in the box so Esc can stop the reply", () => {
    mockFetch();
    render(tree());
    const box = screen.getByLabelText("消息");
    fireEvent.change(box, { target: { value: "why?" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(chat.send).toHaveBeenCalledWith("why?");
    expect(document.activeElement).toBe(box);
  });
});
