/**
 * PlaygroundPage component spec (DEBT-063 closure).
 *
 * Pure-function `llmMessagesFrom` already pinned in
 * `PlaygroundPage.test.ts`. THIS spec exercises the full component:
 * the chat.settled terminal-path effect introduced in AO-3 (5/25
 * 第三轮 audit HIGH-3 / HIGH-4 fix), the cancel / error stash, the
 * input restore on error, and — most importantly — the integration
 * assertion that the next `chat.send()` call after a cancelled or
 * errored turn does NOT include those marker entries in its
 * `messages` payload (the LLM never sees them).
 *
 * Mocking strategy:
 *
 *   - vi.hoisted mutable refs hold the simulated `chat` state. Tests
 *     mutate the ref between renders + call `rerender()` to drive the
 *     terminal-path effect. This keeps the component's own state
 *     (log / input / lastPromptRef) authentic — we only fake what
 *     CROSSES the hook boundary.
 *
 *   - `useApp` returns `lang="en"` so we test against stable strings
 *     ("send" / "cancel" / "clear") regardless of the UI's i18n.
 *
 *   - `useBackends` / `useBackendModels` return a single backend
 *     ("ollama") with one model so the send button enables without
 *     a real query.
 *
 *   - `usePlaygroundChat` is the critical mock: tests configure
 *     `settled` / `delta` / `done` / `status` to drive the
 *     PlaygroundPage's chat.settled effect through every variant.
 *     `chat.send` / `chat.cancel` / `chat.reset` are vi.fn so we can
 *     assert call shapes (especially the messages payload).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import type { WsChatSettled } from "../../lib/hooks/useWsChatStream";
import type { DoneEvent, PlaygroundRequest } from "./usePlaygroundChat";

// Default mock implementations — captured once at module load so
// beforeEach can reset every hook back to a clean baseline without
// per-spec try/finally bookkeeping. AS-4 (5/26 第五轮 code MID-3):
// the prior version relied on each customising spec to manually
// restore via try/finally, which only worked because every spec
// remembered to. New specs that forget will silently leak state into
// subsequent specs.
const { chatState, chatActions, useApp, useBackends, useBackendModels } =
  vi.hoisted(() => {
    type ChatStatus = "idle" | "streaming" | "done" | "error";
    interface ChatState {
      delta: string;
      done: DoneEvent | null;
      status: ChatStatus;
      settled: WsChatSettled | null;
    }
    const chatState: ChatState = {
      delta: "",
      done: null,
      status: "idle",
      settled: null,
    };
    const chatActions = {
      send: vi.fn<(req: PlaygroundRequest) => void>(),
      cancel: vi.fn<() => void>(),
      reset: vi.fn<() => void>(),
    };
    return {
      chatState,
      chatActions,
      useApp: vi.fn(),
      useBackends: vi.fn(),
      useBackendModels: vi.fn(),
    };
  });

const DEFAULT_USE_APP_IMPL = (
  sel?: (s: { lang: "en" | "zh" }) => unknown,
) => (sel ? sel({ lang: "en" }) : { lang: "en" });

const DEFAULT_USE_BACKENDS_IMPL = () => ({
  data: { backends: [{ name: "ollama", host_compute_type: "gpu" }] },
  isLoading: false,
  isError: false,
});

const DEFAULT_USE_BACKEND_MODELS_IMPL = () => ({
  data: { models: ["llama3"] },
  isLoading: false,
  isError: false,
});

vi.mock("../../stores/app", () => ({ useApp }));
vi.mock("./usePlayground", () => ({
  useBackends,
  useBackendModels,
}));
vi.mock("./usePlaygroundChat", () => ({
  usePlaygroundChat: () => ({
    delta: chatState.delta,
    done: chatState.done,
    status: chatState.status,
    settled: chatState.settled,
    send: chatActions.send,
    cancel: chatActions.cancel,
    reset: chatActions.reset,
  }),
}));

// Import AFTER mocks so the component picks up the mocked modules.
import { PlaygroundPage } from "./PlaygroundPage";

function resetChat() {
  chatState.delta = "";
  chatState.done = null;
  chatState.status = "idle";
  chatState.settled = null;
  chatActions.send.mockClear();
  chatActions.cancel.mockClear();
  chatActions.reset.mockClear();
}

/** Reset all 3 hook mocks back to default implementations. Run at the
 *  top of every `beforeEach` so a spec that overrides one of them
 *  (e.g. lang=zh, no-backend) doesn't leak into the next spec. */
