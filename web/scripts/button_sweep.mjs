#!/usr/bin/env node
// Button sweep: on every page, click every control a person could click
// and check that each one does something and breaks nothing.
//
// func_e2e.mjs checks each page's main job end to end; this one is wide
// instead of deep. For every visible button, tab, switch and link it
// records what the click caused — a request, a change on the page, a new
// address, a download — and flags:
//   FAIL  the click threw a page error, or tried to send a request that
//         changes a board (the guard stopped it — the skip list missed it)
//   DEAD  nothing at all happened: no request, no change, no navigation.
//         Usually a broken handler; sometimes a control whose effect is
//         outside the page (a focus move) — read the list.
//   SKIP  never clicked, with the reason (see NEVER below)
//
// Safe on a real bench: the same request guard as func_e2e.mjs aborts every
// request that changes a board or the bench, and controls that act on a
// board over a live connection (terminal input, Ctrl+C, saved commands,
// chat send) or start long jobs (recordings, bugreports) are never clicked.
// Confirm buttons inside a confirmation are never clicked either.
//
// Log rows (`button.log` in a `.logs` list) are one control repeated: only
// the first row of each list is clicked, the rest are SKIP. With a board on
// adb the logcat list never ends and keeps scrolling — 2026-11-05 the sweep
// spent 13 minutes clicking 218 rows of /logs, and rows that had scrolled
// away by the time of the click read as "click failed" (not a page bug).
//
// usage:
//   node web/scripts/button_sweep.mjs            # click everything allowed
//   node web/scripts/button_sweep.mjs --list     # only list what it would click
// env:
//   ALB_BASE     default http://127.0.0.1:8765/app
//   ALB_CDP_URL  drive an already-running browser (e.g. Chrome on the bench
//                Windows machine via web/scripts/win_browser.sh)
//   SWEEP_PAGES  comma list of paths to limit the run, e.g. /bench,/logs
//   E2E_OUT_DIR  report.json + screenshots (default .claude/reports/button-sweep)
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const BASE = (process.env.ALB_BASE || "http://127.0.0.1:8765/app").replace(/\/$/, "");
const OUT = resolve(process.env.E2E_OUT_DIR || ".claude/reports/button-sweep");
const CDP = process.env.ALB_CDP_URL || "";
const LIST_ONLY = process.argv.includes("--list");
const ALL_PAGES = [
  "/dashboard", "/console", "/logs", "/timeline", "/perf", "/files", "/apps", "/screen", "/problems",
  "/info", "/power", "/flash", "/board-config", "/bench", "/chat", "/playground", "/sessions",
  "/doctor", "/audit", "/connections",
];
const PAGES = process.env.SWEEP_PAGES ? process.env.SWEEP_PAGES.split(",").map((s) => s.trim()) : ALL_PAGES;

