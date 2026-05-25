/**
 * useElapsedSeconds — hook that returns "seconds elapsed since the
 * caller marked itself as active" for a long-running op.
 *
 * Pattern:
 *
 *   const elapsed = useElapsedSeconds(mut.isPending);
 *   // …
 *   {mut.isPending ? `collecting… (${elapsed}s)` : "collect"}
 *
 * When `active` flips to true, the timer starts at 0 and ticks every
 * second. When `active` flips back to false, the timer stops at the
 * last value (so the final duration stays visible briefly). Becoming
 * active again resets to 0.
 *
 * Why a hook instead of inline setInterval: avoids leaking intervals
 * when the consumer unmounts or `active` flips fast, and centralises
 * the "0s before first tick" UX choice (we render `0s` immediately so
 * the user sees something the moment they click, not blank for ~1 s).
 */
import { useEffect, useState } from "react";

export function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!active) return;
    setElapsed(0);
    const startedAt = Date.now();
    const id = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => {
      window.clearInterval(id);
    };
  }, [active]);

  return elapsed;
}