function resetMockImpls() {
  useApp.mockImplementation(DEFAULT_USE_APP_IMPL);
  useBackends.mockImplementation(DEFAULT_USE_BACKENDS_IMPL);
  useBackendModels.mockImplementation(DEFAULT_USE_BACKEND_MODELS_IMPL);
}

// jsdom doesn't ship a ResizeObserver — PlaygroundPage's
// --chat-bar-height effect uses it. Stub with a no-op so the effect
// doesn't crash; we don't assert anything about the CSS var.
class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  resetMockImpls();
  resetChat();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetChat();
});

/** Drive the input + click send. Returns the messages payload chat.send
 *  was called with, asserting exactly one call happened. */
function sendPrompt(text: string): PlaygroundRequest {
  const textarea = screen.getByPlaceholderText(/Message…/);
  fireEvent.change(textarea, { target: { value: text } });
  const sendBtn = screen.getByRole("button", { name: "send" });
  fireEvent.click(sendBtn);
  expect(chatActions.send).toHaveBeenCalledTimes(1);
  return chatActions.send.mock.calls[0]![0];
}

describe("PlaygroundPage · empty state + send button gating", () => {
  it("renders empty hint when log is empty and chat idle", () => {
    render(<PlaygroundPage />);
    expect(screen.getByText(/No messages yet/)).toBeInTheDocument();
  });

  it("send button disabled until input has trimmable text", () => {
    render(<PlaygroundPage />);
    const sendBtn = screen.getByRole("button", { name: "send" });
    expect(sendBtn).toBeDisabled();
    const textarea = screen.getByPlaceholderText(/Message…/);
    fireEvent.change(textarea, { target: { value: "   " } }); // whitespace only
    expect(sendBtn).toBeDisabled();
    fireEvent.change(textarea, { target: { value: "hi" } });
    expect(sendBtn).toBeEnabled();
  });
});

