/**
 * ChartsTab spec — UI-4 (drop the stream on device switch so stale
 * telemetry isn't painted) + UIF-11 (localized status label + a connect
 * hint while the 6 cards are empty).
 */
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { appState, mockUseApp, mockStream } = vi.hoisted(() => {
  const appState = { device: "dev-a" as string | null, lang: "en" };
  const mockStream = {
    state: "idle" as string,
    error: null as string | null,
    samples: [] as unknown[],
    intervalS: 1,
    paused: false,
    connect: vi.fn(),
    disconnect: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
  };
  return {
    appState,
    mockUseApp: vi.fn((selector?: (s: any) => any) =>
      selector ? selector(appState) : appState,
    ),
    mockStream,
  };
});

vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("./useMetricsStream", () => ({ useMetricsStream: () => mockStream }));

import { ChartsTab } from "./ChartsTab";

beforeEach(() => {
  appState.device = "dev-a";
  appState.lang = "en";
  mockStream.state = "idle";
  mockStream.samples = [];
  mockStream.error = null;
  mockStream.paused = false;
  mockStream.disconnect.mockClear();
});

describe("ChartsTab", () => {
  it("disconnects the stream when the device changes (UI-4)", () => {
    const { rerender } = render(<ChartsTab />);
    mockStream.disconnect.mockClear(); // ignore the mount-time effect run
    appState.device = "dev-b";
    rerender(<ChartsTab />);
    expect(mockStream.disconnect).toHaveBeenCalled();
  });

  it("shows the connect hint while idle with no samples (UIF-11)", () => {
    const { container } = render(<ChartsTab />);
    expect(container.textContent).toContain("Press Connect");
  });

  it("localizes the status label in zh (UIF-11)", () => {
    appState.lang = "zh";
    const { container } = render(<ChartsTab />);
    expect(container.textContent).toContain("空闲"); // not the raw "idle"
    expect(container.textContent).not.toContain("● idle");
  });
});
