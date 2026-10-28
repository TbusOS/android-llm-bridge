/**
 * Bench (v3): the machine the boards are plugged into: which board is on
 * which serial port, what it sees on USB, the agent that runs there, its
 * logs and its operation table. Baseline: docs/webui-preview-v3-bench.html.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { useAgentStatus, useBenchBoard } from "../../../components/v3/TopBar";
import { CardHead, Confirm, Dot, Empty, ErrorBanner, PageHead, Pill, toast } from "../../../components/v3/ui";
import {
  type NdjsonEvent,
  type OpSpecView,
  type UsbDeviceView,
  benchAgentStart,
  benchAgentUpdate,
  runBenchOp,
  selectBenchBoard,
} from "../../../lib/api3";
import type { AgentStatusItem } from "../../../lib/api";
import { useT } from "../../../lib/i18n";
import { useBenchOps } from "./useBenchOps";

/* ---------------- helpers ---------------- */

function errorOf(ev: NdjsonEvent): { code: string; message: string } {
  const e = ev.error;
  if (e && typeof e === "object") {
    const o = e as { code?: string; message?: string };
    return { code: String(o.code ?? ""), message: String(o.message ?? "") };
  }
  return { code: String(ev.code ?? ""), message: typeof e === "string" ? e : "" };
}