describe("PlaygroundPage · onSend payload", () => {
  it("first send → messages contains exactly the new user prompt", () => {
    render(<PlaygroundPage />);
    const req = sendPrompt("hello");
    expect(req.messages).toEqual([{ role: "user", content: "hello" }]);
    expect(req.backend).toBe("ollama");
    // Model defaults to the "(default)" option (empty string) when the
    // user hasn't picked one, which the payload normalises to null.
    expect(req.model).toBeNull();
  });

  it("input cleared after send · lastPrompt cached on hook (visible via error-path restore)", () => {
    render(<PlaygroundPage />);
    sendPrompt("hello");
    const textarea = screen.getByPlaceholderText(
      /Message…/,
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("");
  });
});

describe("PlaygroundPage · chat.settled terminal paths (AO-3)", () => {
  it("done with content → pushes plain assistant entry (no meta) + chat.reset called", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    chatState.status = "done";
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "hi back",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
    };
    rerender(<PlaygroundPage />);

    expect(screen.getByText("hi back")).toBeInTheDocument();
    // Plain (no-meta) entries don't get the "(not sent to model)" label.
    expect(screen.queryByText(/not sent to model/)).not.toBeInTheDocument();
    expect(chatActions.reset).toHaveBeenCalled();
  });

  it("done with empty content → no assistant entry pushed (handledSettledRef short-circuits)", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    chatState.status = "done";
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
    };
    rerender(<PlaygroundPage />);

    // Only the user prompt — no assistant turn from an empty done.
    const assistantBlocks = screen.queryAllByText(/assistant/);
    expect(assistantBlocks).toHaveLength(0);
  });

  it("cancelled with partial delta → pushes meta:cancelled entry with the partial · 'not sent to model' label visible", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("long prompt");

    chatState.delta = "partial reply";
    chatState.status = "idle"; // cancelled folds to idle in the chat hook
    chatState.settled = { kind: "cancelled" };
    rerender(<PlaygroundPage />);

    expect(screen.getByText("partial reply")).toBeInTheDocument();
    expect(screen.getByText(/cancelled \(not sent to model\)/)).toBeInTheDocument();
  });

  it("cancelled with empty delta → no entry pushed (avoid blank meta:cancelled card)", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    chatState.delta = "";
    chatState.settled = { kind: "cancelled" };
    rerender(<PlaygroundPage />);

    // The user prompt is still there but no cancelled marker card.
    expect(screen.queryByText(/cancelled \(not sent to model\)/)).not.toBeInTheDocument();
  });

  it("error (server source) with done.error → pushes meta:errored entry + restores input from lastPromptRef", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("do thing");

    chatState.status = "error";
    chatState.settled = { kind: "error", source: "server" };
    chatState.done = {
      ok: false,
      content: "",
      finish_reason: "error",
      model: "llama3",
      backend: "ollama",
      error: { code: "RATE_LIMIT", message: "slow down" },
    };
    rerender(<PlaygroundPage />);

    expect(screen.getByText("RATE_LIMIT: slow down")).toBeInTheDocument();
    expect(screen.getAllByText(/error \(not sent to model\)/).length).toBeGreaterThan(0);
    // Input should be restored to lastPrompt because the user just
    // cleared it via sendPrompt — input is empty at error time, so the
    // restore branch fires.
    const textarea = screen.getByPlaceholderText(
      /Message…/,
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("do thing");
  });

  it("error (ws-close source) with partial delta + done.error → pushes BOTH partial-errored AND error-message-errored entries", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("longer prompt");

    chatState.delta = "halfway response";
    chatState.status = "error";
    chatState.settled = {
      kind: "error",
      source: "ws-close",
      code: 1006,
      reasonText: "abnormal",
    };
    chatState.done = {
      ok: false,
      content: "",
      finish_reason: "disconnected",
      model: "",
      backend: "",
      error: { code: "WS_DISCONNECTED", message: "abnormal" },
    };
    rerender(<PlaygroundPage />);

    expect(screen.getByText("halfway response")).toBeInTheDocument();
    expect(screen.getByText("WS_DISCONNECTED: abnormal")).toBeInTheDocument();
    // Both entries carry the errored marker → two "(not sent to model)" labels.
    expect(screen.getAllByText(/error \(not sent to model\)/)).toHaveLength(2);
  });

  it("input restore does NOT clobber a follow-up edit the user typed during streaming", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("original prompt");

    // User types a NEW prompt while waiting (simulates user re-deciding).
    const textarea = screen.getByPlaceholderText(
      /Message…/,
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "different idea" } });

    // Stream errors out.
    chatState.status = "error";
    chatState.settled = { kind: "error", source: "ws-error", reasonText: "x" };
    chatState.done = {
      ok: false,
      content: "",
      finish_reason: "disconnected",
      model: "",
      backend: "",
      error: { code: "WS_DISCONNECTED", message: "x" },
    };
    rerender(<PlaygroundPage />);

    // PlaygroundPage's restore guard: only fills input if it's empty.
    expect(textarea.value).toBe("different idea");
  });
});

