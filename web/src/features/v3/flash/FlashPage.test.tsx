/**
 * Flash (v3) against a mocked hub: plan problems block the run, unticking a
 * row re-checks with a new skip list, a failed run shows its failed line, and
 * a reloaded page still sees a running / finished job. Also: every line of
 * the job and serial record (not a tail), the "Open large" views, the
 * not-read-back note after a write, and why flashing is unavailable.
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

const AGENTS_OK = { v: "1", agents: [{ agent_id: "a1", name: "bench-01", version: 1, caps: ["adb", "fastboot"], current: true, adb_devices: [], com_ports: [] }], forwarders: { adb: { bound: true, port: 1 }, serial: { bound: true, port: 2, configured: true, com: "COM7", baud: 115200 } } };

let calls: { url: string; body: unknown }[] = [];

function mockHub(extra: Handler = () => undefined, base: { jobs?: object; detail?: object; status?: object; agents?: object } = {}) {
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
      if (url.startsWith("/agent/status")) return json(base.agents ?? AGENTS_OK);
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
    ...["/bench", "/timeline", "/board-config"].map((p) => createRoute({ getParentRoute: () => root, path: p, component: () => null })),
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

/* ---------------- restored from the v2 Flash tab ---------------- */

const tl = (...recs: object[]) => `${recs.map((r) => JSON.stringify(r)).join("\n")}\n`;
const JOB_ID = "flash-boot_a-2026-10-28T10-00-00";
const JOB_SUMMARY = { id: JOB_ID, op: "flash", label: "flash boot_a", started_at: "2026-10-28T10:00:00+00:00", ok: true, code: "", failed_line: 0, rows: { total: 1, ok: 1, failed: 0, skipped: 0, not_run: 0 } };
const JOB_DETAIL = { ...JOB_SUMMARY, job: { ok: true, duration_s: 3.1 }, timeline: "timeline.jsonl", timeline_path: `flash/${JOB_ID}/timeline.jsonl` };
const preview = (text: string, extra: object = {}) => ({ ok: true, path: "x", size_bytes: text.length, is_binary: false, truncated: false, text, encoding: "utf-8", ...extra });
const sixtyUart = Array.from({ length: 60 }, (_, i) => ({ t: 1 + i / 10, src: "uart", line: `boot msg ${String(i + 1).padStart(2, "0")}` }));

const oneCard = () => document.getElementById("m-one") as HTMLElement;
const cmdCard = () => document.getElementById("m-cmd") as HTMLElement;

async function writeBootA() {
  fireEvent.click(await screen.findByRole("button", { name: /One partition/ }));
  await waitFor(() => expect(within(oneCard()).getAllByRole("combobox")[1]).toHaveValue("flash-images/boot.img"));
  fireEvent.click(within(oneCard()).getByRole("button", { name: "Write" }));
  // two steps: the first click only asks
  expect(calls.some((c) => c.url === "/api/flash/flash")).toBe(false);
  const ask = within(oneCard()).getByRole("alert");
  expect(ask.textContent).toContain("boot.img");
  expect(ask.textContent).toContain("boot_a");
  fireEvent.click(within(ask).getByRole("button", { name: "Write" }));
}

