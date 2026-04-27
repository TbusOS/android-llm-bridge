/**
 * Inspect → Charts — 6 chart-cards in a charts-grid.  Each card has
 * a title + big mono number + right-aligned caption + 88-px polyline
 * over horizontal dashed gridlines.
 *
 * The network card carries two polylines (rx green + tx blue) and a
 * custom legend foot.  Lifted from docs/webui-preview-v2.html
 * .charts-grid section.
 */
import { useApp } from "../../stores/app";
import { Sparkline } from "../dashboard/Sparkline";
import { MOCK_CHARTS } from "./mockData";
import type { ChartCardData } from "./types";

const CHART_GRID_LINES = [22, 44, 66];

export function ChartsTab() {
  const lang = useApp((s) => s.lang);

  return (
    <div className="charts-grid">
      {MOCK_CHARTS.map((c) => (
        <ChartCard key={c.key} data={c} lang={lang} />
      ))}
    </div>
  );
}

function ChartCard({ data, lang }: { data: ChartCardData; lang: string }) {
  return (
    <article className="chart-card">
      <div className="chart-head">
        <span className="chart-title">{data.title}</span>
        <span className="chart-now">
          {data.nowValue}
          <span className="unit">{data.nowUnit}</span>
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--anth-text-secondary)",
          }}
        >
          {lang === "zh" && data.captionZh ? data.captionZh : data.caption}
        </span>
      </div>

      <ChartSparkBody data={data} />

      {data.footChildren ?? <DefaultFoot lang={lang} legend={data} />}
    </article>
  );
}

function ChartSparkBody({ data }: { data: ChartCardData }) {
  // Two-line variant: render two polylines stacked.  We can't fit both
  // into a single Sparkline call cleanly because Sparkline draws one
  // path; for now overlap two SVGs with the same viewBox.
  if (data.secondSpark && data.secondColor) {
    return (
      <div style={{ position: "relative", width: "100%", height: 88 }}>
        <Sparkline
          points={data.spark}
          width={400}
          height={88}
          color={data.color}
          strokeWidth={2}
          gridLines={CHART_GRID_LINES}
          className="chart-spark"
          style={{ position: "absolute", inset: 0 }}
          ariaLabel={`${data.title} primary`}
        />
        <Sparkline
          points={data.secondSpark}
          width={400}
          height={88}
          color={data.secondColor}
          strokeWidth={2}
          className="chart-spark"
          style={{ position: "absolute", inset: 0 }}
          ariaLabel={`${data.title} secondary`}
        />
      </div>
    );
  }

  return (
    <Sparkline
      points={data.spark}
      width={400}
      height={88}
      color={data.color}
      strokeWidth={2}
      fillTint={data.fillTint}
      gridLines={CHART_GRID_LINES}
      className="chart-spark"
      ariaLabel={`${data.title} 60-second chart`}
    />
  );
}

function DefaultFoot({
  lang,
  legend,
}: {
  lang: string;
  legend: ChartCardData;
}) {
  // Network card gets a 2-color legend, others get the simple
  // -60s … now caption.
  if (legend.secondSpark) {
    return (
      <div className="chart-foot">
        <LegendDot color="#788c5d" /> rx
        <span style={{ width: 12 }} />
        <LegendDot color="#6a9bcc" /> tx
        <span style={{ marginLeft: "auto" }}>now</span>
      </div>
    );
  }
  return (
    <div className="chart-foot">
      <span>{lang === "zh" ? "−60 秒" : "−60 s"}</span>
      <span style={{ marginLeft: "auto" }}>now</span>
    </div>
  );
}

function LegendDot({ color }: { color: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        background: color,
        borderRadius: 2,
        marginRight: 4,
        verticalAlign: "-1px",
      }}
    />
  );
}