describe("PlaygroundPage · LLM payload integration (HIGH-4 regression net)", () => {
  it("next send after a cancelled turn excludes the cancelled partial from messages", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("first prompt");

    // Stream cancelled with a partial.
    chatState.delta = "partial cancelled reply";
    chatState.settled = { kind: "cancelled" };
    rerender(<PlaygroundPage />);
    chatState.settled = null;
    chatState.delta = "";
    rerender(<PlaygroundPage />);

    chatActions.send.mockClear();
    const req = sendPrompt("follow up");
    // The cancelled assistant entry is in the visible log but MUST NOT
    // be in the LLM-bound messages payload.
    expect(req.messages).toEqual([
      { role: "user", content: "first prompt" },
      { role: "user", content: "follow up" },
    ]);
    expect(req.messages.find((m) => m.content.includes("partial"))).toBeUndefined();
  });

  it("next send after an errored turn excludes BOTH the error-message and the partial from messages", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("first");

    chatState.delta = "halfway";
    chatState.status = "error";
    chatState.settled = { kind: "error", source: "ws-close", code: 1006 };
    chatState.done = {
      ok: false,
      content: "",
      finish_reason: "disconnected",
      model: "",
      backend: "",
      error: { code: "WS_DISCONNECTED", message: "bye" },
    };
    rerender(<PlaygroundPage />);
    chatState.settled = null;
    chatState.delta = "";
    chatState.done = null;
    chatState.status = "idle";
    rerender(<PlaygroundPage />);

    chatActions.send.mockClear();
    // Input was restored to "first" on error; user just clicks send.
    const sendBtn = screen.getByRole("button", { name: "send" });
    fireEvent.click(sendBtn);
    expect(chatActions.send).toHaveBeenCalledTimes(1);
    const req = chatActions.send.mock.calls[0]![0];
    // Two visible-log entries (partial + WS_DISCONNECTED message) are
    // both meta-tagged → both stripped from messages.
    expect(req.messages).toEqual([
      { role: "user", content: "first" },
      { role: "user", content: "first" },
    ]);
  });

  it("done turn followed by next send DOES include the assistant reply in messages (plain entries are real history)", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    chatState.status = "done";
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "hello back",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
    };
    rerender(<PlaygroundPage />);
    chatState.settled = null;
    chatState.done = null;
    chatState.status = "idle";
    rerender(<PlaygroundPage />);

    chatActions.send.mockClear();
    const req = sendPrompt("follow up");
    expect(req.messages).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello back" },
      { role: "user", content: "follow up" },
    ]);
  });
});

describe("PlaygroundPage · cancel / clear controls", () => {
  it("cancel button (while streaming) calls chat.cancel", () => {
    const { rerender } = render(<PlaygroundPage />);
    chatState.status = "streaming";
    rerender(<PlaygroundPage />);
    const cancelBtn = screen.getByRole("button", { name: "cancel" });
    fireEvent.click(cancelBtn);
    expect(chatActions.cancel).toHaveBeenCalledTimes(1);
  });

  it("Esc keydown on input (while streaming) calls chat.cancel", () => {
    const { rerender } = render(<PlaygroundPage />);
    chatState.status = "streaming";
    rerender(<PlaygroundPage />);
    const textarea = screen.getByPlaceholderText(/Streaming/);
    fireEvent.keyDown(textarea, { key: "Escape" });
    expect(chatActions.cancel).toHaveBeenCalledTimes(1);
  });

  it("clear button → empties log + calls chat.reset · disabled while streaming", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");
    expect(screen.getByText("hi")).toBeInTheDocument();
    chatActions.reset.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(screen.queryByText("hi")).not.toBeInTheDocument();
    expect(screen.getByText(/No messages yet/)).toBeInTheDocument();
    expect(chatActions.reset).toHaveBeenCalledTimes(1);

    // Reproduce streaming state — clear should disable.
    chatState.status = "streaming";
    rerender(<PlaygroundPage />);
    expect(screen.getByRole("button", { name: "clear" })).toBeDisabled();
  });
});

describe("PlaygroundPage · handledSettledRef guard", () => {
  it("re-rendering with the SAME settled object does not double-push the assistant entry", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    const settledOnce: WsChatSettled = { kind: "done" };
    chatState.status = "done";
    chatState.settled = settledOnce;
    chatState.done = {
      ok: true,
      content: "single reply",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
    };
    rerender(<PlaygroundPage />);
    // Re-render again with the SAME settled ref — handledSettledRef
    // guard should prevent another push.
    rerender(<PlaygroundPage />);
    rerender(<PlaygroundPage />);

    expect(screen.getAllByText("single reply")).toHaveLength(1);
  });

  it("after first settled push, a NEW settled object with NEW content DOES push again — proves handledSettledRef is ref-identity not value-identity (AR-2 / code MID-2)", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    // First settled: push happens.
    chatState.status = "done";
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "first reply",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
    };
    rerender(<PlaygroundPage />);
    expect(screen.getAllByText("first reply")).toHaveLength(1);

    // Simulate reset cycle (PlaygroundPage's chat.reset() in the effect
    // clears settled to null between turns — the guard branch
    // `if (!info) { handledSettledRef.current = null; return; }` is the
    // ONLY way handledSettledRef goes back to null. New settled with the
    // same ref as the prior one would still no-op without this reset.)
    chatState.settled = null;
    rerender(<PlaygroundPage />);

    // Second turn: brand-new settled object literal with DIFFERENT
    // content. The push happens because (a) handledSettledRef was reset
    // to null on the previous frame, (b) the NEW settled ref !== the
    // previous one, and (c) content differs so the setLog
    // last-assistant-content dedup inside the done branch doesn't fire.
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "second reply",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
    };
    rerender(<PlaygroundPage />);

    expect(screen.getByText("first reply")).toBeInTheDocument();
    expect(screen.getByText("second reply")).toBeInTheDocument();
  });
});

