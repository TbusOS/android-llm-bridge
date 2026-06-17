/**
 * LiveSessionCard spec — MBC-6: idle / empty / stale-stream state forms.
 * Pins that the idle card uses `.live-card.is-idle` + `.live-empty`, and
 * that the stale-stream badge styles via the `.live-tps-stale` class
 * (the inline-style workaround was lifted to CSS).
 */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: any) => any) =>
    selector ? selector({ lang: "en" }) : { lang: "en" },
  ),
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));

import { LiveSessionCard } from "./LiveSessionCard";
import type { LiveSessionData } from "./types";

function activeData(): LiveSessionData {
  return {
    active: true,
    deviceId: "rk-board-7c",
    deviceTransport: "uart",
    turn: 1,
    elapsedHuman: "12s",
    elapsedHumanZh: "12s",
    prompt: "hi",
    promptZh: "hi",
    tools: [],
    tps: 42,
    totalTokens: 100,
    modelName: "qwen2.5",
    tpsSpark: [10, 20, 30],
  };
}

describe("LiveSessionCard states (MBC-6)", () => {
  it("idle state renders the is-idle card + empty message", () => {
    const { container } = render(
      <LiveSessionCard data={{ ...activeData(), active: false }} />,
    );
    expect(container.querySelector(".live-card.is-idle")).not.toBeNull();
    expect(container.querySelector(".live-empty")).not.toBeNull();
  });

  it("stale stream renders the live-tps-stale badge via class, no inline style", () => {
    const { container } = render(
      <LiveSessionCard data={activeData()} streamStatus="error" />,
    );
    const stale = container.querySelector(".live-tps-stale");
    expect(stale).not.toBeNull();
    expect(stale!.getAttribute("style")).toBeNull(); // lifted off inline → CSS
  });

  it("fresh stream shows no stale badge", () => {
    const { container } = render(
      <LiveSessionCard data={activeData()} streamStatus="open" />,
    );
    expect(container.querySelector(".live-tps-stale")).toBeNull();
  });
});
