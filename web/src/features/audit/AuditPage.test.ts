/**
 * AuditPage focused specs.
 *
 * Covers the pure-function boundary `formatTs`: the module-level
 * Intl.DateTimeFormat cache must keep byte-identical output to a
 * per-call `Date.toLocaleString(locale, options)` — that equivalence
 * is the whole correctness contract of the formatter cache, so it is
 * asserted directly against the toLocaleString reference (TZ-agnostic)
 * plus a shape check for the rendered pattern.
 *
 * Full AuditPage component spec (useApp / useAuditStream mocks) is
 * not here yet — same trade-off as PlaygroundPage.test.ts.
 */
import { describe, expect, it } from "vitest";

import { formatTs } from "./AuditPage";

const REF_OPTIONS: Intl.DateTimeFormatOptions = {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
};

const SAMPLES = [
  "2026-06-10T08:05:09Z",
  "2026-01-31T23:59:59.123+08:00",
  "2026-12-01T00:00:00Z",
] as const;

describe("formatTs", () => {
  it("zh output is byte-identical to Date.toLocaleString('zh-CN', …)", () => {
    for (const iso of SAMPLES) {
      expect(formatTs(iso, "zh")).toBe(
        new Date(iso).toLocaleString("zh-CN", REF_OPTIONS),
      );
    }
  });

  it("en output is byte-identical to Date.toLocaleString('en-US', …)", () => {
    for (const iso of SAMPLES) {
      expect(formatTs(iso, "en")).toBe(
        new Date(iso).toLocaleString("en-US", REF_OPTIONS),
      );
    }
  });

  it("renders the MM/DD HH:mm:ss table shape", () => {
    // zh-CN joins date and time with a space; en-US adds a comma.
    expect(formatTs(SAMPLES[0], "zh")).toMatch(
      /^\d{2}\/\d{2} \d{2}:\d{2}:\d{2}$/,
    );
    expect(formatTs(SAMPLES[0], "en")).toMatch(
      /^\d{2}\/\d{2}, \d{2}:\d{2}:\d{2}$/,
    );
  });

  it("passes unparsable timestamps through unchanged", () => {
    expect(formatTs("not-a-date", "en")).toBe("not-a-date");
    expect(formatTs("not-a-date", "zh")).toBe("not-a-date");
  });
});
