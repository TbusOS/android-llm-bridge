/**
 * ChatInput spec — UIF-12: while streaming the textarea is readOnly (not
 * disabled) so it keeps focus and Esc cancels the stream; when idle, Enter
 * sends.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: any) => any) =>
    selector ? selector({ lang: "en" }) : { lang: "en" },
  ),
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));

import { ChatInput } from "./ChatInput";

describe("ChatInput streaming behaviour (UIF-12)", () => {
  it("is readOnly (not disabled) while streaming and Esc cancels", () => {
    const onCancel = vi.fn();
    render(
      <ChatInput isStreaming onSend={vi.fn()} onCancel={onCancel} />,
    );
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.readOnly).toBe(true);
    expect(ta.disabled).toBe(false);
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("Enter sends when idle, and does not cancel", () => {
    const onSend = vi.fn();
    const onCancel = vi.fn();
    render(
      <ChatInput isStreaming={false} onSend={onSend} onCancel={onCancel} />,
    );
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.readOnly).toBe(false);
    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(onCancel).not.toHaveBeenCalled();
  });
});
