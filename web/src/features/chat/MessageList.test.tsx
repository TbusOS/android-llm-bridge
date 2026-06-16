/**
 * MessageList component spec — pins the two streaming-perf behaviours:
 *
 *   1. TurnView is memoized: useChatStream.updateTurn replaces only
 *      the turn object being streamed into, so settled turns keep
 *      their reference and must NOT re-render per token. Asserted
 *      through a counting ToolCallCard mock — a re-render of the
 *      settled TurnView would call it again.
 *
 *   2. The tail auto-scroll uses behavior:"auto" while a turn is in
 *      flight (the effect fires on every token; a smooth scroll would
 *      restart each time and never finish) and "smooth" only for the
 *      final settled scroll.
 *
 * jsdom does not implement Element#scrollIntoView, so the spec stubs
 * it on the prototype and asserts the call shapes.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { MessageList } from "./MessageList";
import type { ChatTurn } from "./types";

vi.mock("../../stores/app", () => ({
  useApp: (sel?: (s: { lang: "en" | "zh" }) => unknown) =>
    sel ? sel({ lang: "en" }) : { lang: "en" },
}));

const toolCallCardRenders = vi.hoisted(() => ({ count: 0 }));
vi.mock("./ToolCallCard", () => ({
  ToolCallCard: () => {
    toolCallCardRenders.count += 1;
    return null;
  },
}));

function turn(over: Partial<ChatTurn> & { id: string }): ChatTurn {
  return {
    role: "assistant",
    content: "",
    toolCalls: [],
    artifacts: [],
    status: "done",
    ...over,
  };
}

const scrollSpy = vi.fn();

beforeEach(() => {
  toolCallCardRenders.count = 0;
  scrollSpy.mockClear();
  Element.prototype.scrollIntoView =
    scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
});

describe("MessageList", () => {
  it("renders the empty-state prompt when there are no turns", () => {
    render(<MessageList turns={[]} showPending={false} />);
    expect(screen.getByText(/Start a conversation/)).toBeInTheDocument();
  });

  it("scrolls with behavior:auto while the last turn is streaming", () => {
    const turns = [
      turn({ id: "u1", role: "user", content: "hi" }),
      turn({ id: "a1", content: "tok", status: "streaming" }),
    ];
    render(<MessageList turns={turns} showPending={false} />);
    expect(scrollSpy).toHaveBeenCalledWith({ behavior: "auto", block: "end" });
  });

  it("scrolls with behavior:auto while the last turn is pending", () => {
    const turns = [
      turn({ id: "u1", role: "user", content: "hi" }),
      turn({ id: "a1", status: "pending" }),
    ];
    render(<MessageList turns={turns} showPending={true} />);
    expect(scrollSpy).toHaveBeenCalledWith({ behavior: "auto", block: "end" });
  });

  it("scrolls with behavior:smooth once the last turn settles", () => {
    const user = turn({ id: "u1", role: "user", content: "hi" });
    const streaming = turn({ id: "a1", content: "tok", status: "streaming" });
    const { rerender } = render(
      <MessageList turns={[user, streaming]} showPending={false} />,
    );
    rerender(
      <MessageList
        turns={[user, { ...streaming, status: "done" }]}
        showPending={false}
      />,
    );
    expect(scrollSpy).toHaveBeenLastCalledWith({
      behavior: "smooth",
      block: "end",
    });
  });

  it("does not re-render settled turns on per-token updates (TurnView memo)", () => {
    const user = turn({ id: "u1", role: "user", content: "hi" });
    const settled = turn({
      id: "a1",
      content: "earlier answer",
      toolCalls: [
        {
          id: "tc1",
          name: "device_shell",
          arguments: {},
          status: "done",
          startedAt: 0,
        },
      ],
    });
    const active = turn({ id: "a2", content: "t", status: "streaming" });

    const { rerender } = render(
      <MessageList turns={[user, settled, active]} showPending={false} />,
    );
    const afterMount = toolCallCardRenders.count;
    expect(afterMount).toBeGreaterThan(0);

    // Simulate one token: new turns array, same settled reference,
    // fresh object only for the active turn (mirrors updateTurn).
    rerender(
      <MessageList
        turns={[user, settled, { ...active, content: "to" }]}
        showPending={false}
      />,
    );
    expect(toolCallCardRenders.count).toBe(afterMount);
  });
});
