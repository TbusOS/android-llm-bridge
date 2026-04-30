#!/usr/bin/env node
// L1 web verification probe — navigate to a dev-server route and dump
// structured signals (a11y tree, console errors, DOM counts, screenshot).
//
// usage:
//   node scripts/dev/web_check.mjs [route] [outDir]
// env:
//   WEB_CHECK_BASE_URL  default http://127.0.0.1:5173
//   WEB_CHECK_TIMEOUT   default 15000  (ms, page.goto)
//   WEB_CHECK_WAIT      default networkidle
//
// exits non-zero on navigation error / page error / console error.

import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const route = process.argv[2] || '/app/';
const stamp = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').slice(0, 19);
const outDir = resolve(process.argv[3] || `.claude/reports/web-check/${stamp}`);
const baseUrl = process.env.WEB_CHECK_BASE_URL || 'http://127.0.0.1:5173';
const timeout = Number(process.env.WEB_CHECK_TIMEOUT || 15000);
const waitUntil = process.env.WEB_CHECK_WAIT || 'networkidle';
const url = baseUrl.replace(/\/$/, '') + route;

await mkdir(outDir, { recursive: true });

const consoleMessages = [];
const pageErrors = [];
const networkFailures = [];

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on('console', m => {
  consoleMessages.push({ type: m.type(), text: m.text(), location: m.location() });
});
page.on('pageerror', e => {
  pageErrors.push({ message: e.message, stack: (e.stack || '').split('\n').slice(0, 6).join('\n') });
});
page.on('requestfailed', r => {
  networkFailures.push({ url: r.url(), method: r.method(), error: r.failure()?.errorText });
});

let navError = null;
try {
  await page.goto(url, { waitUntil, timeout });
} catch (e) {
  navError = e.message;
}

await page.waitForTimeout(800);

const title = await page.title().catch(() => null);
const ariaSnapshot = await page.locator('body').ariaSnapshot().catch(e => `(aria-snapshot failed: ${e.message})`);
const bodyText = await page.evaluate(() => (document.body?.innerText || '').slice(0, 4000)).catch(() => '');
const domCounts = await page.evaluate(() => ({
  bodyChildren: document.body?.children.length ?? 0,
  headings: document.querySelectorAll('h1,h2,h3,h4').length,
  buttons: document.querySelectorAll('button').length,
  links: document.querySelectorAll('a').length,
  images: document.querySelectorAll('img').length,
  articles: document.querySelectorAll('article').length,
  hasReactRoot: !!document.querySelector('#root'),
  rootChildCount: document.querySelector('#root')?.children.length ?? 0,
})).catch(() => ({}));

await page.screenshot({ path: join(outDir, 'screenshot.png'), fullPage: true });
await writeFile(join(outDir, 'aria_snapshot.yaml'), ariaSnapshot);
await writeFile(join(outDir, 'console.json'), JSON.stringify({
  errors: consoleMessages.filter(m => m.type === 'error'),
  warnings: consoleMessages.filter(m => m.type === 'warning'),
  pageErrors,
  networkFailures,
}, null, 2));
await writeFile(join(outDir, 'body_text.txt'), bodyText);

const consoleErrorCount = consoleMessages.filter(m => m.type === 'error').length;
const report = {
  url,
  navError,
  title,
  domCounts,
  consoleErrorCount,
  consoleWarnCount: consoleMessages.filter(m => m.type === 'warning').length,
  pageErrorCount: pageErrors.length,
  networkFailureCount: networkFailures.length,
  artifacts: {
    screenshot: join(outDir, 'screenshot.png'),
    aria_snapshot: join(outDir, 'aria_snapshot.yaml'),
    console: join(outDir, 'console.json'),
    body_text: join(outDir, 'body_text.txt'),
  },
};
await writeFile(join(outDir, 'report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));

await browser.close();
const failed = !!navError || consoleErrorCount > 0 || pageErrors.length > 0;
process.exit(failed ? 1 : 0);
