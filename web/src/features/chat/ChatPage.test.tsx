/**
 * ChatPage spec — MBC-7: the block visuals (actions toolbar / meta line /
 * scrolling log / input row) are driven by classes, not inline style.
 */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp, mockUseChatStream } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: any) => any) => {
    const state = { lang: "en", backend: "ollama", model: "qwen2.5" };
    return selector ? selector(state) : state;
  }),
  mockUseChatStream: vi.fn(() => ({
    turns: [],
    sessionId: "abc123def",
    isStreaming: false,
    send: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    reset: vi.fn(),
  })),
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("./useChatStream", () => ({ useChatStream: mockUseChatStream }));

import { ChatPage } from "./ChatPage";

describe("ChatPage layout classes (MBC-7)", () => {
  it("uses classes (not inline style) for actions / meta / log / input row", () => {
    const { container } = render(<ChatPage />);
    const actions = container.querySelector(".chat-actions");
    expect(actions).not.toBeNull();
    expect(actions!.getAttribute("style")).toBeNull(); // toolbar no longer inline
    expect(container.querySelector(".chat-meta")).not.toBeNull();
    expect(container.querySelector(".mock-card.chat-log")).not.toBeNull();
    expect(container.querySelector(".chat-input-row")).not.toBeNull();
  });

  it("shows the backend / model / session meta line", () => {
    const { container } = render(<ChatPage />);
    expect(container.querySelector(".chat-meta")!.textContent).toContain(
      "backend=ollama",
    );
  });
});
