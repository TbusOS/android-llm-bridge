#!/usr/bin/env node
/**
 * Render the project site's social preview image (Open Graph card):
 * docs/assets/site/og-card.html -> docs/assets/site/og.png, 1200x630.
 *
 *   node web/scripts/render_site_og.mjs
 */
import { chromium } from "playwright";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const src = join(repo, "docs", "assets", "site", "og-card.html");
const out = join(repo, "docs", "assets", "site", "og.png");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(src).href, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: out });
await browser.close();
console.log(`wrote ${out}`);
