/**
 * Chat (v3): list grouped by day, the shared live thread, read-only older
 * sessions with "continue", the model / tools / on-board side cards and
 * their loading / error / empty states. The conversation hook is faked; the
 * real AssistantProvider and shell store are used.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const chat = vi.hoisted(() => ({
  state: { turns: [] as any[], sessionId: null as string | null, isStreaming: false },
  send: vi.fn(),
  reset: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
}));

vi.mock("../../chat/useChatStream", () => ({
  useChatStream: () => ({ ...chat.state, send: chat.send, reset: chat.reset, cancel: chat.cancel, retry: chat.retry }),
}));

import { AssistantProvider } from "../../../components/v3/Assistant";
import { SnackbarHost } from "../../../components/v3/ui";
import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";
import { ChatPage3, dayGroup, seedFrom } from "./ChatPage3";

const NOW = Date.now();
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();
const DAY = 86_400_000;

const SESSIONS = [
  { session_id: "20261028-aaaa1111", created: iso(3600_000), backend: "ollama", model: "qwen-x", device: "board-a", turns: 6, last_event_ts: iso(1800_000) },
  { session_id: "20261027-bbbb2222", created: iso(DAY), backend: "openai-compat", model: "cloud-y", device: null, turns: 2, last_event_ts: iso(DAY) },
  { session_id: "20261010-cccc3333", created: iso(18 * DAY), backend: "ollama", model: "qwen-x", device: null, turns: 4, last_event_ts: iso(18 * DAY) },
];

const DETAIL: Record<string, object> = {
  "20261028-aaaa1111": {
    ok: true, session_id: "20261028-aaaa1111", created: null, backend: "ollama", model: "qwen-x", device: "board-a", turns: 6, last_event_ts: null,
    messages: [
      { role: "user", content: "Why did the last boot panic?" },
      { role: "assistant", content: "", tool_calls: [{ id: "c1", name: "alb_diag_pstore", arguments: { device: "board-a" } }] },
      { role: "tool", tool_call_id: "c1", name: "alb_diag_pstore", content: "Kernel panic - not syncing" },
      { role: "assistant", content: "A null pointer in the sensor driver." },
    ],
  },
  "20261027-bbbb2222": {
    ok: true, session_id: "20261027-bbbb2222", created: null, backend: "openai-compat", model: "cloud-y", device: null, turns: 2, last_event_ts: null,
    messages: [{ role: "user", content: "Why is boot slow?" }, { role: "assistant", content: "Two services start serially." }],
  },
  "20261010-cccc3333": {
    ok: true, session_id: "20261010-cccc3333", created: null, backend: "ollama", model: "qwen-x", device: null, turns: 4, last_event_ts: null,
    messages: [{ role: "user", content: "Read the tombstone" }],
  },
};

interface Over {
  sessions?: () => Response;
  detail?: (id: string) => Response;
  backends?: () => Response;
  tools?: () => Response;
}

function mockFetch(o: Over = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (/\/sessions\?/.test(url)) return o.sessions ? o.sessions() : Response.json({ ok: true, sessions: SESSIONS });
      const m = /\/sessions\/([^/?]+)/.exec(url);
      if (m) return o.detail ? o.detail(m[1] as string) : Response.json(DETAIL[m[1] as string] ?? {}, { status: DETAIL[m[1] as string] ? 200 : 404 });
      if (/\/playground\/backends\/[^/]+\/models/.test(url)) return Response.json({ backend: "ollama", models: ["qwen-x", "gemma-z"] });
      if (url.includes("/playground/backends")) {
        return o.backends
          ? o.backends()
          : Response.json({
              backends: [
                { name: "ollama", status: "beta", host_compute_type: "gpu", supports_tool_calls: true, requires: [], description: "" },
                { name: "llama-cpp", status: "planned", host_compute_type: "cpu", supports_tool_calls: false, requires: [], description: "" },
                { name: "openai-compat", status: "beta", host_compute_type: "remote", supports_tool_calls: true, requires: [], description: "" },
              ],
            });
      }
      if (url.endsWith("/tools")) {
        return o.tools
          ? o.tools()
          : Response.json({
              ok: true, count: 3, categories: [],
              tools: [
                { name: "alb_logcat", description: "", category: "logs" },
                { name: "alb_log_search", description: "", category: "logs" },
                { name: "alb_reboot", description: "", category: "power" },
              ],
            });
      }
      return Response.json({ ok: false }, { status: 404 });
    }),
  );
  return calls;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AssistantProvider>
        <ChatPage3 />
        <SnackbarHost />
      </AssistantProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  chat.state = { turns: [], sessionId: null, isStreaming: false };
  chat.send.mockReset();
  chat.reset.mockReset();
  useApp.setState({ lang: "zh", device: "board-a", backend: "ollama", model: null });
  useShell.setState({ source: "host", agentMode: true, context: null });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("helpers", () => {
  it("dayGroup buckets by local calendar day", () => {
    const now = new Date(2026, 9, 28, 12, 0, 0);
    expect(dayGroup(new Date(2026, 9, 28, 0, 5).toISOString(), now)).toBe("today");
    expect(dayGroup(new Date(2026, 9, 27, 23, 59).toISOString(), now)).toBe("yesterday");
    expect(dayGroup(new Date(2026, 9, 20, 9, 0).toISOString(), now)).toBe("earlier");
    expect(dayGroup(null, now)).toBe("earlier");
    expect(dayGroup("not a date", now)).toBe("earlier");
  });

  it("seedFrom keeps the latest messages within the size cap and skips tool output", () => {
    const messages = Array.from({ length: 40 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `m${i} ${"x".repeat(500)}` }));
    const seed = seedFrom({ messages: [...messages, { role: "tool", content: "SECRET-TOOL-OUTPUT" }] } as never, "en");
    expect(seed.length).toBeLessThanOrEqual(4200);
    expect(seed).toContain("m39");
    expect(seed).not.toContain("m0 ");
    expect(seed).not.toContain("SECRET-TOOL-OUTPUT");
  });
});

describe("Chat page", () => {
  it("groups sessions into today / yesterday / earlier with titles from the first message", async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText("Why did the last boot panic?")).toBeInTheDocument();
    expect(screen.getByText("今天")).toBeInTheDocument();
    expect(screen.getByText("昨天")).toBeInTheDocument();
    expect(screen.getByText("更早")).toBeInTheDocument();
    expect(screen.getByText("Why is boot slow?")).toBeInTheDocument();
    // source chip: cloud backend is labelled as a cloud API
    expect(screen.getByText(/云端 API · cloud-y/)).toBeInTheDocument();
  });

  it("search narrows the list", async () => {
    mockFetch();
    renderPage();
    await screen.findByText("Why is boot slow?");
    fireEvent.change(screen.getByLabelText("搜索对话"), { target: { value: "tombstone" } });
    await waitFor(() => expect(screen.queryByText("Why is boot slow?")).toBeNull());
    expect(screen.getByText("Read the tombstone")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("搜索对话"), { target: { value: "zzz" } });
    expect(await screen.findByText("没有匹配的对话")).toBeInTheDocument();
  });

  it("shows an empty state and an error state for the list", async () => {
    mockFetch({ sessions: () => Response.json({ ok: true, sessions: [] }) });
    const first = renderPage();
    expect(await screen.findByText("还没有对话")).toBeInTheDocument();
    first.unmount();
    mockFetch({ sessions: () => new Response("boom", { status: 500 }) });
    renderPage();
    expect(await screen.findByText(/读不到对话列表/)).toBeInTheDocument();
  });

  it("opening an older session is read-only; Continue starts a new chat seeded with it", async () => {
    mockFetch();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Why is boot slow?" }));
    expect(await screen.findByText("Two services start serially.")).toBeInTheDocument();
    expect(screen.getByText("只读")).toBeInTheDocument();
    expect(screen.queryByLabelText("消息")).toBeNull(); // no composer
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(chat.reset).toHaveBeenCalled();
    const ctx = useShell.getState().context;
    expect(ctx?.zh).toContain("Why is boot slow?");
    expect(ctx?.detail).toContain("Two services start serially.");
    // back on the live conversation, composer is there
    expect(await screen.findByLabelText("消息")).toBeInTheDocument();
  });

  it("read-only view shows tool calls with their output", async () => {
    mockFetch();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Why did the last boot panic?" }));
    // the first session is the live one only when its id matches; here it is not
    expect(await screen.findByText(/alb_diag_pstore/)).toBeInTheDocument();
    expect(screen.getByText("Kernel panic - not syncing")).toBeInTheDocument();
  });

  it("read-only tool cards expand to the full arguments and the whole output", async () => {
    const long = `${Array.from({ length: 400 }, (_, i) => `dmesg line ${i}`).join("\n")}\nLAST-LINE`;
    mockFetch({
      detail: (id) =>
        id === "20261027-bbbb2222"
          ? Response.json({
              ...DETAIL[id],
              messages: [
                { role: "user", content: "Why is boot slow?" },
                { role: "assistant", content: "", tool_calls: [{ id: "c9", name: "alb_dmesg", arguments: '{"device":"board-a","since":"boot"}' }] },
                { role: "tool", tool_call_id: "c9", name: "alb_dmesg", content: JSON.stringify({ ok: true, data: long }) },
                { role: "assistant", content: "Two services start serially." },
              ],
            })
          : Response.json(DETAIL[id] ?? {}),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Why is boot slow?" }));
    // the stored JSON argument string is summarised like a live call
    expect(await screen.findByText("alb_dmesg device=board-a since=boot")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /参数和输出/ }));
    expect(screen.getByLabelText("alb_dmesg 的参数").textContent).toContain('"since": "boot"');
    const out = screen.getByLabelText("alb_dmesg 的输出").textContent ?? "";
    expect(long.length).toBeGreaterThan(4000);
    expect(out).toBe(long); // the stored JSON is unwrapped to its log text, nothing cut
    fireEvent.click(screen.getByRole("button", { name: /放大查看/ }));
    expect(within(screen.getByRole("dialog")).getByText("401 行")).toBeInTheDocument();
  });

  it("an unreadable session says so with a retry", async () => {
    mockFetch({ detail: (id) => (id === "20261010-cccc3333" ? new Response("x", { status: 500 }) : Response.json(DETAIL[id] ?? {})) });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^对话 \d\d:\d\d/ })); // title fell back to the time: its detail is unreadable
    expect(await screen.findByText(/读不到这次对话/)).toBeInTheDocument();
  });

  it("the live thread is the shared conversation: turns from the assistant show, send goes through it", async () => {
    chat.state = {
      turns: [
        { id: "u1", role: "user", content: "hello board", toolCalls: [], artifacts: [], status: "done" },
        { id: "a1", role: "assistant", content: "hello human", toolCalls: [], artifacts: [], status: "done", model: "qwen-x" },
      ],
      sessionId: "20261028-aaaa1111",
      isStreaming: false,
    };
    mockFetch();
    renderPage();
    expect(screen.getAllByText("hello board").length).toBeGreaterThan(0);
    expect(screen.getByText("hello human")).toBeInTheDocument();
    // the list row for the live session is the current one, not a duplicate
    const cur = (await screen.findAllByRole("listitem")).filter((li) => li.getAttribute("aria-current") === "true");
    expect(cur.length).toBe(1);
    fireEvent.change(screen.getByLabelText("消息"), { target: { value: "and now?" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(chat.send).toHaveBeenCalledWith("and now?");
  });

  it("a conversation that is not saved yet appears as the current row", async () => {
    chat.state = { turns: [{ id: "u1", role: "user", content: "fresh question", toolCalls: [], artifacts: [], status: "done" }], sessionId: null, isStreaming: false };
    mockFetch();
    renderPage();
    expect(await screen.findByText("进行中")).toBeInTheDocument();
    expect(screen.getAllByText("fresh question").length).toBeGreaterThan(0);
  });

  it("New chat resets the shared conversation", async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: /新对话/ })[0] as HTMLElement);
    expect(chat.reset).toHaveBeenCalled();
  });
});

describe("side column", () => {
  it("tools: agent switch is bound to the shell store, tools are labelled 'runs' (never 'asks')", async () => {
    mockFetch();
    renderPage();
    expect((await screen.findAllByText("直接跑", { selector: ".pill" })).length).toBe(2); // logs + power
    expect(screen.queryByText("先问")).toBeNull();
    expect(screen.getByText(/1 个会改动板子/)).toBeInTheDocument();
    const sw = screen.getByRole("checkbox", { name: "Agent 模式" });
    expect(sw).toBeChecked();
    fireEvent.click(sw);
    expect(useShell.getState().agentMode).toBe(false);
    expect((await screen.findAllByText("未开启")).length).toBeGreaterThan(0);
  });

  it("tools: error with retry, and empty", async () => {
    mockFetch({ tools: () => new Response("x", { status: 500 }) });
    const a = renderPage();
    expect(await screen.findByText(/读不到工具列表/)).toBeInTheDocument();
    a.unmount();
    mockFetch({ tools: () => Response.json({ ok: true, count: 0, categories: [], tools: [] }) });
    renderPage();
    expect(await screen.findByText("没有登记的工具")).toBeInTheDocument();
  });

  it("model picker: host backends only, models from the backend, choice stored in the app store", async () => {
    mockFetch();
    renderPage();
    const backendSel = (await screen.findByLabelText("后端")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(backendSel.options).map((o) => o.value)).toEqual(["ollama"])); // no cloud, no planned
    const modelSel = screen.getByLabelText("模型", { selector: "select" }) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(modelSel.options).map((o) => o.value)).toContain("gemma-z"));
    fireEvent.change(modelSel, { target: { value: "gemma-z" } });
    expect(useApp.getState().model).toBe("gemma-z");
    expect(screen.getByText(/后端没有回报/)).toBeInTheDocument();
  });

  it("model picker follows the source switch: cloud API lists cloud backends and warns about data leaving", async () => {
    useShell.setState({ source: "api" });
    useApp.setState({ backend: "openai-compat" });
    mockFetch();
    renderPage();
    const sel = (await screen.findByLabelText("后端")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(sel.options).map((o) => o.value)).toEqual(["openai-compat"]));
    expect(screen.getByText(/发到云端服务/)).toBeInTheDocument();
  });

  it("model picker: backend list error and empty list are shown", async () => {
    mockFetch({ backends: () => new Response("x", { status: 500 }) });
    const a = renderPage();
    expect(await screen.findByText(/读不到后端列表/)).toBeInTheDocument();
    a.unmount();
    mockFetch({ backends: () => Response.json({ backends: [] }) });
    useApp.setState({ backend: "ollama" });
    renderPage();
    // the current backend is always offered so the page never shows an empty picker for a working setup
    expect(await screen.findByLabelText("后端")).toBeInTheDocument();
  });

  it("board source: no composer, the on-board card explains and the source switch stays", async () => {
    useShell.setState({ source: "board" });
    mockFetch();
    renderPage();
    expect(await screen.findByText("板上模型还不能直接对话")).toBeInTheDocument();
    expect(screen.queryByLabelText("消息")).toBeNull();
    const sw = screen.getAllByRole("group", { name: "模型来源" })[0] as HTMLElement;
    expect(within(sw).getByRole("button", { name: /本机/ })).toBeInTheDocument();
  });

  it("English labels when the language is English", async () => {
    useApp.setState({ lang: "en" });
    mockFetch();
    renderPage();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chat");
    expect(await screen.findByText("Today")).toBeInTheDocument();
    expect(screen.getByText("On-board model")).toBeInTheDocument();
  });
});