function fmtTimeout(s: number): string {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

const MODE_LABEL: Record<string, [string, string]> = {
  fastboot: ["fastboot", "fastboot"],
  adb: ["adb", "adb"],
  "qualcomm-edl": ["下载模式(EDL)", "download (EDL)"],
  "mediatek-preloader": ["下载模式(preloader)", "download (preloader)"],
  "rockchip-usb": ["下载模式(Maskrom/loader)", "download (Maskrom/loader)"],
  "allwinner-fel": ["下载模式(FEL)", "download (FEL)"],
  other: ["其他", "other"],
};

interface UsbResult {
  devices: UsbDeviceView[];
  boards: UsbDeviceView[];
}

/* ---------------- page ---------------- */

export function BenchPage() {
  const t = useT();
  const qc = useQueryClient();
  const agent = useAgentStatus();
  const ops = useBenchOps();
  const agentItem = agent.data?.agents?.[0];
  const connected = !!agentItem;

  /* USB */
  const [usb, setUsb] = useState<{ loading: boolean; error: string | null; data: UsbResult | null }>({ loading: false, error: null, data: null });
  const usbSeq = useRef(0);
  const listUsb = useCallback(async () => {
    const seq = ++usbSeq.current;
    setUsb((s) => ({ ...s, loading: true, error: null }));
    try {
      const d = await runBenchOp("usb-list", {}, () => {});
      if (seq !== usbSeq.current) return;
      if (d.ok) {
        const data = (d.data ?? {}) as Partial<UsbResult>;
        setUsb({ loading: false, error: null, data: { devices: data.devices ?? [], boards: data.boards ?? [] } });
      } else {
        const f = errorOf(d);
        setUsb((s) => ({ loading: false, error: `${f.code || "FAILED"}${f.message ? `: ${f.message}` : ""}`, data: s.data }));
      }
    } catch (e) {
      if (seq === usbSeq.current) setUsb((s) => ({ loading: false, error: e instanceof Error ? e.message : String(e), data: s.data }));
    }
  }, []);
  useEffect(() => {
    void listUsb();
  }, [listUsb]);

  /* agent update / start: one streamed job at a time */
  const [pending, setPending] = useState<"update" | "start" | null>(null);
  const [job, setJob] = useState<{ kind: "update" | "start"; running: boolean; lines: string[]; done: NdjsonEvent | null } | null>(null);
  const startJob = async (kind: "update" | "start") => {
    setPending(null);
    setJob({ kind, running: true, lines: [], done: null });
    const on = (ev: NdjsonEvent) => {
      if (ev.ev === "progress" && ev.text) setJob((j) => (j ? { ...j, lines: [...j.lines, String(ev.text)].slice(-200) } : j));
    };
    let done: NdjsonEvent;
    try {
      done = await (kind === "update" ? benchAgentUpdate(on) : benchAgentStart(on));
    } catch (e) {
      done = { ev: "done", ok: false, error: { code: "NETWORK", message: e instanceof Error ? e.message : String(e) } };
    }
    setJob((j) => (j ? { ...j, running: false, done } : j));
    void qc.invalidateQueries({ queryKey: ["agent-status"] });
    void qc.invalidateQueries({ queryKey: ["bench-ops"] });
    if (done.ok) void listUsb();
  };

  const confirmCopy = {
    update: t(
      "把这台 hub 上的 agent 文件推到台架,它换新版重启;60 秒内没连回来,会自己退回旧版。",
      "Send this hub's agent files to the bench. It restarts on them; if the new version has not connected back within 60 s, it rolls back to the old one by itself.",
    ),
    start: t("经 ssh 在台架上拉起 agent(计划任务),然后等它连回 hub。", "Start the agent on the bench over ssh (its scheduled task), then wait for it to reach the hub."),
  };

  return (
    <>
      <PageHead
        eyebrow={<span>{t("台架", "Bench")}{agentItem?.name ? ` · ${agentItem.name}` : ""}</span>}
        title={t("台架", "Bench")}
        desc={t("板子插着的那台 Windows:哪块板接哪个串口、USB 上看到了什么、那边跑着的 agent。", "The Windows machine the boards are plugged into: which board is on which serial port, what it sees on USB, and the agent that runs there.")}
        actions={
          <>
            <button type="button" className="btn btn--outlined" disabled={usb.loading || !connected} onClick={() => void listUsb()}>
              <Icon name="refresh" />
              {t("重新列 USB", "List USB again")}
            </button>
            <button type="button" className="btn btn--tonal" disabled={!connected || job?.running} onClick={() => setPending("update")}>
              <Icon name="upload" />
              {t("更新 agent", "Update agent")}
            </button>
          </>
        }
      />
      {pending ? (
        <div style={{ marginBottom: 16 }}>
          <Confirm open message={confirmCopy[pending]} confirmLabel={pending === "update" ? t("更新", "Update") : t("拉起", "Start")} cancelLabel={t("取消", "Cancel")} onConfirm={() => void startJob(pending)} onCancel={() => setPending(null)} />
        </div>
      ) : null}

      <section className="grid">
        <BoardsCard />
        <UsbCard usb={usb} connected={connected} />
        <AgentCard
          agentError={agent.isError}
          loading={agent.isLoading}
          item={agentItem}
          opsData={ops.data}
          onUpdate={() => setPending("update")}
          onStart={() => setPending("start")}
          job={job}
          onCloseJob={() => setJob(null)}
        />
        <LogsCard ops={ops.data?.ops} connected={connected} />
        <OpsCard ops={ops.data?.ops} reported={ops.data?.reported} loading={ops.isLoading} error={ops.isError} />
      </section>
    </>
  );
}

/* ---------------- boards ---------------- */

function BoardsCard() {
  const t = useT();
  const qc = useQueryClient();
  const board = useBenchBoard();
  const d = board.data;
  const [switching, setSwitching] = useState<string | null>(null);
  const [force, setForce] = useState<{ name: string; message: string } | null>(null);

  const doSwitch = async (name: string, forced: boolean) => {
    setSwitching(name);
    try {
      const r = await selectBenchBoard(name, forced);
      if (r.ok) {
        toast(t(`串口已切到 ${name}。`, `Serial moved to ${name}.`));
        setForce(null);
        await qc.invalidateQueries({ queryKey: ["bench-board"] });
        void qc.invalidateQueries({ queryKey: ["flash-status"] });
      } else if (r.code === "BENCH_SERIAL_BUSY") {
        setForce({ name, message: r.error ?? "" });
      } else {
        setForce(null);
        toast(`${r.code ?? "ERROR"}: ${r.error ?? ""}`);
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
    } finally {
      setSwitching(null);
    }
  };

  return (
    <article className="card card--flush span-12">
      <CardHead title={t("板子", "Boards")} sub={t("切换只是把串口指过去,不用重启 hub", "switching moves the serial console; no hub restart")} />
      {board.isLoading ? (
        <div className="row t-muted" style={{ padding: 16 }}><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("正在读取板子表…", "Reading the board table…")}</div>
      ) : board.isError ? (
        <div style={{ padding: "0 16px 16px" }}><ErrorBanner>{t("读不到板子表:hub 没有回答。", "Cannot read the board table: the hub did not answer.")}</ErrorBanner></div>
      ) : !d || (d.boards.length === 0 && !d.problem) ? (
        <div style={{ padding: "0 16px 16px" }}>
          <Empty icon="chip" title={t("这台 hub 没有配板子表", "This hub has no board table")}>
            {t("在 hub 的 ALB_BENCH_BOARDS 设置里指向一个板子表(TOML),重启 alb-api 后这里会列出每块板和它的串口。", "Point the hub's ALB_BENCH_BOARDS setting at a boards TOML and restart alb-api; each board and its serial port then show up here.")}
          </Empty>
        </div>
      ) : (
        <>
          {d.problem ? <div style={{ padding: "0 16px" }}><ErrorBanner>{d.problem}</ErrorBanner></div> : null}
          {d.boards.length > 0 ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("板子", "Board")}</th>
                    <th>{t("串口", "Serial port")}</th>
                    <th>{t("备注", "Note")}</th>
                    <th>{t("现在", "Now")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {d.boards.map((b) => {
                    const current = d.active === b.name;
                    return (
                      <tr key={b.name} aria-selected={current}>
                        <td><b>{b.name}</b>{current ? <> <Pill tone="info">{t("当前", "current")}</Pill></> : null}</td>
                        <td className="mono">{b.serial_com} · {b.serial_baud}</td>
                        <td className={b.note ? undefined : "t-muted"}>{b.note || "—"}</td>
                        <td>
                          {current ? (
                            d.serial.bound ? (
                              <Pill tone="ok"><Dot tone="ok" />{t(`串口已连上 · ${d.serial.readers} 个读者`, `serial attached · ${d.serial.readers} reader(s)`)}</Pill>
                            ) : (
                              <Pill tone="warn"><Dot tone="warn" />{t("串口还没连上", "serial not attached")}</Pill>
                            )
                          ) : null}
                        </td>
                        <td className="num">
                          <button type="button" className="btn btn--text btn--sm" disabled={current || switching !== null} onClick={() => void doSwitch(b.name, false)}>
                            {switching === b.name ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : null}
                            {current ? t("已选中", "Current") : t("切到这块", "Switch here")}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          {force ? (
            <div style={{ padding: "0 16px" }}>
              <Confirm
                open
                busy={switching !== null}
                message={t(
                  `串口上还有别的读者(抓取、shell、网页控制台)。强制切到 ${force.name} 会把他们断开。${force.message ? `(${force.message})` : ""}`,
                  `Someone else is reading the serial console (capture, shell, web console). Forcing the switch to ${force.name} cuts them off. ${force.message ? `(${force.message})` : ""}`,
                )}
                confirmLabel={t("强制切换", "Force switch")}
                cancelLabel={t("取消", "Cancel")}
                onConfirm={() => void doSwitch(force.name, true)}
                onCancel={() => setForce(null)}
              />
            </div>
          ) : null}
        </>
      )}
      <div className="banner" style={{ margin: "4px 16px 16px" }}>
        <Icon name="info" />
        <p>{t("板子表本身在 hub 的 ALB_BENCH_BOARDS 设置里,每块板的烧录名单在台架 agent.conf 里;这一页负责查看和切换。", "The table itself lives in the hub's ALB_BENCH_BOARDS setting and each board's flash list in the bench agent.conf; this page shows them and switches between them.")}</p>
      </div>
    </article>
  );
}

/* ---------------- USB ---------------- */

function UsbCard({ usb, connected }: { usb: { loading: boolean; error: string | null; data: UsbResult | null }; connected: boolean }) {
  const t = useT();
  const [showOther, setShowOther] = useState(false);
  const boards = usb.data?.boards ?? [];
  const others = useMemo(() => (usb.data?.devices ?? []).filter((d) => d.mode === "other"), [usb.data]);
  const missing = boards.filter((b) => b.driver === "missing" || b.driver === "problem").length;

  const idText = (d: UsbDeviceView) => (
    <>
      {d.vid}:{d.pid}
      {d.serial ? <> · serial {d.serial}</> : d.windows_id ? <> · {t("Windows 自编号,不是板子序列号", "Windows-made id, not a board serial")}</> : null}
      {d.name ? <> · {d.name}</> : null}
    </>
  );

  return (
    <article className="card span-7">
      <CardHead
        title={t("台架 USB 上看到的", "What the bench sees on USB")}
        sub={usb.data ? t(`${usb.data.devices.length} 个设备${missing ? ` · ${missing} 个驱动有问题` : ""}`, `${usb.data.devices.length} devices${missing ? ` · ${missing} with a driver problem` : ""}`) : undefined}
      />
      {usb.error ? <ErrorBanner>{usb.error}</ErrorBanner> : null}
      {usb.loading && !usb.data ? (
        <div className="row t-muted"><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("正在列 USB…", "Listing USB…")}</div>
      ) : !usb.data ? (
        !usb.error ? (
          <Empty icon="usb" title={connected ? t("还没有列表", "No list yet") : t("台架 agent 没有连上", "The bench agent is not connected")}>
            {connected ? t("点「重新列 USB」。", "Press List USB again.") : t("agent 连上之后才能列出台架上的 USB。", "The bench's USB can only be listed once the agent is connected.")}
          </Empty>
        ) : null
      ) : (
        <>
          {boards.length === 0 ? (
            <Empty icon="usb" title={t("没有看到板子", "No board seen")}>
              {t("台架上没有处于 adb / fastboot / 下载模式的板子。检查线缆,或看下面的其他设备。", "No board in adb, fastboot or download mode on the bench. Check the cable, or look at the other devices below.")}
            </Empty>
          ) : (
            <div className="usb">
              {boards.map((b, i) => {
                const bad = b.driver === "missing";
                const label = MODE_LABEL[b.mode] ?? [b.mode, b.mode];
                return (
                  <div className={`usb__row${bad ? " usb__row--bad" : ""}`} key={`${b.vid}:${b.pid}:${b.serial || b.windows_id}:${i}`}>
                    <span className="usb__mode">{t(label[0], label[1])}</span>
                    <span className="usb__id">{idText(b)}</span>
                    {b.driver === "ok" ? <Pill tone="ok">{t("驱动正常", "driver ok")}</Pill> : b.driver === "missing" ? <Pill tone="bad">{t("没装驱动", "no driver")}</Pill> : b.driver === "problem" ? <Pill tone="warn">{t("驱动有问题", "driver problem")}</Pill> : <Pill>{t("驱动状态未知", "driver unknown")}</Pill>}
                    {b.hint ? <span className="usb__hint">{b.hint}</span> : null}
                  </div>
                );
              })}
            </div>
          )}
          {others.length > 0 ? (
            <>
              <button type="button" className="btn btn--text btn--sm" style={{ alignSelf: "flex-start" }} aria-expanded={showOther} onClick={() => setShowOther((v) => !v)}>
                {showOther ? t("收起其他设备", "Hide other devices") : t(`显示另外 ${others.length} 个设备`, `Show ${others.length} other devices`)}
              </button>
              {showOther ? (
                <div className="usb">
                  {others.map((d, i) => (
                    <div className="usb__row" key={`${d.vid}:${d.pid}:${i}`}>
                      <span className="usb__mode">{d.name || t("其他", "other")}</span>
                      <span className="usb__id">{d.vid}:{d.pid}</span>
                      {d.driver === "ok" ? <Pill tone="ok">{t("驱动正常", "driver ok")}</Pill> : d.driver === "missing" ? <Pill tone="bad">{t("没装驱动", "no driver")}</Pill> : d.driver === "problem" ? <Pill tone="warn">{t("驱动有问题", "driver problem")}</Pill> : <Pill>{t("驱动状态未知", "driver unknown")}</Pill>}
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </>
      )}
    </article>
  );
}

/* ---------------- agent ---------------- */

type Job = { kind: "update" | "start"; running: boolean; lines: string[]; done: NdjsonEvent | null } | null;

function AgentCard({
  agentError,
  loading,
  item,
  opsData,
  onUpdate,
  onStart,
  job,
  onCloseJob,
}: {
  agentError: boolean;
  loading: boolean;
  item: AgentStatusItem | undefined;
  opsData: ReturnType<typeof useBenchOps>["data"];
  onUpdate: () => void;
  onStart: () => void;
  job: Job;
  onCloseJob: () => void;
}) {
  const t = useT();
  const [jobLarge, setJobLarge] = useState(false);
  const build = opsData?.agent?.build ?? "";
  const bundle = opsData?.bundle_build ?? "";
  const stale = !!build && !!bundle && build !== bundle;
  const doneErr = job?.done && !job.done.ok ? errorOf(job.done) : null;
  const outcome = (job?.done?.data as { outcome?: string } | undefined)?.outcome;
  return (
    <article className="card span-5">
      <CardHead
        title={t("台架 agent", "Bench agent")}
        actions={
          loading ? (
            <Pill>{t("读取中", "reading")}</Pill>
          ) : item ? (
            <Pill tone="ok"><Dot tone="ok" />{t("已连接", "connected")}</Pill>
          ) : (
            <Pill tone={agentError ? "bad" : "warn"}><Dot tone={agentError ? "bad" : "warn"} />{agentError ? t("读不到", "unreadable") : t("没连上", "not connected")}</Pill>
          )
        }
      />
      {item ? (
        <dl className="kv">
          <div><dt>{t("名字", "Name")}</dt><dd>{item.name}</dd></div>
          <div><dt>{t("版本", "Version")}</dt><dd className="mono">{build ? build.slice(0, 12) : `v${item.version}`}</dd></div>
          <div>
            <dt>{t("hub 手里的版本", "Version on this hub")}</dt>
            <dd>
              <span className="mono">{bundle ? bundle.slice(0, 12) : "—"}</span>{" "}
              {bundle && build ? stale ? <Pill tone="warn">{t("台架上的较旧", "bench is behind")}</Pill> : <Pill tone="ok">{t("一致", "same")}</Pill> : null}
            </dd>
          </div>
          <div><dt>{t("adb 设备", "adb devices")}</dt><dd>{item.adb_devices.length}</dd></div>
          <div><dt>{t("串口", "COM ports")}</dt><dd>{item.com_ports.length}</dd></div>
          <div><dt>{t("操作表", "Operations")}</dt><dd>{opsData ? t(`${opsData.ops.length} 项`, `${opsData.ops.length} operations`) : "—"}</dd></div>
        </dl>
      ) : !loading ? (
        <Empty icon="plug" title={agentError ? t("读不到 agent 状态", "Cannot read the agent status") : t("台架 agent 没有连上", "The bench agent is not connected")}>
          {t("agent 在板子插着的那台 Windows 上跑。没连上时可以从这里经 ssh 拉起。", "The agent runs on the Windows machine the boards are plugged into. When it is not connected you can start it from here over ssh.")}
        </Empty>
      ) : null}
      <div className="row">
        <button type="button" className="btn btn--tonal btn--sm" disabled={!item || !!job?.running} onClick={onUpdate}>
          <Icon name="upload" size="sm" />
          {t("更新", "Update")}
        </button>
        <button type="button" className="btn btn--outlined btn--sm" disabled={!!item || !!job?.running} title={item ? t("agent 已经连着", "The agent is already connected") : undefined} onClick={onStart}>
          <Icon name="play" size="sm" />
          {t("远程拉起", "Start remotely")}
        </button>
      </div>
      {job ? (
        <div className="stack" aria-live="polite">
          {job.running ? (
            <div className="row t-muted"><span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />{job.kind === "update" ? t("正在推送并等 agent 连回…", "Sending and waiting for the agent to reconnect…") : t("正在拉起并等 agent 连回…", "Starting and waiting for the agent to connect…")}</div>
          ) : job.done?.ok ? (
            <div className="banner banner--ok"><Icon name="check-circle" /><p>{job.kind === "update" ? t("agent 已更新并连回。", "The agent was updated and reconnected.") : t("agent 已连上。", "The agent is connected.")}{outcome ? <> <span className="mono">({outcome})</span></> : null}</p><span /></div>
          ) : (
            <div className="banner banner--error" role="alert"><Icon name="error" /><p><b>{doneErr?.code || "FAILED"}</b>{doneErr?.message ? <> · <span className="mono">{doneErr.message}</span></> : null}</p><span /></div>
          )}
          {job.lines.length > 0 ? <pre className="term" style={{ fontSize: 12, maxHeight: 140 }}>{job.lines.join("\n")}</pre> : null}
          <div className="row">
            {job.lines.length > 0 ? (
              <button type="button" className="btn btn--text btn--sm" onClick={() => setJobLarge(true)}>
                <Icon name="open-full" size="sm" />
                {t("放大查看", "Open large")}
              </button>
            ) : null}
            {!job.running ? <button type="button" className="btn btn--text btn--sm" onClick={onCloseJob}>{t("收起", "Dismiss")}</button> : null}
          </div>
          <LogDialog
            open={jobLarge}
            onClose={() => setJobLarge(false)}
            title={job.kind === "update" ? t("agent 更新过程", "Agent update") : t("agent 拉起过程", "Agent start")}
            sub={job.running ? t("还在进行,新行会继续追加。", "Still running; new lines keep arriving.") : undefined}
            lines={job.lines}
            loading={job.running}
            fileName={`bench-agent-${job.kind}.log`}
          />
        </div>
      ) : null}
      <p className="t-body-small t-muted" style={{ margin: 0 }}>{t("Windows 上只能执行操作表里的操作;板子上的命令不受限制。", "Only the operations in its table can run on Windows; commands on the board itself are not restricted.")}</p>
    </article>
  );
}

/* ---------------- logs ---------------- */

const SOURCE_LABEL: Record<string, [string, string]> = {
  agent: ["agent 日志", "Agent log"],
  setupapi: ["USB 驱动日志", "USB driver log"],
  update: ["更新日志", "Update log"],
};

function LogsCard({ ops, connected }: { ops: OpSpecView[] | undefined; connected: boolean }) {
  const t = useT();
  const spec = ops?.find((o) => o.name === "log-tail");
  const sources = spec?.params.find((p) => p.name === "source")?.choices ?? [];
  const [source, setSource] = useState("");
  const [grep, setGrep] = useState("");
  const [lines, setLines] = useState(200);
  const [state, setState] = useState<{ loading: boolean; text: string; error: string | null; note: string; grep: string }>({ loading: false, text: "", error: null, note: "", grep: "" });
  const [large, setLarge] = useState(false);
  const seq = useRef(0);
  const src = source && sources.includes(source) ? source : (sources[0] ?? "");

  const load = useCallback(
    async (s: string, g: string, n: number) => {
      if (!s) return;
      const my = ++seq.current;
      setState((x) => ({ ...x, loading: true, error: null }));
      try {
        const d = await runBenchOp("log-tail", { source: s, lines: n, grep: g }, () => {});
        if (my !== seq.current) return;
        if (d.ok) {
          const data = (d.data ?? {}) as { lines?: number; truncated?: boolean };
          setState({ loading: false, text: String(d.stdout ?? ""), error: null, note: data.truncated ? t("更早的行没有读进来(agent 每次最多回 2000 行、64 KB)。", "Older lines were not read (the agent returns at most 2000 lines, 64 KB, per read).") : "", grep: g });
        } else {
          const f = errorOf(d);
          setState({ loading: false, text: "", error: `${f.code || "FAILED"}${f.message ? `: ${f.message}` : ""}`, note: "", grep: g });
        }
      } catch (e) {
        if (my === seq.current) setState({ loading: false, text: "", error: e instanceof Error ? e.message : String(e), note: "", grep: g });
      }
    },
    [t],
  );
  useEffect(() => {
    if (src && connected) void load(src, grep, lines);
    // grep is applied on Enter / the button, not on every keystroke
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, lines, connected, load]);

  const rows = state.text ? state.text.split("\n") : [];
  const ready = connected && sources.length > 0;
  const sourceName = (x: string) => (SOURCE_LABEL[x] ? t(SOURCE_LABEL[x][0], SOURCE_LABEL[x][1]) : x);
  // One set of controls, drawn in the card and again in the dialog; both
  // are bound to the same state, so a filter set in one shows in the other.
  const tools = (
    <form
      className="log-tools"
      onSubmit={(e) => {
        e.preventDefault();
        void load(src, grep, lines);
      }}
    >
      <div className="seg seg--sm" role="group" aria-label={t("日志来源", "log source")}>
        {sources.map((s) => (
          <button key={s} type="button" aria-pressed={src === s} onClick={() => setSource(s)}>
            {sourceName(s)}
          </button>
        ))}
      </div>
      <label className="input--search">
        <Icon name="search" size="sm" />
        <input value={grep} onChange={(e) => setGrep(e.target.value)} aria-label={t("关键词", "keyword")} placeholder={t("关键词(回车筛选)", "keyword (Enter to filter)")} />
      </label>
      <select className="select" style={{ width: "auto" }} aria-label={t("行数", "lines")} value={lines} onChange={(e) => setLines(Number(e.target.value))}>
        <option value={200}>{t("最后 200 行", "last 200 lines")}</option>
        <option value={1000}>{t("最后 1000 行", "last 1000 lines")}</option>
        <option value={2000}>{t("最后 2000 行", "last 2000 lines")}</option>
      </select>
    </form>
  );
  return (
    <article className="card span-12">
      <CardHead
        title={t("台架日志", "Bench logs")}
        sub={ready && rows.length > 0 ? t(`${sourceName(src)} · ${rows.length} 行`, `${sourceName(src)} · ${rows.length} lines`) : undefined}
        actions={
          ready ? (
            <>
              <button type="button" className="icon-btn" onClick={() => void load(src, grep, lines)} disabled={state.loading} aria-label={t("重新读取", "Read again")} title={t("重新读取", "Read again")}>
                <Icon name="refresh" />
              </button>
              <button type="button" className="btn btn--tonal btn--sm" onClick={() => setLarge(true)}>
                <Icon name="open-full" size="sm" />
                {t("放大查看", "Open large")}
              </button>
            </>
          ) : undefined
        }
      />
      {!connected ? (
        <Empty icon="logs" title={t("agent 没连上,读不了台架日志", "The agent is not connected, so its logs cannot be read")} />
      ) : sources.length === 0 ? (
        <Empty icon="logs" title={t("这个 agent 没有可读的日志", "This agent has no readable logs")}>
          {t("操作表里没有 log-tail,或它没配日志来源。", "Its operation table has no log-tail, or no log source is set.")}
        </Empty>
      ) : (
        <>
          {large ? null : tools}
          {state.error ? <ErrorBanner>{state.error}</ErrorBanner> : null}
          <pre className="term" style={{ height: 280, fontSize: 12 }} aria-label={t("台架日志内容", "bench log")} onDoubleClick={() => setLarge(true)} title={t("双击放大", "Double-click to open large")}>
            {state.loading && rows.length === 0 ? t("读取中…", "Reading…") : rows.length === 0 && !state.error ? t("没有匹配的行。", "No matching lines.") : rows.map((r, i) => (
              <div key={i} className={/\b(error|fail|failed)\b/i.test(r) ? "e" : undefined}>{r}</div>
            ))}
          </pre>
          {state.note ? <p className="t-body-small t-muted" style={{ margin: 0 }}>{state.note}</p> : null}
          <LogDialog
            open={large}
            onClose={() => setLarge(false)}
            title={t("台架日志", "Bench logs")}
            sub={`${sourceName(src)} · ${t(`最后 ${lines} 行`, `last ${lines} lines`)}${state.grep ? ` · ${t("关键词", "keyword")} "${state.grep}"` : ""}`}
            tools={tools}
            lines={rows}
            loading={state.loading}
            error={state.error}
            note={state.note}
            highlight={state.grep}
            fileName={`bench-${src}.log`}
            onRefresh={() => void load(src, grep, lines)}
            emptyText={t("没有匹配的行。", "No matching lines.")}
          />
        </>
      )}
    </article>
  );
}

/* ---------------- operation table ---------------- */

function OpsCard({ ops, reported, loading, error }: { ops: OpSpecView[] | undefined; reported: boolean | undefined; loading: boolean; error: boolean }) {
  const t = useT();
  return (
    <article className="card card--flush span-12">
      <CardHead title={t("操作表", "Operation table")} sub={t("Windows 上允许执行的操作;命令行在那边拼,不从这边传", "what may run on the Windows side; argv is built there, never sent")} />
      {loading ? (
        <div className="row t-muted" style={{ padding: 16 }}><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("正在读取操作表…", "Reading the operation table…")}</div>
      ) : error ? (
        <div style={{ padding: "0 16px 16px" }}><ErrorBanner>{t("读不到操作表。", "Cannot read the operation table.")}</ErrorBanner></div>
      ) : !ops || ops.length === 0 ? (
        <div style={{ padding: "0 16px 16px" }}>
          <Empty icon="list" title={t("操作表是空的", "The operation table is empty")}>{t("agent 没连上,或它没报任何操作。", "The agent is not connected, or it reported no operations.")}</Empty>
        </div>
      ) : (
        <>
          {reported === false ? (
            <div style={{ padding: "0 16px" }}>
              <div className="banner banner--warn"><Icon name="alert" /><p>{t("这个 agent 版本较旧,没有上报操作表。下面的行是按它的能力推出来的,一律按「会改动板子」处理。", "This agent predates the operation table. The rows are inferred from its capabilities and each one is treated as changing the board.")}</p><span /></div>
            </div>
          ) : null}
          <div className="table-wrap">
            <table className="table table--dense">
              <thead>
                <tr>
                  <th>{t("操作", "Operation")}</th>
                  <th>{t("做什么", "What it does")}</th>
                  <th>{t("改动板子", "Changes the board")}</th>
                  <th>{t("需要 fastboot", "Needs fastboot")}</th>
                  <th>{t("收镜像", "Takes an image")}</th>
                  <th className="num">{t("超时", "Timeout")}</th>
                </tr>
              </thead>
              <tbody>
                {ops.map((o) => (
                  <tr key={o.name}>
                    <td className="mono">{o.name}</td>
                    <td>{o.summary}</td>
                    <td>{o.mutates ? <Pill tone="warn">✓</Pill> : "—"}</td>
                    <td>{o.needs === "fastboot-device" ? "✓" : "—"}</td>
                    <td>{o.image ? "✓" : "—"}</td>
                    <td className="num mono">{fmtTimeout(o.timeout_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </article>
  );
}
