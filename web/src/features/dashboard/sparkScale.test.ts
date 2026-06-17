import { describe, expect, it } from "vitest";

import { scaleSparkPoints } from "./sparkScale";

describe("scaleSparkPoints (ADR-049 shared per-series scaler)", () => {
  it("returns [] for empty input", () => {
    expect(scaleSparkPoints([], 32, 10)).toEqual([]);
  });

  it("maps the peak to y=0 (top) and zero to y=height (bottom)", () => {
    const ys = scaleSparkPoints([0, 20], 32, 10);
    // peak = max(10, 20) = 20 → 20 maps to top (0), 0 maps to bottom (32)
    expect(ys[0]).toBe(32);
    expect(ys[1]).toBe(0);
  });

  it("respects the minCeiling floor so a quiet series isn't amplified", () => {
    // value 5 scales against the floor (10), not its own max
    expect(scaleSparkPoints([5], 32, 10)[0]).toBe(16); // 32 * (1 - 5/10)
  });

  it("scales to the requested height", () => {
    expect(scaleSparkPoints([0, 10], 100, 10)).toEqual([100, 0]);
  });

  it("is NaN/Infinity-safe (DEBT-030): non-finite values are dropped", () => {
    const ys = scaleSparkPoints([NaN, 10, Infinity, 5], 32, 10);
    expect(ys).toHaveLength(2);
    expect(ys.every(Number.isFinite)).toBe(true);
  });
});
