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

// Direct page.goto deep-links work after DEBT-014 fix landed
// (`src/alb/api/ui_static.py SPAStaticFiles`). Earlier the script had
// to load `/app/` first then push routes via history because the old
// StaticFiles 404'd deep paths — that workaround is no longer needed.
for (const vp of VIEWPORTS) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2,
  });
  for (const r of ROUTES) {
    const page = await ctx.newPage();
    const url = `${BASE}${r.path}`;
    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 15_000 });
      await page.waitForTimeout(1200);
      const out = join(OUT_DIR, `${r.name}-${vp.tag}.png`);
      await page.screenshot({ path: out, fullPage: true });
      console.log(`✓ ${r.name} @ ${vp.tag}  →  ${out}`);
      pass++;
    } catch (e) {
      console.log(`✗ ${r.name} @ ${vp.tag}  →  ${e.message}`);
      fail++;
      fails.push(`${r.name}-${vp.tag}: ${e.message}`);
    } finally {
      await page.close();
    }
  }
  await ctx.close();
}
await browser.close();

console.log(`\nF.8 screenshot run: ${pass} pass / ${fail} fail`);
if (fail) {
  console.log("failures:");
  for (const f of fails) console.log("  - " + f);
  process.exit(1);
}
