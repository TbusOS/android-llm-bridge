#!/usr/bin/env node
/**
 * DEBT-015 regression test for the GH Pages SPA fallback dance:
 *   1. docs/404.html redirect script  — bounces /app/<route> to
 *      /app/?spa=1&p=<encoded route>
 *   2. docs/app/index.html recovery script — sees ?spa=1 on first
 *      paint and history.replaceState's the URL back to the original
 *      deep link before TanStack Router resolves the route.
 *
 * The two scripts are pure DOM logic — no React, no fetch, no async
 * state. We extract them via regex and run them through node's vm
 * with mocked window/location/history globals. This catches edge
 * cases (trailing slash, no `p`, hash, no-op when not bounced)
 * without spinning up a real browser.
 *
 * Run:
 *   node tests/web/spa_fallback_test.mjs
 *
 * Exit 0 on success, 1 on first failure.
 */
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import vm from "node:vm";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, "..", "..");

async function loadScript(htmlPath, matcher) {
  const html = await readFile(htmlPath, "utf-8");
  const m = html.match(matcher);
  if (!m) throw new Error(`script not found in ${htmlPath}`);
  return m[1];
}

function runScript(code, win) {
  const sandbox = { window: win, URLSearchParams, encodeURIComponent };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
}

const redirectCode = await loadScript(
  resolve(REPO, "docs/404.html"),
  /<script>\s*(\(function[\s\S]*?\}\)\(\);)\s*<\/script>/,
);
const recoveryCode = await loadScript(
  resolve(REPO, "docs/app/index.html"),
  /<script>\s*(\(function[\s\S]*?spa=1[\s\S]*?\}\)\(\);)\s*<\/script>/,
);

let pass = 0;
const failures = [];
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) {
    pass++;
    console.log(`✓ ${label}`);
  } else {
    failures.push({ label, expected, actual });
    console.log(`✗ ${label}`);
    console.log(`    expected: ${JSON.stringify(expected)}`);
    console.log(`    got:      ${JSON.stringify(actual)}`);
  }
}

// ---- 404.html redirect script ----

function fakeWinForRedirect(pathname, search = "", hash = "") {
  let captured = null;
  return {
    win: {
      location: { pathname, search, hash, replace(url) { captured = url; } },
    },
    get captured() {
      return captured;
    },
  };
}

{
  const f = fakeWinForRedirect("/android-llm-bridge/app/dashboard");
  runScript(redirectCode, f.win);
  check(
    "404 redirect: /app/dashboard → SPA shell ?spa=1&p=dashboard",
    f.captured,
    "/android-llm-bridge/app/?spa=1&p=dashboard",
  );
}
{
  const f = fakeWinForRedirect(
    "/android-llm-bridge/app/sessions/abc-123",
    "?tab=details",
  );
  runScript(redirectCode, f.win);
  check(
    "404 redirect: subpath + querystring → carries qs",
    f.captured,
    "/android-llm-bridge/app/?spa=1&p=sessions%2Fabc-123&qs=tab%3Ddetails",
  );
}
{
  const f = fakeWinForRedirect("/android-llm-bridge/random-typo-path");
  runScript(redirectCode, f.win);
  check(
    "404 redirect: non-/app path → no redirect (shows 404 landing)",
    f.captured,
    null,
  );
}
{
  const f = fakeWinForRedirect("/android-llm-bridge/app/dashboard/");
  runScript(redirectCode, f.win);
  check(
    "404 redirect: trailing slash trimmed",
    f.captured,
    "/android-llm-bridge/app/?spa=1&p=dashboard",
  );
}
{
  const f = fakeWinForRedirect(
    "/android-llm-bridge/app/dashboard",
    "?spa=1&p=dashboard",
  );
  runScript(redirectCode, f.win);
  check(
    "404 redirect: loop guard — already-bounced URL is not re-wrapped",
    f.captured,
    null,
  );
}
{
  const f = fakeWinForRedirect("/android-llm-bridge/app/");
  runScript(redirectCode, f.win);
  check(
    "404 redirect: empty rest (/app/ itself) → no-op",
    f.captured,
    null,
  );
}
{
  const f = fakeWinForRedirect("/android-llm-bridge/app/inspect", "", "#charts");
  runScript(redirectCode, f.win);
  check(
    "404 redirect: hash fragment is carried through",
    f.captured,
    "/android-llm-bridge/app/?spa=1&p=inspect#charts",
  );
}

// ---- index.html recovery script ----

function fakeWinForRecovery(pathname, search) {
  let restored = null;
  return {
    win: {
      location: { pathname, search, hash: "" },
      history: { replaceState(state, title, url) { restored = url; } },
    },
    get restored() {
      return restored;
    },
  };
}

{
  const f = fakeWinForRecovery(
    "/android-llm-bridge/app/",
    "?spa=1&p=dashboard",
  );
  runScript(recoveryCode, f.win);
  check(
    "recovery: ?spa=1&p=dashboard → /app/dashboard",
    f.restored,
    "/android-llm-bridge/app/dashboard",
  );
}
{
  const f = fakeWinForRecovery(
    "/android-llm-bridge/app/",
    "?spa=1&p=sessions%2Fabc-123&qs=tab%3Ddetails",
  );
  runScript(recoveryCode, f.win);
  check(
    "recovery: subpath + qs → /app/sessions/abc-123?tab=details",
    f.restored,
    "/android-llm-bridge/app/sessions/abc-123?tab=details",
  );
}
{
  const f = fakeWinForRecovery("/android-llm-bridge/app/", "?other=1");
  runScript(recoveryCode, f.win);
  check("recovery: no spa=1 → no-op", f.restored, null);
}
{
  // ?spa=1 without `p` should clean residual to keep URL bar tidy.
  const f = fakeWinForRecovery("/android-llm-bridge/app/", "?spa=1");
  runScript(recoveryCode, f.win);
  check(
    "recovery: ?spa=1 missing p → cleans residual to /app/",
    f.restored,
    "/android-llm-bridge/app/",
  );
}
{
  // dev/local fallback (vite dev server: pathname === "/")
  const f = fakeWinForRecovery("/", "?spa=1&p=dashboard");
  runScript(recoveryCode, f.win);
  check(
    "recovery: pathname=/ (vite dev) → /dashboard (no double slash)",
    f.restored,
    "/dashboard",
  );
}

console.log(`\n${pass} pass / ${failures.length} fail`);
process.exit(failures.length === 0 ? 0 : 1);
