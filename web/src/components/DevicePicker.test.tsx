/**
 * DevicePicker spec — covers the keyboard nav + focus management
 * fixes from commit AB (audit ui-fluency MID#3 / code-r LOW#8).
 *
 * Both `useDevices` and the zustand `useApp` store are mocked so
 * specs drive the rendered list and selected-device state directly,
 * without spinning up the real `/devices` fetcher. Mock returns the
 * raw `ApiDevice` shape (DevicePicker projects its own picker view-
 * model from raw — arch HIGH-4 fix 5/25).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiDevice } from "../lib/api";

// ─── mocks ─────────────────────────────────────────────────────────

const { mockUseDevices, mockUseApp, setDeviceFn, setActiveDevice } = vi.hoisted(
  () => {
    let activeDevice: string | null = null;
    let devicesList: ApiDevice[] = [];
    let transportName: string | null = "AdbUsbTransport";
    let isLoading = false;
    let isError = false;
    const refetch = vi.fn();
    const setDeviceFn = vi.fn((d: string | null) => {
      activeDevice = d;
    });
    const setActiveDevice = (d: string | null) => {
      activeDevice = d;
    };
    const setDevicesList = (l: ApiDevice[]) => {
      devicesList = l;
    };
    const setLoading = (v: boolean) => {
      isLoading = v;
    };
    const setError = (v: boolean) => {
      isError = v;
    };
    const setTransport = (n: string | null) => {
      transportName = n;
    };
    const mockUseDevices = vi.fn(() => ({
      devices: devicesList,
      transportName,
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
      __ctl: { setDevicesList, setLoading, setError, setTransport },
    } as const;
  },
);

vi.mock("../lib/hooks/useDevices", () => ({ useDevices: mockUseDevices }));
vi.mock("../stores/app", () => ({ useApp: mockUseApp }));

// Imported AFTER vi.mock so the component picks up the mocks.
import { DevicePicker } from "./DevicePicker";

function mkDevice(
  serial: string,
  state: "device" | "offline" | "unauthorized" = "device",
): ApiDevice {
  return {
    serial,
    state,
    product: "test",
    model: "TestDevice",
  };
}

beforeEach(() => {
  setDeviceFn.mockClear();
  setActiveDevice(null);
  mockUseDevices.mockImplementation(() => ({
    devices: [
      mkDevice("abc123"),
      mkDevice("def456"),
      mkDevice("offline7", "offline"),
    ],
    transportName: "AdbUsbTransport",
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

  it("aria-activedescendant is on the listbox (not the trigger) · L-053 regression", () => {
    // AK-1 真修 (5/25 第二轮 AI-7 假修 → 第三轮 H1 → AK-1 真修)：
    // DOM focus 在 <ul role="listbox"> · 必须把 aria-activedescendant
    // 挂在 listbox 上才能被 SR (NVDA/VoiceOver) 读。任何 future
    // refactor 把它挪回 trigger button = 静默 a11y regression。
    render(<DevicePicker lang="en" />);
    fireEvent.click(screen.getByRole("combobox"));
    const list = screen.getByRole("listbox");
    const trigger = screen.getByRole("combobox");

    // listbox carries activedescendant pointing at focused option
    expect(list).toHaveAttribute(
      "aria-activedescendant",
      expect.stringMatching(/-opt-0$/),
    );
    // trigger MUST NOT carry it (would split AT attention)
    expect(trigger).not.toHaveAttribute("aria-activedescendant");

    // Step through options with ArrowDown → activedescendant follows
    fireEvent.keyDown(list, { key: "ArrowDown" });
    expect(list).toHaveAttribute(
      "aria-activedescendant",
      expect.stringMatching(/-opt-1$/),
    );

    // Every focused option's id matches the activedescendant value
    const activeId = list.getAttribute("aria-activedescendant");
    expect(activeId).not.toBeNull();
    expect(document.getElementById(activeId!)).not.toBeNull();
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
      transportName: "AdbUsbTransport",
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
