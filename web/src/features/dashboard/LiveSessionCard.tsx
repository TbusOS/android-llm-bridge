/**
 * Live agent session hero card — pulse dot, prompt italic, tool call
 * timeline (done / running / err), token throughput sparkline, model
 * + total-tokens corner caption.  Lifted from
 * docs/webui-preview-v2.html .live-card.
 *
 * Data is currently MOCK_LIVE; TODO: subscribe to /chat/ws for the
 * active session and stream events into a Zustand slice.
 */
import { Loader2 } from "lucide-react";
import { useApp } from "../../stores/app";
import { Sparkline } from "./Sparkline";
import type { LiveSessionData } from "./types";

export type LiveSessionStreamStatus =
  | "connecting"
  | "open"
  | "closed"
  | "error";

interface Props {
  data: LiveSessionData;
  /** WS status of the metric stream feeding this card. When the stream
   *  drops, the spark would otherwise freeze silently — we surface a
   *  small inline label so the user knows the spark isn't fresh. */
  streamStatus?: LiveSessionStreamStatus;
  onInterrupt?: () => void;
}

export function LiveSessionCard({ data, streamStatus, onInterrupt }: Props) {
  const lang = useApp((s) => s.lang);

  const streamWarning =
    streamStatus === "closed" || streamStatus === "error";

  if (!data.active) {
    return (
      <article className="live-card is-idle" aria-live="polite">
        <div className="live-head">
          <span
            className="live-pulse"
            style={{ background: "var(--anth-mid-gray)", animation: "none" }}
            aria-hidden={true}
          />
          <span className="live-label">
            {lang === "zh" ? "无活动会话" : "No live session"}
          </span>
        </div>
        <div className="live-empty">
          {lang === "zh"
            ? "当前没有 agent 在跑。新建一个 Chat 试试。"
            : "Nothing running right now. Start a Chat to see it stream here."}
        </div>
      </article>
    );
  }

  return (
    <article className="live-card" aria-live="polite">
      <div className="live-head">
        <span className="live-pulse" aria-hidden={true} />
        <span className="live-label">
          {lang === "zh" ? "Agent 进行中" : "Live agent session"}
        </span>
        <span className="live-device">
          <span className="dot" />
          {data.deviceId} · {data.deviceTransport}
        </span>
        <span className="live-elapsed">
          {lang === "zh"
            ? `第 ${data.turn} 轮 · ${data.elapsedHumanZh}`
            : `turn ${data.turn} · ${data.elapsedHuman}`}
        </span>
        <button
          type="button"
          className="live-btn"
          onClick={onInterrupt}
          disabled={!onInterrupt}
        >
          {lang === "zh" ? "中断" : "Interrupt"}
        </button>
      </div>

      <p className="live-prompt">{lang === "zh" ? data.promptZh : data.prompt}</p>

      <div className="live-tools">
        {data.tools.map((tool, idx) => (
          <div
            key={`${tool.name}-${idx}`}
            className={`live-tool live-tool--${tool.state}`}
          >
            <span className="live-tool-icon">
              {tool.state === "running" ? (
                <Loader2 size={14} className="spin" />
              ) : tool.state === "err" ? (
                "✕"
              ) : (
                "✓"
              )}
            </span>
            <span className="live-tool-name">
              {tool.name}
              <span className="live-tool-arg">{tool.args}</span>
            </span>
            <span className="live-tool-time">{tool.elapsedSec.toFixed(1)} s</span>
          </div>
        ))}
      </div>

      <div className="live-throughput">
        <span className="live-tps">
          {data.tps}
          {/* "now" / "现" makes the semantic split with the KPI's
           * "5m avg" explicit (F.6 arch review #4). LiveCard shows
           * the latest 1 s sample; KPI shows the windowed mean. */}
          <span className="unit">{lang === "zh" ? "tok/s · 现" : "tok/s now"}</span>
          {streamWarning ? (
            <span
              className="live-tps-stale"
              title={
                streamStatus === "error"
                  ? lang === "zh"
                    ? "事件流错误"
                    : "metric stream error"
                  : lang === "zh"
                    ? "事件流断开 · 重连中"
                    : "stream offline · reconnecting"
              }
              style={{
                marginLeft: "var(--space-2)",
                fontSize: "11px",
                color: "var(--anth-danger)",
                fontFamily: "var(--font-body)",
                fontWeight: 400,
              }}
            >
              {lang === "zh" ? "● 离线" : "● stale"}
            </span>
          ) : null}
        </span>
        <Sparkline
          points={data.tpsSpark}
          width={280}
          height={36}
          color="blue"
          strokeWidth={2}
          fillTint
          className="live-tps-spark"
          ariaLabel={
            lang === "zh"
              ? "近 60 秒 token 吞吐"
              : "Token throughput last 60 s"
          }
        />
        <span className="live-tps-meta">
          {data.modelName}
          <br />
          {data.totalTokens} tok
        </span>
      </div>
    </article>
  );
}
