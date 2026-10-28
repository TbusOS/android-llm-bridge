#!/usr/bin/env node
// Screenshots of the v3 design pages for the public Web UI page
// (docs/webui-preview.html): 1440x900, light theme, once in Chinese and
// once in English, JPEG. The design pages carry sample data only — never
// point this at a running hub (real board names, serials and addresses
// must not land in docs/).
//
// Re-run after changing a design page, styles/v3 or icons.ts (and
// `node web/scripts/sync_v3_assets.mjs`), then commit the images.
//
// usage: node web/scripts/shoot_v3_previews.mjs [docs-dir] [out-dir] [quality]
import { chromium } from "playwright";
import { mkdir, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const DOCS = resolve(process.argv[2] || join(repo, "docs"));
const OUT = resolve(process.argv[3] || join(DOCS, "assets", "v3", "shots"));
const Q = Number(process.argv[4] || 82);
const PAGES = [
  ["overview", "webui-preview-v3.html"],
  ["console", "webui-preview-v3-console.html"],
  ["logs", "webui-preview-v3-logs.html"],
  ["timeline", "webui-preview-v3-timeline.html"],
  ["flash", "webui-preview-v3-flash.html"],
  ["bench", "webui-preview-v3-bench.html"],
  ["chat", "webui-preview-v3-chat.html"],
];

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
let total = 0;
let failed = 0;
for (const lang of ["zh", "en"]) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: lang === "zh" ? "zh-CN" : "en-US",
    colorScheme: "light",
  });
  await ctx.addInitScript(([l]) => {
    try { localStorage.setItem("alb-v3-lang", l); localStorage.setItem("alb-v3-theme", "light"); } catch (e) {}
  }, [lang]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  for (const [key, file] of PAGES) {
    errors.length = 0;
    await page.goto(pathToFileURL(join(DOCS, file)).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(400);
    const info = await page.evaluate(() => ({ lang: document.documentElement.lang, theme: document.documentElement.dataset.theme || "", h1: (document.querySelector("main h1") || {}).textContent }));
    const wantLang = lang === "zh" ? "zh-CN" : "en";
    if (info.lang !== wantLang || info.theme !== "light" || errors.length) {
      failed++;
      console.log(`FAIL ${key} ${lang}`, info, errors);
    }
    const path = join(OUT, `${key}-${lang}.jpg`);
    await page.screenshot({ path, type: "jpeg", quality: Q });
    const s = (await stat(path)).size;
    total += s;
    console.log(`${key}-${lang}.jpg ${(s / 1024).toFixed(0)} KB  lang=${info.lang} theme=${info.theme} h1=${(info.h1 || "").trim()}`);
  }
  await ctx.close();
}
await browser.close();
console.log(`total ${(total / 1024).toFixed(0)} KB, failed ${failed}`);
process.exit(failed ? 1 : 0);
