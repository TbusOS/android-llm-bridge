/**
 * Per-board partition lists on the Flash tab (ADR-063 phase 2).
 *
 * `partitions: []` now has two meanings, and the page must tell them apart:
 * with no `partitions_problem` it is "the agent configured no list" (offer
 * the generic names, as before); with one it is "nothing may be written"
 * (offer nothing, say why). Reading the second as the first sent every
 * click to FLASH_BOARD_NOT_LISTED while the page looked ready — the same
 * label/reality drift as the old hard-coded picker.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FlashTab } from "./FlashTab";

const READY = { v: "1", ok: true, available: true, busy: false, job: "" };
const PROBLEM =
  "the agent keeps a partition list per board (board-a, board-b) and no bench board is chosen — `alb bench board use <name>`";

function mockStatus(body: object) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flash/status")) {
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FlashTab />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("per-board partition lists", () => {
  it("offers the chosen board's list and names the board", async () => {
    mockStatus({
      ...READY,
      partitions: ["boot_a", "misc"],
      partitions_problem: "",
      board: "board-a",
    });
    renderTab();
    await waitFor(() =>
      expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["boot_a", "misc"]),
    );
    expect(document.querySelector(".flash-state-card")?.textContent).toContain("board-a");
    expect(document.querySelector(".flash-problem")).toBeNull();
  });

  it("offers nothing and says why when no list applies", async () => {
    mockStatus({ ...READY, partitions: [], partitions_problem: PROBLEM, board: null });
    renderTab();
    await waitFor(() => expect(document.querySelector(".flash-problem")).not.toBeNull());
    expect(document.querySelector(".flash-problem")?.textContent).toContain("alb bench board use");
    // Not the generic stand-in names: each of those would be refused.
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
    const flash = screen.getByRole("button", { name: /^(flash|烧录)$/i }) as HTMLButtonElement;
    expect(flash.disabled).toBe(true);
  });

  it("still offers the generic names when the agent simply has no list", async () => {
    mockStatus({ ...READY, partitions: [], partitions_problem: "", board: null });
    renderTab();
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(0));
    expect(document.querySelector(".flash-problem")).toBeNull();
    const labels = [...document.querySelectorAll(".flash-state-card__field-label")].map(
      (e) => e.textContent,
    );
    expect(labels).not.toContain("board");
  });
});
