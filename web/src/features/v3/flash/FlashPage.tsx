/**
 * Flash (v3): fastboot writes, one partition or a whole vendor package.
 * The board is identified before anything is written. Baseline:
 * docs/webui-preview-v3-flash.html.
 */
import { Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { useBenchBoard, useFlashStatusQuery } from "../../../components/v3/TopBar";
import { CardHead, Confirm, Dot, Empty, ErrorBanner, PageHead, Pill, toast } from "../../../components/v3/ui";
import { type FlashJobSummary, fetchFlashJobs, flashOp } from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useT } from "../../../lib/i18n";
import { CommandsMode } from "./CommandsMode";
import { OneMode } from "./OneMode";
import { PackageMode } from "./PackageMode";
import { type FeedLine, failureOf, fetchFlashJob, fetchTimelineLines, fmtClock, fmtMaxDownload, outputOf, parseGetvar } from "./flashApi";
import { useFlashRun } from "./useFlashRun";
import "./flash.css";

type Mode = "pkg" | "one" | "cmd";

export function FlashPage() {
  const t = useT();
  const qc = useQueryClient();
  const status = useFlashStatusQuery();
  const board = useBenchBoard();
  const [mode, setMode] = useState<Mode>("pkg");
  const [rebootOpen, setRebootOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const refreshJobs = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["flash-jobs"] });
    void qc.invalidateQueries({ queryKey: ["flash-job"] });
    void qc.invalidateQueries({ queryKey: ["flash-status"] });
  }, [qc]);
  const run = useFlashRun(refreshJobs);

  const s = status.data;
  const available = !!s?.available;
  const boardName = s?.board ?? board.data?.active ?? null;
  const disabledReason = status.isError
    ? t("读不到烧录状态。", "Cannot read the flash status.")
    : !s
      ? t("正在读取烧录状态…", "Reading the flash status…")
      : t("台架 agent 没有连上,或它不支持烧录。", "The bench agent is not connected, or it cannot flash.");

  /* ---- who is on the other end: one `getvar all` ---- */
  const [vars, setVars] = useState<Record<string, string> | null>(null);
  const [varsBusy, setVarsBusy] = useState(false);
  const [varsError, setVarsError] = useState<string | null>(null);
  const readVars = useCallback(async () => {
    setVarsBusy(true);
    setVarsError(null);
    try {
      const d = await flashOp("getvar", { name: "" }, () => {});
      if (d.ok) setVars(parseGetvar(`${String(d.stdout ?? "")}\n${String(d.stderr ?? "")}`));
      else {
        const f = failureOf(d);
        setVarsError(`${f.code || "FAILED"}${f.message ? `: ${f.message}` : ""}`);
      }
    } catch (e) {
      setVarsError(e instanceof Error ? e.message : String(e));
    } finally {
      setVarsBusy(false);
    }
  }, []);
  const autoRead = useRef(false);
  useEffect(() => {
    if (!autoRead.current && s?.available && !s.busy) {
      autoRead.current = true;
      void readVars();
    }
  }, [s?.available, s?.busy, readVars]);

  /* ---- jobs: survive a reload ---- */
  const jobs = useDashboardQuery({ queryKey: ["flash-jobs"], queryFn: ({ signal }) => fetchFlashJobs(20, signal), refetchMs: 4_000 });
  const list = jobs.data?.jobs ?? [];
  const runningId = jobs.data?.running ? (list.find((j) => j.ok === null)?.id ?? null) : null;
  const effectiveId = selected ?? runningId ?? list[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["flash-job", effectiveId],
    queryFn: ({ signal }) => fetchFlashJob(effectiveId as string, signal),
    enabled: !!effectiveId,
    refetchInterval: (q) => (q.state.data && q.state.data.ok === null ? 3_000 : false),
    refetchOnWindowFocus: false,
  });
  const timeline = useQuery({
    queryKey: ["flash-timeline", detail.data?.timeline_path, detail.data?.ok],
    queryFn: ({ signal }) => fetchTimelineLines(detail.data?.timeline_path as string, signal),
    enabled: !!detail.data?.timeline_path,
    refetchInterval: detail.data && detail.data.ok === null ? 3_000 : false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const ownActive = run.running || run.done !== null;
  const feed: FeedLine[] = ownActive ? run.lines : (timeline.data ?? []);

  const rebootAndroid = () => {
    setRebootOpen(false);
    void run.start("reboot", (on) => flashOp("reboot", { target: "" }, on)).then((d) => {
      toast(d.ok ? t("已发送重启,板子回系统。", "Reboot sent; the board goes back to Android.") : t("重启没有成功,看右侧输出。", "The reboot did not go through; see the output on the right."));
    });
  };

  const modes: [Mode, string, string, string][] = [
    ["pkg", "package", "整包", "Whole package"],
    ["one", "bolt", "单个分区", "One partition"],
    ["cmd", "terminal", "fastboot 命令", "fastboot commands"],
  ];

  const product = vars?.product;
  const serial = board.data?.serial;

  return (
    <>
      <PageHead
        eyebrow={<span>{t("烧录", "Flash")}{boardName ? ` · ${boardName}` : ""}</span>}
        title={t("烧录", "Flash")}
        desc={t("fastboot 烧写,单个分区或整包。写之前先核对板子是谁,写的全程同时录串口。", "fastboot writes, one partition or a whole vendor package. The board is identified before anything is written, and the serial console is recorded throughout.")}
        actions={
          <>
            <Link className="btn btn--text" to="/bench">
              <Icon name="usb" />
              {t("台架 USB", "Bench USB")}
            </Link>
            <button type="button" className="btn btn--outlined" disabled={!available || run.running} title={!available ? disabledReason : undefined} onClick={() => setRebootOpen(true)}>
              <Icon name="power" />
              {t("重启回系统", "Reboot to Android")}
            </button>
          </>
        }
      />
      <div style={{ marginBottom: rebootOpen ? 16 : 0 }}>
        <Confirm
          open={rebootOpen}
          message={t("通过 fastboot 把板子重启回系统。", "Reboot the board into Android through fastboot.")}
          confirmLabel={t("重启", "Reboot")}
          cancelLabel={t("取消", "Cancel")}
          onConfirm={rebootAndroid}
          onCancel={() => setRebootOpen(false)}
        />
      </div>

      <div className="stack">
        {status.isError ? (
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void status.refetch()}>{t("重试", "Retry")}</button>}>
            {t("读不到烧录状态:hub 没有回答。", "Cannot read the flash status: the hub did not answer.")}
          </ErrorBanner>
        ) : s && !s.available ? (
          <div className="banner banner--warn" role="alert">
            <Icon name="alert" />
            <p>{t("烧录现在不可用:台架 agent 没有连上,或者它的版本不支持烧录。去台架页看 agent 状态。", "Flashing is unavailable: the bench agent is not connected, or its version cannot flash. Check the agent on the Bench page.")}</p>
            <div className="banner__actions"><Link className="btn btn--text btn--sm" to="/bench">{t("台架", "Bench")}</Link></div>
          </div>
        ) : null}

        {/* who is on the other end */}
        <article className="card">
          <div className="ident">
            <div className="ident__board">
              <span className="qa__ic"><Icon name="chip" size="lg" /></span>
              <div>
                <b>{boardName ?? t("未选板子", "No board chosen")}</b>
                {!s ? (
                  <Pill>{t("读取中", "reading")}</Pill>
                ) : s.busy ? (
                  <Pill tone="info"><span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />{t("作业中", "job running")}</Pill>
                ) : s.available ? (
                  <Pill tone="ok"><Dot tone="ok" />{t("fastboot 就绪", "fastboot ready")}</Pill>
                ) : (
                  <Pill tone="warn"><Dot tone="warn" />{t("不可用", "unavailable")}</Pill>
                )}
              </div>
            </div>
            <dl className="kv">
              <div>
                <dt>{t("product(读到)", "product (read)")}</dt>
                <dd className="mono">{varsBusy ? "…" : product ? product : <span className="t-muted">—</span>}</dd>
              </div>
              <div>
                <dt>{t("槽位 · 解锁", "Slot · unlocked")}</dt>
                <dd className="mono">{vars ? `${vars["current-slot"] ? `_${vars["current-slot"]}` : "—"} · ${vars.unlocked ?? "—"}` : "—"}</dd>
              </div>
              <div>
                <dt>max-download-size</dt>
                <dd className="mono">{fmtMaxDownload(vars?.["max-download-size"])}</dd>
              </div>
              <div>
                <dt>{t("烧录名单", "Flash list")}</dt>
                <dd>
                  {s?.partitions_problem ? (
                    <span className="t-muted">{t("没有名单", "no list")}</span>
                  ) : s && s.partitions.length > 0 ? (
                    t(`${boardName ?? "本板"} 共 ${s.partitions.length} 个分区`, `${s.partitions.length} partitions for ${boardName ?? "this board"}`)
                  ) : (
                    <span className="t-muted">{t("没有限制名单", "no list set")}</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>{t("串口录制", "Serial recording")}</dt>
                <dd className="mono">{serial?.com ? `${serial.com} · ${serial.baud}` : <span className="t-muted">{t("未配置", "not set")}</span>}</dd>
              </div>
            </dl>
            <button type="button" className="btn btn--text btn--sm" disabled={!available || varsBusy || run.running} onClick={() => void readVars()}>
              <Icon name="refresh" size="sm" />
              {t("重新读取", "Read again")}
            </button>
          </div>
          {s?.partitions_problem ? (
            <div className="banner banner--warn" role="alert"><Icon name="alert" /><p>{s.partitions_problem}</p><span /></div>
          ) : null}
          {varsError ? <p className="t-body-small t-muted" style={{ margin: 0 }}>{t("没读到板子:", "Could not read the board: ")}<span className="mono">{varsError}</span></p> : null}
        </article>

        <div className="row">
          <div className="seg" role="group" aria-label={t("模式", "mode")}>
            {modes.map(([k, ic, zh, en]) => (
              <button key={k} type="button" aria-pressed={mode === k} aria-controls={`m-${k === "pkg" ? "pkg" : k === "one" ? "one" : "cmd"}`} onClick={() => setMode(k)}>
                <Icon name={ic} size="sm" />
                <span>{t(zh, en)}</span>
              </button>
            ))}
          </div>
        </div>

        <section className="grid">
          <div className="stack span-8">
            <div hidden={mode !== "pkg"}>
              <PackageMode run={run} available={available} board={boardName} disabledReason={disabledReason} />
            </div>
            <div hidden={mode !== "one"}>
              <OneMode run={run} available={available} board={boardName} partitions={s?.partitions ?? []} partitionsProblem={s?.partitions_problem ?? ""} disabledReason={disabledReason} />
            </div>
            <div hidden={mode !== "cmd"}>
              <CommandsMode run={run} available={available} disabledReason={disabledReason} />
            </div>
            {!ownActive ? <LastJob detail={detail.data} loading={detail.isLoading && !!effectiveId} runningLabel={s?.busy ? (s.job ?? "") : ""} /> : null}
          </div>

          <div className="stack span-4">
            <article className="card">
              <CardHead title={t("作业和串口", "Job and serial, one clock")} sub={t("最后 40 行", "last 40 lines")} />
              {feed.length === 0 ? (
                <Empty icon="terminal" title={t("还没有输出", "No output yet")}>
                  {t("执行一次烧录后,作业输出和同一时钟上的串口输出会出现在这里。", "After a flash, the job output and the serial console on the same clock show up here.")}
                </Empty>
              ) : (
                <pre className="term flash-feed" style={{ fontSize: 12, height: 220 }} aria-label={t("作业和串口输出", "job and serial output")}>
                  {feed.slice(-40).map((l, i) => (
                    <div key={i}>
                      <span className="d">+{l.t.toFixed(2)}</span> {l.src === "job" ? <span className="a">job</span> : l.src === "uart" ? "uart" : "meta"} {l.text}
                    </div>
                  ))}
                </pre>
              )}
              {detail.data?.timeline_path && !ownActive ? (
                <Link className="btn btn--tonal btn--sm" style={{ alignSelf: "flex-start" }} to="/timeline">
                  <Icon name="timeline" size="sm" />
                  {t("在时间轴页看串口", "Open the timeline")}
                </Link>
              ) : null}
            </article>
            <article className="card">
              <CardHead title={t("最近作业", "Recent jobs")} sub={t("刷新页面后仍在", "kept after a reload")} />
              {jobs.isError ? (
                <ErrorBanner>{t("读不到作业列表。", "Cannot read the job list.")}</ErrorBanner>
              ) : list.length === 0 ? (
                <Empty icon="history" title={t("还没有作业", "No jobs yet")} />
              ) : (
                <ul className="list">
                  {list.map((j) => (
                    <JobItem key={j.id} job={j} selected={j.id === effectiveId} onPick={() => setSelected(j.id)} />
                  ))}
                </ul>
              )}
            </article>
          </div>
        </section>
      </div>
    </>
  );
}

function JobItem({ job, selected, onPick }: { job: FlashJobSummary; selected: boolean; onPick: () => void }) {
  const t = useT();
  const when = job.started_at ? fmtClock(Date.parse(job.started_at) / 1000) : "";
  return (
    <li className="list__item" aria-current={selected ? "true" : undefined} style={selected ? { background: "var(--md-sys-color-surface-container-high)" } : undefined}>
      <span className="list__lead mono t-muted">{when.slice(6)}</span>
      <span className="list__title">
        <button type="button" className="btn btn--text btn--sm mono" onClick={onPick} title={job.id}>{job.label}</button>
      </span>
      <span className="list__trail">
        {job.ok === null ? (
          <Pill tone="info">{t("进行中", "running")}</Pill>
        ) : job.ok ? (
          <Pill tone="ok">ok</Pill>
        ) : (
          <Pill tone="bad">{job.failed_line ? t(`第 ${job.failed_line} 行`, `line ${job.failed_line}`) : t("失败", "failed")}</Pill>
        )}
      </span>
      <span className="list__meta">{job.ok === false ? job.code : job.rows.total > 1 ? t(`${job.rows.ok}/${job.rows.total} 行`, `${job.rows.ok}/${job.rows.total} rows`) : ""}</span>
    </li>
  );
}

/** The recorded result of the selected / latest job, read from disk. */
function LastJob({ detail, loading, runningLabel }: { detail: Awaited<ReturnType<typeof fetchFlashJob>> | undefined; loading: boolean; runningLabel: string }) {
  const t = useT();
  if (loading) {
    return (
      <article className="card">
        <CardHead title={t("最近一次作业", "Last job")} />
        <div className="row t-muted"><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("正在读取…", "Loading…")}</div>
      </article>
    );
  }
  if (!detail) {
    return runningLabel ? (
      <article className="card">
        <CardHead title={t("有作业在跑", "A job is running")} />
        <div className="banner banner--info"><Icon name="info" /><p className="mono">{runningLabel}</p><span /></div>
      </article>
    ) : null;
  }
  const j = detail.job;
  const running = detail.ok === null;
  const out = j ? outputOf(j as never) : "";
  return (
    <article className="card" aria-label={t("最近一次作业", "Last job")}>
      <CardHead
        title={t("最近一次作业", "Last job")}
        sub={<span className="mono">{detail.label}</span>}
        actions={running ? <Pill tone="info"><span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />{t("进行中", "running")}</Pill> : detail.ok ? <Pill tone="ok">ok</Pill> : <Pill tone="bad">{t("失败", "failed")}</Pill>}
      />
      {running ? (
        <div className="banner banner--info"><Icon name="info" /><p>{runningLabel || t("作业还在跑;关掉页面不影响它,结果会记在这里。", "The job is still running; closing the page does not stop it, and its result is recorded here.")}</p><span /></div>
      ) : detail.ok ? (
        <div className="banner banner--ok">
          <Icon name="check-circle" />
          <p>{j?.total ? t(`整包完成:执行 ${j.ran ?? 0} 行,跳过 ${j.skipped ?? 0} 行。`, `Package done: ${j.ran ?? 0} rows ran, ${j.skipped ?? 0} skipped.`) : t("作业完成。", "Job finished.")}</p>
          <span />
        </div>
      ) : (
        <>
          <div className="banner banner--error" role="alert">
            <Icon name="error" />
            <p>
              <b>{detail.code || j?.code || "FAILED"}</b>
              {detail.failed_line ? <> · {t(`停在第 ${detail.failed_line} 行`, `stopped at line ${detail.failed_line}`)}</> : null}
              {j?.error ? <> · <span className="mono">{j.error}</span></> : null}
              {j?.total ? (
                <>
                  <br />
                  {t(`已执行 ${j.ran ?? 0} 行,${j.not_run ?? 0} 行未执行。`, `${j.ran ?? 0} rows ran, ${j.not_run ?? 0} not run.`)}
                </>
              ) : null}
            </p>
            <span />
          </div>
          {out ? <pre className="term" style={{ fontSize: 12, maxHeight: 140 }}>{out}</pre> : null}
        </>
      )}
    </article>
  );
}
