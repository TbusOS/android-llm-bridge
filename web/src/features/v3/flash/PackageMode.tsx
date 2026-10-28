/**
 * Whole-package flashing: pick a manifest in the workspace, check the plan,
 * run it. Baseline: docs/webui-preview-v3-flash.html (#m-pkg).
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { CardHead, Confirm, Empty, ErrorBanner, Pill, toast } from "../../../components/v3/ui";
import { type NdjsonEvent, type PlanRow, type PlanView, checkPlan, flashOp, runPlan } from "../../../lib/api3";
import { useT } from "../../../lib/i18n";
import { failureOf, fmtBytes, fmtClock, looksLikeImage, outputOf, problemLines, walkWorkspace } from "./flashApi";
import { OutputBlock } from "./OutputBlock";
import type { FlashRun } from "./useFlashRun";

const SKIPPABLE_ACTIONS = ["setvar", "getvar", "oem", "reboot-bootloader", "reboot"];
const MANIFEST_ROOT = "flash-images";

type RowSt = "wait" | "run" | "ok" | "fail";

export interface PackageModeProps {
  run: FlashRun;
  available: boolean;
  board: string | null;
  /** Why nothing can run right now (flash unavailable); shown as a tooltip. */
  disabledReason: string;
}

const isPartitionRow = (r: PlanRow) => r.action === "flash" || r.action === "erase";
/** The skip came from the lists this page sends, not from the plan itself. */
const userSkipped = (r: PlanRow) => !!r.skip && r.skip.startsWith("--skip");

