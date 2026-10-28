import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copyText } from "../../lib/clipboard";
import { ago, duration, pick } from "../../lib/i18n";
import { INSPECT_TAB_KEYS } from "../../router";
import { Icon, parseIcon } from "./Icon";
import { ICONS } from "./icons";
import { LogDialog, lineTone, markMatches } from "./LogDialog";
import { isActive } from "./NavDrawer";
import { ALL_ITEMS, INSPECT_REDIRECTS, NAV } from "./nav";
import { matchItems } from "./PageSearch";
import { Confirm, Spark } from "./ui";

describe("icons", () => {
  it("parses only the four shape tags and known attributes", () => {
    const shapes = parseIcon("<path d='M1 1'/><script src='x'/><circle cx='1' cy='2' r='3' onload='x'/>");
    expect(shapes.map((s) => s.tag)).toEqual(["path", "circle"]);
    expect(shapes[1]?.attrs).toEqual({ cx: "1", cy: "2", r: "3" });
  });

  it("every icon in the set yields at least one shape", () => {
    const empty = Object.entries(ICONS).filter(([, markup]) => parseIcon(markup).length === 0);
    expect(empty).toEqual([]);
  });

  it("renders an svg with the parsed shapes and no injected HTML", () => {
    const { container } = render(<Icon name="check" />);
    const svg = container.querySelector("svg.ic");
    expect(svg).not.toBeNull();
    expect(svg?.querySelectorAll("path").length).toBe(1);
  });
});

describe("navigation map", () => {
  it("has no dotted path segments (DEBT-014) and unique keys", () => {
    for (const it of ALL_ITEMS) expect(it.to.split("/").some((seg) => seg.includes("."))).toBe(false);
    expect(new Set(ALL_ITEMS.map((i) => i.key)).size).toBe(ALL_ITEMS.length);
  });

  it("sends every v2 Inspect tab to a page that is in the navigation", () => {
    const targets = new Set(ALL_ITEMS.map((i) => i.to));
    expect(INSPECT_TAB_KEYS.length).toBe(13);
    for (const key of INSPECT_TAB_KEYS) expect(targets.has(INSPECT_REDIRECTS[key]?.to ?? "")).toBe(true);
  });

  it("old tabs that became a tab of a page land on that tab, with a value the page accepts", async () => {
    const { inspectTarget } = await import("./nav");
    expect(inspectTarget("uart")).toEqual({ to: "/logs", search: { tab: "serial" } });
    expect(inspectTarget("log-search")).toEqual({ to: "/logs", search: { tab: "search" } });
    expect(inspectTarget("logcat")).toEqual({ to: "/logs", search: { tab: "logcat" } });
    expect(inspectTarget("ui-dump")).toEqual({ to: "/screen", search: { tab: "tree" } });
    expect(inspectTarget("screenshot")).toEqual({ to: "/screen", search: { tab: "shot" } });
    expect(inspectTarget("flash")).toEqual({ to: "/flash", search: {} });
    expect(inspectTarget("no-such-tab")).toEqual({ to: "/dashboard", search: {} });
    expect(inspectTarget(undefined)).toEqual({ to: "/dashboard", search: {} });
    // the tab values each page reads (LogsPage TABS, ScreenPage)
    const accepted: Record<string, string[]> = { "/logs": ["logcat", "dmesg", "serial", "search"], "/screen": ["shot", "tree"] };
    for (const key of INSPECT_TAB_KEYS) {
      const r = INSPECT_REDIRECTS[key];
      if (r?.tab) expect(accepted[r.to] ?? []).toContain(r.tab);
    }
  });

  it("the router redirects /inspect/<key> and /inspect?tab=<key> to the page and tab", async () => {
    const { createMemoryHistory, createRouter } = await import("@tanstack/react-router");
    const { routeTree } = await import("../../router");
    const land = async (url: string) => {
      const r = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) });
      await r.load();
      return { path: r.state.location.pathname, search: r.state.location.search };
    };
    expect(await land("/inspect/uart")).toEqual({ path: "/logs", search: { tab: "serial" } });
    expect(await land("/inspect/log-search")).toEqual({ path: "/logs", search: { tab: "search" } });
    expect(await land("/inspect/ui-dump")).toEqual({ path: "/screen", search: { tab: "tree" } });
    expect(await land("/inspect?tab=ui-dump")).toEqual({ path: "/screen", search: { tab: "tree" } });
    expect(await land("/inspect?tab=flash")).toEqual({ path: "/flash", search: {} });
    expect((await land("/inspect")).path).toBe("/dashboard");
    expect((await land("/inspect?tab=nope")).path).toBe("/dashboard");
    expect((await land("/inspect/nope")).path).toBe("/dashboard");
  });

  it("highlights a page and its sub paths, not a sibling that shares a prefix", () => {
    expect(isActive("/sessions", "/sessions")).toBe(true);
    expect(isActive("/sessions/abc", "/sessions")).toBe(true);
    expect(isActive("/sessionsx", "/sessions")).toBe(false);
  });

  it("groups are Device, Flash, Bench, Assistant, System", () => {
    expect(NAV.map((g) => g.en)).toEqual(["Device", "Flash", "Bench", "Assistant", "System"]);
  });
});

