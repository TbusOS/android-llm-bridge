/**
 * LogSearchTab spec — ARCH-1: dmesg was reachable from the CLI / MCP but
 * had no web entry, so the Log Search tab could only ever find an empty
 * result for kernel logs. The "collect dmesg" button must (1) reach the
 * dmesg capability and (2) auto-run the search on success so the freshly
 * collected kernel log is immediately grep-able. Also pins the
 * no-device guard.
 *
 * `lib/api` (postDmesg / fetchLogSearch) and the zustand `useApp` store
 * are mocked; a real QueryClient drives the useMutation lifecycle.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { appState, mockUseApp, mockPostDmesg, mockFetchLogSearch } = vi.hoisted(
  () => {
    const appState = { device: "serial1" as string | null, lang: "en" };
    const mockUseApp = vi.fn((selector?: (s: any) => any) =>
      selector ? selector(appState) : appState,
    );
    return {
      appState,
      mockUseApp,
      mockPostDmesg: vi.fn(),
      mockFetchLogSearch: vi.fn(),
    };
  },
);

vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("../../lib/api", () => ({
  postDmesg: mockPostDmesg,
  fetchLogSearch: mockFetchLogSearch,
}));

// Imported AFTER vi.mock so the component picks up the mocked modules.
import { LogSearchTab } from "./LogSearchTab";

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  appState.device = "serial1";
  appState.lang = "en";
  mockPostDmesg.mockReset();
  mockFetchLogSearch.mockReset();
  // Default search returns an empty (but ok) envelope so the auto-run
  // after a successful collect doesn't reject.
  mockFetchLogSearch.mockResolvedValue({
    ok: true,
    data: { matches: [], match_count: 0, truncated: false },
    timing_ms: 5,
  });
});

describe("LogSearchTab dmesg collection (ARCH-1)", () => {
  it("disables the collect button when no device is selected", () => {
    appState.device = null;
    render(<LogSearchTab />, { wrapper: makeWrapper() });
    expect(
      screen.getByRole("button", { name: /collect dmesg/i }),
    ).toBeDisabled();
    expect(mockPostDmesg).not.toHaveBeenCalled();
  });

  it("collecting dmesg posts to the capability then auto-runs the search", async () => {
    mockPostDmesg.mockResolvedValue({
      ok: true,
      data: { lines: 42, errors: 3, duration_captured_ms: 10000 },
    });
    render(<LogSearchTab />, { wrapper: makeWrapper() });

    fireEvent.click(screen.getByRole("button", { name: /collect dmesg/i }));

    await waitFor(() =>
      expect(mockPostDmesg).toHaveBeenCalledWith("serial1", 10),
    );
    // onSuccess kicks the default-pattern search across the workspace.
    await waitFor(() =>
      expect(mockFetchLogSearch).toHaveBeenCalledWith("panic|oops|fatal", {
        device: "serial1",
        max: 200,
      }),
    );
    // The collected counts surface in the note line.
    await waitFor(() =>
      expect(
        screen.getByText(/dmesg collected: 42 lines \(3 errors\)/i),
      ).toBeInTheDocument(),
    );
  });

  it("surfaces a failed collection's envelope error and skips the search", async () => {
    mockPostDmesg.mockResolvedValue({
      ok: false,
      error: { code: "DMESG_FAILED", message: "adb offline" },
    });
    render(<LogSearchTab />, { wrapper: makeWrapper() });

    fireEvent.click(screen.getByRole("button", { name: /collect dmesg/i }));

    await waitFor(() =>
      expect(screen.getByText(/DMESG_FAILED: adb offline/)).toBeInTheDocument(),
    );
    expect(mockFetchLogSearch).not.toHaveBeenCalled();
  });
});
