#!/usr/bin/env node
// PR-H + PR-C + PR-E.v2 浏览器 end-to-end 验证（2026-05-02）。
// 选 device → 切 5 个有真数据的 tab → 截图 + console 错误统计。
//
// 截图落 web/.claude/reports/web-check/<stamp>/ — 该路径已 gitignore。

import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').slice(0, 19);
const outDir = resolve(`.claude/reports/web-check/${stamp}-inspect-e2e`);
const baseUrl = 'http://127.0.0.1:5173';

await mkdir(outDir, { recursive: true });

const consoleErrors = [];
const pageErrors = [];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push({ text: m.text() });
});
page.on('pageerror', e => pageErrors.push({ message: e.message }));

const visit = async (route, name) => {
  await page.goto(baseUrl + route, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(2500);
  await page.screenshot({
    path: join(outDir, `${name}.png`),
    fullPage: false,
  });
};

// 1) Dashboard 先开（让 zustand store 装载 device 列表）
await visit('/app/dashboard', '01-dashboard');

// 2) 用 zustand persist storage 直接预设 device，避开 combobox UI 交互
// Serial 从环境变量读，本地测试时 export ALB_E2E_DEVICE=<serial> ; 默认
// 不预设 device — 不会破坏 inspect 的 "no device" 占位测试。
const serialFromEnv = process.env.ALB_E2E_DEVICE;
let selected = false;
if (serialFromEnv) {
  await page.evaluate((s) => {
    const existing = JSON.parse(localStorage.getItem('alb.app') || '{}');
    const next = {
      state: { ...(existing.state || {}), device: s },
      version: existing.version ?? 0,
    };
    localStorage.setItem('alb.app', JSON.stringify(next));
  }, serialFromEnv);
  selected = true;
  console.log(`  device preset via localStorage (from ALB_E2E_DEVICE env)`);
} else {
  console.log('  ALB_E2E_DEVICE env not set — Inspect will render no-device state');
}

// 3) 重载 inspect — 现在 store 有 device
await visit('/app/inspect', '02-inspect-no-device');
console.log('device selected:', selected);
await page.waitForTimeout(1500);
await page.screenshot({ path: join(outDir, '03-inspect-device-selected.png'), fullPage: false });

// 4) 切 5 个 tab — 重 lazy load 验证
const tabs = ['Charts', 'UART', 'Logcat', 'Shell', 'Files'];
for (const [i, tabName] of tabs.entries()) {
  await page.click(`role=tab[name="${tabName}"]`).catch(() => {
    return page.click(`button:has-text("${tabName}")`);
  });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: join(outDir, `${String(i + 4).padStart(2, '0')}-inspect-${tabName.toLowerCase()}.png`), fullPage: false });
}

// 5) Files tab — 试触 HITL modal（需要 push 到 /system，会触发）
// 简化：先截 Files tab 现状，HITL 走 backend 端真触发。

await browser.close();

await writeFile(
  join(outDir, 'summary.json'),
  JSON.stringify({
    consoleErrors,
    pageErrors,
    deviceSelected: selected,
    screenshots: ['01-dashboard', '02-inspect-no-device', '03-inspect-device-selected', ...tabs.map((t, i) => `${String(i + 4).padStart(2, '0')}-inspect-${t.toLowerCase()}`)],
  }, null, 2),
);

console.log('=== Summary ===');
console.log(`consoleErrors: ${consoleErrors.length}`);
console.log(`pageErrors: ${pageErrors.length}`);
console.log(`deviceSelected: ${selected}`);
console.log(`outDir: ${outDir}`);
