import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ago, duration, pick } from "../../lib/i18n";
import { INSPECT_TAB_KEYS } from "../../router";
import { Icon, parseIcon } from "./Icon";
import { ICONS } from "./icons";
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
    for (const key of INSPECT_TAB_KEYS) expect(targets.has(INSPECT_REDIRECTS[key] ?? "")).toBe(true);
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
