#!/usr/bin/env node
// v3 shell end-to-end checks — a real browser against a real hub.
//
// Replaces inspect_e2e.mjs (the v2 Inspect sub-nav is gone). Same rule as
// flash_e2e.mjs: every check ASSERTS and any failure exits non-zero;
// screenshots are evidence only. What unit tests cannot see and this does:
// routing (old addresses redirect, the drawer highlights the right page),
// the bundled stylesheet actually applied, console errors on each page,
// the ⌘K search navigating, the assistant sheet opening, language / theme
// switching the document attributes the CSS keys on.
//
// usage:
//   node web/scripts/v3_e2e.mjs
// env:
//   ALB_BASE      default http://127.0.0.1:8765/app
//   E2E_OUT_DIR   screenshot dir (default .claude/reports/v3-e2e, gitignored)
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";

const BASE = (process.env.ALB_BASE || "http://127.0.0.1:8765/app").replace(/\/$/, "");
const OUT = resolve(process.env.E2E_OUT_DIR || ".claude/reports/v3-e2e");

const PAGES = [
  ["/dashboard", "overview"], ["/console", "console"], ["/logs", "logs"], ["/timeline", "timeline"],
  ["/perf", "perf"], ["/files", "files"], ["/apps", "apps"], ["/screen", "screen"], ["/problems", "problems"],
  ["/info", "info"], ["/power", "power"], ["/flash", "flash"], ["/board-config", "board-config"], ["/bench", "bench"],
  ["/chat", "chat"], ["/playground", "playground"], ["/sessions", "sessions"], ["/doctor", "doctor"],
  ["/audit", "audit"], ["/connections", "connections"],
];
const REDIRECTS = [
  ["/inspect/charts", "/perf"], ["/inspect/logcat", "/logs"], ["/inspect/shell", "/console"],
  ["/inspect/ui-dump", "/screen"], ["/inspect/diag", "/problems"], ["/inspect/flash", "/flash"], ["/terminal", "/console"], ["/", "/dashboard"],
];

let failed = 0;
const results = [];
function check(name, ok, detail = "") {
  results.push([ok ? "PASS" : "FAIL", name, detail]);
  if (!ok) failed++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));

// 1. every page renders inside the shell without console errors
for (const [path, key] of PAGES) {
  errors.length = 0;
  const r = await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch((e) => e);
  await page.waitForSelector("main.content h1", { timeout: 15000 }).catch(() => {});
  const h1 = await page.locator("main.content h1").first().textContent().catch(() => null);
  const current = await page.locator('.nav__item[aria-current="page"]').getAttribute("href").catch(() => null);
  // The hub answers 4xx for a few panels when no device is attached; those
  // are reported by the page itself and are not page errors.
  const real = errors.filter((e) => !/Failed to load resource: the server responded with a status of 4\d\d/.test(e));
  check(`page ${path} renders`, !(r instanceof Error) && !!h1, h1 ?? String(r));
  check(`page ${path} highlights itself`, (current ?? "").endsWith(path), current ?? "none");
  check(`page ${path} has no console errors`, real.length === 0, real.slice(0, 2).join(" | "));
  await page.screenshot({ path: join(OUT, `${key}.png`) });
}

// 2. the v3 stylesheet is the one applied
await page.goto(BASE + "/dashboard", { waitUntil: "networkidle" });
const primary = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--md-sys-color-primary").trim());
check("M3 tokens are loaded", primary.length > 0, primary);
const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
check("body uses the bundled Roboto Flex / Noto Sans SC stack", /Roboto Flex/.test(font) && /Noto Sans SC/.test(font), font);

// 3. old addresses land on their new pages
for (const [from, to] of REDIRECTS) {
  await page.goto(BASE + from, { waitUntil: "networkidle" });
  const path = new URL(page.url()).pathname.replace(/^\/app/, "");
  check(`redirect ${from} → ${to}`, path === to, path);
}

// 4. language and theme switch the attributes the CSS keys on
await page.goto(BASE + "/dashboard", { waitUntil: "networkidle" });
const langBefore = await page.evaluate(() => document.documentElement.lang);
await page.locator(".topbar .icon-btn[aria-label*='Chinese'], .topbar .icon-btn[aria-label*='English'], .topbar .icon-btn[aria-label*='中文']").first().click();
await page.waitForTimeout(200);
const langAfter = await page.evaluate(() => document.documentElement.lang);
check("language button switches <html lang>", langBefore !== langAfter && /^(en|zh-CN)$/.test(langAfter), `${langBefore} → ${langAfter}`);
const h1zh = await page.locator("main.content h1").first().textContent();
check("the page heading follows the language", langAfter === "zh-CN" ? h1zh === "总览" : h1zh === "Overview", h1zh ?? "");
const themeBtn = page.locator(".topbar .icon-btn[aria-label*='Theme'], .topbar .icon-btn[aria-label*='主题']").first();
await themeBtn.click();
await themeBtn.click();
const theme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
check("theme button reaches dark and repaints the page", theme === "dark" && bg !== "rgb(248, 250, 253)", `${theme} ${bg}`);
await page.screenshot({ path: join(OUT, "dashboard-dark-zh.png") });
await themeBtn.click();

// 5. ⌘K search navigates
await page.keyboard.press("Control+k");
const palette = await page.locator(".palette").isVisible().catch(() => false);
check("Ctrl+K opens the page search", palette);
await page.locator(".palette input").fill("timeline");
await page.locator(".palette input").press("Enter");
await page.waitForTimeout(300);
check("search + Enter navigates", new URL(page.url()).pathname.endsWith("/timeline"), page.url());

// 6. the assistant sheet opens from the top bar and closes again
await page.goto(BASE + "/dashboard", { waitUntil: "networkidle" });
await page.locator(".topbar .icon-btn[aria-label='Assistant'], .topbar .icon-btn[aria-label='助手']").first().click();
const sheet = await page.locator("aside.sheet").isVisible().catch(() => false);
check("assistant sheet opens", sheet);
await page.screenshot({ path: join(OUT, "assistant.png") });
await page.keyboard.press("Escape");
await page.locator("aside.sheet button[aria-label='Close'], aside.sheet button[aria-label='关闭']").first().click().catch(() => {});
check("assistant sheet closes", !(await page.locator("aside.sheet").isVisible().catch(() => false)));

// 7. phone width: rail/drawer collapse, no sideways scroll
await page.setViewportSize({ width: 390, height: 800 });
await page.goto(BASE + "/dashboard", { waitUntil: "networkidle" });
const sw = await page.evaluate(() => document.documentElement.scrollWidth);
check("390 px wide page does not scroll sideways", sw <= 390, `scrollWidth ${sw}`);
await page.screenshot({ path: join(OUT, "phone.png"), fullPage: true });

await browser.close();
console.log(`\n${results.length - failed} passed, ${failed} failed · screenshots in ${OUT}`);
process.exit(failed ? 1 : 0);
