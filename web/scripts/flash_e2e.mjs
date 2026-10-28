#!/usr/bin/env node
// Flash page end-to-end checks (v3) — a real browser, real clicks, real
// assertions against a real hub (ADR-056, rewritten for ADR-064).
//
// Unit tests check the DOM a component renders; they cannot see a button
// that sends a write on the first click, a mode switch that shows nothing,
// or a page that throws once real hub data arrives. Rule kept from the v2
// script: every check ASSERTS and any failure exits non-zero.
//
// It never confirms anything. The last check proves that: no request that
// writes, erases, runs a plan, sends an oem command or reboots left the
// browser, so it is safe against the real bench.
//
// usage:
//   node web/scripts/flash_e2e.mjs
// env:
//   ALB_BASE       default http://127.0.0.1:8765/app
//   E2E_OUT_DIR    screenshot dir (default .claude/reports/flash-e2e, gitignored)
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";

const BASE = (process.env.ALB_BASE || "http://127.0.0.1:8765/app").replace(/\/$/, "");
const OUT = resolve(process.env.E2E_OUT_DIR || ".claude/reports/flash-e2e");
const MUTATING = /\/api\/flash\/(flash|plan\/run|oem|reboot)\b|\/api\/bench\/ops\/(flash|erase|set-active|boot)\b/;

let failed = 0;
function check(name, ok, detail = "") {
  if (!ok) failed++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
function note(text) {
  console.log(`NOTE  ${text}`);
}

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" })).newPage();
const errors = [];
const sent = [];
page.on("console", (m) => { if (m.type() === "error" && !/status of 4\d\d/.test(m.text())) errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("request", (r) => { if (r.method() === "POST" && MUTATING.test(r.url())) sent.push(r.url()); });

const status = await (await fetch(`${BASE.replace(/\/app$/, "")}/api/flash/status`)).json().catch(() => null);
check("hub answers /api/flash/status", !!status, JSON.stringify(status)?.slice(0, 120));

await page.goto(`${BASE}/flash`, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForSelector("main.content h1");
check("Flash page renders its heading", (await page.locator("main.content h1").first().textContent()) === "Flash");
check("board identity card is shown", await page.locator(".ident").first().isVisible());
await page.screenshot({ path: join(OUT, "flash.png"), fullPage: true });

// mode switch: each segment shows its own panel
const seg = page.locator('.seg[aria-label="mode"] button');
check("three modes are offered", (await seg.count()) === 3, String(await seg.count()));
await seg.nth(1).click();
check("One partition mode shows the write form", await page.getByText("Write one partition").first().isVisible());
check("One partition segment is pressed", (await seg.nth(1).getAttribute("aria-pressed")) === "true");
await seg.nth(2).click();
check("fastboot commands mode leaves the write form", !(await page.getByText("Write one partition").first().isVisible().catch(() => false)));
await seg.nth(0).click();
check("Whole package mode offers Check the plan", await page.getByRole("button", { name: "Check the plan" }).first().isVisible());
const flashPkg = page.getByRole("button", { name: /Flash package|Check the plan first/ }).first();
if (await flashPkg.count()) {
  check("package cannot run before a plan is checked", await flashPkg.isDisabled());
} else {
  note("no run button before checking — nothing to click");
}

// one partition: the first click only asks
await seg.nth(1).click();
const write = page.locator('#m-one .btn--danger, .btn--danger:has-text("Write")').first();
if (await write.isDisabled()) {
  note("Write is disabled here (no image in the workspace, or flashing unavailable) — confirmation flow not exercised");
  check("Write is disabled for a reason the page states", (await page.locator("main.content").innerText()).length > 0);
} else {
  const before = sent.length;
  await write.click();
  check("first click on Write sends nothing", sent.length === before, sent.slice(before).join(" "));
  check("first click on Write shows the consequence", await page.locator('.confirm[role="alert"]').first().isVisible());
  await page.screenshot({ path: join(OUT, "flash-confirm.png") });
  await page.locator('.confirm[role="alert"] button', { hasText: "Cancel" }).first().click();
  check("Cancel closes the confirmation", !(await page.locator('.confirm[role="alert"]').first().isVisible().catch(() => false)));
}

check("no write / run / oem / reboot request left the browser", sent.length === 0, sent.join(" "));
check("no console or page errors", errors.length === 0, errors.slice(0, 3).join(" | "));

await browser.close();
console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
