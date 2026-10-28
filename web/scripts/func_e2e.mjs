#!/usr/bin/env node
// Functional end-to-end checks: does each page's main job actually work
// against a real hub and, where one is attached, a real board?
//
// v3_e2e.mjs proves the pages render; this one presses the buttons and
// checks what comes back. Every check ends PASS, FAIL or SKIP. A SKIP
// always names the missing precondition (no adb device, no model backend…)
// and never counts as a pass, so "all green" on a serial-only board still
// says which adb features went untested. Exit code is non-zero on any FAIL.
//
// Safe on a real bench by construction: every request that would change a
// board or the bench (flash / erase / reboot, root / remount, break-in, app
// install / uninstall / clear, file push, agent update / start, switching
// the active board, any DELETE) is aborted in the browser before it leaves
// and is itself a FAIL. What it does send to a board: `echo alb-func-<n>`
// lines on the serial console (FUNC_SERIAL=0 turns them off), an `echo` in
// an adb shell when a device is online, and a 30 s timeline recording.
//
// usage:
//   node web/scripts/func_e2e.mjs
// env:
//   ALB_BASE       default http://127.0.0.1:8765/app
//   ALB_LAN_BASE   the address other machines open (plain http, e.g.
//                  http://<hub-host>:8765/app). Copy buttons are checked
//                  there, where the page is not a secure context.
//   FUNC_SERIAL    1 (default) send echo lines on the serial console; 0 skips
//   FUNC_CHAT      1 (default) send one chat message to the model backend
//   ALB_CDP_URL    drive a browser that is already running instead of
//                  launching one, e.g. Edge on the bench Windows machine
//                  started with --remote-debugging-port and reached through
//                  `ssh -L` (see web/scripts/win_browser.sh). ALB_BASE must
//                  then be an address that machine can open, not 127.0.0.1.
//   E2E_OUT_DIR    screenshots + report.json (default .claude/reports/func-e2e)
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const BASE = (process.env.ALB_BASE || "http://127.0.0.1:8765/app").replace(/\/$/, "");
const LAN_BASE = (process.env.ALB_LAN_BASE || "").replace(/\/$/, "");
const ORIGIN = new URL(BASE).origin;
const OUT = resolve(process.env.E2E_OUT_DIR || ".claude/reports/func-e2e");
const SERIAL_OK = process.env.FUNC_SERIAL !== "0";
const CHAT_OK = process.env.FUNC_CHAT !== "0";
const MARK = `alb-func-${Date.now().toString(36)}`;

/* ---------------- result bookkeeping ---------------- */

const results = [];
function record(status, name, detail = "") {
  results.push({ status, name, detail });
  console.log(`${status.padEnd(4)}  ${name}${detail ? `  — ${detail}` : ""}`);
}
const pass = (n, d) => record("PASS", n, d);
const fail = (n, d) => record("FAIL", n, d);
const skip = (n, why) => record("SKIP", n, why);
function check(name, ok, detail = "") {
  (ok ? pass : fail)(name, detail);
  return ok;
}
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    fail(name, String(e?.message ?? e).split("\n")[0]);
  }
}

/* ---------------- the guard ---------------- */