describe("Flash page: what the v2 tab had", () => {
  it("shows every line of the recorded job and serial, not the last 40, and opens them large", async () => {
    mockHub(
      (url) => (url.startsWith("/workspace/files/preview/") ? preview(tl({ t: 0, src: "meta", ev: "start", label: "flash boot_a", op: "flash", partition: "boot_a", size: 5000000, sha256: "314f71f4aa" }, ...sixtyUart, { t: 9, src: "job", ev: "done", ok: true })) : undefined),
      { jobs: { ok: true, running: null, jobs: [JOB_SUMMARY] }, detail: JOB_DETAIL },
    );
    renderPage();
    const feed = await screen.findByLabelText("job and serial output");
    await waitFor(() => expect(feed.textContent).toContain("boot msg 01"));
    expect(feed.textContent).toContain("boot msg 60");
    // the header line carries the size and digest of what was written
    expect(feed.textContent).toContain("flash boot_a · 5000000 bytes · sha256 314f71f4aa");
    expect(screen.getByText("62 lines")).toBeInTheDocument();
    // where the record is
    expect(screen.getByText(`flash/${JOB_ID}/timeline.jsonl`)).toBeInTheDocument();
    expect(screen.getByText(/Record file \(in the workspace\)/)).toBeInTheDocument();
    // the preview reads up to the hub's 1 MiB cap, not 256 KiB
    expect(calls.find((c) => c.url.startsWith("/workspace/files/preview/"))?.url).toBe(`/workspace/files/preview/flash/${JOB_ID}/timeline.jsonl?max_bytes=1048576`);
    // Timeline page cannot open a flash job, so the link does not claim to
    expect(screen.getByRole("link", { name: /Open the Timeline page/ })).toBeInTheDocument();

    const card = feed.closest("article") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Open large" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("62 lines")).toBeInTheDocument();
    expect(dialog.textContent).toContain("boot msg 01");
    expect(dialog.textContent).toContain("+1.00 uart boot msg 01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("says when the record is longer than one read", async () => {
    mockHub(
      (url) => (url.startsWith("/workspace/files/preview/") ? preview(tl(...sixtyUart), { truncated: true, size_bytes: 3_000_000 }) : undefined),
      { jobs: { ok: true, running: null, jobs: [JOB_SUMMARY] }, detail: JOB_DETAIL },
    );
    renderPage();
    expect(await screen.findByText(/only its first 1\.0 MB is shown here/)).toBeInTheDocument();
  });

  it("a single write names partition, file and bytes from what was sent, and says nothing was read back", async () => {
    mockHub((url) =>
      url === "/api/flash/flash"
        ? ndjson([
            { ev: "progress", phase: "transfer", done: 2_500_000, total: 5_000_000, text: "" },
            { ev: "done", ok: true, rc: 0, code: "", error: "", stdout: "Writing 'boot_a' OKAY", stderr: "", duration_s: 3.14, artifacts: `/srv/ws/flash/${JOB_ID}`, data: {} },
          ])
        : undefined,
    );
    renderPage();
    await writeBootA();
    const result = await within(oneCard()).findByLabelText("Write result");
    expect(result.textContent).toContain("boot_a ← boot.img (5000000 bytes)");
    expect(result.textContent).toContain("3.1 s");
    expect(result.textContent).toMatch(/fastboot reported the write succeeded; the partition was not read back/);
    expect(within(result).getByRole("link", { name: "Board config" })).toHaveAttribute("href", "/board-config");
    expect(calls.find((c) => c.url === "/api/flash/flash")?.body).toEqual({ partition: "boot_a", image: "flash-images/boot.img" });
    // the record path from the verdict, as the v2 tab showed it
    expect(await screen.findByText(`/srv/ws/flash/${JOB_ID}/timeline.jsonl`)).toBeInTheDocument();
    // changing the pick afterwards does not relabel the finished write
    fireEvent.change(within(oneCard()).getAllByRole("combobox")[0] as HTMLSelectElement, { target: { value: "super" } });
    expect(within(oneCard()).getByLabelText("Write result").textContent).toContain("boot_a ← boot.img");
  });

  it("after its own write the card switches to the record on disk, serial lines included", async () => {
    let flashed = false;
    mockHub((url) => {
      if (url === "/api/flash/flash") {
        flashed = true;
        return ndjson([{ ev: "done", ok: true, stdout: "", stderr: "", duration_s: 3, artifacts: `/srv/ws/flash/${JOB_ID}` }]);
      }
      if (url.startsWith("/api/flash/jobs/")) return flashed ? JOB_DETAIL : json({}, 404);
      if (url.startsWith("/api/flash/jobs")) return { ok: true, running: null, jobs: flashed ? [JOB_SUMMARY] : [] };
      if (url.startsWith("/workspace/files/preview/")) return preview(tl({ t: 0.5, src: "job", ev: "progress", text: "Sending 'boot_a'" }, { t: 4.2, src: "uart", line: "U-Boot SPL starting" }));
      return undefined;
    });
    renderPage();
    await writeBootA();
    const feed = await screen.findByLabelText("job and serial output");
    await waitFor(() => expect(feed.textContent).toContain("U-Boot SPL starting"));
    expect(screen.getByText(`flash/${JOB_ID}/timeline.jsonl`)).toBeInTheDocument();
  });

  it("a recorded single write from disk also says it was not read back", async () => {
    mockHub(undefined, { jobs: { ok: true, running: null, jobs: [JOB_SUMMARY] }, detail: { ...JOB_DETAIL, timeline: null, timeline_path: null } });
    renderPage();
    const card = await screen.findByLabelText("Last job");
    await waitFor(() => expect(card.textContent).toMatch(/not read back/));
  });

  it("a failed write's output opens large with every line", async () => {
    const out = Array.from({ length: 30 }, (_, i) => `fastboot line ${i + 1}`).join("\n");
    mockHub((url) => (url === "/api/flash/flash" ? ndjson([{ ev: "done", ok: false, code: "FLASH_WRITE_FAILED", error: "FAILED (remote: nope)", stdout: out, stderr: "", duration_s: 1 }]) : undefined));
    renderPage();
    await writeBootA();
    await within(oneCard()).findByText("FLASH_WRITE_FAILED");
    fireEvent.click(within(oneCard()).getByRole("button", { name: "Open large" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("30 lines")).toBeInTheDocument();
    expect(dialog.textContent).toContain("fastboot line 1");
    expect(dialog.textContent).toContain("fastboot line 30");
  });

  it("fastboot command output opens large", async () => {
    mockHub();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /fastboot commands/ }));
    await waitFor(() => expect(within(cmdCard()).getByRole("button", { name: "Read" })).not.toBeDisabled());
    fireEvent.click(within(cmdCard()).getByRole("button", { name: "Read" }));
    await within(cmdCard()).findByText(/product:board_x/);
    fireEvent.click(within(cmdCard()).getByRole("button", { name: "Open large" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("$ fastboot getvar all");
    expect(dialog.textContent).toContain("(bootloader) product:board_x");
  });

  it("unavailable, agent connected without fastboot: says to set fastboot_path in agent.conf and restart", async () => {
    mockHub(undefined, { status: { available: false }, agents: { ...AGENTS_OK, agents: [{ ...AGENTS_OK.agents[0], caps: ["adb"] }] } });
    renderPage();
    const banner = await screen.findByText(/agent bench-01 is connected but does not advertise fastboot/);
    expect(banner.textContent).toContain("fastboot_path");
    expect(banner.textContent).toContain("agent.conf");
    expect(banner.textContent).toContain("restart the agent");
    // the same reason is the tooltip on the disabled buttons
    expect(screen.getByRole("button", { name: "Reboot to Android" })).toHaveAttribute("title", banner.textContent ?? "");
  });

  it("unavailable, no agent connected: says to start the agent", async () => {
    mockHub(undefined, { status: { available: false }, agents: { ...AGENTS_OK, agents: [] } });
    renderPage();
    expect(await screen.findByText(/no bench agent is connected to the hub/)).toBeInTheDocument();
  });

  it("unavailable and the agent status cannot be read: still names the fastboot_path fix", async () => {
    mockHub((url) => (url.startsWith("/agent/status") ? json({ detail: "down" }, 503) : undefined), { status: { available: false } });
    renderPage();
    const banner = await screen.findByText(/Flashing is unavailable/);
    await waitFor(() => expect(banner.textContent).toContain("set fastboot_path in its agent.conf"));
  });

  it("links to the board config page, where the config partition is read back", async () => {
    mockHub();
    renderPage();
    expect(await screen.findByRole("link", { name: "Board config" })).toHaveAttribute("href", "/board-config");
  });
});