describe("PlaygroundPage · keyboard shortcuts (AR-2 / ui-f HIGH-3)", () => {
  it("⌘+Enter (metaKey) on input → sends · same as send button", () => {
    render(<PlaygroundPage />);
    const textarea = screen.getByPlaceholderText(/Message…/);
    fireEvent.change(textarea, { target: { value: "via cmd-enter" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });
    expect(chatActions.send).toHaveBeenCalledTimes(1);
    expect(chatActions.send.mock.calls[0]![0].messages).toEqual([
      { role: "user", content: "via cmd-enter" },
    ]);
  });

  it("Ctrl+Enter on input → sends (parity with ⌘+Enter for non-Mac users)", () => {
    render(<PlaygroundPage />);
    const textarea = screen.getByPlaceholderText(/Message…/);
    fireEvent.change(textarea, { target: { value: "via ctrl-enter" } });
    fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true });
    expect(chatActions.send).toHaveBeenCalledTimes(1);
  });

  it("plain Enter (no modifier) does NOT send — preserves newline behaviour in the textarea", () => {
    render(<PlaygroundPage />);
    const textarea = screen.getByPlaceholderText(/Message…/);
    fireEvent.change(textarea, { target: { value: "draft" } });
    fireEvent.keyDown(textarea, { key: "Enter" }); // no modifier
    expect(chatActions.send).not.toHaveBeenCalled();
  });

  it("⌘+Enter while streaming → does NOT trigger another send (gated by chat.status)", () => {
    const { rerender } = render(<PlaygroundPage />);
    chatState.status = "streaming";
    rerender(<PlaygroundPage />);
    const textarea = screen.getByPlaceholderText(/Streaming/);
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });
    expect(chatActions.send).not.toHaveBeenCalled();
  });
});

describe("PlaygroundPage · send button disabled paths (AR-2 / ui-f HIGH-3)", () => {
  it("send button disabled when no backend is available (useBackends returns empty)", () => {
    // mockImplementation (not mockReturnValueOnce) — React may call
    // useBackends multiple times during render commit / effect flush;
    // Once-mocks revert to the default after the first call and leak
    // the populated backends list back into the second render.
    //
    // AS-4 (5/26 第五轮 code MID-3): cleanup is automatic — beforeEach
    // re-installs DEFAULT_USE_BACKENDS_IMPL on every spec start, no
    // try/finally needed here.
    useBackends.mockImplementation(() => ({
      data: { backends: [] },
      isLoading: false,
      isError: false,
    }));
    render(<PlaygroundPage />);
    const textarea = screen.getByPlaceholderText(/Message…/);
    fireEvent.change(textarea, { target: { value: "hi" } });
    // disabled = !input.trim() || !backend → empty backend list → !backend
    expect(screen.getByRole("button", { name: "send" })).toBeDisabled();
  });
});