describe("page search", () => {
  it("matches Chinese names, English names and keywords", () => {
    expect(matchItems("时间轴").map((i) => i.key)).toEqual(["timeline"]);
    expect(matchItems("flash").map((i) => i.key)).toContain("flash");
    expect(matchItems("logcat").map((i) => i.key)).toContain("logs");
    expect(matchItems("截屏").map((i) => i.key)).toEqual(["screen"]);
  });

  it("an empty query lists every page; a nonsense query lists none", () => {
    expect(matchItems("").length).toBe(ALL_ITEMS.length);
    expect(matchItems("zzzz-no-such-page")).toEqual([]);
  });
});

describe("i18n helpers", () => {
  it("picks the language", () => {
    expect(pick("zh", "总览", "Overview")).toBe("总览");
    expect(pick("en", "总览", "Overview")).toBe("Overview");
  });

  it("formats relative time and durations in both languages", () => {
    expect(ago("en", 42)).toBe("42 s ago");
    expect(ago("zh", 42)).toBe("42 秒前");
    expect(ago("en", 7200)).toBe("2 h ago");
    expect(duration("en", 5 * 86400 + 3 * 3600)).toBe("5 d 3 h");
    expect(duration("zh", 5 * 86400 + 3 * 3600)).toBe("5 天 3 小时");
    expect(duration("zh", 125)).toBe("2 分 5 秒");
  });
});

describe("Confirm", () => {
  it("renders nothing while closed", () => {
    const { container } = render(<Confirm open={false} message="m" confirmLabel="Go" cancelLabel="No" onConfirm={() => {}} onCancel={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it("commits on the confirm button and cancels on Escape", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<Confirm open message="Reboot board-a" confirmLabel="Reboot" cancelLabel="Cancel" onConfirm={onConfirm} onCancel={onCancel} />);
    expect(screen.getByRole("alert").textContent).toContain("Reboot board-a");
    fireEvent.click(screen.getByRole("button", { name: "Reboot" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe("Spark", () => {
  it("draws nothing for fewer than two points and a line plus a dot otherwise", () => {
    const { container: a } = render(<Spark lines={[[1]]} lo={0} hi={2} />);
    expect(a.querySelectorAll("path").length).toBe(0);
    const { container: b } = render(<Spark lines={[[0, 1, 2]]} lo={0} hi={2} />);
    expect(b.querySelectorAll("path").length).toBe(2);
    expect(b.querySelectorAll("circle").length).toBe(1);
  });
});

describe("LogDialog", () => {
  const lines = ["09:12 boot ok", "09:13 job 41: FAILED (remote: nope)", "09:14 warning: slow", "09:15 Failed again"];

  it("renders nothing while closed", () => {
    const { container } = render(<LogDialog open={false} onClose={() => {}} title="Log" lines={lines} fileName="x.log" />);
    expect(container.innerHTML).toBe("");
    expect(document.querySelector(".dialog")).toBeNull();
  });

  it("shows every line numbered, marks errors and warnings, highlights the keyword and closes on Escape", () => {
    const onClose = vi.fn();
    render(<LogDialog open onClose={onClose} title="Bench logs" lines={lines} fileName="x.log" highlight="failed" />);
    const dialog = screen.getByRole("dialog", { name: "Bench logs" });
    const rows = dialog.querySelectorAll(".term--lines > div");
    expect(rows.length).toBe(4);
    expect(rows[1]?.querySelector("i")?.textContent).toBe("2");
    expect(rows[1]?.className).toBe("e");
    expect(rows[2]?.className).toBe("w");
    expect(Array.from(dialog.querySelectorAll("mark")).map((m) => m.textContent)).toEqual(["FAILED", "Failed"]);
    expect(dialog.textContent).toContain("4 lines");
    expect(dialog.textContent).toContain('2 lines contain "failed"');
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("wrap toggles off to a horizontally scrolling log", () => {
    render(<LogDialog open onClose={() => {}} title="Log" lines={lines} fileName="x.log" />);
    const wrap = screen.getByRole("button", { name: "Wrap lines" });
    expect(wrap).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(wrap);
    expect(wrap).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelector(".term--nowrap")).not.toBeNull();
  });

  it("lineTone and markMatches", () => {
    expect(lineTone("E/fastboot: error 5")).toBe("e");
    expect(lineTone("terror")).toBeUndefined();
    expect(markMatches("abc", "")).toBe("abc");
    expect(markMatches("abc", "zz")).toBe("abc");
  });
});

describe("copyText", () => {
  it("falls back to a selection copy when navigator.clipboard is missing (plain http on the LAN)", async () => {
    const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const exec = vi.fn(() => true);
    const had = document.execCommand;
    document.execCommand = exec as unknown as typeof document.execCommand;
    try {
      expect(await copyText("hello")).toBe(true);
      expect(exec).toHaveBeenCalledWith("copy");
      expect(document.querySelector("textarea")).toBeNull();
    } finally {
      document.execCommand = had;
      if (original) Object.defineProperty(navigator, "clipboard", original);
    }
  });
});