export function PackageMode({ run, available, board, disabledReason }: PackageModeProps) {
  const t = useT();
  const files = useQuery({ queryKey: ["flash-manifests"], queryFn: ({ signal }) => walkWorkspace(MANIFEST_ROOT, 2, signal), retry: false, refetchOnWindowFocus: false });
  const [showImages, setShowImages] = useState(false);
  const [manifest, setManifest] = useState<string | null>(null);
  const [skip, setSkip] = useState<string[]>([]);
  const [skipActions, setSkipActions] = useState<string[]>([]);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [rowSt, setRowSt] = useState<Record<number, RowSt>>({});
  const [rowText, setRowText] = useState<Record<number, string>>({});
  const [curLine, setCurLine] = useState<number | null>(null);
  const [stepNo, setStepNo] = useState<{ n: number; of: number } | null>(null);
  const [rebootConfirm, setRebootConfirm] = useState(false);
  // Progress text lands on the row that is running when it arrives.
  const curLineRef = useRef<number | null>(null);

  const mine = run.label === "plan";
  const running = run.running;
  const done = mine ? run.done : null;

  const manifests = useMemo(() => {
    const all = files.data ?? [];
    return showImages ? all : all.filter((f) => !looksLikeImage(f.path));
  }, [files.data, showImages]);
  const hiddenCount = (files.data ?? []).length - manifests.length;

  const doCheck = useCallback(async (m: string, sk: string[], sa: string[]) => {
    setChecking(true);
    setCheckError(null);
    try {
      const p = await checkPlan(m, sk, sa);
      if (Array.isArray(p.rows)) setPlan(p);
      else {
        setPlan(null);
        const e = (p as unknown as { error?: { code?: string; message?: string } }).error;
        setCheckError(`${e?.code ?? "ERROR"}${e?.message ? `: ${e.message}` : ""}`);
      }
    } catch (e) {
      setPlan(null);
      setCheckError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(false);
    }
  }, []);

  const pick = (m: string) => {
    setManifest(m);
    setSkip([]);
    setSkipActions([]);
    setPlan(null);
    setCheckError(null);
    setConfirm(false);
  };

  const toggle = (row: PlanRow, checked: boolean) => {
    if (!manifest) return;
    let sk = skip;
    let sa = skipActions;
    if (isPartitionRow(row) && row.partition) {
      const p = row.partition;
      sk = checked ? skip.filter((x) => x !== p) : [...skip.filter((x) => x !== p), p];
    } else {
      const a = row.action;
      sa = checked ? skipActions.filter((x) => x !== a) : [...skipActions.filter((x) => x !== a), a];
    }
    setSkip(sk);
    setSkipActions(sa);
    setConfirm(false);
    void doCheck(manifest, sk, sa);
  };

  const problems = plan?.problems ?? [];
  const badLines = useMemo(() => problemLines(problems), [problems]);
  const rows = plan?.rows ?? [];
  const toRun = rows.filter((r) => !r.skip);
  const skippedN = rows.length - toRun.length;
  const totalBytes = toRun.reduce((a, r) => a + (r.size ?? 0), 0);
  const hasUserdata = toRun.some((r) => isPartitionRow(r) && r.partition === "userdata");
  const untickable = rows.filter((r) => badLines.has(r.line) && isPartitionRow(r) && !r.skip && r.partition);
  const canRun = !!plan && plan.ok && problems.length === 0 && toRun.length > 0 && available && !running && !checking;

  const onPlanEvent = (ev: NdjsonEvent) => {
    if (ev.ev === "progress") {
      const text = String(ev.text ?? "");
      const m = /^line (\d+):/.exec(text);
      if (ev.phase === "step" && m) {
        const line = Number(m[1]);
        setRowSt((s) => {
          const next = { ...s };
          for (const k of Object.keys(next)) if (next[Number(k)] === "run") next[Number(k)] = "ok";
          next[line] = "run";
          return next;
        });
        curLineRef.current = line;
        setCurLine(line);
        setStepNo({ n: Number(ev.done ?? 0), of: Number(ev.total ?? 0) });
      } else if (text) {
        setRowText((s) => (curLineRef.current === null ? s : { ...s, [curLineRef.current]: text }));
      }
    } else if (ev.ev === "done") {
      const finalRows = (ev.rows as PlanRow[] | undefined) ?? [];
      if (finalRows.length) {
        const next: Record<number, RowSt> = {};
        for (const r of finalRows) {
          if (r.status === "ok") next[r.line] = "ok";
          else if (r.status === "failed") next[r.line] = "fail";
          else if (r.status === "not-run") next[r.line] = "wait";
        }
        setRowSt(next);
      }
      curLineRef.current = null;
      setCurLine(null);
    }
  };

  const start = () => {
    if (!manifest || !plan) return;
    setConfirm(false);
    const init: Record<number, RowSt> = {};
    for (const r of toRun) init[r.line] = "wait";
    setRowSt(init);
    setRowText({});
    curLineRef.current = null;
    setCurLine(null);
    setStepNo(null);
    void run.start("plan", (on) => runPlan(manifest, skip, skipActions, plan.fingerprint, on), onPlanEvent);
  };

  const rebootFastbootd = () => {
    setRebootConfirm(false);
    void run.start("reboot", (on) => flashOp("reboot", { target: "fastboot" }, on)).then((d) => {
      toast(d.ok ? t("已发送:重启到 fastbootd。", "Sent: reboot to fastbootd.") : t("重启没有成功。", "The reboot did not go through."));
    });
  };

  const doneRows = (done?.rows as PlanRow[] | undefined) ?? [];
  const bytesDone = (() => {
    let sum = 0;
    for (const r of toRun) {
      const st = rowSt[r.line];
      if (st === "ok") sum += r.size ?? 0;
      else if (st === "run" && run.frac !== null) sum += (r.size ?? 0) * run.frac;
    }
    return sum;
  })();
  const pct = totalBytes > 0 ? Math.round((bytesDone / totalBytes) * 100) : stepNo && stepNo.of > 0 ? Math.round((Math.max(0, stepNo.n - 1) / stepNo.of) * 100) : 0;
  const curRow = rows.find((r) => r.line === curLine);

  return (
    <div className="stack" id="m-pkg">
        {/* step 1 */}
        <article className="card">
          <CardHead
            title={t("1 · 选清单", "1 · Pick a manifest")}
            sub={t("在工作区 flash-images/ 下找到", "found under flash-images/ in the workspace")}
            actions={
              <button type="button" className="btn btn--text btn--sm" onClick={() => void files.refetch()} disabled={files.isFetching}>
                <Icon name="refresh" size="sm" />
                {t("重新扫描", "Rescan")}
              </button>
            }
          />
          {files.isLoading ? (
            <div className="row t-muted"><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("正在扫描工作区…", "Scanning the workspace…")}</div>
          ) : files.isError ? (
            <ErrorBanner>{t("读不到工作区目录:", "Cannot read the workspace: ")}{files.error instanceof Error ? files.error.message : String(files.error)}</ErrorBanner>
          ) : manifests.length === 0 ? (
            <Empty icon="folder" title={t("flash-images/ 下没有文件", "Nothing under flash-images/")}>
              {t("把整包(清单文本加镜像)放到这台 Linux 工作区的 flash-images/ 下,再点「重新扫描」。", "Put the package (manifest text plus images) under flash-images/ in this Linux host's workspace, then press Rescan.")}
            </Empty>
          ) : (
            <ul className="list manifests">
              {manifests.map((f) => (
                <li className="list__item" key={f.path}>
                  <span className="list__lead">
                    <input type="radio" name="manifest" className="check" aria-label={f.path} checked={manifest === f.path} onChange={() => pick(f.path)} disabled={running} />
                  </span>
                  <span className="list__title mono">{f.path}</span>
                  <span className="list__trail t-muted num">{fmtClock(f.mtime)}</span>
                  <span className="list__meta">{fmtBytes(f.size)}</span>
                </li>
              ))}
            </ul>
          )}
          {hiddenCount > 0 || showImages ? (
            <button type="button" className="btn btn--text btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setShowImages((v) => !v)}>
              {showImages ? t("隐藏镜像文件", "Hide image files") : t(`显示另外 ${hiddenCount} 个镜像类文件`, `Show ${hiddenCount} more image-like files`)}
            </button>
          ) : null}
          <div className="banner banner--info">
            <Icon name="info" />
            <p>{t("整包放在这台 Linux 的工作区 flash-images/ 下(指向别处的软链接也行)。网页上传上限 2 GiB,整包一般更大。", "Put packages under flash-images/ in the workspace on this Linux host (a symlink to elsewhere is fine). Browser uploads stop at 2 GiB, and a whole package is usually bigger.")}</p>
            <div className="banner__actions">
              <button type="button" className="btn btn--text btn--sm" onClick={() => toast(t(`工作区路径:${MANIFEST_ROOT}/`, `Workspace path: ${MANIFEST_ROOT}/`))}>
                {t("显示路径", "Show path")}
              </button>
            </div>
          </div>
          <div className="row">
            <button type="button" className="btn btn--filled" disabled={!manifest || checking || running || !available} title={!available ? disabledReason : undefined} onClick={() => manifest && void doCheck(manifest, skip, skipActions)}>
              {checking ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="check-circle" />}
              {t("检查计划", "Check the plan")}
            </button>
            <span className="t-body-small t-muted">{t("检查时不写任何东西。", "Nothing is written while checking.")}</span>
          </div>
        </article>

        {/* step 2 */}
        <article className="card plan">
          <CardHead title={t("2 · 计划", "2 · Plan")} />
          {checkError ? <ErrorBanner>{checkError}</ErrorBanner> : null}
          {!plan && !checkError ? (
            <Empty icon="list" title={t("还没有计划", "No plan yet")}>
              {t("选一份清单,点「检查计划」。这一步只读清单和镜像,不碰板子。", "Pick a manifest and press Check the plan. It only reads the manifest and images, never the board.")}
            </Empty>
          ) : null}
          {plan ? (
            <>
              <div className="plan-sum">
                <Pill>{rows.length} {t("行", "rows")}</Pill>
                <Pill tone="info">{toRun.length} {t("执行", "to run")}</Pill>
                <Pill>{skippedN} {t("跳过", "skipped")}</Pill>
                <Pill>{fmtBytes(totalBytes)}</Pill>
                <span className="pill mono" title="fingerprint"># {plan.fingerprint.slice(0, 8)}</span>
              </div>
              {problems.length > 0 ? (
                <div className="banner banner--error" role="alert">
                  <Icon name="error" />
                  <p>
                    {t(`${problems.length} 个问题,一行都不会执行:`, `${problems.length} problem(s), so no row will run:`)}
                    {problems.map((p, i) => (
                      <span key={i} style={{ display: "block" }} className="mono">{p}</span>
                    ))}
                  </p>
                  <div className="banner__actions">
                    {untickable.length > 0 ? (
                      <button
                        type="button"
                        className="btn btn--text btn--sm"
                        disabled={checking || running}
                        onClick={() => {
                          if (!manifest) return;
                          const sk = [...new Set([...skip, ...untickable.map((r) => r.partition as string)])];
                          setSkip(sk);
                          void doCheck(manifest, sk, skipActions);
                        }}
                      >
                        {t("勾掉这些行", "Untick them")}
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : null}
              <div className="well table-wrap">
                <table className="table table--dense">
                  <thead>
                    <tr>
                      <th><span className="visually-hidden">{t("执行", "run")}</span></th>
                      <th className="num">{t("行", "Line")}</th>
                      <th>{t("动作", "Action")}</th>
                      <th>{t("分区 / 检查", "Partition / check")}</th>
                      <th>{t("镜像", "Image")}</th>
                      <th className="num">{t("大小", "Size")}</th>
                      <th>sha256</th>
                      <th>{t("名单", "List")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const locked = !!r.skip && !userSkipped(r);
                      const cls = r.skip ? "is-skip" : badLines.has(r.line) ? "is-problem" : undefined;
                      const target = isPartitionRow(r) ? r.partition : r.action === "oem" ? r.command : r.key ? `${r.key}${r.value ? ` = ${r.value}` : ""}` : "";
                      const canSkipAction = isPartitionRow(r) || SKIPPABLE_ACTIONS.includes(r.action);
                      return (
                        <tr key={r.line} className={cls}>
                          <td>
                            <input
                              type="checkbox"
                              className="check"
                              checked={!r.skip}
                              disabled={locked || !canSkipAction || checking || running}
                              title={locked ? r.skip : !isPartitionRow(r) ? t(`会一并影响所有「${r.action}」行`, `Affects every "${r.action}" row`) : undefined}
                              aria-label={t(`执行第 ${r.line} 行`, `run line ${r.line}`)}
                              onChange={(e) => toggle(r, e.target.checked)}
                            />
                          </td>
                          <td className="num mono">{r.line}</td>
                          <td>{r.action}</td>
                          <td className="mono">{target}</td>
                          <td className="mono">{r.file ?? ""}</td>
                          <td className="num mono">{r.size ? fmtBytes(r.size) : ""}</td>
                          <td className="mono">{r.sha256 ? r.sha256.slice(0, 8) : ""}</td>
                          <td>
                            {r.skip ? (
                              <Pill title={r.skip}>{t("跳过", "skipped")}</Pill>
                            ) : badLines.has(r.line) ? (
                              <Pill tone="bad">{t("有问题", "problem")}</Pill>
                            ) : isPartitionRow(r) ? (
                              <Pill tone="ok">✓</Pill>
                            ) : r.action === "getvar" ? (
                              <Pill tone="ok">{t("检查", "check")}</Pill>
                            ) : r.action === "oem" ? (
                              <Pill tone="ok">{t("允许", "allowed")}</Pill>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="t-body-small t-muted" style={{ margin: 0 }}>
                {t("勾掉或勾上一行会重新检查,得到新的指纹;执行的永远是你确认的那一份。", "Unticking a row re-checks the plan and gives a new fingerprint; only the plan you confirm is run.")}
              </p>
            </>
          ) : null}
        </article>

        {/* step 3 */}
        {running && mine ? (
          <article className="card">
            <CardHead
              title={t("3 · 执行中", "3 · Running")}
              actions={
                <span className="pill pill--info">
                  <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />
                  <span>{stepNo ? t(`第 ${stepNo.n} / ${stepNo.of} 行`, `row ${stepNo.n} of ${stepNo.of}`) : t("准备中", "starting")}</span>
                </span>
              }
            />
            <div className="run-head">
              <span>
                <b className="mono">{curRow ? `${curRow.action} ${curRow.partition ?? curRow.command ?? curRow.key ?? ""}${curRow.file ? ` ← ${curRow.file}` : ""}` : "…"}</b>{" "}
                {curRow?.size && run.frac !== null ? <span className="t-muted">{fmtBytes(curRow.size * run.frac)} / {fmtBytes(curRow.size)}</span> : null}
              </span>
              <span className="t-muted num">{pct}%</span>
              <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></div>
            </div>
          </article>
        ) : null}
        {(running || done) && mine ? (
          <article className="card">
            <CardHead title={running ? t("各行进度", "Rows") : t("3 · 结果", "3 · Result")} />
            {done && !running ? <ResultBanner done={done} doneRows={doneRows} onFastbootd={() => setRebootConfirm(true)} /> : null}
            <Confirm
              open={rebootConfirm}
              tone="danger"
              message={t(`把${board ?? "板子"}重启到 fastbootd,继续刷逻辑分区。`, `Reboot ${board ?? "the board"} to fastbootd to flash the logical partitions.`)}
              confirmLabel={t("重启到 fastbootd", "Reboot to fastbootd")}
              cancelLabel={t("取消", "Cancel")}
              onConfirm={rebootFastbootd}
              onCancel={() => setRebootConfirm(false)}
            />
            <ul className="list">
              {(doneRows.length ? doneRows : toRun).filter((r) => !r.skip && r.status !== "skipped").map((r) => {
                const st = rowSt[r.line] ?? "wait";
                const dur = doneRows.find((d) => d.line === r.line)?.duration_s;
                return (
                  <li className="list__item" key={r.line}>
                    <span className="list__lead">
                      <span className={`rowstate rowstate--${st}`}>
                        {st === "ok" ? <Icon name="check" size="sm" /> : st === "fail" ? <Icon name="error" size="sm" /> : st === "run" ? <span className="spinner" style={{ width: 16, height: 16 }} /> : <Icon name="clock" size="sm" />}
                      </span>
                    </span>
                    <span className="list__title mono">{r.line} {r.action} {r.partition ?? r.command ?? r.key ?? ""}</span>
                    <span className="list__trail mono t-muted">{dur ? `${dur.toFixed(1)} s` : ""}</span>
                    <span className="list__meta">{st === "fail" ? (r.code ?? "FAILED") : st === "wait" ? t("等待", "waiting") : (rowText[r.line] ?? (st === "ok" ? "OKAY" : ""))}</span>
                  </li>
                );
              })}
            </ul>
            <div className="banner">
              <Icon name="info" />
              <p>{t("作业结束前一直占着烧录锁,其他写入插不进来。", "The job holds the flash lock until it ends, so no other write can slip in between rows.")}</p>
            </div>
          </article>
        ) : null}
        <article className="card">
          <CardHead title={mine && (running || done) ? t("再执行一次", "Run again") : t("3 · 执行", "3 · Run")} />
          {confirm ? (
            <Confirm
              open
              message={t(
                `会写到 ${board ?? "这块板子"}。第一次写入前会再读一次 product 核对板子。执行 ${toRun.length} 行${hasUserdata ? ",包括 userdata" : ",不含 userdata"},在这里撤不回。`,
                `This writes to ${board ?? "the board"}. Its product is read again right before the first write. ${toRun.length} rows run, ${hasUserdata ? "userdata included" : "userdata not included"}, and cannot be undone from here.`,
              )}
              confirmLabel={t("烧整包", "Flash package")}
              cancelLabel={t("取消", "Cancel")}
              onConfirm={start}
              onCancel={() => setConfirm(false)}
            />
          ) : (
            <div className="row">
              <button type="button" className="btn btn--danger" disabled={!canRun} aria-disabled={!canRun} title={!available ? disabledReason : problems.length ? t("先解决上面的问题", "Fix the problems above first") : !plan ? t("先检查计划", "Check the plan first") : undefined} onClick={() => setConfirm(true)}>
                <Icon name="package" />
                {t("烧整包", "Flash package")}
              </button>
              {problems.length > 0 ? <span className="t-body-small t-muted">{t("有问题的计划一行都不会执行。", "A plan with problems runs nothing.")}</span> : null}
            </div>
          )}
          <p className="t-body-small t-muted" style={{ margin: 0 }}>
            {t("执行时可以关掉页面,作业会继续跑;回来在这里和「最近作业」里都能看到结果。", "You can close this page while it runs; the job keeps going and its result shows up here and under Recent jobs.")}
          </p>
        </article>
    </div>
  );
}

function ResultBanner({ done, doneRows, onFastbootd }: { done: NdjsonEvent; doneRows: PlanRow[]; onFastbootd: () => void }) {
  const t = useT();
  const ran = Number(done.ran ?? 0);
  const skipped = Number(done.skipped ?? 0);
  const notRun = Number(done.not_run ?? 0);
  if (done.ok) {
    return (
      <div className="banner banner--ok">
        <Icon name="check-circle" />
        <p>{t(`整包烧完:执行 ${ran} 行,跳过 ${skipped} 行,用时 ${Number(done.duration_s ?? 0).toFixed(1)} 秒。`, `Package flashed: ${ran} rows ran, ${skipped} skipped, ${Number(done.duration_s ?? 0).toFixed(1)} s.`)}</p>
        <span />
      </div>
    );
  }
  const f = failureOf(done);
  const failedLine = Number(done.failed_line ?? 0);
  const out = outputOf(done);
  const wantsFastbootd = /fastbootd/i.test(f.message) || /fastbootd/i.test(out);
  const okRows = doneRows.filter((r) => r.status === "ok").length;
  return (
    <>
      <div className="banner banner--error" role="alert">
        <Icon name="error" />
        <p>
          <b>{f.code || "FAILED"}</b>
          {failedLine ? <> · {t(`停在第 ${failedLine} 行`, `stopped at line ${failedLine}`)}</> : null}
          {f.message ? <> · <span className="mono">{f.message}</span></> : null}
          <br />
          <span>{t(`已完成 ${okRows || Math.max(0, ran - 1)} 行,${notRun} 行未执行。`, `${okRows || Math.max(0, ran - 1)} rows done, ${notRun} not run.`)}</span>
        </p>
        <div className="banner__actions">
          {wantsFastbootd ? <button type="button" className="btn btn--text btn--sm" onClick={onFastbootd}>{t("重启到 fastbootd", "Reboot to fastbootd")}</button> : null}
        </div>
      </div>
      {out ? <OutputBlock text={out} title={failedLine ? t(`停在第 ${failedLine} 行时的输出`, `Output where it stopped (line ${failedLine})`) : t("整包作业的输出", "Package job output")} fileName={`flash-plan${failedLine ? `-line-${failedLine}` : ""}.log`} maxHeight={160} /> : null}
    </>
  );
}
