/**
 * deriveOverallBackendStatus — coarse status for the global LLM pill (UI-2).
 */
import { describe, expect, it } from "vitest";

import {
  deriveOverallBackendStatus,
  type UseBackendsResult,
} from "./useBackends";

function mk(partial: Partial<UseBackendsResult>): UseBackendsResult {
  return {
    backends: [],
    runtime: {},
    isLoading: false,
    isError: false,
    error: null,
    ...partial,
  };
}

describe("deriveOverallBackendStatus", () => {
  it("manifest error → down", () => {
    expect(deriveOverallBackendStatus(mk({ isError: true }))).toBe("down");
  });

  it("manifest still loading → checking", () => {
    expect(deriveOverallBackendStatus(mk({ isLoading: true }))).toBe("checking");
  });

  it("any backend reachable → up (planned/down siblings ignored)", () => {
    expect(
      deriveOverallBackendStatus(
        mk({
          runtime: {
            a: { kind: "down", reason: null, error: null },
            b: { kind: "up", latencyMs: 5, model: "m" },
            p: { kind: "planned" },
          },
        }),
      ),
    ).toBe("up");
  });

  it("all probeable backends down → down (planned ignored)", () => {
    expect(
      deriveOverallBackendStatus(
        mk({
          runtime: {
            a: { kind: "down", reason: null, error: "x" },
            p: { kind: "planned" },
          },
        }),
      ),
    ).toBe("down");
  });

  it("a probe still loading and none up yet → checking", () => {
    expect(
      deriveOverallBackendStatus(
        mk({
          runtime: {
            a: { kind: "loading" },
            b: { kind: "down", reason: null, error: null },
          },
        }),
      ),
    ).toBe("checking");
  });

  it("only planned / unprobed → checking (unknown, never a false green)", () => {
    expect(
      deriveOverallBackendStatus(
        mk({ runtime: { a: { kind: "planned" }, b: { kind: "unprobed" } } }),
      ),
    ).toBe("checking");
  });
});