// Never clicked: action buttons whose label STARTS with one of these verbs
// (English UI). Rows of a history list ("reboot normal", "agent-update
// a -> b") and tab / segment pickers only select what is shown, so the
// verb rule does not apply to them.
const NEVER = [
  [/^(reboot|restart|power off|shut ?down|sleep|wake)\b/i, "reboots or sleeps the board"],
  [/^(flash|write|erase|wipe|format|set active|oem|lock|unlock)\b/i, "writes to the board"],
  [/^(install|uninstall|clear data|force stop|stop app|start app|launch|kill)\b/i, "changes apps on the board"],
  [/^(root|unroot|remount|adb root|adb unroot|adb remount)\b/i, "changes adb mode"],
  [/^(delete|remove|discard)\b/i, "deletes something"],
  [/^(update|start remotely|switch|use this board)\b/i, "changes the bench"],
  [/^(send|run|execute|ctrl\+c|stop in bootloader|break)\b/i, "sends input to the board"],
  [/^(record|capture|collect|bugreport|pull|push|upload)\b/i, "starts a long job or moves files"],
  [/^(save|apply)\b/i, "stores a change"],
];
const BLOCK = [
  /\/api\/flash\/(flash|plan\/run|oem|reboot|erase|set-active|boot)\b/,
  /\/api\/bench\/ops\/(?!usb-list\b|log-tail\b)[\w-]+/,
  /\/api\/bench\/(agent-update|agent-start)\b/,
  /\/api\/serial\/break-in\b/,
  /\/devices\/[^/]+\/files\/push/,
  /\/workspace\/files\/upload/,
];
const BLOCK_NON_GET = [/\/api\/adb\//, /\/api\/power\//, /\/api\/app\//, /\/api\/bench\/board\b/, /\/uart\/capture\b/, /\/api\/timeline\/capture\b/];

const results = [];
const blocked = [];
function record(status, page, name, detail = "") {
  results.push({ status, page, name, detail });
  if (status !== "PASS" || process.env.SWEEP_VERBOSE) console.log(`${status.padEnd(4)}  ${page}  «${name}»${detail ? `  — ${detail}` : ""}`);
}

/* A headless Edge that downloaded a file and then opened another page
   accepts the next DevTools connection and never finishes it: it keeps a
   hidden page with no renderer that Playwright attaches to and waits on
   (bench, 2026-11-02). Chrome, win_browser.sh's default, does not. */
async function connectRemote(url) {
  try {
    return await chromium.connectOverCDP(url, { timeout: 30000 });
  } catch (e) {
    console.error(`cannot drive the browser at ${url}: ${String(e.message).split("\n")[0]}`);
    console.error("restart it: stop web/scripts/win_browser.sh (Ctrl+C) and start it again, then rerun");
    console.error("(a headless Edge does this after a run with a download; win_browser.sh without --edge starts Chrome)");
    process.exit(2);
  }
}

await mkdir(OUT, { recursive: true });
const browser = CDP ? await connectRemote(CDP) : await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US", acceptDownloads: true });
await ctx.addInitScript(() => {
  try {
    const raw = localStorage.getItem("alb.app");
    const v = raw ? JSON.parse(raw) : { state: {}, version: 0 };
    if (v.state?.lang !== "en") {
      v.state = { ...(v.state ?? {}), lang: "en" };
      localStorage.setItem("alb.app", JSON.stringify(v));
    }
  } catch {}
  // count DOM changes so a click's effect can be measured
  window.__albMut = 0;
  new MutationObserver((m) => {
    window.__albMut += m.length;
  }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
});
await ctx.route("**/*", (route) => {
  const req = route.request();
  const url = req.url();
  const m = req.method();
  if (m === "DELETE" || BLOCK.some((re) => re.test(url)) || (m !== "GET" && BLOCK_NON_GET.some((re) => re.test(url)))) {
    blocked.push(`${m} ${url}`);
    return route.abort("blockedbyclient");
  }
  return route.continue();
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).split("\n")[0]));
let requests = 0;
page.on("request", () => {
  requests += 1;
});
let downloads = 0;
page.on("download", () => {
  downloads += 1;
});
console.log(`browser ${browser.version()} · ${await page.evaluate(() => navigator.userAgent)}`);

const SELECTOR = [
  "main.content button",
  "main.content [role=tab]",
  "main.content a[href]",
  "main.content input[type=checkbox]",
  "main.content summary",
].join(", ");

/** Visible, enabled controls on the page, each with a stable key. */
async function controls() {
  return page.evaluate((sel) => {
    const seen = new Map();
    const out = [];
    // tags from an earlier pass may sit on elements now hidden; a stale tag
    // would make two elements answer to one key
    for (const old of document.querySelectorAll("[data-sweep]")) old.removeAttribute("data-sweep");
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (r.width === 0 || r.height === 0 || style.visibility === "hidden") continue;
      if (el.closest("[inert], [hidden]")) continue;
      // accessible name, roughly: aria-label, then a <label> (an input's
      // name comes from it, not from its own text), then text, then title
      const labelText = el.labels && el.labels.length ? el.labels[0].textContent : "";
      const name = (el.getAttribute("aria-label") || labelText || el.textContent || el.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 60);
      const n = seen.get(name) ?? 0;
      seen.set(name, n + 1);
      const key = `${el.tagName}:${name}#${n}`;
      el.setAttribute("data-sweep", key);
      out.push({
        key,
        name,
        nth: n,
        tag: el.tagName.toLowerCase(),
        // for an unnamed control: enough to find it in the source
        hint: name ? "" : `${el.getAttribute("type") ?? ""} ${el.id ? `#${el.id}` : ""} near «${(el.closest("label, li, tr, div")?.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40)}»`.trim(),
        disabled: el.disabled === true || el.getAttribute("aria-disabled") === "true",
        inConfirm: !!el.closest(".confirm, [role=alertdialog]") && /btn--danger|btn--filled/.test(el.className),
        inTerminal: !!el.closest(".xterm, .term--x"),
        // already the selected tab / segment / row: clicking it again is a no-op
        selected:
          el.getAttribute("aria-selected") === "true" ||
          el.getAttribute("aria-pressed") === "true" ||
          !!el.closest('[aria-current="true"], [aria-current="page"]'),
        // a history row or a tab / segment: selects what is shown
        selects: el.matches(".log, .list__item--action, [role=tab]") || !!el.closest(".seg, [role=tablist], .list__title"),
        // which log list this row belongs to (-1: not a log row); only the
        // first row of each list is clicked
        logList: el.matches("button.log") ? [...document.querySelectorAll(".logs")].indexOf(el.closest(".logs")) : -1,
        href: el.tagName === "A" ? el.getAttribute("href") : null,
        external: el.tagName === "A" ? !!el.href && new URL(el.href).origin !== location.origin : false,
      });
    }
    return out;
  }, SELECTOR);
}

