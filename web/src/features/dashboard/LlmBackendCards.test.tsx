/**
 * LlmBackendCards spec — ADR-049 / round10 MBC-3: a reachable backend
 * card renders a per-backend token-throughput sparkline; one with no
 * recent samples shows the flat dashed baseline; a non-reachable backend
 * has no spark.
 */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: any) => any) => {
    const state = { lang: "en" };
    return selector ? selector(state) : state;
  }),
}));
vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));

import { LlmBackendCards } from "./LlmBackendCards";
import type { BackendRuntimeState } from "./useBackends";
import type { BackendCardData } from "./types";

function cards(name: string): BackendCardData[] {
  return [{ name, model: "qwen2.5", status: "up" }];
}

describe("LlmBackendCards throughput sparkline", () => {
  it("renders a polyline spark for an up backend with throughput", () => {
    const runtime: Record<string, BackendRuntimeState> = {
      ollama: {
        kind: "up",
        latencyMs: 5,
        model: "qwen2.5",
        throughput: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      },
    };
    const { container } = render(
      <LlmBackendCards backends={cards("ollama")} runtime={runtime} />,
    );
    const spark = container.querySelector(".be-spark");
    expect(spark).not.toBeNull();
    expect(spark!.querySelector("polyline")).not.toBeNull();
  });

  it("renders the dashed empty baseline for an up backend with no samples", () => {
    const runtime: Record<string, BackendRuntimeState> = {
      ollama: { kind: "up", latencyMs: 5, model: "qwen2.5" },
    };
    const { container } = render(
      <LlmBackendCards backends={cards("ollama")} runtime={runtime} />,
    );
    const spark = container.querySelector(".be-spark");
    expect(spark).not.toBeNull();
    expect(spark!.querySelector("polyline")).toBeNull(); // dashed <line>, no line series
    expect(spark!.querySelector("line")).not.toBeNull();
  });

  it("renders no sparkline for a non-reachable backend", () => {
    const runtime: Record<string, BackendRuntimeState> = {
      ollama: { kind: "down", reason: "down", error: "connection refused" },
    };
    const { container } = render(
      <LlmBackendCards backends={cards("ollama")} runtime={runtime} />,
    );
    expect(container.querySelector(".be-spark")).toBeNull();
  });
});
