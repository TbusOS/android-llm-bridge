/**
 * SystemInfoTab spec — ARCH-2: the security / gpu / processes panels were
 * CLI/MCP-only (verified boot / AVB / SELinux, GPU governor, top
 * processes). This pins that the web tab now renders all three, and
 * degrades to a per-card error / loading state without blanking the tab.
 *
 * `useDeviceSystem` + `useDeviceInfoPanels` + the `useApp` store are
 * mocked as mutable singletons so specs flip a panel envelope per test.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { appState, system, panels, mockUseApp } = vi.hoisted(() => {
  const appState = { device: "serial1" as string | null, lang: "en" };
  const mockUseApp = vi.fn((selector?: (s: any) => any) =>
    selector ? selector(appState) : appState,
  );
  const system = {
    data: undefined as any,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
  const panels = {
    security: undefined as any,
    gpu: undefined as any,
    processes: undefined as any,
    isLoading: false,
  };
  return { appState, system, panels, mockUseApp };
});

vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("./useDeviceSystem", () => ({ useDeviceSystem: () => system }));
vi.mock("./useDeviceInfoPanels", () => ({ useDeviceInfoPanels: () => panels }));

// Imported AFTER vi.mock so the component picks up the mocked hooks.
import { SystemInfoTab } from "./SystemInfoTab";

function healthySystem() {
  return {
    ok: true,
    system: {
      props: { "ro.product.model": "Test" },
      meminfo: { MemTotal: 8192000, MemAvailable: 4096000 },
      block_devices: [],
      partitions: [],
      mounts: [],
      storage: {},
      network: [],
      battery: {},
      thermal: [],
    },
  };
}

beforeEach(() => {
  appState.device = "serial1";
  appState.lang = "en";
  system.data = healthySystem();
  system.refetch = vi.fn();
  panels.security = {
    ok: true,
    data: {
      verified_boot_state: "green",
      avb_version: "1.2",
      verity_mode: "enforcing",
      crypto_state: "encrypted",
      crypto_type: "file",
      file_encryption: "aes-256-xts",
      selinux_mode: "Enforcing",
      selinux_policy_version: "33",
      oem_unlock_allowed: false,
      oem_unlock_supported: true,
      adb_secure: true,
    },
  };
  panels.gpu = {
    ok: true,
    data: {
      name: "Mali-G52",
      vendor: "ARM",
      renderer: "Mali",
      freq_hz_current: 800_000_000,
      freq_hz_max: 1_000_000_000,
      freq_hz_min: 200_000_000,
      governor: "simple_ondemand",
      util_pct: 17,
    },
  };
  panels.processes = {
    ok: true,
    data: {
      count: 1,
      top_cpu: [
        {
          pid: 123,
          user: "system",
          cpu_pct: 12.5,
          mem_pct: 3,
          rss_kb: 50_000,
          name: "system_server",
        },
      ],
      top_mem: [],
    },
  };
  panels.isLoading = false;
});

describe("SystemInfoTab info panels (ARCH-2)", () => {
  it("renders the security / gpu / processes cards from the panel data", () => {
    render(<SystemInfoTab />);

    // Security / Boot card — high-value verified-boot field.
    expect(
      screen.getByRole("heading", { name: /Security \/ Boot/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Verified boot")).toBeInTheDocument();
    expect(screen.getByText("green")).toBeInTheDocument();
    expect(screen.getByText("Enforcing")).toBeInTheDocument();

    // GPU card.
    expect(screen.getByRole("heading", { name: "GPU" })).toBeInTheDocument();
    expect(screen.getByText("simple_ondemand")).toBeInTheDocument();

    // Processes card (top CPU).
    expect(
      screen.getByRole("heading", { name: /Processes \(top CPU\)/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("system_server (123)")).toBeInTheDocument();
  });

  it("shows a per-card error when a panel envelope fails, keeping siblings", () => {
    panels.security = {
      ok: false,
      error: { code: "INFO_FAILED", message: "selinux read denied" },
    };
    render(<SystemInfoTab />);

    expect(
      screen.getByRole("heading", { name: /Security \/ Boot/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("selinux read denied")).toBeInTheDocument();
    // Sibling panels still render — one bad panel must not blank the tab.
    expect(screen.getByText("simple_ondemand")).toBeInTheDocument();
    expect(screen.getByText("system_server (123)")).toBeInTheDocument();
  });

  it("renders a loading placeholder while a panel is still pending", () => {
    panels.gpu = undefined;
    render(<SystemInfoTab />);

    expect(screen.getByRole("heading", { name: "GPU" })).toBeInTheDocument();
    expect(screen.getByText("loading…")).toBeInTheDocument();
  });

  it("shows the no-device card and no panels when nothing is selected", () => {
    appState.device = null;
    render(<SystemInfoTab />);

    expect(screen.getByText("No device")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "GPU" })).toBeNull();
    expect(screen.queryByText("Verified boot")).toBeNull();
  });
});
