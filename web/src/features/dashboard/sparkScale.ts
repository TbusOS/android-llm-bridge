/**
 * Scale raw rate samples (oldest → newest) to SVG Y-coordinates for the
 * `Sparkline` component: 0 = top of the spark, `height` = bottom (SVG Y
 * grows downward, so a higher rate maps to a smaller y). The ceiling is
 * dynamic — a small series (e.g. 3 tok/s on a heavy model) still shows a
 * profile — with a floor (`minCeiling`) so pure noise isn't amplified.
 *
 * NaN-safe (DEBT-030 / L-030): explicit `Number.isFinite` filter, because
 * JS `Math.max` propagates NaN unconditionally — a single NaN would make
 * every point NaN and produce `<polyline points="x,NaN ...">`. This is the
 * mapping originally written for the live-session card; extracted so the
 * dashboard be-card throughput sparkline (ADR-049) reuses the exact same
 * battle-tested, per-series scaling instead of re-deriving it.
 */
export function scaleSparkPoints(
  samples: number[],
  height: number,
  minCeiling: number,
): number[] {
  const finite = samples.filter(Number.isFinite);
  if (finite.length === 0) return [];
  const peak = Math.max(minCeiling, ...finite);
  return finite.map((v) => {
    const norm = peak > 0 ? v / peak : 0;
    return Math.max(0, Math.min(height, height * (1 - norm)));
  });
}
