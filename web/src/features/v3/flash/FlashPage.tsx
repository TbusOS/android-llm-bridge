/**
 * Flash (v3): fastboot writes, one partition or a whole vendor package.
 * The board is identified before anything is written. Baseline:
 * docs/webui-preview-v3-flash.html.
 */
import { Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { useAgentStatus, useBenchBoard, useFlashStatusQuery } from "../../../components/v3/TopBar";
import { CardHead, Confirm, Dot, Empty, ErrorBanner, PageHead, Pill, toast } from "../../../components/v3/ui";
import type { AgentStatus } from "../../../lib/api";
import { type FlashJobSummary, fetchFlashJobs, flashOp } from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { type T, useT } from "../../../lib/i18n";
import { CommandsMode } from "./CommandsMode";
import { NotReadBack, OneMode } from "./OneMode";
import { OpenLargeButton, OutputBlock } from "./OutputBlock";
import { PackageMode } from "./PackageMode";
import {
  type FeedLine,
  TIMELINE_READ_BYTES,
  failureOf,
  feedText,
  fetchFlashJob,
  fetchTimelineLines,
  fmtBytes,
  fmtClock,
  fmtMaxDownload,
  jobIdOfArtifacts,
  outputOf,
  parseGetvar,
  timelineFileOf,
} from "./flashApi";
import { useFlashRun } from "./useFlashRun";
import "./flash.css";

type Mode = "pkg" | "one" | "cmd";

/**
 * Why GET /api/flash/status says `available: false`. That endpoint only
 * carries the boolean (agent connected AND it advertises fastboot), so the
 * reason comes from GET /agent/status: no agent at all, or an agent whose
 * `caps` lack `fastboot` because it found no fastboot executable on its host.
 */
export function unavailableReason(t: T, agents: AgentStatus | undefined): string {
  if (agents) {
    const current = agents.agents.find((a) => a.current) ?? agents.agents[0];
    if (!current) {
      return t(
        "烧录现在不可用:没有台架 agent 连到 hub。在接板子的那台 Windows 上启动 agent,或者去台架页远程拉起。",
        "Flashing is unavailable: no bench agent is connected to the hub. Start the agent on the Windows machine the boards are plugged into, or start it remotely from the Bench page.",
      );
    }
    if (!current.caps.includes("fastboot")) {
      const who = current.name || current.agent_id;
      return t(
        `烧录现在不可用:agent ${who} 连上了,但没有报告 fastboot 能力,它那台机器上找不到 fastboot。在那台机器的 agent.conf 里把 fastboot_path 设成 fastboot 可执行文件的路径,然后重启 agent。`,
        `Flashing is unavailable: agent ${who} is connected but does not advertise fastboot, because it finds no fastboot executable on its machine. Set fastboot_path in that machine's agent.conf to the fastboot executable, then restart the agent.`,
      );
    }
  }
  return t(
    "烧录现在不可用:台架 agent 没有连上,或者它没有报告 fastboot 能力。agent 连着时,在它的 agent.conf 里设 fastboot_path 后重启 agent。",
    "Flashing is unavailable: the bench agent is not connected, or it does not advertise fastboot. If it is connected, set fastboot_path in its agent.conf and restart it.",
  );
}

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

  const agents = useAgentStatus();
  const s = status.data;
  const available = !!s?.available;
  const boardName = s?.board ?? board.data?.active ?? null;
  const whyUnavailable = unavailableReason(t, agents.data);
  const disabledReason = status.isError
    ? t("读不到烧录状态。", "Cannot read the flash status.")
    : !s
      ? t("正在读取烧录状态…", "Reading the flash status…")
      : whyUnavailable;

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
  const ownActive = run.running || run.done !== null;
  // The job this page just ran, found on disk by the directory its verdict names.
  const ownJobId = !run.running ? jobIdOfArtifacts(run.done, list.map((j) => j.id)) : null;
  const effectiveId = selected ?? ownJobId ?? runningId ?? list[0]?.id ?? null;
  // A new run takes the right-hand card back to its own live stream.
  useEffect(() => {
    if (run.running) setSelected(null);
  }, [run.running]);
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
  // The record on disk has the serial console on the job's clock; the live
  // stream has job lines only. Show the record unless this page is streaming,
  // or its own just-finished job is not on disk (yet).
  const fromRecord = !run.running && (!ownActive || selected !== null || ownJobId !== null);
  const recorded = timeline.data?.lines ?? [];
  const feed: FeedLine[] = !fromRecord ? run.lines : selected !== null || !ownActive || recorded.length > 0 ? recorded : run.lines;
  const feedFromDisk = fromRecord && feed === recorded;
  const showLast = !run.running && (!ownActive || selected !== null);
  const recordFile = feedFromDisk && detail.data?.timeline_path
    ? { onHub: false, path: detail.data.timeline_path }
    : timelineFileOf(run.done)
      ? { onHub: true, path: timelineFileOf(run.done) }
      : null;
  const truncatedNote =
    feedFromDisk && timeline.data?.truncated
      ? t(
          `记录文件有 ${fmtBytes(timeline.data.size)},这里只读了前 ${fmtBytes(TIMELINE_READ_BYTES)},更后面的行只在文件里。`,
          `The record is ${fmtBytes(timeline.data.size)}; only its first ${fmtBytes(TIMELINE_READ_BYTES)} is shown here, later lines are only in the file.`,
        )
      : "";
  const [feedLarge, setFeedLarge] = useState(false);
  const feedRef = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  // Newest lines are at the bottom: stay there unless the reader scrolled up.
  // biome-ignore lint/correctness/useExhaustiveDependencies: new lines are the trigger
  useEffect(() => {
    const el = feedRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [feed]);

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
            <Link className="btn btn--text" to="/board-config" title={t("板子开机后,从系统里读回配置分区", "Read the config partition back from the booted board")}>
              <Icon name="sliders" />
              {t("配置分区", "Board config")}
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
            <p>{whyUnavailable}</p>
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
            {showLast ? <LastJob detail={detail.data} loading={detail.isLoading && !!effectiveId} runningLabel={s?.busy ? (s.job ?? "") : ""} /> : null}
          </div>

          <div className="stack span-4">
            <article className="card">
              <CardHead
                title={t("作业和串口", "Job and serial, one clock")}
                sub={feed.length > 0 ? t(`共 ${feed.length} 行`, `${feed.length} lines`) : undefined}
                actions={feed.length > 0 ? <OpenLargeButton tonal onClick={() => setFeedLarge(true)} /> : undefined}
              />
              {feed.length === 0 ? (
                <Empty icon="terminal" title={t("还没有输出", "No output yet")}>
                  {t("执行一次烧录后,作业输出和同一时钟上的串口输出会出现在这里。", "After a flash, the job output and the serial console on the same clock show up here.")}
                </Empty>
              ) : (
                <pre
                  ref={feedRef}
                  className="term flash-feed"
                  style={{ fontSize: 12, height: 220 }}
                  aria-label={t("作业和串口输出", "job and serial output")}
                  title={t("双击放大", "Double-click to open large")}
                  onDoubleClick={() => setFeedLarge(true)}
                  onScroll={(e) => {
                    const el = e.currentTarget;
                    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
                  }}
                >
                  {feed.map((l, i) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: a line's position in the record is its identity
                    <div key={i}>
                      <span className="d">+{l.t.toFixed(2)}</span> {l.src === "job" ? <span className="a">job</span> : l.src === "uart" ? "uart" : "meta"} {l.text}
                    </div>
                  ))}
                </pre>
              )}
              {truncatedNote ? <p className="t-body-small t-muted" style={{ margin: 0 }}>{truncatedNote}</p> : null}
              {recordFile ? (
                <p className="t-body-small t-muted" style={{ margin: 0, overflowWrap: "anywhere" }}>
                  {recordFile.onHub ? t("记录文件(hub 上):", "Record file (on the hub): ") : t("记录文件(工作区内):", "Record file (in the workspace): ")}
                  <span className="mono">{recordFile.path}</span>
                </p>
              ) : null}
              {feedFromDisk && detail.data?.timeline_path ? (
                // The Timeline page opens its own recordings (timeline/capture-*), not a
                // flash job's record (flash/<job>/), so this cannot deep-link to the job.
                <Link className="btn btn--text btn--sm" style={{ alignSelf: "flex-start" }} to="/timeline" title={t("时间轴页列的是在那里录的录制,打不开这次作业的记录;这次作业的全部行在「放大查看」里。", "The Timeline page lists the recordings made there; it cannot open this job's record. Every line of this job is under Open large.")}>
                  <Icon name="timeline" size="sm" />
                  {t("打开时间轴页", "Open the Timeline page")}
                </Link>
              ) : null}
              <LogDialog
                open={feedLarge}
                onClose={() => setFeedLarge(false)}
                title={t("作业和串口", "Job and serial, one clock")}
                sub={feedFromDisk && detail.data ? <span className="mono">{detail.data.label}</span> : run.running ? t("还在进行,新行会继续追加。", "Still running; new lines keep arriving.") : undefined}
                lines={feed.map(feedText)}
                loading={run.running}
                note={[truncatedNote, recordFile?.path ?? ""].filter(Boolean).join(" · ")}
                fileName={`flash-${(feedFromDisk && detail.data?.id) || run.label || "job"}.log`}
              />
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
          <p>
            {j?.total ? t(`整包完成:执行 ${j.ran ?? 0} 行,跳过 ${j.skipped ?? 0} 行。`, `Package done: ${j.ran ?? 0} rows ran, ${j.skipped ?? 0} skipped.`) : t("作业完成。", "Job finished.")}
            {detail.op === "flash" ? (
              <>
                <br />
                <NotReadBack />
              </>
            ) : null}
          </p>
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
          {out ? <OutputBlock text={out} title={t(`${detail.label} 的输出`, `Output of ${detail.label}`)} fileName={`flash-${detail.id}.log`} /> : null}
        </>
      )}
    </article>
  );
}
