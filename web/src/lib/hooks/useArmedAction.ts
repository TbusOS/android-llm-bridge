/**
 * useArmedAction — generic "two-step confirm" hook for destructive
 * actions like `reboot recovery` and `app uninstall`.
 *
 * Pattern: the first call to ``trigger`` marks the action as **armed**
 * (state visible to the caller, with optional auto-disarm timeout).
 * The second call within the timeout actually fires the underlying
 * action and resets the armed state.  Caller can also call
 * ``disarm()`` for an explicit cancel button.
 *
 * Why a hook (vs N=2 inline implementations across PowerTab + AppTab):
 *   - centralises the armed timeout so we never ship "armed forever"
 *     by accident (see ui-fluency HIGH#3 critique of AppTab uninstall);
 *   - lets each call site share the same default disarm window (8 s)
 *     so users get a consistent mental model;
 *   - keeps the danger semantics (``requiresArm: boolean``) declarative
 *     so non-danger modes (e.g. ``reboot normal``) skip the armed
 *     step entirely.
 *
 * Closes finding ``arch MID#5`` (危险操作 N=2 抽 hook) and unblocks
 * ``ui-fluency HIGH#6`` (AppTab uninstall armed 无 timeout / 无 cancel).
 */
import { useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_DISARM_MS = 8000;

export interface UseArmedActionOptions {
  /** When true, the first ``trigger`` arms but does NOT fire.  When
   *  false (e.g. non-destructive code path), the very first ``trigger``
   *  invokes the action immediately.  Default: ``true``. */
  requiresArm?: boolean;
  /** Milliseconds the armed state stays active before auto-disarming.
   *  Default: 8000.  Pass ``Infinity`` to disable auto-disarm (caller
   *  fully controls). */
  disarmMs?: number;
}

export interface UseArmedActionApi {
  /** True between the first and second ``trigger`` call (or until
   *  ``disarmMs`` elapsed). */
  armed: boolean;
  /** Call once to arm, twice to fire.  Returns ``true`` if the action
   *  actually fired this call, ``false`` if it only armed / no-op'd. */
  trigger: () => boolean;
  /** Explicit cancel — clears the armed flag and the auto-disarm timer. */
  disarm: () => void;
}

export function useArmedAction(
  onFire: () => void,
  opts: UseArmedActionOptions = {},
): UseArmedActionApi {
  const requiresArm = opts.requiresArm ?? true;
  const disarmMs = opts.disarmMs ?? DEFAULT_DISARM_MS;
  const [armed, setArmed] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const disarm = useCallback(() => {
    clearTimer();
    setArmed(false);
  }, [clearTimer]);

  const trigger = useCallback((): boolean => {
    if (!requiresArm) {
      onFire();
      return true;
    }
    if (!armed) {
      setArmed(true);
      clearTimer();
      if (Number.isFinite(disarmMs)) {
        timerRef.current = setTimeout(() => {
          timerRef.current = null;
          setArmed(false);
        }, disarmMs);
      }
      return false;
    }
    clearTimer();
    setArmed(false);
    onFire();
    return true;
  }, [armed, requiresArm, disarmMs, onFire, clearTimer]);

  // Ensure the timer is cleared when the consuming component unmounts
  // (otherwise the setState in the timeout fires on an unmounted
  // component and React logs a warning).
  useEffect(() => () => clearTimer(), [clearTimer]);

  return { armed, trigger, disarm };
}
