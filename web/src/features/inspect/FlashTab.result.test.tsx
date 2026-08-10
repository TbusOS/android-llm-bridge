/**
 * Flash result banner + "last job" row.
 *
 * Why these exist: before this, a finished job announced itself with the word
 * `ok` on the timeline's last line — same size, same column, same styling as
 * every `job` label above it. On real hardware that is genuinely hard to spot,
 * and the state card kept saying `ready` (it answers "can this bench flash?",
 * not "how did the last one go?").
 *
 * The property most worth pinning is the LABEL/REALITY pairing: the partition
 * picker and the file input stay editable while a result is on screen, so a
 * banner rendered from live form state would silently relabel a finished job.
 * That is the same defect class as the hard-coded partition picker — the label
 * and the reality drifted apart and nothing failed.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FlashTab } from "./FlashTab";

const STATUS = { v: "1", ok: true, available: true, busy: false, job: "", partitions: ["cfg", "boot"] };

function ndjson(lines: object[]): Response {
  const encoder = new TextEncoder();
  const chunks = lines.map((l) => `${JSON.stringify(l)}\n`);
  let i = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
      }),
    },
  } as unknown as Response;
}

const DONE_OK = {
  ev: "done",
  ok: true,
  rc: 0,
  code: "",
  error: "",
  stdout: "",
  stderr: "",
  duration_s: 0.14,
  artifacts: "/w/flash/x",
};

let verdictLines: object[] = [DONE_OK];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/flash/status")) {
      return { ok: true, status: 200, json: async () => STATUS } as unknown as Response;
    }
    if (url.includes("/workspace/files/upload")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ path: "flash-images/cfg-RESTORE-V03.ini" }),
      } as unknown as Response;
    }
    if (url.includes("/api/flash/")) return ndjson(verdictLines);
    throw new Error(`unexpected fetch ${url}`);
  });
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FlashTab />
    </QueryClientProvider>,
  );
}

async function flashOnce(user: ReturnType<typeof userEvent.setup>) {
  const file = new File([new Uint8Array(941)], "cfg-RESTORE-V03.ini");
  // The input is `hidden` and driven by a button, so there is no label to
  // query — go at the element directly, the same way a file picker does.
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(input, file);
  const btn = await screen.findByRole("button", { name: /^(flash|烧录)$/i });
  await user.click(btn); // arms
  await user.click(btn); // runs
}

beforeEach(() => {
  verdictLines = [DONE_OK];
  vi.stubGlobal("fetch", mockFetch());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("result banner", () => {
  it("is absent before anything has run", () => {
    renderTab();
    expect(document.querySelector(".flash-result")).toBeNull();
  });

  it("appears with the outcome once a job finishes", async () => {
    const user = userEvent.setup();
    renderTab();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeTruthy());
    await flashOnce(user);
    await waitFor(() => expect(document.querySelector(".flash-result")).not.toBeNull());
    expect(document.querySelector(".flash-result")?.getAttribute("data-ok")).toBe("true");
  });

  it("states what was established and NOT that the contents were verified", async () => {
    const user = userEvent.setup();
    renderTab();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeTruthy());
    await flashOnce(user);
    const scope = await waitFor(() => {
      const el = document.querySelector(".flash-result__scope");
      expect(el).not.toBeNull();
      return el as Element;
    });
    // `Writing OKAY` is fastboot saying it wrote. alb performs no readback,
    // and the banner must not let anyone read "succeeded" as "verified".
    expect(scope.textContent).toMatch(/not read back|没有回读校验/);
  });

  it("shows the failure code instead of the reassurance when the job fails", async () => {
    verdictLines = [
      {
        ...DONE_OK,
        ok: false,
        rc: 1,
        code: "FLASH_PARTITION_REJECTED",
        error: "partition 'boot' rejected by this agent",
      },
    ];
    const user = userEvent.setup();
    renderTab();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeTruthy());
    await flashOnce(user);
    await waitFor(() =>
      expect(document.querySelector(".flash-result")?.getAttribute("data-ok")).toBe("false"),
    );
    expect(document.querySelector(".flash-result__code")?.textContent).toContain(
      "FLASH_PARTITION_REJECTED",
    );
    expect(document.querySelector(".flash-result__scope")).toBeNull();
  });
});

describe("the banner describes the job that ran, not the form as it is now", () => {
  it("keeps the original partition after the picker is changed", async () => {
    const user = userEvent.setup();
    renderTab();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeTruthy());
    await flashOnce(user);
    await waitFor(() => expect(document.querySelector(".flash-result")).not.toBeNull());
    expect(document.querySelector(".flash-result__what")?.textContent).toContain("cfg");

    // Operator now eyes a different partition. The finished result must not
    // follow — a banner that relabels itself is worse than no banner.
    await user.selectOptions(screen.getByRole("combobox"), "boot");
    expect(document.querySelector(".flash-result__what")?.textContent).toContain("cfg");
    expect(document.querySelector(".flash-result__what")?.textContent).not.toContain("boot");
  });
});

describe("state card last-job row", () => {
  it("is absent until a job has run", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeTruthy());
    expect(screen.queryByText(/last job|上次作业/)).toBeNull();
  });

  it("reports the outcome the capability line cannot", async () => {
    const user = userEvent.setup();
    renderTab();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeTruthy());
    await flashOnce(user);
    // `ready` is still correct — it means the bench can flash. That is exactly
    // why it cannot double as job feedback.
    await waitFor(() => expect(screen.getByText(/last job|上次作业/)).toBeTruthy());
    const row = screen.getByText(/last job|上次作业/).parentElement;
    expect(row?.textContent).toContain("ok");
    expect(row?.textContent).toContain("cfg");
  });
});
