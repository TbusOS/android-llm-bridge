#!/usr/bin/env node
// Flash tab end-to-end checks — a real browser, real clicks, real
// assertions (ADR-056).
//
// Why this exists alongside web_check.mjs / inspect_e2e.mjs: those two
// OBSERVE (navigate, screenshot, count console errors). Neither can fail
// because a button did nothing, or because a class name points at CSS that
// was never written. Three real bugs shipped past them and past 268 unit
// tests on 2026-08-07:
//
//   1. `.flash-*` class names copied into React while the rules stayed in
//      the mockup file — the page rendered as unstyled semantic HTML. Unit
//      tests check the DOM, not what it looks like.
//   2. `fastboot reboot` blocking forever when no board is in fastboot,
//      holding the single-job lock. Only a real click found it.
//   3. Inspect's sub-nav highlight stuck on the first tab for all 13 tabs —
//      a routing bug invisible to component tests, which never mount the
//      router.
//
// So the rule this file follows: every check ASSERTS and a failure exits
// non-zero. Screenshots are evidence, not the product.
//
// usage:
//   node web/scripts/flash_e2e.mjs
// env:
//   ALB_BASE       default http://127.0.0.1:8765/app  (the hub — where the
//                  agent's real state lives; a dev server would show an
//                  empty bench and prove less)
//   E2E_OUT_DIR    screenshot dir (default under .claude/reports/, gitignored)
//   E2E_HEADED=1   watch it run
//
// Requires a browser once:  cd web && npx playwright install chromium
//
// ⚠ It tests THE BUNDLE THE HUB SERVES, not this checkout. `npm run build`
// here writes docs/app/ in THIS repo; if the hub runs from a different
// checkout (the usual split of "edit here, run there"), your change is
// invisible to this script until it reaches that checkout and is rebuilt
// there. A green run against a stale bundle proves nothing — that is how
// the first version of this very self-check passed while the CSS it was
// supposed to catch was missing. Point ALB_BASE at the hub you actually
// changed, or sync first.

