#!/usr/bin/env node
/**
 * F.8 visual end-to-end screenshot run.
 *
 * Hits the live alb-api at http://127.0.0.1:8765/app/<route>, captures
 * each top-level dashboard module at desktop (1440) and tablet (768)
 * viewports, and writes PNGs into the timestamped reports dir.
 *
 * Run AFTER kicking off a chat session so KPI / spark / timeline have
 * real data to render.
 */
import { chromium } from "/home/zhangbh/claude-tools/sky-skills/node_modules/playwright/index.mjs";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const BASE = process.env.ALB_BASE ?? "http://127.0.0.1:8765/app";
const OUT_DIR =
  process.env.F8_OUT_DIR ??
  "/home/zhangbh/android-llm-bridge/.claude/reports/screenshots/2026-04-29-f8";

const ROUTES = [
  { name: "dashboard", path: "/dashboard" },
  { name: "chat", path: "/chat" },
  { name: "inspect", path: "/inspect" },
  { name: "sessions", path: "/sessions" },
  { name: "playground", path: "/playground" },
  { name: "audit", path: "/audit" },
];

const VIEWPORTS = [
  { tag: "1440", width: 1440, height: 900 },
  { tag: "768", width: 768, height: 1024 },
];

await mkdir(OUT_DIR, { recursive: true });

const browser = await chromium.launch();
let pass = 0;
let fail = 0;
const fails = [];

for (const vp of VIEWPORTS) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2,
  });
  // One persistent page per viewport; SPA navigation via client-side
  // router avoids the alb-api StaticFiles 404 on deep paths.
  const page = await ctx.newPage();
  try {
    await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 15_000 });
    // First-render settle (KPI / sessions hooks fire on mount).
    await page.waitForTimeout(1500);
  } catch (e) {
    console.log(`✗ initial load @ ${vp.tag}: ${e.message}`);
    fail++;
    fails.push(`init-${vp.tag}: ${e.message}`);
    await ctx.close();
    continue;
  }
  for (const r of ROUTES) {
    try {
      // Click the matching nav link (sidebar / topnav anchor). Falls
      // back to history.pushState if no link is found (some routes
      // like /audit aren't in the visible nav).
      const navigated = await page.evaluate((path) => {
        const anchors = Array.from(document.querySelectorAll("a"));
        const target = anchors.find((a) => {
          const href = a.getAttribute("href") || "";
          return href === path || href.endsWith(path);
        });
        if (target) {
          target.click();
          return true;
        }
        // Fallback: synth a click via TanStack Router's history. The
        // router subscribes to popstate/pushstate, so this works.
        history.pushState({}, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
        return false;
      }, r.path);
      await page.waitForTimeout(navigated ? 1200 : 1500);
      const out = join(OUT_DIR, `${r.name}-${vp.tag}.png`);
      await page.screenshot({ path: out, fullPage: true });
      console.log(
        `✓ ${r.name} @ ${vp.tag}  (${navigated ? "click" : "history"}) →  ${out}`,
      );
      pass++;
    } catch (e) {
      console.log(`✗ ${r.name} @ ${vp.tag}  →  ${e.message}`);
      fail++;
      fails.push(`${r.name}-${vp.tag}: ${e.message}`);
    }
  }
  await page.close();
  await ctx.close();
}
await browser.close();

console.log(`\nF.8 screenshot run: ${pass} pass / ${fail} fail`);
if (fail) {
  console.log("failures:");
  for (const f of fails) console.log("  - " + f);
  process.exit(1);
}