describe("PlaygroundPage · backends fetch failure (UIF-06 第十轮)", () => {
  const failedBackendsImpl = (refetch: () => void) => () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch,
  });

  it("error row visible · retry wired to refetch · select shows placeholder option · send stays disabled", () => {
    const refetch = vi.fn();
    useBackends.mockImplementation(failedBackendsImpl(refetch));
    render(<PlaygroundPage />);

    // Actionable error copy (mirrors DashboardPage's backends error).
    expect(
      screen.getByText(/Could not fetch backends — is alb-api running\?/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);

    // Select no longer renders empty — placeholder option explains.
    expect(
      screen.getByRole("option", { name: "(failed to load)" }),
    ).toBeInTheDocument();

    // Send still gated on !backend — but the rail now says why.
    const textarea = screen.getByPlaceholderText(/Message…/);
    fireEvent.change(textarea, { target: { value: "hi" } });
    expect(screen.getByRole("button", { name: "send" })).toBeDisabled();
    expect(chatActions.send).not.toHaveBeenCalled();
  });

  it("lang='zh' renders the error row + retry + placeholder option in Chinese", () => {
    useApp.mockImplementation(
      (sel?: (s: { lang: "en" | "zh" }) => unknown) =>
        sel ? sel({ lang: "zh" }) : { lang: "zh" },
    );
    const refetch = vi.fn();
    useBackends.mockImplementation(failedBackendsImpl(refetch));
    render(<PlaygroundPage />);

    expect(
      screen.getByText(/无法获取后端列表，检查 alb-api 是否在运行/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "（拉取失败）" }),
    ).toBeInTheDocument();
  });

  it("empty-but-ok backends list → neutral placeholder option, no error row", () => {
    useBackends.mockImplementation(() => ({
      data: { backends: [] },
      isLoading: false,
      isError: false,
    }));
    render(<PlaygroundPage />);

    expect(screen.getByRole("option", { name: "(none)" })).toBeInTheDocument();
    expect(screen.queryByText(/alb-api/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "retry" }),
    ).not.toBeInTheDocument();
  });
});

describe("PlaygroundPage · i18n zh path (AR-2 / ui-f HIGH-3)", () => {
  it("lang='zh' renders 发送 / 取消 / 清空 buttons + cancelled label in Chinese", () => {
    // AS-4 (5/26 第五轮 code MID-3): override lang to zh; cleanup is
    // automatic via beforeEach → resetMockImpls re-installs the
    // DEFAULT_USE_APP_IMPL (lang=en) before the next spec runs.
    useApp.mockImplementation(
      (sel?: (s: { lang: "en" | "zh" }) => unknown) =>
        sel ? sel({ lang: "zh" }) : { lang: "zh" },
    );
    const { rerender } = render(<PlaygroundPage />);

    // Send button shows "发送"
    expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "清空" })).toBeInTheDocument();

    // Drive a cancelled turn — should show Chinese "已取消" label.
    const textarea = screen.getByPlaceholderText(/输入消息/);
    fireEvent.change(textarea, { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    chatState.delta = "部分回复";
    chatState.settled = { kind: "cancelled" };
    rerender(<PlaygroundPage />);

    expect(screen.getByText(/已取消（未发给模型）/)).toBeInTheDocument();
  });
});

describe("PlaygroundPage · per-turn metrics survive reset (UIF-01 第十轮)", () => {
  it("metrics stay visible after chat.reset() wipes status/done back to idle", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    // Turn settles with metrics attached.
    chatState.status = "done";
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "hi back",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
      metrics: { tps: 42.5 },
    };
    rerender(<PlaygroundPage />);
    expect(chatActions.reset).toHaveBeenCalled();

    // Simulate the real post-reset state: stream back to idle, done
    // nulled, settled cleared. Pre-fix the metrics block was keyed on
    // `chat.status === "done" && chat.done` — both gone here — so it
    // unmounted after ≤1 frame and the user never saw it.
    chatState.status = "idle";
    chatState.settled = null;
    chatState.done = null;
    rerender(<PlaygroundPage />);

    expect(screen.getByText("tps: 42.5")).toBeInTheDocument();
    expect(screen.getByText("finish: stop")).toBeInTheDocument();
  });

  it("metrics cleared on next send — stale numbers never describe a new turn", () => {
    const { rerender } = render(<PlaygroundPage />);
    sendPrompt("hi");

    chatState.status = "done";
    chatState.settled = { kind: "done" };
    chatState.done = {
      ok: true,
      content: "hi back",
      finish_reason: "stop",
      model: "llama3",
      backend: "ollama",
      metrics: { tps: 42.5 },
    };
    rerender(<PlaygroundPage />);
    chatState.status = "idle";
    chatState.settled = null;
    chatState.done = null;
    rerender(<PlaygroundPage />);
    expect(screen.getByText("tps: 42.5")).toBeInTheDocument();

    // Next turn starts — metrics block must go.
    chatActions.send.mockClear();
    sendPrompt("again");
    expect(screen.queryByText("tps: 42.5")).not.toBeInTheDocument();
  });
});
