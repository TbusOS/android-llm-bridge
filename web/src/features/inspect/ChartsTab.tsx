/**
 * Inspect → Charts — live device telemetry (PR-F).
 *
 * Replaces the previous mock-driven 6-chart grid with a real
 * `WS /metrics/stream` subscription. 6 cards driven off
 * `useMetricsStream`:
 *
 *   CPU % · CPU temp °C · MemUsed % · GPU util %
 *   Battery temp °C · Network rx/tx KB/s
 *
 * Connect / Pause / Disconnect controls map directly to the WS
 * control frames the metrics_route protocol exposes.
 */

import { CircleStop, Pause, Play } from "lucide-react";
import { useApp } from "../../stores/app";
import { Sparkline } from "../dashboard/Sparkline";
import { type MetricSample, useMetricsStream } from "./useMetricsStream";

const SPARK_HEIGHT = 88;

export function ChartsTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const m = useMetricsStream();

  if (!device) {
    return (
      <div className="mock-card">
        <h1 style={{ fontSize: 22 }}>{lang === "zh" ? "实时图表" : "Charts"}</h1>
        <p className="section-sub">
          {lang === "zh"
            ? "顶栏选一台设备再回这里。"
            : "Pick a device from the top-bar picker, then come back."}
        </p>
      </div>
    );
  }

  const last = m.samples[m.samples.length - 1];
  const isLive = m.state === "ready" || m.state === "connecting";

  return (
    <>
      <div className="uart-tab__bar" style={{ marginTop: "var(--space-3)" }}>
        {!isLive ? (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => m.connect(device, 60)}
          >
            <Play size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "连接" : "Connect"}
          </button>
        ) : (
          <button type="button" className="btn" onClick={m.disconnect}>
            <CircleStop size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "断开" : "Disconnect"}
          </button>
        )}
        {isLive && (
          <button
            type="button"
            className="btn"
            onClick={() => (m.paused ? m.resume() : m.pause())}
          >
            <Pause size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {m.paused
              ? lang === "zh" ? "继续" : "Resume"
              : lang === "zh" ? "暂停" : "Pause"}
          </button>
        )}
        <span className={
          m.state === "ready" ? "uart-tab__state uart-tab__state--ok"
            : m.state === "connecting" ? "uart-tab__state uart-tab__state--warn"
              : m.state === "error" ? "uart-tab__state uart-tab__state--err"
                : "uart-tab__state"
        }>
          ● {labelState(m.state, m.paused, lang)}
        </span>
        <span className="uart-tab__last">
          {m.samples.length} samples · interval {m.intervalS}s
        </span>
        {m.error && (
          <span className="uart-tab__last uart-tab__last--err">{m.error}</span>
        )}
      </div>

      <div className="charts-grid">
        <ChartCard
          title={lang === "zh" ? "CPU 占用" : "CPU usage"}
          big={fmtPct(last?.cpu_pct_total)}
          unit="%"
          spark={mapSpark(m.samples, (s) => s.cpu_pct_total, 100)}
          color="orange"
          caption={lang === "zh" ? "全核累计" : "all-core total"}
        />
        <ChartCard
          title={lang === "zh" ? "CPU 温度" : "CPU temp"}
          big={fmtTemp(last?.cpu_temp_c)}
          unit="°C"
          spark={mapSpark(m.samples, (s) => s.cpu_temp_c, 100)}
          color="blue"
          caption={lang === "zh" ? "thermal zones max" : "thermal zones max"}
        />
        <ChartCard
          title={lang === "zh" ? "内存用量" : "Memory used"}
          big={fmtMemPct(last)}
          unit="%"
          spark={mapSpark(m.samples, (s) => memPct(s), 100)}
          color="green"
          caption={
            last
              ? `${(last.mem_used_kb / 1024 / 1024).toFixed(1)} / ${(last.mem_total_kb / 1024 / 1024).toFixed(1)} GB`
              : "—"
          }
        />
        <ChartCard
          title={lang === "zh" ? "GPU 利用率" : "GPU util"}
          big={fmtGpu(last?.gpu_util_pct)}
          unit="%"
          spark={mapSpark(m.samples, (s) => Math.max(0, s.gpu_util_pct), 100)}
          color="orange"
          caption={
            last && last.gpu_freq_hz > 0
              ? `${(last.gpu_freq_hz / 1_000_000).toFixed(0)} MHz`
              : "—"
          }
        />
        <ChartCard
          title={lang === "zh" ? "电池温度" : "Battery temp"}
          big={fmtTemp(last?.battery_temp_c)}
          unit="°C"
          spark={mapSpark(m.samples, (s) => s.battery_temp_c, 60)}
          color="blue"
          caption=""
        />
        <ChartCard
          title={lang === "zh" ? "网络 rx" : "Net RX"}
          big={fmtBps(last?.net_rx_bytes_per_s)}
          unit="KB/s"
          spark={mapSpark(
            m.samples,
            (s) => s.net_rx_bytes_per_s / 1024,
            // dynamic max via highest sample so the line uses the full
            // 88-px height
            Math.max(1, ...m.samples.map((s) => s.net_rx_bytes_per_s / 1024)),
          )}
          color="green"
          caption={
            last
              ? `tx ${(last.net_tx_bytes_per_s / 1024).toFixed(1)} KB/s`
              : ""
          }
        />
      </div>
    </>
  );
}

interface ChartCardProps {
  title: string;
  big: string;
  unit: string;
  spark: number[];
  color: "blue" | "green" | "orange";
  caption: string;
}

function ChartCard({ title, big, unit, spark, color, caption }: ChartCardProps) {
  return (
    <div className="chart-card">
      <h3>{title}</h3>
      <div className="chart-num">
        {big}
        <span className="chart-unit">{unit}</span>
      </div>
      <div style={{ height: SPARK_HEIGHT }}>
        <Sparkline
          points={spark}
          color={color}
          className="chart-spark"
          ariaLabel={title}
        />
      </div>
      {caption && <div className="chart-foot">{caption}</div>}
    </div>
  );
}

function memPct(s: MetricSample | undefined): number {
  if (!s || s.mem_total_kb <= 0) return 0;
  return Math.round((s.mem_used_kb / s.mem_total_kb) * 100);
}

function mapSpark(
  samples: MetricSample[],
  pick: (s: MetricSample) => number,
  scaleMax: number,
): number[] {
  if (samples.length === 0) return [];
  const norm = scaleMax > 0 ? scaleMax : 1;
  return samples.map((s) => {
    const v = pick(s);
    if (!isFinite(v) || v < 0) return 0;
    return Math.min(100, (v / norm) * 100);
  });
}

function fmtPct(v?: number): string {
  return typeof v === "number" ? v.toFixed(0) : "—";
}

function fmtTemp(v?: number): string {
  return typeof v === "number" && v > 0 ? v.toFixed(1) : "—";
}

function fmtMemPct(s?: MetricSample): string {
  if (!s || s.mem_total_kb <= 0) return "—";
  return `${memPct(s)}`;
}

function fmtGpu(v?: number): string {
  if (typeof v !== "number" || v < 0) return "—";
  return v.toFixed(0);
}

function fmtBps(v?: number): string {
  if (typeof v !== "number") return "—";
  return (v / 1024).toFixed(1);
}

function labelState(
  s: "idle" | "connecting" | "ready" | "ended" | "error",
  paused: boolean,
  lang: string,
): string {
  if (paused) return lang === "zh" ? "暂停" : "paused";
  return s;
}
