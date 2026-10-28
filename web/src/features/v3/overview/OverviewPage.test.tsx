import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApp } from "../../../stores/app";
import { renderPage, stubFetch } from "../../../test/renderPage";
import { OverviewPage } from "./OverviewPage";

const EMPTY = {
  "/devices": { ok: true, transport: "AdbTransport", devices: [] },
  "/agent/status": { v: "1", agents: [], forwarders: { adb: { bound: false, port: 0 }, serial: { bound: false, port: 0, configured: false, com: null, baud: 0 } } },
  "/api/bench/board": { ok: true, table: null, boards: [], active: null, source: "env", problem: null, serial: { com: null, baud: 0, bound: false, readers: 0 } },
  "/api/flash/status": { ok: true, available: false, busy: false, job: null, partitions: [], partitions_problem: "", board: null },
  "/audit": { ok: true, since: "", until: "", events: [] },
  "/api/timeline/captures": { ok: true, data: [] },
};

describe("OverviewPage", () => {
  beforeEach(() => {
    useApp.setState({ lang: "en", device: null });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("explains what to do when adb lists no device", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("No device yet")).toBeTruthy();
    expect(screen.getByText("Check the bench")).toBeTruthy();
    expect(screen.getByText("Board state")).toBeTruthy();
    expect(screen.getByText("Connections")).toBeTruthy();
  });

  it("shows every channel as missing and offers a first boot recording", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("no agent connected")).toBeTruthy();
    expect(await screen.findByText("No boot recorded yet")).toBeTruthy();
    expect(await screen.findByText("Nothing in the last 4 hours")).toBeTruthy();
  });

  it("speaks Chinese when the app language is Chinese", async () => {
    useApp.setState({ lang: "zh", device: null });
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    expect(await screen.findByText("还没有设备")).toBeTruthy();
    expect(screen.getByText("板子状态")).toBeTruthy();
  });

  it("asks before rebooting into the bootloader", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(EMPTY)));
    await renderPage(<OverviewPage />);
    (await screen.findByText("Stop in bootloader")).click();
    expect((await screen.findByRole("alert")).textContent).toContain("bootloader countdown");
  });
});
