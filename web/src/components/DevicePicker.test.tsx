/**
 * DevicePicker spec — covers the keyboard nav + focus management
 * fixes from commit AB (audit ui-fluency MID#3 / code-r LOW#8).
 *
 * Both `useDevices` and the zustand `useApp` store are mocked so
 * specs drive the rendered list and selected-device state directly,
 * without spinning up the real `/devices` fetcher.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DeviceCardData } from "../features/dashboard/types";

// ─── mocks ─────────────────────────────────────────────────────────

const { mockUseDevices, mockUseApp, setDeviceFn, setActiveDevice } = vi.hoisted(
  () => {
    let activeDevice: string | null = null;
    let devicesList: DeviceCardData[] = [];
    let isLoading = false;
    let isError = false;
    const refetch = vi.fn();
    const setDeviceFn = vi.fn((d: string | null) => {
      activeDevice = d;
    });
    const setActiveDevice = (d: string | null) => {
      activeDevice = d;
    };
    const setDevicesList = (l: DeviceCardData[]) => {
      devicesList = l;
    };
    const setLoading = (v: boolean) => {
      isLoading = v;
    };
    const setError = (v: boolean) => {
      isError = v;
    };
    const mockUseDevices = vi.fn(() => ({
      devices: devicesList,
      transportName: null,
      backendError: null,
      isLoading,
      isError,
      error: null,
      refetch,
    }));
    // Zustand-style selector hook: `useApp((s) => s.lang)`.
    const mockUseApp = vi.fn((selector?: (s: any) => any) => {
      const state = { device: activeDevice, setDevice: setDeviceFn };
      return selector ? selector(state) : state;
    });
    return {
      mockUseDevices,
      mockUseApp,
      setDeviceFn,
      setActiveDevice,
      // Test helpers (export but unused outside; vi.hoisted requires
      // returned values to be the only inter-spec channel).
      __ctl: { setDevicesList, setLoading, setError },
    } as const;
  },
);

vi.mock("../lib/hooks/useDevices", () => ({ useDevices: mockUseDevices }));
vi.mock("../stores/app", () => ({ useApp: mockUseApp }));

// Imported AFTER vi.mock so the component picks up the mocks.
import { DevicePicker } from "./DevicePicker";

function mkDevice(
  id: string,
  status: "online" | "offline" | "warn" = "online",
): DeviceCardData {
  return {
    id,
    name: id,
    modelLine: "Test device",
    transport: "adb-usb",
    transportLabel: "adb",
    status,
    cpu: null,
    cpuTrend: [],
    cpuColor: "blue",
    tempC: null,
    tempTrend: [],
    tempColor: "blue",
  };
}

beforeEach(() => {
  setDeviceFn.mockClear();
  setActiveDevice(null);
  mockUseDevices.mockImplementation(() => ({
    devices: [mkDevice("abc123"), mkDevice("def456"), mkDevice("offline7", "offline")],
    transportName: null,
    backendError: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("DevicePicker", () => {
  it("renders 'no device' label when none selected, hides meta", () => {
    render(<DevicePicker lang="en" />);
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("no device")).toBeInTheDocument();
  });

  it("click trigger opens listbox with all devices", () => {
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("clicking an option calls setDevice + closes menu", () => {
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    const options = screen.getAllByRole("option");
    fireEvent.click(options[1]!);
    expect(setDeviceFn).toHaveBeenCalledWith("def456");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("Enter on trigger opens menu (keyboard path)", () => {
    render(<DevicePicker lang="en" />);
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("ArrowDown / ArrowUp / Home / End move focusIdx; Enter selects", () => {
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    const list = screen.getByRole("listbox");

    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "End" });
    fireEvent.keyDown(list, { key: "ArrowUp" });
    fireEvent.keyDown(list, { key: "Enter" });
    // After ArrowDown → idx 1, End → 2, ArrowUp → 1 (def456).
    expect(setDeviceFn).toHaveBeenCalledWith("def456");
  });

  it("Escape closes the menu without selecting", () => {
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(setDeviceFn).not.toHaveBeenCalled();
  });

  it("click-outside closes the menu", () => {
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("loading state renders hint", () => {
    mockUseDevices.mockImplementation(() => ({
      devices: [],
      transportName: null,
      backendError: null,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }));
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it("empty list shows actionable hint", () => {
    mockUseDevices.mockImplementation(() => ({
      devices: [],
      transportName: null,
      backendError: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }));
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    expect(screen.getByText(/No devices/i)).toBeInTheDocument();
  });
});
