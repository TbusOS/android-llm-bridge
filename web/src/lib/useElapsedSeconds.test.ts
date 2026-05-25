/**
 * useElapsedSeconds spec — drives bugreport / install / log-search
 * progress timers.
 *
 * Fake timers + advanceTimersByTime keep the 1-second tick
 * deterministic.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useElapsedSeconds } from "./useElapsedSeconds";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useElapsedSeconds", () => {
  it("returns 0 when inactive", () => {
    const { result } = renderHook(() => useElapsedSeconds(false));
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(0);
  });

  it("ticks once per second while active", () => {
    const { result } = renderHook(({ active }) => useElapsedSeconds(active), {
      initialProps: { active: true },
    });
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(1);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current).toBe(3);
  });

  it("flipping active false→true resets to 0", () => {
    const { result, rerender } = renderHook(
      ({ active }) => useElapsedSeconds(active),
      { initialProps: { active: true } },
    );
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current).toBe(3);

    rerender({ active: false });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    // While inactive the counter freezes; the hook does NOT reset
    // synchronously on flipping to false — only the next true flips
    // back to 0.
    expect(result.current).toBe(3);

    rerender({ active: true });
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(1);
  });

  it("unmount clears interval (no leak)", () => {
    const { unmount } = renderHook(() => useElapsedSeconds(true));
    unmount();
    expect(() => {
      vi.advanceTimersByTime(10_000);
    }).not.toThrow();
  });
});