// Read-only bench ops are the only ones allowed through /api/bench/ops/.
const BLOCK = [
  /\/api\/flash\/(flash|plan\/run|oem|reboot|erase|set-active|boot)\b/,
  /\/api\/bench\/ops\/(?!usb-list\b|log-tail\b)[\w-]+/,
  /\/api\/bench\/(agent-update|agent-start)\b/,
  /\/api\/serial\/break-in\b/,
  /\/devices\/[^/]+\/files\/push/,
  /\/workspace\/files\/upload/,
];
const BLOCK_NON_GET = [/\/api\/adb\//, /\/api\/power\//, /\/api\/app\//, /\/api\/bench\/board\b/];
const blocked = [];
function mustBlock(req) {
  const url = req.url();
  const m = req.method();
  if (m === "DELETE") return true;
  if (BLOCK.some((re) => re.test(url))) return true;
  return m !== "GET" && BLOCK_NON_GET.some((re) => re.test(url));
}
async function guard(ctx) {
  await ctx.route("**/*", (route) => {
    const req = route.request();
    if (mustBlock(req)) {
      blocked.push(`${req.method()} ${req.url()}`);
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
}

/* ---------------- hub probes ---------------- */

async function api(path) {
  const r = await fetch(ORIGIN + path, { signal: AbortSignal.timeout(20000) }).catch((e) => e);
  if (r instanceof Error) return { ok: false, status: 0, data: null, error: r.message };
  const data = await r.json().catch(() => null);
  return { ok: r.ok, status: r.status, data };
}

const devices = await api("/devices");
const online = (devices.data?.devices ?? []).filter((d) => !d.state || d.state === "device");
const adbSerial = online[0]?.serial ?? null;
const agentStatus = await api("/agent/status");
const agentUp = (agentStatus.data?.agents ?? []).length > 0;
const board = await api("/api/bench/board");
const serialBound = !!board.data?.serial?.bound;
const backends = await api("/playground/backends");
const backendList = Array.isArray(backends.data) ? backends.data : (backends.data?.backends ?? []);

console.log(`hub ${ORIGIN} · adb ${adbSerial ? "online" : "none"} · agent ${agentUp ? "up" : "down"} · serial ${serialBound ? "bound" : "unbound"} · marker ${MARK}`);
const NO_ADB = "no adb device online";

/* ---------------- browser ---------------- */

await mkdir(OUT, { recursive: true });
const CDP = process.env.ALB_CDP_URL || "";
const browser = CDP ? await chromium.connectOverCDP(CDP) : await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US", acceptDownloads: true });
const probe = await ctx.newPage();
const agentString = await probe.evaluate(() => navigator.userAgent);
await probe.close();
console.log(`browser ${browser.version()} · ${CDP ? `remote (${CDP})` : "launched here"} · ${agentString}`);
await guard(ctx);
// English labels keep the selectors below stable; typing on the serial
// console is on so the echo checks can type.
await ctx.addInitScript(() => {
  try {
    localStorage.setItem("alb.console.serialTyping", "1");
    const raw = localStorage.getItem("alb.app");
    const v = raw ? JSON.parse(raw) : { state: {}, version: 0 };
    if (v.state?.lang !== "en") {
      v.state = { ...(v.state ?? {}), lang: "en" };
      localStorage.setItem("alb.app", JSON.stringify(v));
    }
  } catch {}
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

async function open(p, path) {
  await p.goto(BASE + path, { waitUntil: "domcontentloaded", timeout: 30000 });
  await p.waitForSelector("main.content h1", { timeout: 20000 });
}
const shot = (p, name) => p.screenshot({ path: join(OUT, `${name}.png`) }).catch(() => {});
const termText = (p, scope) => p.locator(scope).evaluate((el) => el.textContent ?? "");

/* wait for the visible console tab's status to read "connected" */
async function connected(p) {
  await p.waitForFunction(
    () => /^connected/.test(document.querySelector('[role="tabpanel"]:not([hidden]) .console__bar')?.textContent ?? ""),
    null,
    { timeout: 20000 },
  );
}

/* serial console: type a command, return what the terminal showed */
async function serialEcho(p, text) {
  await open(p, "/console");
  await p.getByRole("tab", { name: /serial/i }).first().click();
  const panel = p.locator('[role="tabpanel"]:not([hidden]) .term--x');
  await connected(p);
  await panel.click();
  await p.keyboard.type(`echo ${text}\r`, { delay: 15 });
  await p.waitForTimeout(2500);
  return termText(p, '[role="tabpanel"]:not([hidden]) .xterm-rows');
}

/* ================= checks ================= */

// 1. shell: top bar knows the board and the channels
await step("top bar shows the active board and channel states", async () => {
  await open(page, "/dashboard");
  const bar = await page.locator(".topbar").first().textContent();
  const active = board.data?.active;
  check("top bar shows the active board and channel states", !!bar && (!active || bar.includes(active)) && /adb/i.test(bar) && /serial/i.test(bar), active ? "board named" : "no board table");
});

// 2. console
if (!SERIAL_OK) skip("serial console echoes a command", "FUNC_SERIAL=0");
else if (!serialBound) skip("serial console echoes a command", "bench serial port not bound");
else
  await step("serial console echoes a command", async () => {
    const out = await serialEcho(page, `${MARK}-c`);
    const n = out.split(`${MARK}-c`).length - 1;
    check("serial console echoes a command", n >= 2, `marker seen ${n}× (typed + printed)`);
    await shot(page, "console-serial");
  });
if (!adbSerial) skip("adb shell runs a command", NO_ADB);
else
  await step("adb shell runs a command", async () => {
    await open(page, "/console");
    await page.getByRole("tab", { name: /adb/i }).first().click();
    await connected(page);
    await page.locator('[role="tabpanel"]:not([hidden]) .term--x').click();
    await page.keyboard.type(`echo ${MARK}-a\r`, { delay: 15 });
    await page.waitForTimeout(2500);
    const out = await termText(page, '[role="tabpanel"]:not([hidden]) .xterm-rows');
    check("adb shell runs a command", out.split(`${MARK}-a`).length - 1 >= 2);
  });

// 3. logs: live serial shows what the console sends
if (!SERIAL_OK || !serialBound) skip("logs page shows live serial output", SERIAL_OK ? "bench serial port not bound" : "FUNC_SERIAL=0");
else
  await step("logs page shows live serial output", async () => {
    await open(page, "/logs?tab=serial");
    await page.getByRole("tab", { name: "Serial" }).click();
    const other = await ctx.newPage();
    await serialEcho(other, `${MARK}-l`);
    await other.close();
    await page.waitForTimeout(1500);
    const txt = await page.locator("#p-uart").textContent();
    check("logs page shows live serial output", (txt ?? "").includes(`${MARK}-l`), "marker sent from a second tab");
    await shot(page, "logs-serial");
  });
if (!adbSerial) {
  skip("logs page streams logcat", NO_ADB);
  skip("logs page reads dmesg", NO_ADB);
} else {
  await step("logs page streams logcat", async () => {
    await open(page, "/logs?tab=logcat");
    await page.waitForFunction(() => document.querySelectorAll("#p-logcat .log, #p-logcat [role=row]").length > 0, null, { timeout: 20000 });
    pass("logs page streams logcat");
  });
  await step("logs page reads dmesg", async () => {
    await open(page, "/logs?tab=dmesg");
    const r = page.waitForResponse((x) => /dmesg/.test(x.url()), { timeout: 30000 });
    await page.getByRole("button", { name: /read|refresh/i }).first().click().catch(() => {});
    check("logs page reads dmesg", (await r).ok());
  });
}

// 4. timeline: record 30 s while the console sends a marker, then find it
if (!SERIAL_OK || (!serialBound && !adbSerial)) skip("timeline records and finds the marker", SERIAL_OK ? "neither adb nor bench serial" : "FUNC_SERIAL=0");
else
  await step("timeline records and finds the marker", async () => {
    await open(page, "/timeline");
    const rec = page.getByRole("button", { name: /^Record$/ });
    if (await rec.isDisabled()) return fail("timeline records and finds the marker", "Record is disabled");
    await rec.click();
    const done = page.waitForResponse((x) => x.url().includes("/api/timeline/capture?") || x.url().endsWith("/api/timeline/capture"), { timeout: 15000 });
    await page.getByRole("menuitem", { name: /30 seconds/ }).click();
    await done;
    await page.waitForTimeout(3000);
    const other = await ctx.newPage();
    if (serialBound) await serialEcho(other, `${MARK}-t`);
    await other.close();
    await page.getByText("Recording finished.").waitFor({ timeout: 60000 });
    await page.waitForTimeout(1500);
    const search = page.getByRole("textbox", { name: "search" });
    await search.fill(`${MARK}-t`);
    await page.waitForTimeout(2000);
    const body = await page.locator("main.content").textContent();
    check("timeline records and finds the marker", !serialBound || (body ?? "").includes(`${MARK}-t`), serialBound ? "serial line found in the recording" : "recorded (no serial to mark)");
    await shot(page, "timeline");
  });

// 5. log search over the recordings
if (!SERIAL_OK || !serialBound) skip("log search finds the marker in saved recordings", "no serial marker was recorded");
else
  await step("log search finds the marker in saved recordings", async () => {
    await open(page, "/logs?tab=search");
    await page.getByRole("tab", { name: "Search all recordings" }).click();
    const box = page.getByRole("textbox", { name: "search" });
    await box.fill(`${MARK}-t`);
    const r = page.waitForResponse((x) => /search/.test(x.url()), { timeout: 30000 });
    await box.press("Enter");
    await r;
    await page.waitForTimeout(1000);
    const txt = await page.locator("main.content").textContent();
    check("log search finds the marker in saved recordings", (txt ?? "").includes(`${MARK}-t`));
  });

// 6. adb-only device pages
const adbPages = [
  ["performance shows live metrics", "/perf"],
  ["apps lists packages", "/apps"],
  ["screen takes a screenshot", "/screen"],
  ["system info reads properties", "/info"],
  ["power reads the battery", "/power"],
  ["problems reads pstore", "/problems"],
];
if (!adbSerial) for (const [n] of adbPages) skip(n, NO_ADB);
else {
  await step("screen takes a screenshot", async () => {
    await open(page, "/screen");
    const r = page.waitForResponse((x) => /\/screenshot\b/.test(x.url()) && x.request().method() === "POST", { timeout: 30000 });
    await page.getByRole("button", { name: /capture|take|screenshot/i }).first().click();
    const resp = await r;
    await page.waitForSelector("main.content img", { timeout: 20000 });
    check("screen takes a screenshot", resp.ok());
  });
  await step("system info reads properties", async () => {
    const r = page.waitForResponse((x) => /\/system\b/.test(x.url()), { timeout: 30000 });
    await open(page, "/info");
    check("system info reads properties", (await r).ok());
  });
  await step("apps lists packages", async () => {
    const r = page.waitForResponse((x) => /\/api\/app\/list/.test(x.url()), { timeout: 30000 });
    await open(page, "/apps");
    check("apps lists packages", (await r).ok());
  });
  await step("power reads the battery", async () => {
    const r = page.waitForResponse((x) => /\/api\/power\/battery/.test(x.url()), { timeout: 30000 });
    await open(page, "/power");
    check("power reads the battery", (await r).ok());
  });
  await step("performance shows live metrics", async () => {
    await open(page, "/perf");
    await page.waitForTimeout(6000);
    const n = await page.locator("main.content svg path").count();
    check("performance shows live metrics", n > 0, `${n} chart paths`);
  });
  await step("problems reads pstore", async () => {
    await open(page, "/problems");
    const r = page.waitForResponse((x) => /\/api\/diag\/pstore/.test(x.url()), { timeout: 60000 });
    await page.getByRole("button", { name: "Read and save" }).click();
    check("problems reads pstore", (await r).status() < 500);
  });
}

// 7. files: the workspace lists and previews
await step("files lists the workspace", async () => {
  const r = page.waitForResponse((x) => /\/workspace\/files/.test(x.url()), { timeout: 20000 });
  await open(page, "/files");
  await page.getByRole("tab", { name: /workspace/i }).first().click().catch(() => {});
  const resp = await r;
  const body = await resp.json().catch(() => null);
  const n = (body?.entries ?? body?.data?.entries ?? body?.files ?? []).length;
  check("files lists the workspace", resp.ok() && n > 0, `${n} entries`);
});

// 8. flash: status and job history load (writes are covered, without
//    confirming, by flash_e2e.mjs)
await step("flash page reads its status and job history", async () => {
  const st = page.waitForResponse((x) => /\/api\/flash\/status/.test(x.url()), { timeout: 20000 });
  const jobs = page.waitForResponse((x) => /\/api\/flash\/jobs/.test(x.url()), { timeout: 20000 });
  await open(page, "/flash");
  const [a, b] = await Promise.all([st, jobs]);
  check("flash page reads its status and job history", a.ok() && b.ok());
});

// 9. bench
if (!agentUp) {
  for (const n of ["bench lists USB devices", "bench log opens large with the same lines", "bench log saves to a file"]) skip(n, "bench agent not connected");
} else {
  await step("bench lists USB devices", async () => {
    const r = page.waitForResponse((x) => x.url().endsWith("/api/bench/ops/usb-list"), { timeout: 40000 });
    await open(page, "/bench");
    const resp = await r;
    const text = await resp.text();
    const n = (text.match(/"vid"/g) ?? []).length;
    check("bench lists USB devices", resp.ok() && n > 0, `${n} devices`);
  });
  await step("bench log opens large with the same lines", async () => {
    await open(page, "/bench");
    const pre = page.locator('pre[aria-label="bench log"]');
    await page.waitForFunction(() => document.querySelectorAll('pre[aria-label="bench log"] > div').length > 0, null, { timeout: 30000 });
    const inCard = await pre.locator(":scope > div").count();
    await page.getByRole("button", { name: "Open large" }).first().click();
    const dlg = page.getByRole("dialog", { name: "Bench logs" });
    await dlg.waitFor();
    const inDialog = await dlg.locator(".term--lines > div").count();
    const box = await dlg.boundingBox();
    check("bench log opens large with the same lines", inDialog === inCard && (box?.height ?? 0) > 600, `${inDialog}/${inCard} lines, dialog ${Math.round(box?.height ?? 0)} px tall`);
    await shot(page, "bench-log-dialog");
    const download = page.waitForEvent("download", { timeout: 10000 });
    await dlg.getByRole("button", { name: "Save as file" }).click();
    const d = await download;
    if (CDP) {
      // A remote browser saves on its own machine and Playwright cannot
      // stream the file back ("canceled"); that the download started with
      // the right name is what is checked here. 2026-10-28 the saved file
      // was read on the bench over ssh: 200 lines, same as the dialog.
      check("bench log saves to a file", d.suggestedFilename().endsWith(".log"), `${d.suggestedFilename()} (remote browser: content not read back)`);
    } else {
      const chunks = [];
      for await (const c of await d.createReadStream()) chunks.push(c);
      const saved = Buffer.concat(chunks).toString("utf8");
      check("bench log saves to a file", d.suggestedFilename().endsWith(".log") && saved.trimEnd().split("\n").length === inDialog, `${d.suggestedFilename()}`);
    }
    await page.keyboard.press("Escape");
    check("bench log dialog closes on Esc", (await page.getByRole("dialog").count()) === 0);
  });
}

// 10. copy works where people actually open the hub: plain http on the LAN
if (!LAN_BASE) skip("copy works over plain http", "set ALB_LAN_BASE to the address other machines open");
else if (!agentUp) skip("copy works over plain http", "bench agent not connected (uses the bench log)");
else
  await step("copy works over plain http", async () => {
    const p = await ctx.newPage();
    await p.goto(`${LAN_BASE}/bench`, { waitUntil: "domcontentloaded" });
    await p.waitForSelector("main.content h1");
    const secure = await p.evaluate(() => window.isSecureContext);
    await p.waitForFunction(() => document.querySelectorAll('pre[aria-label="bench log"] > div').length > 0, null, { timeout: 30000 });
    await p.getByRole("button", { name: "Open large" }).first().click();
    await p.getByRole("dialog").getByRole("button", { name: "Copy all" }).click();
    const snack = await p.locator(".snackbar").first().textContent({ timeout: 5000 }).catch(() => "");
    check("copy works over plain http", !secure && /Copied \d+ lines/.test(snack ?? ""), `secure context ${secure}; "${snack}"`);
    await p.close();
  });

// 11. assistant: one plain chat message (agent mode off, so no tools run)
const usable = backendList.filter((b) => b.status !== "planned");
if (!CHAT_OK) skip("chat gets a reply from the model", "FUNC_CHAT=0");
else if (usable.length === 0) skip("chat gets a reply from the model", "no model backend configured");
else
  await step("chat gets a reply from the model", async () => {
    await open(page, "/chat");
    const agent = page.getByRole("checkbox", { name: "Agent mode" });
    if (await agent.isChecked()) await agent.click();
    const box = page.locator("#assist-q").first();
    await box.fill("Reply with just the word: pong");
    await box.press("Enter");
    await page.waitForFunction(
      () => /pong/i.test(document.querySelector("main.content")?.textContent?.split("Reply with just the word")[1] ?? ""),
      null,
      { timeout: 120000 },
    );
    pass("chat gets a reply from the model");
    await shot(page, "chat");
    // the side sheet shares the conversation (navigate inside the app: a
    // reload would start a new one by design)
    await page.locator('.nav__item[href$="/dashboard"]').first().click();
    await page.waitForURL(/\/dashboard$/);
    await page.getByRole("button", { name: "Assistant" }).first().click();
    const sheet = await page.locator("aside.sheet").textContent({ timeout: 5000 });
    check("assistant sheet shows the same conversation", (sheet ?? "").includes("Reply with just the word"));
  });

// 12. system pages read their data
const reads = [
  ["health check runs", "/doctor", /\/api\/doctor/],
  ["connections reads the agent state", "/connections", /\/agent\/status/],
  ["sessions lists past sessions", "/sessions", /^\/sessions\b/],
  ["playground lists model backends", "/playground", /\/playground\/backends/],
];
for (const [name, path, re] of reads)
  await step(name, async () => {
    // match the API path, not the page's own /app/... document
    const r = page.waitForResponse((x) => re.test(new URL(x.url()).pathname) && x.request().method() === "GET", { timeout: 20000 });
    await open(page, path);
    const resp = await r;
    check(name, resp.ok(), `${resp.status()} ${new URL(resp.url()).pathname}`);
  });

// the audit page reads a live stream, not a GET
await step("audit log streams operations", async () => {
  const frame = new Promise((res, rej) => {
    const timer = setTimeout(() => rej(new Error("no frame on /audit/stream within 20 s")), 20000);
    page.on("websocket", (ws) => {
      if (!/\/audit\/stream/.test(ws.url())) return;
      ws.on("framereceived", (f) => {
        clearTimeout(timer);
        res(String(f.payload).slice(0, 80));
      });
    });
  });
  await open(page, "/audit");
  const first = await frame;
  check("audit log streams operations", !!first, first);
});

/* ---------------- verdict ---------------- */

check("no request that changes a board or the bench was sent", blocked.length === 0, blocked.slice(0, 3).join(" | "));
check("no uncaught page errors", errors.length === 0, errors.slice(0, 2).join(" | "));
// a remote browser belongs to whoever started it: close our context only
if (CDP) await ctx.close();
else await browser.close();

const count = (s) => results.filter((r) => r.status === s).length;
await writeFile(join(OUT, "report.json"), JSON.stringify({ base: BASE, browser: agentString, marker: MARK, adb: !!adbSerial, agent: agentUp, serial: serialBound, results }, null, 2));
console.log(`\n${count("PASS")} passed · ${count("FAIL")} failed · ${count("SKIP")} skipped  (report: ${join(OUT, "report.json")})`);
for (const r of results.filter((x) => x.status === "SKIP")) console.log(`  skipped: ${r.name} — ${r.detail}`);
process.exit(count("FAIL") ? 1 : 0);
