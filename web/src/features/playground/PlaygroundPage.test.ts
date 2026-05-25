/**
 * PlaygroundPage focused specs.
 *
 * Full component spec is heavy (4 mocks: useApp / useBackends /
 * useBackendModels / usePlaygroundChat). For now we cover the
 * critical pure-function boundary:
 *
 *   llmMessagesFrom() — filters out cancelled / errored markers so
 *   they NEVER feed back into the model's context. This is the AO-3
 *   fix for ui-f HIGH-1 (LLM context pollution); a regression here
 *   would be a silent business-correctness bug.
 *
 * Full PlaygroundPage component test that exercises the chat.settled
 * effect (cancel/error stash) is DEBT-063 candidate — needs all 4
 * hook mocks + react-query provider.
 */
import { describe, expect, it } from "vitest";

import {
  llmMessagesFrom,
  type ChatMessage,
} from "./PlaygroundPage";

describe("llmMessagesFrom", () => {
  it("returns empty list for empty log", () => {
    expect(llmMessagesFrom([])).toEqual([]);
  });

  it("passes through plain user + assistant messages unchanged", () => {
    const log: ChatMessage[] = [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello back" },
    ];
    expect(llmMessagesFrom(log)).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello back" },
    ]);
  });

  it("filters out meta:cancelled markers · LLM never sees [cancelled]", () => {
    const log: ChatMessage[] = [
      { role: "user", content: "long prompt" },
      {
        role: "assistant",
        content: "partial reply",
        meta: { kind: "cancelled" },
      },
      { role: "user", content: "follow up" },
    ];
    const result = llmMessagesFrom(log);
    expect(result).toEqual([
      { role: "user", content: "long prompt" },
      { role: "user", content: "follow up" },
    ]);
    // critical assertion: no entry mentions "cancelled" or the partial
    expect(result.find((m) => m.content === "partial reply")).toBeUndefined();
  });

  it("filters out meta:errored markers · LLM never sees error placeholders", () => {
    const log: ChatMessage[] = [
      { role: "user", content: "do thing" },
      {
        role: "assistant",
        content: "partial before error",
        meta: { kind: "errored" },
      },
      {
        role: "assistant",
        content: "WS_DISCONNECTED: socket closed",
        meta: { kind: "errored" },
      },
      { role: "user", content: "retry" },
    ];
    expect(llmMessagesFrom(log)).toEqual([
      { role: "user", content: "do thing" },
      { role: "user", content: "retry" },
    ]);
  });

  it("preserves message order across filter", () => {
    const log: ChatMessage[] = [
      { role: "user", content: "1" },
      { role: "assistant", content: "X1", meta: { kind: "cancelled" } },
      { role: "assistant", content: "1-reply" },
      { role: "user", content: "2" },
      { role: "assistant", content: "X2", meta: { kind: "errored" } },
      { role: "user", content: "3" },
    ];
    expect(llmMessagesFrom(log).map((m) => m.content)).toEqual([
      "1",
      "1-reply",
      "2",
      "3",
    ]);
  });

  it("strips meta field even on plain-shaped messages (only role+content survive)", () => {
    const log: ChatMessage[] = [{ role: "user", content: "hi" }];
    const result = llmMessagesFrom(log);
    expect(result[0]).toEqual({ role: "user", content: "hi" });
    expect(Object.keys(result[0]!)).toEqual(["role", "content"]);
  });
});
