/**
 * Tiny inline sparkline — pure SVG polyline, no library.  Dimensions
 * controlled by parent via CSS (className).
 *
 * Coords come in as 0..maxY values; we plot them across the SVG width
 * preserving the original aspect.  Used by device cards (CPU / temp
 * mini-trend) and the Live-session throughput strip.
 */
import type { CSSProperties } from "react";

const COLOR_MAP = {
  blue: "#6a9bcc",
  green: "#788c5d",
  orange: "#d97757",
} as const;

export type SparkColor = keyof typeof COLOR_MAP;

interface Props {
  /** Y-coords (already scaled to fit the SVG viewBox). */
  points: number[];
  /** SVG viewBox width. */
  width?: number;
  /** SVG viewBox height. */
  height?: number;
  color?: SparkColor;
  strokeWidth?: number;
  /** Optional translucent fill under the line. */
  fillTint?: boolean;
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
  /** Render a flat dashed line instead (offline / no data). */
  empty?: boolean;
}

export function Sparkline({
  points,
  width = 100,
  height = 22,
  color = "blue",
  strokeWidth = 1.6,
  fillTint = false,
  className,
  style,
  ariaLabel,
  empty,
}: Props) {
  if (empty || points.length < 2) {
    return (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className={className}
        style={style}
        role="img"
        aria-label={ariaLabel ?? "no data"}
      >
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="#b0aea5"
          strokeWidth={1}
          strokeDasharray="2 3"
        />
      </svg>
    );
  }

  const stroke = COLOR_MAP[color];
  const stepX = points.length > 1 ? width / (points.length - 1) : width;
  const polyPoints = points
    .map((y, i) => `${(i * stepX).toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  const fillPoints = fillTint
    ? `${polyPoints} ${width.toFixed(1)},${height.toFixed(1)} 0,${height.toFixed(1)}`
    : null;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      style={style}
      role="img"
      aria-label={ariaLabel ?? "sparkline"}
    >
      {fillPoints ? (
        <polyline
          points={fillPoints}
          fill={`${stroke}1f`}
          stroke="none"
        />
      ) : null}
      <polyline
        points={polyPoints}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