import { chromium } from "playwright";
import { mkdir, writeFile, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";

const BASE = process.env.ALB_BASE ?? "http://127.0.0.1:8765/app";
const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const OUT = resolve(process.env.E2E_OUT_DIR ?? `.claude/reports/web-check/${stamp}-flash-e2e`);

let passed = 0;
const failures = [];

function check(name, condition, detail = "") {
  if (condition) {
    passed += 1;
    console.log(`  ok    ${name}`);
  } else {
    failures.push({ name, detail });
    console.log(`  FAIL  ${name}${detail ? `\n          ${detail}` : ""}`);
  }
}

await mkdir(OUT, { recursive: true });

let browser;
try {
  browser = await chromium.launch({ headless: !process.env.E2E_HEADED });
} catch (e) {
  console.error(`cannot launch chromium: ${e.message}`);
  console.error("install it once:  cd web && npx playwright install chromium");
  process.exit(2);
}

const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

const posts = [];
page.on("request", (r) => {
  if (r.method() === "POST") posts.push(new URL(r.url()).pathname);
});

// ── 1. sub-nav highlight follows the URL ────────────────────────────────
// Regression: all 13 tab routes are declared with literal paths, so
// `useParams()` never yielded a tabKey and every tab highlighted the first
// one. Checking more than one tab is the point — a single-tab check passes
// even when the value is hard-coded.
console.log("\nsub-nav highlight");
for (const [tab, label] of [
  ["flash", "Flash"],
  ["power", "Power"],
  ["system", "System Info"],
]) {
  await page.goto(`${BASE}/inspect/${tab}`, { waitUntil: "networkidle", timeout: 20000 });
  // Scope to .subnav: the activity bar also uses .is-active, and an
  // unscoped selector matches its (text-less) icon link first — the check
  // would then fail for a reason that has nothing to do with the sub-nav.
  const active = await page
    .locator('.subnav [role="tab"].is-active')
    .first()
    .innerText()
    .catch(() => "");
  check(`/inspect/${tab} highlights ${label}`, active.trim() === label, `got ${JSON.stringify(active.trim())}`);
  // Visual highlight and the accessible state must agree — a sighted user
  // and a screen-reader user should not be told different things.
  const ariaSelected = await page
    .locator(`.subnav [role="tab"][aria-selected="true"]`)
    .innerText()
    .catch(() => "");
  check(`/inspect/${tab} announces ${label} to assistive tech`, ariaSelected.trim() === label, `aria=${JSON.stringify(ariaSelected.trim())}`);
}

// ── 2. the stylesheet actually carries the class names ──────────────────
// Regression: the mockup's rules never reached components.css. Asserting a
// computed value the default UA stylesheet would never produce is what
// separates "styled" from "semantic HTML that happens to have class names".
console.log("\nstyles are wired up");
await page.goto(`${BASE}/inspect/flash`, { waitUntil: "networkidle", timeout: 20000 });
await page.waitForSelector(".flash-state-card", { timeout: 10000 });

const layout = await page.evaluate(() => {
  const card = document.querySelector(".flash-state-card");
  const tab = document.querySelector(".flash-tab");
  const cs = getComputedStyle(card);
  return {
    border: cs.borderTopWidth,
    radius: cs.borderTopLeftRadius,
    padding: cs.paddingTop,
    display: getComputedStyle(tab).display,
    columns: getComputedStyle(tab).gridTemplateColumns,
  };
});
check("state card has a border", layout.border !== "0px", JSON.stringify(layout));
check("state card has rounded corners", layout.radius !== "0px", `radius=${layout.radius}`);
check("state card has padding", layout.padding !== "0px", `padding=${layout.padding}`);
check("tab uses the two-column grid", layout.display === "grid", `display=${layout.display}`);
check(
  "grid really has two tracks",
  layout.columns.trim().split(/\s+/).length === 2,
  `columns=${layout.columns}`,
);

// ── 3. state card reflects the hub ──────────────────────────────────────
console.log("\nstate card");
const hub = await (await fetch(`${BASE.replace(/\/app$/, "")}/api/flash/status`)).json();
const domState = await page.locator(".flash-state-card__verdict").getAttribute("data-state");
const expected = !hub.available ? "unavailable" : hub.busy ? "busy" : "ready";
check(`data-state matches the hub (${expected})`, domState === expected, `dom=${domState}`);
check(
  "the card explains itself",
  (await page.locator(".flash-state-card__why").innerText()).trim().length > 20,
);

// ── 4. destructive control is gated ─────────────────────────────────────
console.log("\nflash button is gated");
check("disabled with no image chosen", await page.locator(".flash-btn--danger").isDisabled());

if (hub.available && !hub.busy) {
  // Upload a real file through the real endpoint — the FormData path is
  // mocked away in unit tests, so this is the only place it is exercised.
  const probe = join(tmpdir(), `alb-e2e-${process.pid}.bin`);
  await writeFile(probe, Buffer.alloc(64, 0xa5));
  await page.locator('input[type="file"]').setInputFiles(probe);
  await page.waitForSelector(".flash-file__name", { timeout: 15000 });
  check("uploaded file shows up", (await page.locator(".flash-file__name").innerText()).length > 0);
  check("flash button enabled once an image is present", !(await page.locator(".flash-btn--danger").isDisabled()));

  // THE safety property: the first click must only arm. If this ever sends
  // a request, one stray click writes a partition.
  const before = posts.length;
  await page.locator(".flash-btn--danger").click();
  await page.waitForTimeout(800);
  check("first click does NOT send a request", posts.length === before, `posts=${JSON.stringify(posts.slice(before))}`);
  check("first click shows the consequence", await page.locator(".flash-arm").isVisible());

  // Changing the target must re-arm — otherwise a click meant for one
  // partition lands on another.
  await page.locator("#flash-partition").selectOption({ index: 1 });
  await page.waitForTimeout(300);
  check("changing partition disarms the confirmation", !(await page.locator(".flash-arm").isVisible()));
  await rm(probe, { force: true });
} else {
  console.log("  skip  upload/arm checks — hub reports unavailable or busy");
}

// ── 5. a click reaches the backend and a verdict is rendered ────────────
console.log("\nreboot click round-trip");
const beforeReboot = posts.length;
await page.locator(".flash-btn--ghost").click();
await page.waitForTimeout(2500);
check(
  "click issues POST /api/flash/reboot",
  posts.slice(beforeReboot).includes("/api/flash/reboot"),
  `posts=${JSON.stringify(posts.slice(beforeReboot))}`,
);
const timeline = (await page.locator(".flash-timeline__log").innerText()).trim();
check("timeline renders the outcome", timeline.length > 0 && !/No job yet/.test(timeline), timeline.slice(0, 120));

// ── 6. no console errors ────────────────────────────────────────────────
console.log("\nconsole");
check("no console / page errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));

await page.screenshot({ path: join(OUT, "flash-tab.png"), fullPage: true });
await browser.close();

console.log(`\nscreenshot: ${join(OUT, "flash-tab.png")}`);
console.log(`${passed} passed, ${failures.length} failed`);
if (failures.length) {
  console.log("\nfailures:");
  for (const f of failures) console.log(`  - ${f.name}${f.detail ? `: ${f.detail}` : ""}`);
  process.exit(1);
}
