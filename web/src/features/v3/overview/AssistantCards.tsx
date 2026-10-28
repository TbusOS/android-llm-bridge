/**
 * The assistant side of the overview: the agent session running right
 * now, the LLM backends and whether they answer, and four counts for
 * the whole alb service (devices online, sessions, MCP tools, tokens per
 * second over the last 5 minutes).
 */
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import { Icon } from "../../../components/v3/Icon";
import { CardHead, Dot, Empty, ErrorBanner, Pill, Spark, type Tone } from "../../../components/v3/ui";
import { useAuditStream } from "../../../lib/hooks/useAuditStream";
import { useDevices } from "../../../lib/hooks/useDevices";
import { duration, useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { backendWord, type BackendState, runningSessions, transportLabel, useBackends, useBackendThroughput, useMetricsSummary, useSessionList, useToolCatalog } from "../../../lib/overviewData";
import { activeSession } from "./liveSession";

/* ---------------- live agent session ---------------- */

/**
 * Owns its own audit subscription with tps_sample included, so the 1 Hz
 * samples re-render only this card. A separate socket from the activity
 * card on purpose: pausing that list must not freeze these numbers.
 */
export function LiveSessionCard() {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const stream = useAuditStream({ includeMetrics: true });
  const live = useMemo(() => activeSession(stream.rawEvents), [stream.rawEvents]);
  const dropped = stream.status === "closed" || stream.status === "error";
  const now = Date.now();
  const hi = Math.max(10, ...(live?.tpsSamples ?? [0]));
  return (
    <article className="card span-7" aria-live="polite">
      <CardHead
        title={t("AI 会话", "Agent session")}
        sub={live ? t("正在跑,来自对话页或命令行", "running now, from the chat page or the CLI") : t("对话页或命令行里正在跑的 AI 会话显示在这里", "an agent session running in the chat page or the CLI shows up here")}
        actions={
          <>
            {dropped ? (
              <Pill tone="warn" title={t("实时连接断了,正在重连;数字可能不是最新的", "The live stream dropped and is reconnecting; the numbers may be stale")}>
                {t("连接断开", "stream dropped")}
              </Pill>
            ) : null}
            <Link className="btn btn--text btn--sm" to="/chat">{t("打开对话", "Open chat")}</Link>
          </>
        }
      />
      {!live ? (
        <p className="live__idle">
          <Dot />
          {t("现在没有 AI 会话在跑。", "No agent session is running.")}
        </p>
      ) : (
        <>
          <div className="live__head">
            <Dot tone="live" />
            <b>{t(`第 ${live.turn} 轮`, `turn ${live.turn}`)}</b>
            <span>{duration(lang, Math.max(0, (now - live.startedMs) / 1000))}</span>
            <span className="mono">{live.sessionId.slice(-8)}</span>
          </div>
          {live.prompt ? <p className="live__prompt">{live.prompt}</p> : null}
          {live.tools.length ? (
            <ul className="live__tools" aria-label={t("工具调用", "Tool calls")}>
              {live.tools.map((tool) => (
                <li key={tool.id} className={`live__tool live__tool--${tool.state}`}>
                  {tool.state === "running" ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name={tool.state === "err" ? "error" : "check-circle"} size="sm" />}
                  <code>{tool.name}</code>
                  <span className="num">
                    {tool.state === "running" ? t(`进行中 ${((now - tool.startedMs) / 1000).toFixed(0)} s`, `running ${((now - tool.startedMs) / 1000).toFixed(0)} s`) : `${tool.tookS.toFixed(1)} s`}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="live__tps">
            <div className="metric__value">
              {live.tps}
              <small>{t("tok/s · 当前", "tok/s now")}</small>
            </div>
            <Spark lines={[live.tpsSamples]} lo={0} hi={hi} label={t("最近 60 秒每秒 token 数", "Tokens per second, last 60 s")} />
            <span className="live__meta mono">
              {live.model || live.backend || "—"}
              <br />
              {live.totalTokens} tok
            </span>
          </div>
        </>
      )}
    </article>
  );
}

/* ---------------- LLM backends ---------------- */

function stateTone(s: BackendState): Tone {
  return s.kind === "up" ? "ok" : s.kind === "down" || s.kind === "error" ? "bad" : s.kind === "loading" ? "busy" : "idle";
}

export function BackendsCard() {
  const t = useT();
  const be = useBackends();
  const tp = useBackendThroughput();
  const up = be.backends.filter((b) => b.state.kind === "up").length;
  const planned = be.backends.filter((b) => b.state.kind === "planned").length;
  return (
    <article className="card span-5">
      <CardHead
        title={t("模型后端", "LLM backends")}
        sub={be.isLoading || be.isError ? undefined : t(`${up} 个可达 · 共 ${be.backends.length} 个${planned ? `,${planned} 个计划中` : ""}`, `${up} reachable · ${be.backends.length} registered${planned ? `, ${planned} planned` : ""}`)}
        actions={<Link className="btn btn--text btn--sm" to="/playground">{t("去试一下", "Try one")}</Link>}
      />
      {be.isError ? (
        <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void be.refetch()}>{t("重试", "Retry")}</button>}>
          {t("读不到模型后端列表(GET /playground/backends 失败)。alb 服务可能没在跑。", "Can't read the backend list (GET /playground/backends failed). The alb service may be down.")}
        </ErrorBanner>
      ) : be.isLoading ? (
        <div className="skeleton" style={{ height: 96 }} />
      ) : be.backends.length === 0 ? (
        <Empty icon="robot" title={t("没有注册的模型后端", "No backends registered")} />
      ) : (
        <ul className="list" aria-label={t("模型后端", "LLM backends")}>
          {be.backends.map((b) => {
            const s = b.state;
            const series = tp.data?.backends?.[b.name]?.samples ?? [];
            return (
              <li className="list__item" key={b.name}>
                <span className="list__lead"><Icon name="robot" /></span>
                <span className="list__title"><Dot tone={stateTone(s)} inline />{b.name}</span>
                <span className="list__trail">
                  {s.kind === "up" && series.length > 1 ? <Spark small lines={[series]} lo={0} hi={Math.max(10, ...series)} label={t(`${b.name} 最近 5 分钟每秒 token 数`, `${b.name} tokens per second, last 5 min`)} /> : null}
                  {s.kind === "up" ? (
                    <span className="mono num">{s.latencyMs !== null ? `${s.latencyMs} ms` : "—"}</span>
                  ) : (
                    <Pill tone={s.kind === "down" || s.kind === "error" ? "bad" : undefined}>{backendWord(t, s)}</Pill>
                  )}
                </span>
                <span className="list__meta" title={s.kind === "down" && s.error ? s.error : undefined}>
                  {s.kind === "up" ? (s.model || b.about || "—") : s.kind === "down" && s.error ? s.error : b.about || "—"}
                  {b.compute ? ` · ${b.compute}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}

/* ---------------- service summary ---------------- */

export function SummaryCard() {
  const t = useT();
  const list = useDevices();
  const sessions = useSessionList();
  const tools = useToolCatalog();
  const tps = useMetricsSummary();
  const online = list.devices.filter((d) => d.state === "device").length;
  const running = runningSessions(sessions.data);
  const mean = tps.data?.tps ? Math.round(tps.data.tps.mean * 10) / 10 : null;
  const failed = t("读取失败", "couldn't read");
  // "—" on loading and on error so an outage never reads as a real zero.
  const tiles: { k: string; label: string; value: string; unit?: string; sub: string }[] = [
    {
      k: "devices",
      label: t("设备在线", "Devices online"),
      value: list.isLoading || list.isError ? "—" : String(online),
      unit: list.devices.length ? `/ ${list.devices.length}` : undefined,
      sub: list.isError ? failed : list.backendError ? t(`${transportLabel(list.transportName)} 用不了`, `${transportLabel(list.transportName)} not working`) : t(`经 ${transportLabel(list.transportName)}`, `via ${transportLabel(list.transportName)}`),
    },
    {
      k: "sessions",
      label: t("AI 会话", "Agent sessions"),
      value: sessions.isLoading || sessions.isError ? "—" : String(sessions.data?.sessions.length ?? 0),
      sub: sessions.isError ? failed : running ? t(`${running} 个正在跑`, `${running} running`) : t("都没在跑", "none running"),
    },
    {
      k: "tools",
      label: t("MCP 工具", "MCP tools"),
      value: tools.isLoading || tools.isError ? "—" : String(tools.data?.count ?? 0),
      sub: tools.isError ? failed : t(`${tools.data?.categories.length ?? 0} 类`, `${tools.data?.categories.length ?? 0} groups`),
    },
    {
      k: "tps",
      label: t("模型吞吐", "LLM throughput"),
      value: tps.isError || mean === null ? "—" : String(mean),
      unit: "tok/s",
      sub: tps.isError ? failed : mean === null ? t("最近 5 分钟没有生成", "nothing generated in the last 5 min") : t(`5 分钟平均 · ${tps.data?.sample_count ?? 0} 个采样`, `5-min average · ${tps.data?.sample_count ?? 0} samples`),
    },
  ];
  return (
    <article className="card span-12">
      <CardHead title={t("服务概况", "Service at a glance")} sub={t("整个 alb 服务,不只这块板子", "the whole alb service, not just this board")} />
      <div className="metrics metrics--4">
        {tiles.map((x) => (
          <div className="metric" key={x.k} data-kpi={x.k}>
            <div className="metric__label"><span>{x.label}</span></div>
            <div className="metric__value">
              {x.value}
              {x.unit ? <small>{x.unit}</small> : null}
            </div>
            <div className="metric__sub">{x.sub}</div>
          </div>
        ))}
      </div>
    </article>
  );
}
