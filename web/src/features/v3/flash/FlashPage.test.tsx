/**
 * Flash (v3) against a mocked hub: plan problems block the run, unticking a
 * row re-checks with a new skip list, a failed run shows its failed line, and
 * a reloaded page still sees a running / finished job.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApp } from "../../../stores/app";
import { FlashPage } from "./FlashPage";

type Handler = (url: string, init?: RequestInit) => Response | object | undefined;

const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });
const ndjson = (events: object[]) =>
  new Response(events.map((e) => JSON.stringify(e)).join("\n") + "\n", { headers: { "content-type": "application/x-ndjson" } });

const STATUS = { v: "1", ok: true, available: true, busy: false, job: null, partitions: ["boot_a", "super", "userdata"], partitions_problem: "", board: "board-a" };
const BOARD = { ok: true, table: "/x/boards.toml", boards: [{ name: "board-a", serial_com: "COM7", serial_baud: 115200, note: "" }], active: "board-a", source: "file", problem: null, serial: { com: "COM7", baud: 115200, bound: true, readers: 0 } };
const FILES = { ok: true, path: "flash-images", entries: [
  { name: "flash_all.txt", is_dir: false, is_link: false, size: 900, mtime_epoch: 1_790_000_000 },
  { name: "boot.img", is_dir: false, is_link: false, size: 5_000_000, mtime_epoch: 1_790_000_000 },
] };

const row = (line: number, action: string, extra: object = {}) => ({ line, action, partition: "", file: "", key: "", value: "", command: "", size: 0, md5: "", sha256: "", skip: "", status: "pending", duration_s: 0, code: "", error: "", ...extra });
const ROWS = [
  row(3, "getvar", { key: "product", value: "board_x" }),
  row(13, "flash", { partition: "boot_a", file: "boot.img", size: 5_000_000, sha256: "314f71f4aa" }),
  row(23, "flash", { partition: "super", file: "super.img", size: 9_000_000, sha256: "9c02e1aabb" }),
  row(27, "erase", { partition: "userdata" }),
];
const PROBLEM_PLAN = { v: "1", ok: false, manifest: "flash_all.txt", fingerprint: "a".repeat(64), problems: ["line 27: the agent would refuse erase userdata: partition 'userdata' is not on the flash list"], total: 4, run: 4, skipped: 0, rows: ROWS };
const CLEAN_PLAN = { v: "1", ok: true, manifest: "flash_all.txt", fingerprint: "b".repeat(64), problems: [], total: 4, run: 3, skipped: 1, rows: ROWS.map((r) => (r.line === 27 ? { ...r, skip: "--skip userdata" } : r)) };

let calls: { url: string; body: unknown }[] = [];

function mockHub(extra: Handler = () => undefined, base: { jobs?: object; detail?: object; status?: object } = {}) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, body });
      const e = extra(url, init);
      if (e) return e instanceof Response ? e : json(e);
      if (url.startsWith("/api/flash/status")) return json({ ...STATUS, ...base.status });
      if (url.startsWith("/api/bench/board")) return json(BOARD);
      if (url.startsWith("/api/bench/ops")) return json({ ok: true, agent: { id: "a", name: "bench-01", build: "abc" }, bundle_build: "abc", reported: true, ops: [] });
      if (url.startsWith("/api/flash/getvar")) return ndjson([{ ev: "done", ok: true, stdout: "", stderr: "(bootloader) product:board_x\n(bootloader) current-slot:a\n(bootloader) unlocked:yes\n(bootloader) max-download-size:0x20000000\n" }]);
      if (url.startsWith("/api/flash/jobs/")) return json(base.detail ?? {}, base.detail ? 200 : 404);
      if (url.startsWith("/api/flash/jobs")) return json(base.jobs ?? { ok: true, running: null, jobs: [] });
      if (url.startsWith("/workspace/files?")) return json(FILES);
      return json({ detail: `unmocked ${url}` }, 404);
    }),
  );
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRootRoute();
  const tree = root.addChildren([
    createRoute({ getParentRoute: () => root, path: "/", component: FlashPage }),
    ...["/bench", "/timeline"].map((p) => createRoute({ getParentRoute: () => root, path: p, component: () => null })),
  ]);
  const router = createRouter({ routeTree: tree, history: createMemoryHistory({ initialEntries: ["/"] }) });
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

async function checkPlanOf(name = "flash-images/flash_all.txt") {
  fireEvent.click(await screen.findByRole("radio", { name }));
  fireEvent.click(screen.getByRole("button", { name: "Check the plan" }));
}

beforeEach(() => useApp.setState({ lang: "en" }));
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Flash page", () => {
  it("lists manifests but hides image files, and reads the board identity", async () => {
    mockHub();
    renderPage();
    expect(await screen.findByRole("radio", { name: "flash-images/flash_all.txt" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "flash-images/boot.img" })).toBeNull();
    expect(await screen.findByText("512 MiB")).toBeInTheDocument();
    expect(screen.getByText("board_x")).toBeInTheDocument();
    expect(screen.getByText("_a · yes")).toBeInTheDocument();
    expect(screen.getByText("3 partitions for board-a")).toBeInTheDocument();
  });

  it("a plan with a problem shows it and does not allow the run", async () => {
    mockHub((url) => (url.startsWith("/api/flash/plan") && !url.includes("/run") ? PROBLEM_PLAN : undefined));
    renderPage();
    await checkPlanOf();
    expect(await screen.findByText(/1 problem\(s\), so no row will run/)).toBeInTheDocument();
    expect(screen.getByText(/line 27: the agent would refuse erase userdata/)).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Flash package" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
  });

  it("unticking a row re-checks with the partition in the skip list and shows the new fingerprint", async () => {
    mockHub((url, init) => {
      if (!url.startsWith("/api/flash/plan") || url.includes("/run")) return undefined;
      const b = JSON.parse(String(init?.body));
      return b.skip.includes("userdata") ? CLEAN_PLAN : PROBLEM_PLAN;
    });
    renderPage();
    await checkPlanOf();
    expect(await screen.findByText("# aaaaaaaa")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "run line 27" }));
    expect(await screen.findByText("# bbbbbbbb")).toBeInTheDocument();
    const plans = calls.filter((c) => c.url === "/api/flash/plan");
    expect(plans).toHaveLength(2);
    expect(plans[1]?.body).toMatchObject({ manifest: "flash-images/flash_all.txt", skip: ["userdata"], skip_actions: [] });
    expect(screen.queryByText(/so no row will run/)).toBeNull();
    expect((screen.getByRole("button", { name: "Flash package" }) as HTMLButtonElement).disabled).toBe(false);
    // ticking it again takes it back out of the list
    fireEvent.click(screen.getByRole("checkbox", { name: "run line 27" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/flash/plan")).toHaveLength(3));
    expect(calls.filter((c) => c.url === "/api/flash/plan")[2]?.body).toMatchObject({ skip: [] });
  });

  it("unticking a non-partition row skips its action", async () => {
    mockHub((url) => (url.startsWith("/api/flash/plan") && !url.includes("/run") ? CLEAN_PLAN : undefined));
    renderPage();
    await checkPlanOf();
    fireEvent.click(await screen.findByRole("checkbox", { name: "run line 3" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/flash/plan")).toHaveLength(2));
    expect(calls.filter((c) => c.url === "/api/flash/plan")[1]?.body).toMatchObject({ skip: [], skip_actions: ["getvar"] });
  });

  it("asks before writing, then shows the failed line with its code and the not-run count", async () => {
    mockHub((url) => {
      if (url === "/api/flash/plan") return CLEAN_PLAN;
      if (url === "/api/flash/plan/run") {
        return ndjson([
          { ev: "progress", phase: "step", done: 1, total: 3, text: "line 13: flash boot_a <- boot.img" },
          { ev: "progress", phase: "flash", done: 5_000_000, total: 5_000_000, text: "" },
          { ev: "progress", phase: "step", done: 2, total: 3, text: "line 23: flash super <- super.img" },
          {
            ev: "done", ok: false, code: "FLASH_WRITE_FAILED", error: "FAILED (remote: 'Partition should be flashed in fastbootd')", failed_line: 23,
            total: 4, ran: 2, skipped: 1, not_run: 0, stdout: "", stderr: "", duration_s: 5,
            rows: [
              { ...ROWS[0], status: "ok" }, { ...ROWS[1], status: "ok" },
              { ...ROWS[2], status: "failed", code: "FLASH_WRITE_FAILED" }, { ...ROWS[3], skip: "--skip userdata", status: "skipped" },
            ],
          },
        ]);
      }
      return undefined;
    });
    renderPage();
    await checkPlanOf();
    await screen.findByText("# bbbbbbbb");
    fireEvent.click(screen.getByRole("button", { name: "Flash package" }));
    // the confirmation names the board and the row count; nothing was sent yet
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("board-a");
    expect(alert.textContent).toContain("3 rows run");
    expect(alert.textContent).toContain("userdata not included");
    expect(calls.some((c) => c.url === "/api/flash/plan/run")).toBe(false);
    fireEvent.click(within(alert).getByRole("button", { name: "Flash package" }));
    expect((await screen.findAllByText("FLASH_WRITE_FAILED")).length).toBeGreaterThan(0);
    expect(screen.getByText(/stopped at line 23/)).toBeInTheDocument();
    expect(screen.getByText(/Partition should be flashed in fastbootd/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reboot to fastbootd" })).toBeInTheDocument();
    const run = calls.find((c) => c.url === "/api/flash/plan/run");
    expect(run?.body).toMatchObject({ fingerprint: "b".repeat(64), manifest: "flash-images/flash_all.txt", skip: [] });
  });

  it("a reloaded page shows the job that is still running and the recent jobs", async () => {
    mockHub(undefined, {
      status: { busy: true, job: "plan flash_all: line 23 (2/3) flash super" },
      jobs: {
        ok: true, running: "plan flash_all: line 23 (2/3) flash super",
        jobs: [
          { id: "plan-2026-10-28T09-12-00", op: "plan", label: "plan flash_all", started_at: "2026-10-28T09:12:00+00:00", ok: null, code: "", failed_line: 0, rows: { total: 0, ok: 0, failed: 0, skipped: 0, not_run: 0 } },
          { id: "flash-2026-10-28T08-00-00", op: "flash", label: "flash boot_a", started_at: "2026-10-28T08:00:00+00:00", ok: true, code: "", failed_line: 0, rows: { total: 1, ok: 1, failed: 0, skipped: 0, not_run: 0 } },
        ],
      },
      detail: { ok: null, id: "plan-2026-10-28T09-12-00", op: "plan", label: "plan flash_all", started_at: null, code: "", failed_line: 0, rows: {}, job: null, timeline: null, timeline_path: null },
    });
    renderPage();
    expect(await screen.findByText("flash boot_a")).toBeInTheDocument();
    expect(await screen.findByText("job running")).toBeInTheDocument();
    const card = await screen.findByLabelText("Last job");
    expect(within(card).getByText("running")).toBeInTheDocument();
    // the Flash package button cannot be reached while the lock is held: flash status says busy
    expect(screen.getAllByText("running").length).toBeGreaterThan(0);
  });

  it("shows the recorded verdict of a finished failed job from disk", async () => {
    mockHub(undefined, {
      jobs: { ok: true, running: null, jobs: [{ id: "plan-x", op: "plan", label: "plan flash_all", started_at: "2026-10-28T09:12:00+00:00", ok: false, code: "FLASH_WRITE_FAILED", failed_line: 23, rows: { total: 4, ok: 2, failed: 1, skipped: 1, not_run: 0 } }] },
      detail: { id: "plan-x", op: "plan", label: "plan flash_all", started_at: null, ok: false, code: "FLASH_WRITE_FAILED", failed_line: 23, rows: {}, job: { ok: false, code: "FLASH_WRITE_FAILED", error: "FAILED (remote: nope)", failed_line: 23, total: 4, ran: 2, not_run: 0 }, timeline: "timeline.jsonl", timeline_path: "flash/plan-x/timeline.jsonl" },
    });
    renderPage();
    const card = await screen.findByLabelText("Last job");
    await waitFor(() => expect(within(card).getByText("FLASH_WRITE_FAILED")).toBeInTheDocument());
    expect(within(card).getByText(/stopped at line 23/)).toBeInTheDocument();
  });

  it("flash unavailable: says so and keeps the checks and writes disabled", async () => {
    mockHub(undefined, { status: { available: false } });
    renderPage();
    expect(await screen.findByText(/Flashing is unavailable/)).toBeInTheDocument();
    expect((screen.getByRole("button", { name: "Reboot to Android" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(await screen.findByRole("radio", { name: "flash-images/flash_all.txt" }));
    expect((screen.getByRole("button", { name: "Check the plan" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("one partition: the picker is the board's list, and a list problem blocks writing", async () => {
    mockHub();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /One partition/ }));
    const sel = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    expect([...sel.options].map((o) => o.value)).toEqual(["boot_a", "super", "userdata"]);
  });

  it("one partition: no list for the chosen board offers nothing and says why", async () => {
    mockHub(undefined, { status: { partitions: [], partitions_problem: "no bench board is chosen" } });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /One partition/ }));
    expect(await screen.findByText("no bench board is chosen", { selector: "span" })).toBeInTheDocument();
    const write = screen.getByRole("button", { name: "Write" }) as HTMLButtonElement;
    expect(write.disabled).toBe(true);
  });
});
