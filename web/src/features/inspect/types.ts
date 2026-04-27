/**
 * Inspect placeholder types.  Matches the future shape of GET
 * /devices/{id}/info/{panel} (system / cpu / storage / memory /
 * network / battery) and GET /metrics/{kind}.
 */
import type { ReactNode } from "react";

export interface KvEntry {
  /** Key — bilingual; `key` is the English form, `keyZh` Chinese.  When
   * keyZh is omitted the same `key` shows in both languages. */
  key: string;
  keyZh?: string;
  value: string;
}

export interface SysCardData {
  title: string;
  titleZh: string;
  entries: KvEntry[];
}

export type PartFillColor = "orange" | "green" | "blue" | "red";

export interface PartitionRowData {
  name: string;
  pct: number; // 0..100
  color?: PartFillColor;
}

export interface StorageCardData {
  title: string;
  titleZh: string;
  partitions: PartitionRowData[];
}

export type ChartColor = "blue" | "green" | "orange";

export interface ChartCardData {
  key: string;
  title: string;
  /** Big-number display, e.g. "42". */
  nowValue: string;
  /** Unit shown next to the number, e.g. "%", "°C", "MB/s". */
  nowUnit: string;
  /** Right-aligned caption — "avg 38 · max 71" / "limit 85 °C". */
  caption: string;
  captionZh?: string;
  /** y-coords (0..88) for the polyline.  Already scaled to chart-spark
   * height. */
  spark: number[];
  color: ChartColor;
  /** Optional translucent fill under the line. */
  fillTint?: boolean;
  /** Second polyline for dual-line charts (network rx + tx). */
  secondSpark?: number[];
  secondColor?: ChartColor;
  /** Custom foot (e.g. legend); when omitted we render "−60 s … now". */
  footChildren?: ReactNode;
}
