/**
 * Timeline (v3): serial, dmesg, logcat and flash jobs on one clock.
 * Baseline: docs/webui-preview-v3-timeline.html.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { type CSSProperties, type PointerEvent as RPointerEvent, type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { CardHead, Confirm, Empty, ErrorBanner, Menu, PageHead, toast } from "../../../components/v3/ui";
import { copyText } from "../../../lib/clipboard";
import {
  type BootView,
  captureBoot,
  captureDensity,
  captureLines,
  type CaptureSummary,
  type DensityView,
  fetchBenchBoard,
  fetchFlashJobs,
  listCaptures,
  startCapture,
  type TimelineLine,
} from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";
import { BootBars } from "../overview/OverviewPage";
import { clamp, clock, isProblem, nearest, stamp, ticks } from "./timelineUtil";
import "./timeline.css";

type Src = "uart" | "dmesg" | "logcat" | "job";
const ROW_CAP = 300;
const LABEL_W = 132;
const TAG_CLASS: Record<string, string> = { uart: "src--uart", dmesg: "src--kernel", logcat: "src--logcat", job: "src--job" };

interface Rec {
  total: number;
  elapsed: number;
  lines: Record<string, number>;
  boot: boolean;
}

export function TimelinePage() {
  const t = useT();
  const device = useApp((s) => s.device);
  const openSheet = useShell((s) => s.openSheet);
  const search = useSearch({ strict: false }) as { record?: string; name?: string };
  const qc = useQueryClient();
  // A board on the bench serial port with no adb (in its bootloader, or adb
  // dead) is exactly when a timeline matters most. The hub records the
  // serial console without a device; those recordings live under
  // workspace/timeline instead of workspace/devices/<serial>/timeline.
  const board = useDashboardQuery({ queryKey: ["bench-board"], queryFn: ({ signal }) => fetchBenchBoard(signal), refetchMs: 30_000 });
  const serialOnly = !device && !!board.data?.serial?.bound;
  const canRecord = !!device || serialOnly;

  const caps = useDashboardQuery({
    queryKey: ["captures", device],
    queryFn: ({ signal }) => listCaptures(device, signal),
    refetchMs: 60_000,
    enabled: canRecord,
  });
  const list: CaptureSummary[] = caps.data?.data ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const cap = list.find((c) => c.name === picked) ?? list.find((c) => c.name === search.name) ?? list[0] ?? null;
  const name = cap?.name ?? null;
  const base = Date.parse(cap?.started_at ?? "");

  /* ---- recording ---- */
  const [rec, setRec] = useState<Rec | null>(null);
  const [confirmBoot, setConfirmBoot] = useState(false);
  const askedBoot = useRef(false);
  useEffect(() => {
    if (search.record === "boot" && !askedBoot.current) {
      askedBoot.current = true;
      setConfirmBoot(true);
    }
  }, [search.record]);

  const record = async (seconds: number, reboot: boolean) => {
    setConfirmBoot(false);
    setRec({ total: seconds, elapsed: 0, lines: {}, boot: reboot });
    try {
      const sources = serialOnly ? ["uart"] : ["logcat", "dmesg", "uart"];
      const done = await startCapture(device, { duration_s: seconds, sources, reboot: reboot ? "system" : "" }, (ev) => {
        if (ev.ev === "progress") {
          setRec((r) => (r ? { ...r, elapsed: Number(ev.elapsed_s ?? 0), lines: (ev.lines as Record<string, number>) ?? {} } : r));
        }
      });
      if (done.ok) {
        toast(t("录好了。", "Recording finished."));
        if (typeof done.name === "string") setPicked(done.name);
      } else {
        const e = done.error as { code?: string; message?: string } | undefined;
        toast(`${e?.code ?? "ERROR"}: ${e?.message ?? ""}`);
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
    } finally {
      setRec(null);
      void qc.invalidateQueries({ queryKey: ["captures", device] });
    }
  };

  /* ---- density, boot ---- */
  const dur = Math.max(cap?.duration_s ?? 0, 1);
  const bucket = Math.min(60, Math.max(0.1, Math.round((dur / 600) * 100) / 100));
  const dens = useDashboardQuery({
    queryKey: ["tl-density", device, name, bucket],
    queryFn: ({ signal }) => captureDensity(name as string, device, bucket, signal),
    refetchMs: 3_600_000,
    enabled: !!name,
  });
  const d = dens.data?.data;
  const total = Math.max(d?.t1 ?? 0, cap?.duration_s ?? 0, 1);
  const boot = useDashboardQuery({
    queryKey: ["boot", device, name],
    queryFn: ({ signal }) => captureBoot(name as string, device, signal),
    refetchMs: 3_600_000,
    enabled: !!name && !!cap?.has_boot,
  });
  const jobs = useDashboardQuery({
    queryKey: ["flash-jobs", 20],
    queryFn: ({ signal }) => fetchFlashJobs(20, signal),
    refetchMs: 120_000,
    enabled: !!name,
  });

  /* ---- range, selection, filters ---- */
  const [userRange, setUserRange] = useState<{ key: string; a: number; b: number } | null>(null);
  const [selT, setSelT] = useState<{ key: string; t: number } | null>(null);
  const firstBad = useMemo(() => d?.markers.find((m) => m.kind === "panic" || m.kind === "crash" || m.kind === "anr") ?? d?.markers.find((m) => m.kind === "avc") ?? null, [d]);
  const defWin = Math.min(total, 10);
  const defStart = total <= 30 ? 0 : clamp((firstBad?.t ?? 0) - defWin * 0.4, 0, total - defWin);
  const defRange = total <= 30 ? { a: 0, b: total } : { a: defStart, b: defStart + defWin };
  const range = userRange && userRange.key === name ? userRange : { key: name ?? "", ...defRange };
  const r0 = range.a;
  const r1 = Math.max(range.b, r0 + 0.1);
  const setRange = (a: number, b: number) => {
    const w = clamp(b - a, 0.5, total);
    const s = clamp(a, 0, Math.max(0, total - w));
    setUserRange({ key: name ?? "", a: s, b: s + w });
  };
  const zoom = (f: number) => {
    const c = (r0 + r1) / 2;
    const w = clamp((r1 - r0) * f, 0.5, total);
    setRange(c - w / 2, c + w / 2);
  };

  const [srcs, setSrcs] = useState<Record<Src, boolean>>({ uart: true, dmesg: true, logcat: true, job: true });
  const [grepIn, setGrepIn] = useState("");
  const [grep, setGrep] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setGrep(grepIn.trim()), 300);
    return () => window.clearTimeout(id);
  }, [grepIn]);

  const chosen = (["logcat", "dmesg", "uart"] as const).filter((s) => srcs[s]);
  const linesQ = useDashboardQuery({
    queryKey: ["tl-lines", device, name, chosen.join(","), grep, r0.toFixed(2), r1.toFixed(2)],
    queryFn: ({ signal }) =>
      captureLines(name as string, { device, sources: chosen.length === 3 ? undefined : chosen.join(","), grep, t0: r0, t1: r1, limit: 2000 }, signal),
    refetchMs: 3_600_000,
    enabled: !!name && chosen.length > 0,
  });
  const allLines: TimelineLine[] = chosen.length > 0 ? linesQ.data?.data?.lines ?? [] : [];
  const lines = useMemo(() => (onlyProblems ? allLines.filter(isProblem) : allLines), [allLines, onlyProblems]);

  const selIdx = useMemo(() => {
    if (lines.length === 0) return -1;
    if (selT && selT.key === name) return nearest(lines, selT.t);
    const bad = lines.findIndex(isProblem);
    return bad >= 0 ? bad : 0;
  }, [lines, selT, name]);
  const sel = selIdx >= 0 ? (lines[selIdx] as TimelineLine) : null;
  const curT = selT && selT.key === name ? selT.t : sel?.t ?? null;

  const selectT = useCallback(
    (tt: number) => {
      setSelT({ key: name ?? "", t: tt });
      if (tt < r0 || tt > r1) {
        const w = r1 - r0;
        setRange(tt - w / 2, tt + w / 2);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [name, r0, r1, total],
  );

  const winStart = Math.max(0, Math.min(selIdx - ROW_CAP / 2, lines.length - ROW_CAP));
  const shown = lines.slice(winStart, winStart + ROW_CAP);

  const marks = (d?.markers ?? []).filter((m) => m.t >= r0 && m.t <= r1);
  const jobMarks = useMemo(() => {
    if (!Number.isFinite(base)) return [];
    return (jobs.data?.jobs ?? [])
      .map((j) => ({ id: j.id, label: j.label || j.op, ok: j.ok, t: (Date.parse(j.started_at ?? "") - base) / 1000 }))
      .filter((j) => Number.isFinite(j.t) && j.t >= 0 && j.t <= total);
  }, [jobs.data, base, total]);

  /* ---- cursor position needs the label column width ---- */
  const bodyRef = useRef<HTMLDivElement>(null);
  const [labelW, setLabelW] = useState(LABEL_W);
  useLayoutEffect(() => {
    const measure = () => {
      const el = bodyRef.current?.querySelector(".track__label") as HTMLElement | null;
      if (el && el.offsetWidth > 0) setLabelW(el.offsetWidth);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [name]);

  const askAssistant = () => {
    if (!sel) return;
    const near = allLines.filter((l) => Math.abs(l.t - sel.t) <= 1).slice(0, 80);
    const text = near.map((l) => `${clock(base, l.t, true)} [${l.src}${l.level ? `/${l.level}` : ""}] ${l.text}`).join("\n").slice(0, 6000);
    openSheet({
      zh: `时间轴 · ${clock(base, sel.t - 1)}–${clock(base, sel.t + 1)}`,
      en: `Timeline · ${clock(base, sel.t - 1)}–${clock(base, sel.t + 1)}`,
      detail: `Recording ${name}, device ${device}. Lines within 1 s of ${clock(base, sel.t, true)}:\n${text}`,
    });
  };
  const copyLine = async () => {
    if (!sel) return;
    toast((await copyText(sel.text)) ? t("已复制这一行。", "Line copied.") : t("复制失败。", "Copy failed."));
  };

  /* ---- render ---- */
  const eyebrow = serialOnly ? (
    <span>{t("只有串口", "Serial only")}{board.data?.serial?.com ? ` · ${board.data.serial.com}` : ""}</span>
  ) : (
    <span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span>
  );
  const head = (
    <PageHead
      eyebrow={eyebrow}
      title={t("时间轴", "Timeline")}
      desc={t("串口、dmesg、logcat 和烧录作业放在同一个时钟上,一眼看出谁先谁后。", "Serial, dmesg, logcat and flash jobs on one clock, so you can see what happened first.")}
      actions={
        <>
          <Menu
            align="end"
            trigger={(p) => (
              <button type="button" className="btn btn--tonal" disabled={!canRecord || !!rec} {...p}>
                <Icon name="record" />
                {t("录一段", "Record")}
                <Icon name="chevron-down" size="sm" />
              </button>
            )}
            items={[
              { key: "30", icon: "clock", label: t("30 秒", "30 seconds"), onSelect: () => void record(30, false) },
              { key: "60", icon: "clock", label: t("1 分钟", "1 minute"), onSelect: () => void record(60, false) },
              { key: "300", icon: "clock", label: t("5 分钟", "5 minutes"), onSelect: () => void record(300, false) },
            ]}
          />
          <button type="button" className="btn btn--filled" disabled={!canRecord || !!rec} onClick={() => setConfirmBoot(true)}>
            <Icon name="power" />
            {t("录一次开机", "Record a boot")}
          </button>
        </>
      }
    />
  );

  const confirm = confirmBoot ? (
    <div style={{ marginBottom: 16 }}>
      <Confirm
        open
        message={t(
          `先开始录,再重启 ${device ?? "板子"}。录 60 秒,串口上的 bootloader 输出也能录进来。`,
          `Start recording, then reboot ${device ?? "the board"}. Recording runs 60 s so the bootloader lines on the serial console are caught too.`,
        )}
        confirmLabel={t("重启并录制", "Reboot and record")}
        cancelLabel={t("取消", "Cancel")}
        onConfirm={() => void record(60, true)}
        onCancel={() => setConfirmBoot(false)}
      />
    </div>
  ) : null;

  if (!canRecord) {
    return (
      <>
        {head}
        <article className="card">
          <Empty icon="timeline" title={t("没有设备,台架串口也没接上", "No device, and no bench serial port")}>
            {t(
              "在顶栏选一台 adb 设备;或者在台架页给当前板子配好串口,没有 adb 时也能只录串口。",
              "Pick an adb device in the top bar, or give the current board a serial port on the Bench page — then the serial console can be recorded without adb.",
            )}
          </Empty>
        </article>
      </>
    );
  }

  const serialNote = serialOnly ? (
    <div className="banner banner--info" style={{ marginBottom: 16 }}>
      <Icon name="info" />
      <p>{t("没有 adb 设备,这里只录、只列串口。dmesg 和 logcat 要等 adb 连上。", "No adb device, so only the serial console is recorded and listed here. dmesg and logcat need adb.")}</p>
      <span />
    </div>
  ) : null;

  const recBox = rec ? (
    <div className="tl-rec" role="status" style={{ marginBottom: 16 }}>
      <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
      <b>{rec.boot ? t("正在录开机", "Recording the boot") : t("正在录", "Recording")}</b>
      <span className="mono">{Math.min(rec.elapsed, rec.total).toFixed(0)} / {rec.total} s</span>
      <div className="tl-rec__bar"><i style={{ width: `${clamp((rec.elapsed / rec.total) * 100, 0, 100)}%` }} /></div>
      <span className="mono t-muted">
        {t("串口", "serial")} {rec.lines.uart ?? 0} · dmesg {rec.lines.dmesg ?? 0} · logcat {rec.lines.logcat ?? 0}
      </span>
    </div>
  ) : null;

  if (caps.isError) {
    return (
      <>
        {head}
        {serialNote}
        {confirm}
        <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void caps.refetch()}>{t("重试", "Retry")}</button>}>
          {t("读不到录制列表:", "Could not read the recordings: ")}{caps.error?.message}
        </ErrorBanner>
      </>
    );
  }
  if (caps.isLoading) {
    return (
      <>
        {head}
        {serialNote}
        {confirm}
        {recBox}
        <div className="skeleton" style={{ height: 240 }} />
      </>
    );
  }
  if (!cap) {
    return (
      <>
        {head}
        {serialNote}
        {confirm}
        {recBox}
        <article className="card">
          <Empty icon="timeline" title={serialOnly ? t("串口上还没有录制", "No serial recordings yet") : t("这台设备还没有录制", "No recordings for this device yet")}>
            {t("点右上角的「录一段」,或者「录一次开机」。录的时候串口、dmesg、logcat 一起开始。", "Use Record at the top right, or Record a boot. Serial, dmesg and logcat start together.")}
          </Empty>
        </article>
      </>
    );
  }

  const frac = (tt: number) => clamp((tt - r0) / (r1 - r0), 0, 1);
  const rangeLabel = t(
    `${clock(base, r0)} – ${clock(base, r1)},共 ${total.toFixed(0)} 秒中的 ${(r1 - r0).toFixed(1)} 秒`,
    `${clock(base, r0)} – ${clock(base, r1)}, ${(r1 - r0).toFixed(1)} s of ${total.toFixed(0)} s`,
  );
  const srcTag = (s: string) => (
    <span className={`src-tag ${TAG_CLASS[s] ?? ""}`}>{s === "uart" ? t("串口", "serial") : s === "job" ? t("烧录", "flash") : s}</span>
  );
  const missing = (s: "uart" | "dmesg" | "logcat") => (cap.sources[s] ?? 0) === 0;

  return (
    <>
      {head}
      {serialNote}
      {confirm}
      {recBox}
      <div className="stack">
        {/* picker + filters */}
        <article className="card">
          <div className="tl-bar">
            <label className="visually-hidden" htmlFor="tl-cap">{t("录制", "Recording")}</label>
            <select className="select" id="tl-cap" value={cap.name} onChange={(e) => setPicked(e.target.value)}>
              {list.map((c) => (
                <option key={c.name} value={c.name}>
                  {stamp(c.started_at)} · {c.duration_s.toFixed(0)} s · {(c.sources.logcat ?? 0) + (c.sources.dmesg ?? 0) + (c.sources.uart ?? 0)}{c.has_boot ? " · boot" : ""}
                </option>
              ))}
            </select>
            <div className="seg seg--sm" role="group" aria-label={t("来源", "sources")}>
              {(["uart", "dmesg", "logcat", "job"] as const).map((s) => (
                <button key={s} type="button" aria-pressed={srcs[s]} onClick={() => setSrcs((p) => ({ ...p, [s]: !p[s] }))}>
                  {srcTag(s)}
                </button>
              ))}
            </div>
            <label className="input--search">
              <Icon name="search" size="sm" />
              <input value={grepIn} onChange={(e) => setGrepIn(e.target.value)} placeholder={t("在这段里搜索(支持正则)", "Search this recording (regex)")} aria-label={t("搜索", "search")} />
            </label>
            <button type="button" className="chip" aria-pressed={onlyProblems} onClick={() => setOnlyProblems((v) => !v)}>
              {t("只看问题", "Problems only")}
            </button>
            <div className="row" style={{ gap: 0 }}>
              <button type="button" className="icon-btn" aria-label={t("缩小", "Zoom out")} onClick={() => zoom(2)}><Icon name="zoom-out" /></button>
              <button type="button" className="icon-btn" aria-label={t("放大", "Zoom in")} onClick={() => zoom(0.5)}><Icon name="zoom-in" /></button>
            </div>
          </div>
        </article>

        {/* tracks */}
        <article className="card">
          <CardHead
            title={rangeLabel}
            sub={t("在概览条上拖动选范围,点标记跳到那一行", "drag on the overview to pick a range; click a mark to jump to its line")}
            actions={
              <button type="button" className="btn btn--text btn--sm" disabled={!firstBad} onClick={() => firstBad && selectT(firstBad.t)}>
                <Icon name="alert" size="sm" />
                {t("第一个问题", "First problem")}
              </button>
            }
          />
          {dens.isError ? (
            <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void dens.refetch()}>{t("重试", "Retry")}</button>}>
              {t("读不到这段录制的密度数据:", "Could not read the density data: ")}{dens.error?.message}
            </ErrorBanner>
          ) : !d ? (
            <div className="skeleton" style={{ height: 200 }} />
          ) : (
            <>
              <div className="tracks">
                <div className="track track--overview">
                  <div className="track__label"><span>{t("整段", "Whole recording")}</span></div>
                  <Overview d={d} total={total} r0={r0} r1={r1} onRange={setRange} />
                </div>
                <div className="track track--axis">
                  <div />
                  <div className="track__lane tl-axis">
                    {ticks(r0, r1).map((v) => (
                      <span key={v} style={{ left: `${frac(v) * 100}%`, transform: frac(v) > 0.95 ? "translateX(-100%)" : frac(v) < 0.02 ? "none" : undefined }}>{clock(base, v)}</span>
                    ))}
                  </div>
                </div>
                <div className="tracks__body" ref={bodyRef}>
                  {srcs.uart ? (
                    <Lane label={srcTag("uart")} count={cap.sources.uart ?? 0} empty={missing("uart") ? t("没录到串口(检查串口是否被占用)", "no serial lines (is the port in use?)") : null}>
                      <Bars d={d} r0={r0} r1={r1} h={36} series={[{ vals: d.tracks.uart, color: "--alb-color-src-uart" }]} />
                    </Lane>
                  ) : null}
                  {srcs.dmesg ? (
                    <Lane label={srcTag("dmesg")} count={cap.sources.dmesg ?? 0} empty={missing("dmesg") ? t("没录到 dmesg", "no dmesg lines") : null}>
                      <Bars d={d} r0={r0} r1={r1} h={36} series={[{ vals: d.tracks.dmesg, color: "--alb-color-src-kernel" }]} />
                    </Lane>
                  ) : null}
                  {srcs.logcat ? (
                    <Lane label={srcTag("logcat")} count={cap.sources.logcat ?? 0} tall empty={missing("logcat") ? t("没录到 logcat(adb 是否在线?)", "no logcat lines (is adb online?)") : null}>
                      <Bars
                        d={d}
                        r0={r0}
                        r1={r1}
                        h={56}
                        series={[
                          { vals: sum(d.tracks.logcat.V, d.tracks.logcat.D), color: "--alb-color-log-d" },
                          { vals: d.tracks.logcat.I, color: "--alb-color-log-i" },
                          { vals: d.tracks.logcat.W, color: "--alb-color-log-w" },
                          { vals: sum(d.tracks.logcat.E, d.tracks.logcat.F), color: "--alb-color-log-e" },
                        ]}
                      />
                    </Lane>
                  ) : null}
                  {srcs.job ? (
                    <div className="track">
                      <div className="track__label">{srcTag("job")}<small>{jobMarks.length ? jobMarks.length : t("无作业", "no job")}</small></div>
                      <div className="track__lane">
                        {jobMarks.filter((j) => j.t >= r0 && j.t <= r1).map((j) => (
                          <i key={j.id} className="marker marker--job" style={{ left: `${frac(j.t) * 100}%` }} title={`${j.label} · ${clock(base, j.t)}`} />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <div className="track">
                    <div className="track__label"><span>{t("标记", "Marks")}</span><small className="num">{marks.length}</small></div>
                    <div className="track__lane">
                      {marks.map((m, i) => (
                        <i
                          key={`${m.t}-${i}`}
                          className={`marker marker--${m.kind}`}
                          role="button"
                          tabIndex={0}
                          style={{ left: `${frac(m.t) * 100}%` }}
                          title={`${m.kind} · ${clock(base, m.t, true)}`}
                          aria-label={`${m.kind} ${clock(base, m.t, true)}`}
                          onClick={() => selectT(m.t)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") selectT(m.t);
                          }}
                        />
                      ))}
                    </div>
                  </div>
                  {curT !== null && curT >= r0 && curT <= r1 ? (
                    <div className="tl-cursor" style={{ left: `calc(${labelW}px + (100% - ${labelW}px) * ${frac(curT)})` } as CSSProperties}>
                      <span>{clock(base, curT, true)}</span>
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="legend">
                <span style={{ "--src": "var(--alb-color-log-e)" } as CSSProperties}><i />logcat E/F</span>
                <span style={{ "--src": "var(--alb-color-log-w)" } as CSSProperties}><i />W</span>
                <span style={{ "--src": "var(--alb-color-log-i)" } as CSSProperties}><i />I</span>
                <span style={{ "--src": "var(--alb-color-log-d)" } as CSSProperties}><i />D/V</span>
                <span><i className="marker marker--panic" style={{ position: "static", margin: 0, width: 10, height: 10 }} />{t("崩溃", "panic / crash")}</span>
                <span><i className="marker marker--avc" style={{ position: "static", margin: 0, width: 10, height: 10 }} />ANR / SELinux</span>
                <span><i className="marker marker--boot" style={{ position: "static", margin: 0, width: 10, height: 10 }} />{t("开机节点", "boot milestone")}</span>
              </div>
            </>
          )}
        </article>

        {/* boot */}
        {cap.has_boot && boot.data?.data?.found ? (
          <article className="card">
            <CardHead title={t("这次开机", "Boot breakdown")} sub={t("每一段花了多久", "how long each stage took")} />
            <BootBars boot={boot.data.data as BootView} />
          </article>
        ) : null}

        {/* lines + detail */}
        <section className="grid tl-split">
          <article className="card card--flush span-8">
            <CardHead
              title={t("这段时间的日志", "Lines in this range")}
              sub={
                linesQ.isLoading
                  ? t("读取中…", "loading…")
                  : grep
                    ? t(`${lines.length} 行含 “${grep}”`, `${lines.length} lines match “${grep}”`)
                    : t(`${lines.length} 行`, `${lines.length} lines`) + (linesQ.data?.data?.truncated ? t(",只取前 2000 行", ", first 2000 only") : "")
              }
            />
            {linesQ.isError ? (
              <div style={{ padding: 16 }}>
                <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void linesQ.refetch()}>{t("重试", "Retry")}</button>}>
                  {t("读不到日志行:", "Could not read the lines: ")}{linesQ.error?.message}
                </ErrorBanner>
              </div>
            ) : chosen.length === 0 ? (
              <Empty icon="filter" title={t("没有选日志来源", "No log source selected")}>{t("在上面至少打开串口、dmesg、logcat 中的一个。", "Turn on at least one of serial, dmesg or logcat above.")}</Empty>
            ) : lines.length === 0 && !linesQ.isLoading ? (
              <Empty icon="logs" title={t("这个范围里没有日志", "No lines in this range")}>{grep || onlyProblems ? t("去掉搜索词或「只看问题」试试。", "Clear the search or Problems only.") : t("拖动概览条换一个范围。", "Drag on the overview to pick another range.")}</Empty>
            ) : (
              <div className="logs" style={{ "--log-cols": "112px 58px 22px 128px minmax(0, 1fr)", maxHeight: 460 } as CSSProperties}>
                {shown.map((l, i) => {
                  const idx = winStart + i;
                  const bad = l.level === "E" || l.level === "F" ? "E" : l.level === "W" ? "W" : "";
                  return (
                    <button
                      type="button"
                      key={`${l.t}-${idx}`}
                      className={`log${bad ? ` log--${bad}` : ""}`}
                      aria-pressed={idx === selIdx}
                      onClick={() => setSelT({ key: name ?? "", t: l.t })}
                    >
                      <span className="log__t">{clock(base, l.t, true)}</span>
                      {srcTag(l.src)}
                      {l.level ? <span className={`lvl lvl--${l.level}`}>{l.level}</span> : <span />}
                      <span className="log__tag">{l.tag ?? ""}</span>
                      <span className="log__msg">{l.text}</span>
                    </button>
                  );
                })}
                {lines.length > ROW_CAP ? (
                  <div className="tl-more">{t(`显示第 ${winStart + 1}–${winStart + shown.length} 行,共 ${lines.length} 行;点标记或缩小范围看其余。`, `Showing ${winStart + 1}–${winStart + shown.length} of ${lines.length}; click a mark or narrow the range for the rest.`)}</div>
                ) : null}
              </div>
            )}
          </article>

          <article className="card span-4">
            <CardHead title={t("选中的这一行", "Selected line")} />
            {!sel ? (
              <p className="t-muted" style={{ margin: 0 }}>{t("还没有选中的行。", "No line selected.")}</p>
            ) : (
              <>
                <dl className="detail-kv">
                  <dt>{t("时刻", "Time")}</dt>
                  <dd className="mono">{clock(base, sel.t, true)} <span className="t-muted">{t(`(录制开始后 ${sel.t.toFixed(2)} 秒)`, `(+${sel.t.toFixed(2)} s into the recording)`)}</span></dd>
                  <dt>{t("来源", "Source")}</dt>
                  <dd>{srcTag(sel.src)}{sel.tag ? <> <span className="mono">{sel.tag}</span></> : null}</dd>
                  <dt>{t("前 1 秒", "Before it")}</dt>
                  <dd>{around(allLines, sel.t - 1, sel.t, t)}</dd>
                  <dt>{t("之后", "After it")}</dt>
                  <dd>{nextText(allLines, sel, t)}</dd>
                </dl>
                <div className="row">
                  <button type="button" className="btn btn--tonal btn--sm" onClick={askAssistant}>
                    <Icon name="spark" size="sm" />
                    {t("问助手这 2 秒", "Ask about these 2 s")}
                  </button>
                  <Link className="btn btn--text btn--sm" to="/logs" search={{ tab: "logcat" } as never}>{t("在日志页打开", "Open in Logs")}</Link>
                  <button type="button" className="icon-btn icon-btn--sm" aria-label={t("复制", "Copy")} onClick={() => void copyLine()}><Icon name="copy" size="sm" /></button>
                </div>
              </>
            )}
            <div className="divider" />
            <CardHead level={3} title={t("这段里的标记", "Marks in this range")} />
            {marks.length === 0 ? (
              <p className="t-muted" style={{ margin: 0 }}>{t("这个范围里没有标记。", "No marks in this range.")}</p>
            ) : (
              <ul className="list">
                {marks.slice(0, 8).map((m, i) => (
                  <li key={`${m.t}-${i}`}>
                    <button type="button" className="list__item list__item--action" onClick={() => selectT(m.t)}>
                    <span className="list__lead"><i className={`marker marker--${m.kind}`} style={{ position: "static", margin: 0 }} /></span>
                    <span className="list__title">{m.kind}</span>
                    <span className="list__trail mono t-muted">{clock(base, m.t, true)}</span>
                    <span className="list__meta">{m.text}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </article>
        </section>
      </div>
    </>
  );
}

/* ---------------- helpers ---------------- */

function sum(a: readonly number[], b: readonly number[]): number[] {
  const n = Math.max(a.length, b.length);
  return Array.from({ length: n }, (_, i) => (a[i] ?? 0) + (b[i] ?? 0));
}

type TFn = (zh: string, en: string) => string;

function around(lines: TimelineLine[], from: number, to: number, t: TFn): string {
  const w = lines.filter((l) => l.t >= from && l.t < to);
  const bad = w.filter((l) => l.level === "E" || l.level === "F" || l.level === "W").length;
  return t(`${w.length} 行,其中 ${bad} 行警告或错误`, `${w.length} lines, ${bad} of them warnings or errors`);
}

function nextText(lines: TimelineLine[], sel: TimelineLine, t: TFn): string {
  const nxt = lines.find((l) => l.t > sel.t);
  if (!nxt) return t("后面没有更多行", "no later lines in this range");
  return `+${(nxt.t - sel.t).toFixed(2)} s · ${nxt.text.slice(0, 80)}`;
}

function Lane({ label, count, empty, tall, children }: { label: ReactNode; count: number; empty: string | null; tall?: boolean; children: ReactNode }) {
  return (
    <div className="track" style={tall ? { minHeight: 64 } : undefined}>
      <div className="track__label">{label}<small className="num">{count.toLocaleString()}</small></div>
      <div className="track__lane">{empty ? <span className="tl-lane-empty">{empty}</span> : children}</div>
    </div>
  );
}

interface Serie {
  vals: readonly number[];
  color: string;
}

/** Stacked bars for the buckets inside [r0, r1]. */
function Bars({ d, r0, r1, h, series }: { d: DensityView; r0: number; r1: number; h: number; series: Serie[] }) {
  const b = d.bucket_s;
  const i0 = Math.max(0, Math.floor(r0 / b));
  const i1 = Math.max(i0 + 1, Math.ceil(r1 / b));
  const n = i1 - i0;
  const w = 1000 / n;
  let max = 1;
  for (let i = i0; i < i1; i++) max = Math.max(max, series.reduce((a, s) => a + (s.vals[i] ?? 0), 0));
  const rects: ReactNode[] = [];
  for (let i = i0; i < i1; i++) {
    let acc = 0;
    series.forEach((s, k) => {
      const v = s.vals[i] ?? 0;
      if (!v) return;
      const hh = (v / max) * (h - 2);
      rects.push(<rect key={`${i}-${k}`} x={((i - i0) * w + 0.3).toFixed(1)} width={Math.max(0.5, w - 0.6).toFixed(1)} y={(h - acc - hh).toFixed(1)} height={hh.toFixed(1)} style={{ fill: `var(${s.color})` }} />);
      acc += hh;
    });
  }
  return (
    <svg viewBox={`0 0 1000 ${h}`} preserveAspectRatio="none" role="img" aria-label="density">
      {rects}
    </svg>
  );
}

/** Whole-recording strip with a draggable brush. */
function Overview({ d, total, r0, r1, onRange }: { d: DensityView; total: number; r0: number; r1: number; onRange: (a: number, b: number) => void }) {
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null);
  const anchor = useRef<number | null>(null);
  const at = (e: RPointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return r.width > 0 ? clamp((e.clientX - r.left) / r.width, 0, 1) * total : null;
  };
  const down = (e: RPointerEvent<HTMLDivElement>) => {
    const x = at(e);
    if (x === null) return;
    anchor.current = x;
    setDrag({ a: x, b: x });
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* not supported in this environment */
    }
  };
  const move = (e: RPointerEvent<HTMLDivElement>) => {
    const x = at(e);
    if (x === null || anchor.current === null) return;
    setDrag({ a: Math.min(anchor.current, x), b: Math.max(anchor.current, x) });
  };
  const up = () => {
    if (anchor.current === null || !drag) return;
    const moved = drag.b - drag.a > total * 0.01;
    if (moved) onRange(drag.a, drag.b);
    else {
      const w = r1 - r0;
      onRange(drag.a - w / 2, drag.a + w / 2);
    }
    anchor.current = null;
    setDrag(null);
  };
  const all = useMemo(() => {
    const n = d.tracks.uart.length;
    const lc = d.tracks.logcat;
    return Array.from({ length: n }, (_, i) => (d.tracks.uart[i] ?? 0) + (d.tracks.dmesg[i] ?? 0) + (["V", "D", "I", "W", "E", "F"] as const).reduce((a, k) => a + (lc[k][i] ?? 0), 0));
  }, [d]);
  const sh = drag ?? { a: r0, b: r1 };
  return (
    <div className="track__lane tl-overview" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} data-testid="overview">
      <Bars d={{ ...d }} r0={0} r1={total} h={28} series={[{ vals: all, color: "--md-sys-color-outline" }]} />
      <div className="tl-brush" style={{ left: `${(sh.a / total) * 100}%`, width: `${Math.max(0.5, ((sh.b - sh.a) / total) * 100)}%` }} />
    </div>
  );
}
