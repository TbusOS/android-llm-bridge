import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ search: {} as Record<string, string> }));
vi.mock("@tanstack/react-router", () => ({
  useSearch: () => router.search,
  useNavigate: () => vi.fn(),
  Link: ({ children, to, className }: { children: ReactNode; to: string; className?: string }) => (
    <a href={to} className={className}>{children}</a>
  ),
}));

import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";
import { TimelinePage } from "./TimelinePage";

const DEVICE = "0123456789ABCDEF";
const CAP = { name: "capture-2026-10-28T09-30-58", started_at: "2026-10-28T09:30:58", duration_s: 90, sources: { logcat: 3, dmesg: 2, uart: 1 }, size_bytes: 1000, has_boot: true };
const zeros = (n: number) => Array.from({ length: n }, () => 0);
const DENSITY = {
  t0: 0,
  t1: 90,
  bucket_s: 0.15,
  tracks: { uart: zeros(601), dmesg: zeros(601), logcat: { V: zeros(601), D: zeros(601), I: zeros(601), W: zeros(601), E: zeros(601), F: zeros(601) } },
  markers: [
    { t: 63.2, src: "dmesg", kind: "panic", text: "Kernel panic - not syncing" },
    { t: 10, src: "dmesg", kind: "avc", text: "avc: denied { read }" },
  ],
};
const LINES = [
  { t: 62.9, ts: null, src: "logcat", text: "open failed: Permission denied", level: "E", tag: "SensorsHal", pid: 12 },
  { t: 63.2, ts: null, src: "dmesg", text: "Kernel panic - not syncing: Oops", level: null, tag: null, pid: null },
  { t: 63.5, ts: null, src: "uart", text: "Rebooting in 3 seconds..", level: null, tag: null, pid: null },
];
const BOOT = { found: true, total_s: 20, milestones: [{ key: "kernel", label: "kernel start", src: "dmesg", t: 2 }], stages: [{ key: "kernel", src: "dmesg", t0: 0, t1: 2 }], previous: null };

type Routes = Record<string, (url: string, init?: RequestInit) => Response | Promise<Response>>;
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

function mockFetch(routes: Routes) {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    for (const [k, h] of Object.entries(routes)) if (url.includes(k)) return h(url, init);
    return json({}, 404);
  });
  vi.stubGlobal("fetch", f);
  return f;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TimelinePage /></QueryClientProvider>);
}

const full: Routes = {
  "/api/timeline/captures?": () => json({ ok: true, data: [CAP] }),
  "/density": () => json({ ok: true, data: DENSITY }),
  "/lines": () => json({ ok: true, data: { lines: LINES, truncated: false } }),
  "/boot": () => json({ ok: true, data: BOOT }),
  "/api/flash/jobs": () => json({ ok: true, running: null, jobs: [] }),
};

beforeEach(() => {
  router.search = {};
  useApp.setState({ device: DEVICE, lang: "en" });
  useShell.setState({ sheetOpen: false, context: null });
});
afterEach(() => vi.unstubAllGlobals());

describe("TimelinePage", () => {
  it("asks for a device when none is selected", () => {
    useApp.setState({ device: null });
    mockFetch({});
    renderPage();
    expect(screen.getByText("No device selected")).toBeInTheDocument();
  });

  it("shows the empty state when the device has no recordings", async () => {
    mockFetch({ "/api/timeline/captures?": () => json({ ok: true, data: [] }) });
    renderPage();
    expect(await screen.findByText("No recordings for this device yet")).toBeInTheDocument();
  });

  it("shows an error banner when the list cannot be read", async () => {
    mockFetch({ "/api/timeline/captures?": () => json({ detail: "boom" }, 500) });
    renderPage();
    expect(await screen.findByText(/Could not read the recordings/)).toBeInTheDocument();
  });

  it("draws the lanes, lists lines, and a marker click selects its line", async () => {
    mockFetch(full);
    renderPage();
    expect(await screen.findByText("Kernel panic - not syncing: Oops")).toBeInTheDocument();
    expect(screen.getByText("Boot breakdown")).toBeInTheDocument();
    expect(screen.getByTestId("overview")).toBeInTheDocument();
    // default selection is the first problem line (the E line)
    expect(screen.getAllByText("open failed: Permission denied").length).toBeGreaterThan(0);
    const panic = await screen.findAllByRole("button", { name: /^panic/ });
    fireEvent.click(panic[0] as HTMLElement);
    await waitFor(() => {
      const sel = document.querySelector('.log[aria-selected="true"]');
      expect(sel?.textContent).toContain("Kernel panic");
    });
    expect(screen.getByText(/into the recording/)).toHaveTextContent("+63.20 s");
  });

  it("opens the assistant with the lines around the selection", async () => {
    mockFetch(full);
    renderPage();
    await screen.findByText("Kernel panic - not syncing: Oops");
    fireEvent.click(screen.getByRole("button", { name: /Ask about these 2 s/ }));
    const ctx = useShell.getState().context;
    expect(useShell.getState().sheetOpen).toBe(true);
    expect(ctx?.detail).toContain("Kernel panic");
  });

  it("asks before rebooting and then records a boot", async () => {
    router.search = { record: "boot" };
    const f = mockFetch({
      ...full,
      "/api/timeline/capture?": () =>
        new Response('{"ev":"progress","elapsed_s":1,"lines":{"uart":2}}\n{"ev":"done","ok":true,"name":"capture-2026-10-28T10-00-00"}\n', {
          headers: { "content-type": "application/x-ndjson" },
        }),
    });
    renderPage();
    const go = await screen.findByRole("button", { name: "Reboot and record" });
    // nothing was sent before the confirmation
    expect(f.mock.calls.some(([u, i]) => String(u).includes("/api/timeline/capture?") && (i as RequestInit | undefined)?.method === "POST")).toBe(false);
    fireEvent.click(go);
    await waitFor(() => {
      const call = f.mock.calls.find(([u]) => String(u).includes("/api/timeline/capture?"));
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call?.[1] as RequestInit).body));
      expect(body).toMatchObject({ duration_s: 60, reboot: "system" });
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reboot and record" })).not.toBeInTheDocument());
  });

  it("says so when a source recorded nothing", async () => {
    mockFetch({ ...full, "/api/timeline/captures?": () => json({ ok: true, data: [{ ...CAP, sources: { logcat: 3, dmesg: 2, uart: 0 }, has_boot: false }] }) });
    renderPage();
    expect(await screen.findByText(/no serial lines/)).toBeInTheDocument();
  });
});
