/** Pure helpers behind the tool card and the files row. */
import { describe, expect, it } from "vitest";

import { argsSummary, fileKind, firstOutputLine, logText, parseResult, pretty, screenshotRef, shortId, toLines, toolError, workspaceRel } from "./toolText";

describe("tool text", () => {
  it("argsSummary is one line and clipped; pretty keeps everything, indented", () => {
    const args = { cmd: "ls -l\n/data", nested: { a: 1 }, long: "x".repeat(200) };
    const s = argsSummary(args);
    expect(s).not.toContain("\n");
    expect(s.startsWith('cmd=ls -l /data nested={"a":1}')).toBe(true);
    expect(s.endsWith("…")).toBe(true);
    expect(s.length).toBeLessThanOrEqual(97);
    const full = pretty(args);
    expect(full).toContain('"nested": {\n    "a": 1\n  }');
    expect(full).toContain("x".repeat(200));
    // history stores arguments as a JSON string: re-indented, plain text left alone
    expect(pretty('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(pretty("not { json")).toBe("not { json");
    expect(argsSummary(undefined)).toBe("");
  });

  it("logText finds the log inside the result envelopes the tools return", () => {
    expect(logText("plain")).toBe("plain");
    expect(logText({ ok: true, data: "tail\nlines" })).toBe("tail\nlines");
    expect(logText({ ok: true, data: { stdout: "out", stderr: "err", exit_code: 1 } })).toBe("out\nerr");
    expect(logText({ ok: true, data: { lines: ["a", "b"] } })).toBe("a\nb");
    expect(logText({ ok: true, data: { lines: 40, errors: 2 } })).toBeNull();
    expect(logText([1, 2])).toBeNull();
    expect(firstOutputLine({ ok: true, data: "\n\n  first real line  \nsecond" })).toBe("first real line");
    expect(parseResult('{"ok":true,"data":"x"}')).toEqual({ ok: true, data: "x" });
    expect(parseResult("Kernel panic")).toBe("Kernel panic");
    expect(toLines("a\r\nb\n")).toEqual(["a", "b"]);
    expect(toLines("")).toEqual([]);
  });

  it("toolError reads the {ok:false, error} envelope only", () => {
    expect(toolError({ ok: false, error: { code: "DEVICE_NOT_FOUND", message: "gone" } })).toEqual({ code: "DEVICE_NOT_FOUND", message: "gone" });
    expect(toolError({ ok: false })).toEqual({ code: "ERROR", message: "" });
    expect(toolError({ ok: true, error: null })).toBeNull();
    expect(toolError("ok: false")).toBeNull();
  });

  it("workspaceRel maps absolute paths under the workspace and refuses the rest", () => {
    expect(workspaceRel("/srv/ws/devices/b1/logs/a.txt", "/srv/ws")).toBe("devices/b1/logs/a.txt");
    expect(workspaceRel("/srv/ws/devices/b1/logs/a.txt", "/srv/ws/")).toBe("devices/b1/logs/a.txt");
    // tool wrote through a symlinked parent; the hub reports the resolved root
    expect(workspaceRel("/mnt/link/proj/workspace/devices/b1/x.log", "/data/proj/workspace")).toBe("devices/b1/x.log");
    expect(workspaceRel("C:\\alb\\ws\\logs\\a.txt", "C:\\alb\\ws")).toBe("logs/a.txt");
    expect(workspaceRel("/tmp/elsewhere/a.txt", "/srv/ws")).toBeNull();
    expect(workspaceRel("/srv/ws/../etc/passwd", "/srv/ws")).toBeNull();
    expect(workspaceRel("/srv/ws/a.txt", null)).toBeNull();
    // a prefix that is only part of a folder name does not count
    expect(workspaceRel("/srv/ws2/a.txt", "/srv/ws")).toBeNull();
  });

  it("file kinds, screenshot paths and short ids", () => {
    expect(fileKind("shot.PNG")).toBe("image");
    expect(fileKind("bugreport.zip")).toBe("binary");
    expect(fileKind("logcat.txt")).toBe("text");
    expect(fileKind("dmesg-2026.log")).toBe("text");
    expect(screenshotRef("devices/b1/screenshots/s1.png")).toEqual({ serial: "b1", name: "s1.png" });
    expect(screenshotRef("devices/b1/logs/s1.png")).toBeNull();
    expect(shortId("20261028-aaaa1111bbbb")).toBe("20261028…bbbb");
    expect(shortId("short")).toBe("short");
  });
});
