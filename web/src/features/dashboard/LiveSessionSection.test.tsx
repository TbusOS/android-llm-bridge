/**
 * LiveSessionSection spec — PERF-4: the 1 Hz metric subscription
 * (includeMetrics) is owned HERE, not at the DashboardPage top, so the
 * per-second churn doesn't re-render the whole page. Pins that this
 * component is the one holding the includeMetrics stream + renders the
 * live card.
 */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp, mockUseAuditStream, mockUseLiveSession } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: any) => any) =>
    selector ? selector({ lang: "en" }) : { lang: "en" },
  ),
  mockUseAuditStream: vi.fn(() => ({ status: "open", rawEvents: [] })),
  mockUseLiveSession: vi.fn(() => ({
    active: false,
    deviceId: "",
    deviceTransport: "",
    turn: 0,
    elapsedHuman: "",
    elapsedHumanZh: "",
    prompt: "",
    promptZh: "",
    tools: [],
    tps: 0,
    totalTokens: 0,
    modelName: "",
    tpsSpark: [],
  })),
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("../../lib/hooks/useAuditStream", () => ({
  useAuditStream: mockUseAuditStream,
}));
vi.mock("./useLiveSession", () => ({ useLiveSession: mockUseLiveSession }));

import { LiveSessionSection } from "./LiveSessionSection";

describe("LiveSessionSection (PERF-4)", () => {
  it("owns the includeMetrics subscription and renders the live card", () => {
    const { container } = render(<LiveSessionSection />);
    expect(mockUseAuditStream).toHaveBeenCalledWith({ includeMetrics: true });
    expect(container.querySelector(".live-card")).not.toBeNull();
  });
});