function nameLocator(c) {
  // controls() tagged every element it listed; icon-only buttons have no
  // text to find them by
  return page.locator(`[data-sweep=${JSON.stringify(c.key)}]`);
}

async function openPage(path) {
  await page.goto(BASE + path, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("main.content h1", { timeout: 20000 });
  // lazy pages and their first reads; a page that keeps a stream open never
  // goes idle, hence the cap
  await page.waitForLoadState("networkidle", { timeout: 6000 }).catch(() => {});
  await page.waitForTimeout(800);
}

/** Click one control and record what it caused; "gone" if it is not on the page now. */
async function clickOne(path, c0) {
  // find it again in the current DOM (earlier clicks may have moved things);
  // a live log list scrolls, so the row listed a moment ago may be gone —
  // any row of the same list stands for it
  const all = await controls();
  const now = all.find((c) => c.key === c0.key) ?? (c0.logList >= 0 ? all.find((c) => c.logList === c0.logList) : undefined);
  if (!now) return "gone";
  if (now.disabled) { record("SKIP", path, c0.name, "disabled now"); return "done"; }
  // a snackbar from the previous click hides an identical one from this
  // click (same text, no DOM change) — let it go first
  await page.waitForFunction(() => !document.querySelector(".snackbar"), null, { timeout: 5000 }).catch(() => {});
  const loc = nameLocator(now);
  const urlBefore = page.url();
  const mutBefore = await page.evaluate(() => window.__albMut);
  const reqBefore = requests;
  const dlBefore = downloads;
  const errBefore = errors.length;
  const blockedBefore = blocked.length;
  // a checkbox flips a property, not an attribute: no DOM mutation to see
  const checkedBefore = now.tag === "input" ? await loc.isChecked().catch(() => null) : null;
  const logRow = now.logList >= 0;
  // the open / selected row of that list, by text (null: none)
  const markedRow = () =>
    page
      .evaluate((i) => document.querySelectorAll(".logs")[i]?.querySelector('button.log[aria-expanded="true"], button.log[aria-pressed="true"]')?.textContent ?? null, now.logList)
      .catch(() => null);
  const markedBefore = logRow ? await markedRow() : null;
  let clickedRow = null; // text of the log row that was clicked
  try {
    if (logRow) {
      // A live log list replaces its row elements while Playwright waits for
      // the one it found to hold still (11-05 /logs: "click failed: Timeout"
      // on every try). Find a row and click it in one step: the LAST row —
      // a live list drops its oldest rows, so the first one is gone (and its
      // open state with it) by the time the check below looks.
      const clicked = await page.evaluate((i) => {
        const rows = document.querySelectorAll(".logs")[i]?.querySelectorAll("button.log") ?? [];
        const b = rows[rows.length - 1];
        if (!b) return null;
        b.click();
        return b.textContent ?? "";
      }, now.logList);
      if (clicked === null) throw new Error("the log list has no row any more");
      clickedRow = clicked;
    } else {
      await loc.click({ timeout: 4000 });
    }
  } catch (e) {
    record("FAIL", path, c0.name, `click failed: ${String(e.message).split("\n")[0]}`);
    await openPage(path);
    return "done";
  }
  await page.waitForTimeout(900);
  const mut = (await page.evaluate(() => window.__albMut).catch(() => mutBefore)) - mutBefore;
  const effects = [];
  if (page.url() !== urlBefore) effects.push(`→ ${new URL(page.url()).pathname.replace(/^\/app/, "")}`);
  if (requests > reqBefore) effects.push(`${requests - reqBefore} req`);
  if (downloads > dlBefore) effects.push("download");
  let alreadySelected = now.selected;
  if (logRow) {
    // a live list changes the DOM on its own, so the count says nothing here:
    // a row click opens (logcat) or selects (timeline) a row of that list
    // only the clicked row counts: another row the page selected by itself
    // (the timeline opens on its first problem line) proves nothing
    const markedAfter = await markedRow();
    if (markedAfter === clickedRow && markedBefore !== clickedRow) effects.push("the clicked row opened / got selected");
    else if (markedAfter === clickedRow) alreadySelected = true;
  } else if (mut > 0) effects.push(`${mut} DOM changes`);
  if (checkedBefore !== null) {
    const checkedAfter = await loc.isChecked().catch(() => null);
    if (checkedAfter !== null && checkedAfter !== checkedBefore) effects.push(checkedAfter ? "checked" : "unchecked");
  }
  if (blocked.length > blockedBefore) {
    record("FAIL", path, c0.name, `tried a board-changing request: ${blocked.slice(blockedBefore).join(" | ")}`);
  } else if (errors.length > errBefore) {
    record("FAIL", path, c0.name, `page error: ${errors.slice(errBefore).join(" | ")}`);
  } else if (effects.length === 0 && alreadySelected) {
    record("PASS", path, c0.name, "already selected — nothing to change");
  } else if (effects.length === 0) {
    record("DEAD", path, c0.name, "no request, no page change, no navigation");
  } else {
    record("PASS", path, c0.name, effects.join(", "));
  }
  // put things back: close what the click opened, return to the page
  await page.keyboard.press("Escape").catch(() => {});
  await page.keyboard.press("Escape").catch(() => {});
  if (new URL(page.url()).pathname !== new URL(BASE + path).pathname) await openPage(path);
  return "done";
}

for (const path of PAGES) {
  await openPage(path);
  const first = await controls();
  console.log(`\n${path}: ${first.length} controls`);
  // the first row of each log list stands for the rest of that list
  const logRowsSeen = new Set();
  const moreLogRow = (c) => {
    if (c.logList < 0) return false;
    if (logRowsSeen.has(c.logList)) return true;
    logRowsSeen.add(c.logList);
    return false;
  };
  const MORE_LOG_ROW = "another row of a log list the first row already stands for";
  if (LIST_ONLY) {
    for (const c of first) {
      const why = moreLogRow(c) ? [null, MORE_LOG_ROW] : c.selects ? undefined : NEVER.find(([re]) => re.test(c.name));
      console.log(`  ${why ? "skip " : "click"} ${c.tag.padEnd(6)} «${c.name}»${c.disabled ? " (disabled)" : ""}${why ? ` — ${why[1]}` : ""}`);
    }
    continue;
  }
  const done = new Set();
  const retry = [];
  for (const c0 of first) {
    if (done.has(c0.key)) continue;
    done.add(c0.key);
    if (moreLogRow(c0)) { record("SKIP", path, c0.name, MORE_LOG_ROW); continue; }
    const why = c0.selects ? undefined : NEVER.find(([re]) => re.test(c0.name));
    if (why) { record("SKIP", path, c0.name, why[1]); continue; }
    if (c0.inConfirm) { record("SKIP", path, c0.name, "confirm button"); continue; }
    if (c0.inTerminal) { record("SKIP", path, c0.name, "inside a terminal"); continue; }
    if (c0.disabled) { record("SKIP", path, c0.name, "disabled now"); continue; }
    if (c0.external) { record(c0.href ? "PASS" : "FAIL", path, c0.name, `external link ${c0.href}`); continue; }
    if (!c0.name) { record("SKIP", path, `(no accessible name) ${c0.hint}`, `${c0.tag} — unnamed control, an accessibility gap`); continue; }
    if ((await clickOne(path, c0)) === "gone") retry.push(c0);
  }
  // Controls an earlier click hid (another tab took over, a section closed):
  // try each once more on a freshly opened page.
  for (const c0 of retry) {
    await openPage(path);
    if ((await clickOne(path, c0)) === "gone") record("SKIP", path, c0.name, "only appears after another action");
  }
  await page.screenshot({ path: join(OUT, `${path.replace(/\W+/g, "_")}.png`) }).catch(() => {});
}

if (CDP) await ctx.close();
else await browser.close();
if (LIST_ONLY) process.exit(0);

const count = (s) => results.filter((r) => r.status === s).length;
await writeFile(join(OUT, "report.json"), JSON.stringify({ base: BASE, browser: browser.version(), results, blocked }, null, 2));
console.log(`\n${count("PASS")} worked · ${count("FAIL")} failed · ${count("DEAD")} did nothing · ${count("SKIP")} not clicked  (report: ${join(OUT, "report.json")})`);
process.exit(count("FAIL") ? 1 : 0);
