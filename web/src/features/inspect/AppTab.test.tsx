/**
 * AppTab spec — UIF-05: a confirmed successful uninstall must drop the
 * detail-panel selection, otherwise start / stop / clear stay clickable
 * against a package that no longer exists.
 *
 * `useAppActions` hooks + the zustand `useApp` store are mocked. The
 * mutation mocks are shared mutable singletons so specs can flip
 * `isSuccess` / `data` and force a re-render to drive the success
 * effect — the components re-read those fields every render, the same
 * way they would observe a real react-query mutation transition.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ─── mocks ─────────────────────────────────────────────────────────

const { mutations, queries, mockUseApp } = vi.hoisted(() => {
  type Env = {
    ok: boolean;
    data?: unknown;
    error?: { code: string; message: string };
  };
  const mkMutation = () => {
    const m = {
      isPending: false,
      isSuccess: false,
      data: undefined as Env | undefined,
      mutate: vi.fn(),
      reset: vi.fn(),
    };
    m.reset.mockImplementation(() => {
      m.isPending = false;
      m.isSuccess = false;
      m.data = undefined;
    });
    return m;
  };
  const mutations = {
    install: mkMutation(),
    start: mkMutation(),
    stop: mkMutation(),
    clear: mkMutation(),
    uninstall: mkMutation(),
  };
  const queries = {
    list: {
      data: {
        ok: true,
        data: { packages: ["com.foo", "com.bar"], count: 2 },
      },
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    },
    info: {
      data: {
        ok: true,
        data: {
          package: "com.foo",
          version_name: "1.0",
          version_code: "1",
          first_install_time: "2026-01-01",
          last_update_time: "2026-01-02",
          requested_permissions: [],
        },
      },
      isLoading: false,
    },
  };
  // Zustand-style selector hook: `useApp((s) => s.device)`.
  const mockUseApp = vi.fn((selector?: (s: any) => any) => {
    const state = { device: "serial1", lang: "en", setDevice: vi.fn() };
    return selector ? selector(state) : state;
  });
  return { mutations, queries, mockUseApp };
});

vi.mock("./useAppActions", () => ({
  useAppList: () => queries.list,
  useAppInfo: () => queries.info,
  useAppInstallMutation: () => mutations.install,
  useAppStartMutation: () => mutations.start,
  useAppStopMutation: () => mutations.stop,
  useAppClearDataMutation: () => mutations.clear,
  useAppUninstallMutation: () => mutations.uninstall,
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));

// Imported AFTER vi.mock so the component picks up the mocks.
import { AppTab } from "./AppTab";

// jsdom lacks ResizeObserver + Element.scrollTo (both used by
// @tanstack/react-virtual) — stub with no-ops, the virtual rows
// themselves are not asserted on.
class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = (() => {}) as typeof Element.prototype.scrollTo;
  }
  Object.values(mutations).forEach((m) => {
    m.reset();
    m.mutate.mockClear();
    m.reset.mockClear();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Keyboard-select the first package (virtual rows render no buttons
 *  in jsdom's zero-height scroll element; ArrowDown drives onSelect
 *  through the listbox handler instead). */
function selectFirstPackage() {
  const listbox = screen.getByRole("listbox", { name: /installed packages/i });
  fireEvent.keyDown(listbox, { key: "ArrowDown" });
}

function detailRegion(): HTMLElement {
  const el = document.querySelector(".app-detail");
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

/** Two-step armed click on a danger button inside the detail panel. */
function armAndConfirm(name: string | RegExp) {
  const detail = detailRegion();
  fireEvent.click(within(detail).getByRole("button", { name }));
  fireEvent.click(
    within(detail).getByRole("button", { name: /click again to confirm/i }),
  );
}

describe("AppTab package detail", () => {
  it("selecting a package shows its detail panel", () => {
    render(<AppTab />);
    expect(
      screen.getByText("Select a package to see details."),
    ).toBeInTheDocument();
    selectFirstPackage();
    expect(screen.getByRole("heading", { name: "com.foo" })).toBeInTheDocument();
    expect(
      screen.queryByText("Select a package to see details."),
    ).toBeNull();
  });

  it("confirmed successful uninstall drops the selection back to the empty state (UIF-05)", () => {
    const { rerender } = render(<AppTab />);
    selectFirstPackage();
    armAndConfirm("uninstall");
    expect(mutations.uninstall.mutate).toHaveBeenCalledWith("com.foo");

    // Backend reports success → success effect must clear selection.
    mutations.uninstall.isSuccess = true;
    mutations.uninstall.data = { ok: true, data: {} };
    rerender(<AppTab />);

    expect(
      screen.getByText("Select a package to see details."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "com.foo" })).toBeNull();
    expect(screen.queryByRole("button", { name: "uninstall" })).toBeNull();
  });

  it("failed uninstall keeps the detail panel and shows the error", () => {
    const { rerender } = render(<AppTab />);
    selectFirstPackage();
    armAndConfirm("uninstall");

    mutations.uninstall.isSuccess = true; // HTTP ok, envelope ok: false
    mutations.uninstall.data = {
      ok: false,
      error: { code: "APP_OP_FAILED", message: "uninstall rejected" },
    };
    rerender(<AppTab />);

    expect(screen.getByRole("heading", { name: "com.foo" })).toBeInTheDocument();
    expect(screen.getByText(/APP_OP_FAILED/)).toBeInTheDocument();
  });

  it("detail clear-data still requires the two-step arm and fires with the package (bec0391 regression)", () => {
    render(<AppTab />);
    selectFirstPackage();
    const detail = detailRegion();

    // First click arms only — nothing fired yet.
    fireEvent.click(within(detail).getByRole("button", { name: "clear data" }));
    expect(mutations.clear.mutate).not.toHaveBeenCalled();

    fireEvent.click(
      within(detail).getByRole("button", { name: /click again to confirm/i }),
    );
    expect(mutations.clear.mutate).toHaveBeenCalledWith("com.foo");
  });
});
