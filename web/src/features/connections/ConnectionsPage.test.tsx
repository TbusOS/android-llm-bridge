/**
 * ConnectionsPage spec (P2) — renders the GET /agent/status snapshot:
 * connected agent cards, the empty state, and adb/serial forwarder rows.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp, mockUseConnections } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: { lang: string }) => unknown) =>
    selector ? selector({ lang: "en" }) : { lang: "en" },
  ),
  mockUseConnections: vi.fn(),
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("./useConnections", () => ({ useConnections: mockUseConnections }));

import { ConnectionsPage } from "./ConnectionsPage";

const STATUS = {
  v: "1",
  agents: [
    {
      agent_id: "a1b2c3d4e5f6",
      name: "bench-win-01",
      version: 1,
      caps: ["adb", "serial"],
      current: true,
      adb_devices: ["serial-1"],
      com_ports: [{ port: "COM27", desc: "USB serial" }],
    },
  ],
  forwarders: {
    adb: { bound: true, port: 5037 },
    serial: { bound: true, port: 9001, configured: true, com: "COM27", baud: 1500000 },
  },
};

describe("ConnectionsPage (P2)", () => {
  it("renders connected agents + forwarder rows", () => {
    mockUseConnections.mockReturnValue({
      data: STATUS,
      isLoading: false,
      isError: false,
    });
    const { container } = render(<ConnectionsPage />);
    expect(screen.getByText("bench-win-01")).toBeTruthy();
    // "current" badge present
    expect(container.querySelector(".agent-card__badge")).not.toBeNull();
    // adb + serial rows
    expect(container.querySelectorAll(".fwd-row").length).toBe(2);
    // both bound → on pills
    expect(container.querySelectorAll(".fwd-row__pill--on").length).toBe(2);
  });

  it("renders each agent's adb + com devices", () => {
    mockUseConnections.mockReturnValue({
      data: STATUS,
      isLoading: false,
      isError: false,
    });
    const { container } = render(<ConnectionsPage />);
    const devs = container.querySelectorAll(".agent-dev");
    expect(devs.length).toBe(2); // 1 adb serial + 1 com port
    expect(devs[0]?.textContent).toContain("serial-1");
    expect(devs[1]?.textContent).toContain("COM27");
  });

  it("shows 'no devices reported' when an agent has none", () => {
    const agent = { ...STATUS.agents[0], adb_devices: [], com_ports: [] };
    mockUseConnections.mockReturnValue({
      data: { ...STATUS, agents: [agent] },
      isLoading: false,
      isError: false,
    });
    const { container } = render(<ConnectionsPage />);
    expect(container.querySelector(".agent-dev--muted")).not.toBeNull();
  });

  it("shows the empty state when no agent is connected", () => {
    mockUseConnections.mockReturnValue({
      data: { ...STATUS, agents: [] },
      isLoading: false,
      isError: false,
    });
    const { container } = render(<ConnectionsPage />);
    expect(container.querySelector(".conn-empty")).not.toBeNull();
    expect(container.querySelector(".agent-card__dot--off")).not.toBeNull();
  });

  it("shows an error block when the fetch fails", () => {
    mockUseConnections.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    const { container } = render(<ConnectionsPage />);
    expect(container.querySelector(".conn-empty")).not.toBeNull();
    expect(container.querySelector(".conn-agents")).toBeNull();
  });

  it("serial not-configured renders the off pill", () => {
    mockUseConnections.mockReturnValue({
      data: {
        ...STATUS,
        forwarders: {
          adb: { bound: true, port: 5037 },
          serial: { bound: false, port: 9001, configured: false, com: null, baud: 115200 },
        },
      },
      isLoading: false,
      isError: false,
    });
    const { container } = render(<ConnectionsPage />);
    expect(container.querySelector(".fwd-row__pill--off")).not.toBeNull();
  });
});
