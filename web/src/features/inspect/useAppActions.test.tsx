/**
 * useAppActions spec — pins the `allow_dangerous` wiring of the two
 * destructive mutations (clear-data / uninstall). The armed two-step
 * confirm in the UI is the user's authorisation, so the mutation layer
 * must always opt in; the backend rejects both ops without the flag.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockPostAppClearData, mockPostAppUninstall } = vi.hoisted(() => ({
  mockPostAppClearData: vi.fn(async () => ({ ok: true, data: {} })),
  mockPostAppUninstall: vi.fn(async () => ({ ok: true, data: {} })),
}));

vi.mock("../../lib/api", () => ({
  fetchAppInfo: vi.fn(),
  fetchAppList: vi.fn(),
  postAppClearData: mockPostAppClearData,
  postAppInstall: vi.fn(),
  postAppStart: vi.fn(),
  postAppStop: vi.fn(),
  postAppUninstall: mockPostAppUninstall,
}));

// Imported AFTER vi.mock so the hooks pick up the mocked api module.
import {
  useAppClearDataMutation,
  useAppUninstallMutation,
} from "./useAppActions";

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  mockPostAppClearData.mockClear();
  mockPostAppUninstall.mockClear();
});

describe("destructive app mutations pass allow_dangerous", () => {
  it("clear-data mutation opts in with allow_dangerous: true", async () => {
    const { result } = renderHook(() => useAppClearDataMutation("serial1"), {
      wrapper: makeWrapper(),
    });
    act(() => {
      result.current.mutate("com.example.app");
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockPostAppClearData).toHaveBeenCalledWith(
      "serial1",
      "com.example.app",
      { allow_dangerous: true },
    );
  });

  it("uninstall mutation opts in with allow_dangerous: true", async () => {
    const { result } = renderHook(() => useAppUninstallMutation("serial1"), {
      wrapper: makeWrapper(),
    });
    act(() => {
      result.current.mutate("com.example.app");
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockPostAppUninstall).toHaveBeenCalledWith(
      "serial1",
      "com.example.app",
      { allow_dangerous: true },
    );
  });
});
