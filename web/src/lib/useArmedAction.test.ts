/**
 * useArmedAction spec — covers the two-step confirm semantics that
 * back PowerTab reboot + AppTab uninstall + ScreenshotRow delete.
 *
 * Uses fake timers so the 8s auto-disarm and explicit disarm paths
 * are deterministic; cleanup is essential or vitest leaks timer
 * handles into the next test.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useArmedAction } from "./useArmedAction";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useArmedAction", () => {
  it("initial state is disarmed", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() => useArmedAction(onFire));
    expect(result.current.armed).toBe(false);
    expect(onFire).not.toHaveBeenCalled();
  });

  it("first trigger arms (no fire), second within window fires", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() => useArmedAction(onFire));

    let fired = false;
    act(() => {
      fired = result.current.trigger();
    });
    expect(fired).toBe(false);
    expect(result.current.armed).toBe(true);
    expect(onFire).not.toHaveBeenCalled();

    act(() => {
      fired = result.current.trigger();
    });
    expect(fired).toBe(true);
    expect(result.current.armed).toBe(false);
    expect(onFire).toHaveBeenCalledTimes(1);
  });

  it("auto-disarms after default 8s window", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() => useArmedAction(onFire));

    act(() => {
      result.current.trigger();
    });
    expect(result.current.armed).toBe(true);

    act(() => {
      vi.advanceTimersByTime(7999);
    });
    expect(result.current.armed).toBe(true);

    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(result.current.armed).toBe(false);
    expect(onFire).not.toHaveBeenCalled();
  });

  it("custom disarmMs honoured", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() =>
      useArmedAction(onFire, { disarmMs: 1500 }),
    );

    act(() => {
      result.current.trigger();
    });
    act(() => {
      vi.advanceTimersByTime(1499);
    });
    expect(result.current.armed).toBe(true);
    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(result.current.armed).toBe(false);
  });

  it("disarmMs=Infinity stays armed indefinitely", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() =>
      useArmedAction(onFire, { disarmMs: Infinity }),
    );
    act(() => {
      result.current.trigger();
    });
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current.armed).toBe(true);
  });

  it("disarm() clears armed + timer (no late fire)", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() => useArmedAction(onFire));

    act(() => {
      result.current.trigger();
    });
    act(() => {
      result.current.disarm();
    });
    expect(result.current.armed).toBe(false);

    // Even past the original 8s window nothing fires.
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(onFire).not.toHaveBeenCalled();
  });

  it("requiresArm=false fires immediately on first trigger", () => {
    const onFire = vi.fn();
    const { result } = renderHook(() =>
      useArmedAction(onFire, { requiresArm: false }),
    );
    let fired = false;
    act(() => {
      fired = result.current.trigger();
    });
    expect(fired).toBe(true);
    expect(result.current.armed).toBe(false);
    expect(onFire).toHaveBeenCalledTimes(1);
  });

  it("unmount cancels pending disarm timer (no late setState warn)", () => {
    const onFire = vi.fn();
    const { result, unmount } = renderHook(() => useArmedAction(onFire));

    act(() => {
      result.current.trigger();
    });
    unmount();
    // If the timer wasn't cleared, this would try setState on an
    // unmounted hook and React would log a warning. We just verify
    // no exception thrown.
    expect(() => {
      vi.advanceTimersByTime(10_000);
    }).not.toThrow();
  });
});
