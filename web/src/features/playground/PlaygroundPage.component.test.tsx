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

// Hoisted mock state — tests mutate `chatState` between renders.
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
      useApp: vi.fn((sel?: (s: { lang: "en" | "zh" }) => unknown) =>
        sel ? sel({ lang: "en" }) : { lang: "en" },
      ),
      useBackends: vi.fn(() => ({
        data: { backends: [{ name: "ollama", host_compute_type: "gpu" }] },
        isLoading: false,
        isError: false,
      })),
      useBackendModels: vi.fn(() => ({
        data: { models: ["llama3"] },
        isLoading: false,
        isError: false,
      })),
    };
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
});
