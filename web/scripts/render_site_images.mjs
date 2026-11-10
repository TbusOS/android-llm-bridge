#!/usr/bin/env node
/**
 * Render the project site's static images with Playwright:
 *
 *   docs/assets/site/og-card.html  -> docs/assets/site/og.png             (1200x630 social preview)
 *   docs/index.html  #topo figure  -> docs/assets/site/readme/topology-{en,zh}-{light,dark}.png
 *                                     (the topology picture at the top of README.md / README.en.md)
 *
 *   node web/scripts/render_site_images.mjs
 *
 * Re-run after changing the topology SVG, the og card or the shared tokens.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const site = join(repo, "docs", "assets", "site");
const browser = await chromium.launch();

async function render(url, out, { width, height, lang = "en", theme = "light", selector = null, scale = 1 }) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: scale, colorScheme: theme });
  await ctx.addInitScript(([l, t]) => {
    try { localStorage.setItem("alb-site-lang", l); localStorage.setItem("alb-site-theme", t); } catch (e) { /* ignore */ }
  }, [lang, theme]);
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  if (selector) await page.locator(selector).screenshot({ path: out, animations: "disabled" });
  else await page.screenshot({ path: out, animations: "disabled" });
  await ctx.close();
  console.log(`wrote ${out}`);
}

await render(pathToFileURL(join(site, "og-card.html")).href, join(site, "og.png"), { width: 1200, height: 630 });

mkdirSync(join(site, "readme"), { recursive: true });
for (const lang of ["en", "zh"]) {
  for (const theme of ["light", "dark"]) {
    await render(pathToFileURL(join(repo, "docs", "index.html")).href,
      join(site, "readme", `topology-${lang}-${theme}.png`),
      { width: 1280, height: 900, lang: lang === "zh" ? "zh-CN" : "en", theme, selector: "#topo", scale: 1.5 });
  }
}
await browser.close();
