/**
 * Overview (v3): where the board is, which channels reach it, how it is
 * doing, how the last boot went, what broke, what happened lately; then
 * the assistant side (live agent session, LLM backends, service counts).
 * Board cards come first: that is what a person debugging a board needs.
 * Baseline: docs/webui-preview-v3.html.
 */
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { useAgentStatus, useBenchBoard, useFlashStatusQuery } from "../../../components/v3/TopBar";
import { CardHead, Confirm, Dot, Empty, ErrorBanner, Menu, PageHead, Pill, Spark, toast } from "../../../components/v3/ui";
import {
  type AuditResponse,
  type DeviceDetailsResponse,
  type DiagArtifactsResponse,
  fetchAudit,
  fetchDeviceDetails,
  fetchDiagArtifacts,
  postReboot,
  type RebootRequest,
} from "../../../lib/api";
import { type BootView, breakIn, captureBoot, listCaptures, pullPstore, type PstoreRecord } from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useAuditStream } from "../../../lib/hooks/useAuditStream";
import { useDevices } from "../../../lib/hooks/useDevices";
import { duration, useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";
import { type MetricSample, useMetricsStream } from "../../inspect/useMetricsStream";
import { BackendsCard, LiveSessionCard, SummaryCard } from "./AssistantCards";
import { deviceListCopy, deviceListState, transportLabel } from "../../../lib/overviewData";
import { DevicesCard, showDeviceStrip, useRefreshDevices } from "./DevicesCard";

type RebootKind = RebootRequest["mode"] | "breakin";

export function OverviewPage() {
  const t = useT();
  const device = useApp((s) => s.device);
  const navigate = useNavigate();
  const { devices } = useDevices();
  const [pending, setPending] = useState<RebootKind | null>(null);
  const [busy, setBusy] = useState(false);

  const doReboot = async (kind: RebootKind) => {
    setBusy(true);
    try {
      if (kind === "breakin") {
        const done = await breakIn(device, "auto", () => {});
        if (done.ok) toast(t("已停在 bootloader,去控制台的串口页签操作。", "Stopped in the bootloader; use the serial tab in Console."));
        else toast(`${(done.error as { code?: string })?.code ?? "ERROR"}: ${(done.error as { message?: string })?.message ?? ""}`);
      } else {
        const r = await postReboot(device, { mode: kind, wait_boot: false });
        if (r.ok) toast(t("已发送重启。", "Reboot sent."));
        else toast(`${r.error?.code ?? "ERROR"}: ${r.error?.message ?? ""}`);
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  const rebootCopy: Record<RebootKind, [string, string, string, string]> = {
    normal: ["把板子重启回系统。adb 不在时改走串口。", "Reboot the board into Android. Uses the serial console when adb is gone.", "重启", "Reboot"],
    bootloader: ["把板子重启到 bootloader。adb 会断开,串口照常可用;fastboot 出现后烧录页就能用。", "Reboot into the bootloader. adb drops off, the serial console stays; Flash works once fastboot appears.", "重启到 bootloader", "Reboot to bootloader"],
    recovery: ["把板子重启到 recovery。", "Reboot into recovery.", "重启到 recovery", "Reboot to recovery"],
    fastboot: ["把板子重启到 fastbootd(用户态 fastboot)。", "Reboot into fastbootd (userspace fastboot).", "重启到 fastbootd", "Reboot to fastbootd"],
    sideload: ["把板子重启到 sideload。", "Reboot into sideload.", "重启", "Reboot"],
    breakin: ["重启板子,在 bootloader 倒计时里按键停下,之后在控制台的串口页签里操作。", "Reboot and press a key during the bootloader countdown; then work in the serial tab of Console.", "重启并停下", "Reboot and stop"],
  };

  return (
    <>
      <PageHead
        eyebrow={<span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span>}
        title={t("总览", "Overview")}
        desc={t("板子现在在哪一步、哪几条通道能连上、最近出了什么问题。", "Where the board is, which channels reach it, and what went wrong lately.")}
        actions={
          <>
            <Menu
              align="end"
              trigger={(p) => (
                <button type="button" className="btn btn--outlined" {...p}>
                  <Icon name="power" />
                  {t("重启", "Reboot")}
                  <Icon name="chevron-down" size="sm" />
                </button>
              )}
              items={[
                { key: "n", icon: "power", label: t("重启到系统", "To Android"), desc: t("adb 不在时改走串口", "uses serial when adb is gone"), onSelect: () => setPending("normal") },
                { key: "b", icon: "bolt", label: t("重启到 bootloader(fastboot)", "To bootloader (fastboot)"), desc: t("adb 会断开,台架上出现 fastboot", "adb drops off; fastboot appears"), onSelect: () => setPending("bootloader") },
                { key: "r", icon: "shield", label: t("重启到 recovery", "To recovery"), onSelect: () => setPending("recovery") },
                { key: "f", icon: "package", label: t("重启到 fastbootd", "To fastbootd"), desc: t("用户态 fastboot,刷逻辑分区用", "userspace fastboot, for logical partitions"), onSelect: () => setPending("fastboot") },
              ]}
            />
            <button type="button" className="btn btn--outlined" onClick={() => setPending("breakin")}>
              <Icon name="stop" />
              {t("停在 bootloader", "Stop in bootloader")}
            </button>
            <button type="button" className="btn btn--filled" onClick={() => void navigate({ to: "/timeline", search: { record: "boot" } as never })}>
              <Icon name="record" />
              {t("录一次开机", "Record a boot")}
            </button>
          </>
        }
      />
      {pending ? (
        <div style={{ marginBottom: 16 }}>
          <Confirm
            open
            busy={busy}
            message={t(rebootCopy[pending][0], rebootCopy[pending][1])}
            confirmLabel={t(rebootCopy[pending][2], rebootCopy[pending][3])}
            cancelLabel={t("取消", "Cancel")}
            onConfirm={() => void doReboot(pending)}
            onCancel={() => setPending(null)}
          />
        </div>
      ) : null}
      <section className="grid">
        {showDeviceStrip(devices, device) ? <DevicesCard /> : null}
        <BoardStateCard onBootloader={() => setPending("bootloader")} />
        <ConnectionsCard onBootloader={() => setPending("bootloader")} />
        <PerfCard />
        <LastBootCard />
        <ProblemsCard />
        <ActivityCard />
        <QuickCard />
        <LiveSessionCard />
        <BackendsCard />
        <SummaryCard />
      </section>
    </>
  );
}

/* ---------------- board state ---------------- */

function BoardStateCard({ onBootloader }: { onBootloader: () => void }) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const list = useDevices();
  const { devices } = list;
  const refresh = useRefreshDevices();
  const listState = deviceListState(list);
  const tl = transportLabel(list.transportName);
  const listBroken = listState.kind === "fetch" || listState.kind === "transport";
  const current = devices.find((d) => d.serial === device);
  const details = useDashboardQuery<DeviceDetailsResponse>({
    queryKey: ["device-details", device],
    queryFn: ({ signal }) => fetchDeviceDetails(device as string, signal),
    refetchMs: 30_000,
    enabled: !!device && current?.state === "device",
  });
  const d = details.data?.device ?? null;
  const online = current?.state === "device";

  const steps: [string, string, string, string][] = online
    ? [["上电", "Power on", "ROM / SPL", "done"], ["Bootloader", "Bootloader", "", "done"], ["内核", "Kernel", "", "done"], ["Android 启动", "Android start", "", "done"], ["开机完成", "Booted", d ? duration(lang, d.uptime_sec) : "", "now"]]
    : [["上电", "Power on", "", "todo"], ["Bootloader", "Bootloader", "", "todo"], ["内核", "Kernel", "", "todo"], ["Android 启动", "Android start", "", "todo"], ["开机完成", "Booted", "", "todo"]];

  return (
    <article className="card span-8">
      <CardHead
        title={t("板子状态", "Board state")}
        sub={t("由 adb、串口、台架 USB 三处合起来判断", "read from adb, serial and the bench USB together")}
        actions={
          <button type="button" className="btn btn--text btn--sm" onClick={refresh}>
            <Icon name="refresh" size="sm" />
            {t("刷新", "Refresh")}
          </button>
        }
      />
      {!device && listState.kind === "loading" ? (
        <div className="skeleton" style={{ height: 160 }} />
      ) : !device && listBroken ? (
        <Empty
          icon="error"
          title={deviceListCopy(t, listState, tl).title}
          action={
            <div className="row">
              <button type="button" className="btn btn--tonal btn--sm" onClick={refresh}><Icon name="refresh" size="sm" />{t("重新读取", "Read again")}</button>
              <Link className="btn btn--text btn--sm" to="/bench">{t("去台架页看看", "Check the bench")}</Link>
            </div>
          }
        >
          {deviceListCopy(t, listState, tl).body}
        </Empty>
      ) : !device ? (
        <Empty
          icon="phone"
          title={t("还没有设备", "No device yet")}
          action={
            <div className="row">
              <Link className="btn btn--tonal btn--sm" to="/bench">{t("去台架页看看", "Check the bench")}</Link>
              <button type="button" className="btn btn--text btn--sm" onClick={refresh}><Icon name="refresh" size="sm" />{t("重新读取", "Read again")}</button>
            </div>
          }
        >
          {t(
            `${tl} 里没有设备。板子可能停在 bootloader、在 fastboot,或者没上电;也可能是台架电脑上的 adb 没启动,或者台架隧道断了。去台架页看 USB 和串口。`,
            `${tl} lists no device. The board may be in its bootloader, in fastboot, or off; or adb is not running on the bench computer, or the bench tunnel is down. The Bench page shows USB and serial.`,
          )}
        </Empty>
      ) : (
        <>
          <div className="state">
            {online ? (
              <Pill tone="ok"><Dot tone="ok" />{t("已开机", "Booted")}</Pill>
            ) : listState.kind === "loading" ? (
              <Pill><Dot />{t("读取中", "reading")}</Pill>
            ) : (
              <Pill tone="warn"><Dot tone="warn" />{current?.state ?? t("离线", "offline")}</Pill>
            )}
            <span className="state__big">
              {d ? `Android ${d.release} · ${t("开机完成", "boot completed")}` : online || listState.kind === "loading" ? t("读取中…", "Reading…") : t(`${tl} 不可用`, `${tl} not available`)}
            </span>
            {d ? (
              <span className="state__meta mono">
                API {d.sdk} · {t(`已运行 ${duration(lang, d.uptime_sec)}`, `up ${duration(lang, d.uptime_sec)}`)}
              </span>
            ) : null}
          </div>
          <ol className="steps" aria-label={t("开机路径", "Boot path")}>
            {steps.map(([zh, en, sub, st]) => (
              <li key={en} className={st === "done" ? "is-done" : st === "now" ? "is-now" : ""}>
                <b>{t(zh, en)}</b>
                {sub ? <span>{sub}</span> : null}
              </li>
            ))}
          </ol>
          {listBroken ? (
            <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={refresh}>{t("重新读取", "Read again")}</button>}>
              <b>{deviceListCopy(t, listState, tl).title}</b> {deviceListCopy(t, listState, tl).body}
            </ErrorBanner>
          ) : !online && listState.kind !== "loading" ? (
            <div className="banner banner--warn">
              <Icon name="alert" />
              <p>
                {current
                  ? t("adb 看不到这块板子。它可能在 bootloader、fastboot、recovery,或者卡死了;串口控制台通常还能用。", "adb cannot see the board. It may be in the bootloader, fastboot or recovery, or hung; the serial console usually still works.")
                  : t(
                      `选中的 ${device} 不在 ${tl} 设备列表里:可能在重启,或者线被拔了。选择会一直保留,回来后自动接上;要换板子用顶栏的菜单。串口控制台通常还能用。`,
                      `The selected ${device} is not in the ${tl} device list: rebooting, or unplugged? The choice is kept and reconnects when it comes back; switch boards from the top bar menu. The serial console usually still works.`,
                    )}
              </p>
              <div className="banner__actions">
                <Link className="btn btn--text btn--sm" to="/console">{t("打开串口", "Open serial")}</Link>
                <button type="button" className="btn btn--text btn--sm" onClick={onBootloader}>{t("重启到 bootloader", "To bootloader")}</button>
              </div>
            </div>
          ) : null}
          {details.isError ? (
            <div className="banner banner--error"><Icon name="error" /><p>{String(details.error?.message ?? details.error)}</p><span /></div>
          ) : null}
          {d ? (
            <dl className="kv facts">
              <div><dt>{t("型号", "Model")}</dt><dd>{d.brand} {d.model}</dd></div>
              <div><dt>{t("硬件", "Hardware")}</dt><dd className="mono">{d.hardware || d.extras.soc}</dd></div>
              <div><dt>Android</dt><dd>{d.release} · API {d.sdk}</dd></div>
              <div><dt>ABI</dt><dd className="mono">{d.abi}</dd></div>
              <div className="kv--wide"><dt>{t("构建指纹", "Build fingerprint")}</dt><dd className="mono">{d.build_fingerprint}</dd></div>
              <div><dt>CPU</dt><dd className="num">{d.extras.cpu_cores} {t("核", "cores")} · {(d.extras.cpu_max_khz / 1e6).toFixed(2)} GHz</dd></div>
              <div><dt>{t("内存", "Memory")}</dt><dd className="num">{(d.extras.ram_total_kb / 1048576).toFixed(1)} GiB</dd></div>
              <div><dt>{t("电池", "Battery")}</dt><dd className="num">{d.battery_level}%{d.extras.temp_c ? ` · ${d.extras.temp_c.toFixed(1)} °C` : ""}</dd></div>
              <div><dt>{t("屏幕", "Display")}</dt><dd className="num">{d.extras.display.size ?? "—"}</dd></div>
              {Object.entries(d.storage).slice(0, 2).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd className="num">{v}</dd></div>
              ))}
              <div><dt>{t("序列号", "Serial number")}</dt><dd className="mono">{d.serialno || device}</dd></div>
            </dl>
          ) : null}
        </>
      )}
    </article>
  );
}

/* ---------------- connections ---------------- */

function ConnectionsCard({ onBootloader }: { onBootloader: () => void }) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const { devices } = useDevices();
  const agent = useAgentStatus();
  const board = useBenchBoard();
  const flash = useFlashStatusQuery();
  const current = devices.find((d) => d.serial === device);
  const a = agent.data?.agents?.[0] as (Record<string, unknown> & { name?: string; build?: string; connected_at?: number }) | undefined;
  const serial = board.data?.serial;
  return (
    <article className="card span-4">
      <CardHead title={t("连接", "Connections")} sub={t("能连上这块板子的四条路", "four ways to reach this board")} />
      <ul className="list">
        <li className="list__item">
          <span className="list__lead"><Icon name="terminal" /></span>
          <span className="list__title"><Dot tone={current?.state === "device" ? "ok" : current ? "warn" : "idle"} inline />adb</span>
          <span className="list__trail">{current ? <Pill tone={current.state === "device" ? "ok" : "warn"}>{current.state}</Pill> : <Pill>{t("没有", "none")}</Pill>}</span>
          <span className="list__meta mono">{current?.serial ?? t("adb 里没有设备", "no device in adb")}</span>
        </li>
        <li className="list__item">
          <span className="list__lead"><Icon name="plug" /></span>
          <span className="list__title"><Dot tone={serial?.bound ? "ok" : serial?.com ? "warn" : "idle"} inline />{t("串口控制台", "Serial console")}</span>
          <span className="list__trail"><Link className="btn btn--text btn--sm" to="/console">{t("打开", "Open")}</Link></span>
          <span className="list__meta">{serial?.com ? `${serial.com} · ${serial.baud} · ${t(`${serial.readers} 个读者`, `${serial.readers} reader(s)`)}` : board.data?.problem ?? t("hub 没配串口", "no serial port configured")}</span>
        </li>
        <li className="list__item">
          <span className="list__lead"><Icon name="bolt" /></span>
          <span className="list__title"><Dot tone={flash.data?.busy ? "busy" : flash.data?.available ? "ok" : "idle"} inline />fastboot</span>
          <span className="list__trail"><button type="button" className="btn btn--text btn--sm" onClick={onBootloader}>{t("切过去", "Go there")}</button></span>
          <span className="list__meta">
            {flash.data?.busy ? flash.data.job : flash.data?.available ? t(`烧录可用 · 名单 ${flash.data.partitions.length} 个分区`, `ready · list of ${flash.data.partitions.length} partitions`) : t("台架上没有 fastboot 能力", "the bench has no fastboot")}
          </span>
        </li>
        <li className="list__item">
          <span className="list__lead"><Icon name="server" /></span>
          <span className="list__title"><Dot tone={a ? "ok" : "idle"} inline />{t("台架 agent", "Bench agent")}</span>
          <span className="list__trail">{a ? <Pill tone="ok">{t("在线", "online")}</Pill> : <Pill>{t("离线", "offline")}</Pill>}</span>
          <span className="list__meta mono">{a ? `${a.name ?? ""} · ${String(a.build ?? "").slice(0, 12)}${typeof a.connected_at === "number" ? ` · ${duration(lang, Date.now() / 1000 - a.connected_at)}` : ""}` : t("没有 agent 连上来", "no agent connected")}</span>
        </li>
      </ul>
    </article>
  );
}

/* ---------------- performance ---------------- */

function series(samples: MetricSample[], f: (s: MetricSample) => number): number[] {
  return samples.map(f);
}

function PerfCard() {
  const t = useT();
  const device = useApp((s) => s.device);
  const m = useMetricsStream(150);
  const { connect, disconnect } = m;
  useEffect(() => {
    if (!device) return;
    connect(device, 300);
    return () => disconnect();
  }, [device, connect, disconnect]);
  const s = m.samples;
  const last = s[s.length - 1];
  const tiles = useMemo(() => {
    if (!last) return [];
    const memTotal = last.mem_total_kb / 1048576 || 1;
    return [
      { k: "cpu", zh: "CPU", en: "CPU", v: last.cpu_pct_total.toFixed(0), unit: "%", sub: t(`${last.cpu_freq_khz.length} 核`, `${last.cpu_freq_khz.length} cores`), lines: [series(s, (x) => x.cpu_pct_total)], lo: 0, hi: 100, fmt: (v: number) => `${v.toFixed(0)}%` },
      { k: "mem", zh: "内存", en: "Memory", v: (last.mem_used_kb / 1048576).toFixed(2), unit: `/ ${memTotal.toFixed(1)} GiB`, sub: t(`可用 ${(last.mem_avail_kb / 1048576).toFixed(1)} GiB`, `${(last.mem_avail_kb / 1048576).toFixed(1)} GiB free`), lines: [series(s, (x) => x.mem_used_kb / 1048576)], lo: 0, hi: memTotal, fmt: (v: number) => `${v.toFixed(1)} GiB` },
      { k: "temp", zh: "CPU 温度", en: "CPU temp", v: last.cpu_temp_c.toFixed(1), unit: "°C", sub: last.battery_temp_c ? t(`电池 ${last.battery_temp_c.toFixed(1)} °C`, `battery ${last.battery_temp_c.toFixed(1)} °C`) : "", lines: [series(s, (x) => x.cpu_temp_c)], lo: 20, hi: 100, fmt: (v: number) => `${v.toFixed(0)} °C`, warn: last.cpu_temp_c > 70 },
      { k: "gpu", zh: "GPU", en: "GPU", v: last.gpu_util_pct >= 0 ? last.gpu_util_pct.toFixed(0) : "—", unit: last.gpu_util_pct >= 0 ? "%" : "", sub: last.gpu_freq_hz ? `${(last.gpu_freq_hz / 1e6).toFixed(0)} MHz` : t("读不到", "unknown"), lines: [series(s, (x) => Math.max(0, x.gpu_util_pct))], lo: 0, hi: 100, fmt: (v: number) => `${v.toFixed(0)}%` },
      { k: "net", zh: "网络", en: "Network", v: (last.net_rx_bytes_per_s / 1e6).toFixed(2), unit: "MB/s ↓", sub: `↑ ${(last.net_tx_bytes_per_s / 1e3).toFixed(0)} KB/s`, lines: [series(s, (x) => x.net_rx_bytes_per_s / 1e6), series(s, (x) => x.net_tx_bytes_per_s / 1e6)], lo: 0, hi: Math.max(0.1, ...series(s, (x) => Math.max(x.net_rx_bytes_per_s, x.net_tx_bytes_per_s) / 1e6)), fmt: (v: number) => `${v.toFixed(1)} MB/s` },
      { k: "disk", zh: "磁盘", en: "Disk", v: (last.disk_read_kb_per_s / 1024).toFixed(1), unit: "MB/s r", sub: `w ${(last.disk_write_kb_per_s / 1024).toFixed(1)} MB/s`, lines: [series(s, (x) => x.disk_read_kb_per_s / 1024), series(s, (x) => x.disk_write_kb_per_s / 1024)], lo: 0, hi: Math.max(1, ...series(s, (x) => Math.max(x.disk_read_kb_per_s, x.disk_write_kb_per_s) / 1024)), fmt: (v: number) => `${v.toFixed(0)} MB/s` },
    ];
  }, [s, last, t]);
  return (
    <article className="card span-12">
      <CardHead
        title={t("性能", "Performance")}
        sub={t(`最近 ${Math.round((s.length * m.intervalS) / 60) || 0} 分钟,每 ${m.intervalS} 秒一个点`, `last ${Math.round((s.length * m.intervalS) / 60) || 0} min, a point every ${m.intervalS} s`)}
        actions={<Link className="btn btn--text btn--sm" to="/perf">{t("看大图", "Open charts")}</Link>}
      />
      {!device ? (
        <Empty icon="chart" title={t("没有设备", "No device")}>{t("选中一台 adb 设备后这里显示 CPU、内存、温度。", "CPU, memory and temperature show up once an adb device is selected.")}</Empty>
      ) : m.state === "error" ? (
        <div className="banner banner--error"><Icon name="error" /><p>{m.error}</p><span /></div>
      ) : !last ? (
        <div className="metrics">{[0, 1, 2, 3, 4, 5].map((i) => <div className="metric" key={i}><div className="skeleton" style={{ height: 88 }} /></div>)}</div>
      ) : (
        <div className="metrics">
          {tiles.map((tile) => (
            <div className="metric" key={tile.k}>
              <div className="metric__label"><span>{t(tile.zh, tile.en)}</span></div>
              <div className="metric__value">{tile.v}<small>{tile.unit}</small></div>
              <div className="metric__sub">{tile.sub}</div>
              <Spark lines={tile.lines} lo={tile.lo} hi={tile.hi} className={tile.warn ? "spark--warn" : ""} label={tile.en} />
              <div className="metric__range">
                <span>{t("低 ", "min ")}{tile.fmt(Math.min(...(tile.lines[0] ?? [0])))}</span>
                <span>{t("高 ", "max ")}{tile.fmt(Math.max(...(tile.lines[0] ?? [0])))}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

/* ---------------- last boot ---------------- */

const SRC_OF: Record<string, string> = { uart: "uart", dmesg: "kernel", logcat: "logcat" };

export function BootBars({ boot }: { boot: BootView }) {
  const t = useT();
  const max = Math.max(boot.total_s, boot.previous?.total_s ?? 0) || 1;
  const scale = Math.ceil(max / 5) * 5;
  const ticks = Array.from({ length: scale / 5 + 1 }, (_, i) => i * 5);
  const prevStages = boot.previous ? stagesFrom(boot.previous.milestones) : [];
  return (
    <>
      <div className="legend">
        <span className="src--uart"><i />{t("串口(bootloader)", "serial (bootloader)")}</span>
        <span className="src--kernel"><i />dmesg {t("(内核)", "(kernel)")}</span>
        <span className="src--logcat"><i />logcat {t("(Android)", "(Android)")}</span>
      </div>
      <div className="boot">
        <div className="boot__row">
          <span>{t("本次", "this")}</span>
          <div className="boot__track">
            {boot.stages.map((st) => (
              <div key={st.key} className={`seg-bar src--${SRC_OF[st.src] ?? "logcat"}`} style={{ left: `${(st.t0 / scale) * 100}%`, width: `${((st.t1 - st.t0) / scale) * 100}%` }} title={`${st.key} ${st.t0.toFixed(1)}–${st.t1.toFixed(1)} s`} />
            ))}
          </div>
          <span className="num">{boot.total_s.toFixed(1)} s</span>
        </div>
        {boot.previous ? (
          <div className="boot__row">
            <span>{t("上次", "previous")}</span>
            <div className="boot__track boot__track--prev">
              {prevStages.map((st) => (
                <div key={st.key} className={`seg-bar src--${SRC_OF[st.src] ?? "logcat"}`} style={{ left: `${(st.t0 / scale) * 100}%`, width: `${((st.t1 - st.t0) / scale) * 100}%` }} />
              ))}
            </div>
            <span className="num">{boot.previous.total_s.toFixed(1)} s</span>
          </div>
        ) : null}
      </div>
      <div className="axis">
        {ticks.map((v) => (
          <span key={v} style={{ left: `${(v / scale) * 100}%` }}>{v}{v === scale ? " s" : ""}</span>
        ))}
      </div>
    </>
  );
}

function stagesFrom(ms: { key: string; src: string; t: number }[]) {
  const sorted = [...ms].sort((a, b) => a.t - b.t);
  return sorted.slice(1).map((m, i) => ({ key: m.key, src: m.src, t0: sorted[i]?.t ?? 0, t1: m.t }));
}

function LastBootCard() {
  const t = useT();
  const device = useApp((s) => s.device);
  const caps = useDashboardQuery({ queryKey: ["captures", device], queryFn: ({ signal }) => listCaptures(device, signal), refetchMs: 60_000 });
  const withBoot = caps.data?.data?.find((c) => c.has_boot) ?? null;
  const boot = useDashboardQuery({
    queryKey: ["boot", device, withBoot?.name],
    queryFn: ({ signal }) => captureBoot(withBoot?.name as string, device, signal),
    refetchMs: 300_000,
    enabled: !!withBoot,
  });
  const b = boot.data?.data;
  const prevMs = new Map((b?.previous?.milestones ?? []).map((m) => [m.key, m.t]));
  const delta = b && b.previous ? b.total_s - b.previous.total_s : null;
  return (
    <article className="card span-7">
      <CardHead
        title={t("上次开机", "Last boot")}
        sub={t("串口、dmesg、logcat 放在同一个时钟上", "serial, dmesg and logcat on one clock")}
        actions={<Link className="btn btn--text btn--sm" to="/timeline">{t("打开时间轴", "Open timeline")}</Link>}
      />
      {caps.isLoading ? (
        <div className="skeleton" style={{ height: 120 }} />
      ) : !withBoot ? (
        <Empty icon="timeline" title={t("还没有录过开机", "No boot recorded yet")} action={<Link className="btn btn--tonal btn--sm" to="/timeline" search={{ record: "boot" } as never}>{t("录一次开机", "Record a boot")}</Link>}>
          {t("录一次开机:先开始录串口、dmesg、logcat,再重启板子。录完这里显示每一段花了多久。", "Recording a boot starts serial, dmesg and logcat first, then reboots the board. The time each stage took shows up here.")}
        </Empty>
      ) : !b ? (
        <div className="skeleton" style={{ height: 120 }} />
      ) : !b.found ? (
        <Empty icon="timeline" title={t("这段录制里没有开机节点", "No boot milestones in this recording")}>{t("录制时要包含 logcat 的 events 缓冲。", "The recording needs logcat's events buffer.")}</Empty>
      ) : (
        <>
          <BootBars boot={b} />
          {delta !== null ? (
            <p style={{ margin: 0 }}>
              {t(
                `这次开机 ${b.total_s.toFixed(1)} 秒,比上次${delta >= 0 ? "多" : "少"} ${Math.abs(delta).toFixed(1)} 秒。`,
                `This boot took ${b.total_s.toFixed(1)} s, ${Math.abs(delta).toFixed(1)} s ${delta >= 0 ? "more" : "less"} than the previous one.`,
              )}
            </p>
          ) : null}
          <div className="well table-wrap">
            <table className="table table--dense">
              <thead><tr><th>{t("节点", "Milestone")}</th><th>{t("来源", "Source")}</th><th className="num">{t("时刻", "At")}</th><th className="num">{t("比上次", "vs previous")}</th></tr></thead>
              <tbody>
                {b.milestones.map((m) => {
                  const p = prevMs.get(m.key);
                  const d = p === undefined ? null : m.t - p;
                  return (
                    <tr key={m.key}>
                      <td className="mono">{m.label}</td>
                      <td><span className={`src-tag src--${SRC_OF[m.src] ?? m.src}`}>{m.src === "uart" ? t("串口", "serial") : m.src}</span></td>
                      <td className="num mono">{m.t.toFixed(2)} s</td>
                      <td className={`num mono ${d !== null && d > 0.5 ? "delta-up" : "delta-flat"}`}>{d === null ? "—" : `${d >= 0 ? "+" : ""}${d.toFixed(2)} s`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </article>
  );
}

/* ---------------- problems ---------------- */

function ProblemsCard() {
  const t = useT();
  const device = useApp((s) => s.device);
  const openSheet = useShell((s) => s.openSheet);
  const arts = useDashboardQuery<DiagArtifactsResponse>({
    queryKey: ["diag-artifacts", device],
    queryFn: ({ signal }) => fetchDiagArtifacts(device as string, signal),
    refetchMs: 60_000,
    enabled: !!device,
  });
  const [pstore, setPstore] = useState<PstoreRecord[] | null>(null);
  const [pBusy, setPBusy] = useState(false);
  const readPstore = async () => {
    setPBusy(true);
    try {
      const r = await pullPstore(device);
      if (r.ok && r.data) setPstore(r.data.records);
      else toast(`${r.error?.code ?? "ERROR"}: ${r.error?.message ?? ""}${r.error?.suggestion ? ` — ${r.error.suggestion}` : ""}`);
    } finally {
      setPBusy(false);
    }
  };
  const tomb = arts.data?.data?.tombstones ?? [];
  const anr = arts.data?.data?.anr ?? [];
  const panics = (pstore ?? []).filter((r) => r.has_panic);
  const items: { sev: "bad" | "warn"; icon: string; zh: string; en: string; tag: string; body: string; meta: string; ctx: string }[] = [
    ...panics.map((r) => ({ sev: "bad" as const, icon: "alert", zh: "内核崩溃", en: "Kernel panic", tag: t("上一次开机", "previous boot"), body: r.summary, meta: `pstore · ${r.name}`, ctx: `pstore ${r.name}: ${r.summary}` })),
    ...tomb.slice(0, 2).map((b) => ({ sev: "bad" as const, icon: "bug", zh: "原生进程崩溃", en: "Native crash", tag: `${b.count} tombstone`, body: b.files.map((f) => f.name).slice(0, 3).join(" · "), meta: b.bundle, ctx: `tombstone bundle ${b.bundle}` })),
    ...anr.slice(0, 2).map((b) => ({ sev: "warn" as const, icon: "clock", zh: "ANR", en: "ANR", tag: `${b.count}`, body: b.files.map((f) => f.name).slice(0, 3).join(" · "), meta: b.bundle, ctx: `ANR bundle ${b.bundle}` })),
  ];
  return (
    <article className="card span-5">
      <CardHead
        title={t("问题", "Problems")}
        sub={t("已经拉回来的崩溃记录和上一次开机留下的", "crash records already pulled, plus what the last boot left")}
        actions={
          <button type="button" className="btn btn--text btn--sm" onClick={() => void readPstore()} disabled={!device || pBusy}>
            {pBusy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="refresh" size="sm" />}
            {t("读上次崩溃记录", "Read last crash log")}
          </button>
        }
      />
      {!device ? (
        <Empty icon="bug" title={t("没有设备", "No device")} />
      ) : items.length === 0 ? (
        <Empty icon="check-circle" title={t("没有记录", "Nothing recorded")} action={<Link className="btn btn--text btn--sm" to="/problems">{t("去问题页拉取", "Pull on the Problems page")}</Link>}>
          {pstore ? t("上一次开机没有留下内核崩溃。ANR 和 tombstone 要先在问题页拉回来。", "The previous boot left no kernel panic. ANRs and tombstones show once pulled on the Problems page.") : t("ANR 和 tombstone 在问题页拉取;上一次开机的内核崩溃点上面的按钮读。", "Pull ANRs and tombstones on the Problems page; read the previous boot's kernel panic with the button above.")}
        </Empty>
      ) : (
        <div className="issues">
          {items.map((it, i) => (
            <div className={`issue issue--${it.sev}`} key={i}>
              <span className="issue__ic"><Icon name={it.icon} /></span>
              <div className="issue__title">{t(it.zh, it.en)}<Pill tone={it.sev === "bad" ? "bad" : "warn"}>{it.tag}</Pill></div>
              <div className="issue__body">{it.body}</div>
              <div className="issue__meta">
                <span className="mono">{it.meta}</span>
                <span className="row">
                  <Link className="btn btn--text btn--sm" to="/problems">{t("打开", "Open")}</Link>
                  <button type="button" className="btn btn--text btn--sm" onClick={() => openSheet({ zh: `问题 · ${it.zh}`, en: `Problem · ${it.en}`, detail: `Context: ${it.ctx}. Device ${device}.` })}>
                    <Icon name="spark" size="sm" />
                    {t("解释", "Explain")}
                  </button>
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

/* ---------------- activity ---------------- */

const ACTIVITY_MINUTES = 240;

function ActivityCard() {
  const t = useT();
  // Pushed live over WS /audit/stream; the server stops pushing to this
  // socket while paused. While the socket is not open (first connect, a
  // proxy that drops WebSockets, a reconnect) GET /audit fills in.
  const stream = useAuditStream({ minutes: ACTIVITY_MINUTES });
  const live = stream.status === "open";
  const poll = useDashboardQuery<AuditResponse>({
    queryKey: ["audit-recent"],
    queryFn: ({ signal }) => fetchAudit(ACTIVITY_MINUTES, 8, signal),
    refetchMs: 15_000,
    enabled: !live,
  });
  const events = live || stream.businessRaw.length ? stream.businessRaw.slice(0, 8) : [...(poll.data?.events ?? [])].reverse().slice(0, 8);
  const dropped = stream.status === "closed" || stream.status === "error";
  return (
    <article className="card span-7">
      <CardHead
        title={t("最近操作", "Recent activity")}
        sub={t("来自网页、命令行和 AI 会话", "from the web, the CLI and AI sessions")}
        actions={
          <>
            {live ? (
              <Pill tone={stream.paused ? "warn" : "ok"}>{stream.paused ? t("已暂停", "paused") : t("实时", "live")}</Pill>
            ) : dropped ? (
              <Pill tone="warn" title={t("实时连接断了,正在重连;先每 15 秒读一次", "The live stream dropped and is reconnecting; reading every 15 s meanwhile")}>{t("连接断开", "stream dropped")}</Pill>
            ) : null}
            <button type="button" className="btn btn--text btn--sm" disabled={!live} onClick={stream.paused ? stream.resume : stream.pause}>
              <Icon name={stream.paused ? "play" : "pause"} size="sm" />
              {stream.paused ? t("继续", "Resume") : t("暂停", "Pause")}
            </button>
            <Link className="btn btn--text btn--sm" to="/audit">{t("全部", "All")}</Link>
          </>
        }
      />
      {poll.isError && !live && events.length === 0 ? (
        <div className="banner banner--error"><Icon name="error" /><p>{String(poll.error?.message)}</p><span /></div>
      ) : events.length === 0 ? (
        <Empty icon="history" title={t("最近 4 小时没有操作", "Nothing in the last 4 hours")} />
      ) : (
        <ul className="list">
          {events.map((e, i) => (
            <li className="list__item" key={`${e.ts}-${i}`}>
              <span className="list__lead mono">{e.ts.slice(11, 16)}</span>
              <span className="list__title">{e.summary}</span>
              <span className="list__trail"><Pill>{e.kind}</Pill></span>
              <span className="list__meta">{e.source} · {e.session_id.slice(0, 8)}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

/* ---------------- common tasks ---------------- */

function QuickCard() {
  const t = useT();
  const openSheet = useShell((s) => s.openSheet);
  const links: [string, string, string, string, string, string][] = [
    ["/console", "terminal", "adb shell", "adb shell", "浏览器里的终端", "terminal in the browser"],
    ["/console", "plug", "串口控制台", "Serial console", "adb 不通也能用", "works when adb is down"],
    ["/logs", "logs", "实时 logcat", "Live logcat", "按 tag / 级别 / pid 过滤", "filter by tag, level, pid"],
    ["/timeline", "timeline", "录时间轴", "Record a timeline", "logcat + dmesg + 串口", "logcat + dmesg + serial"],
    ["/flash", "package", "烧整包", "Flash a package", "先检查计划再执行", "check the plan, then run"],
    ["/screen", "camera", "截屏", "Take a screenshot", "截图和界面树", "screenshot and UI tree"],
    ["/doctor", "check-circle", "环境检查", "Health check", "环境、adb、串口、ssh 逐层查", "env, adb, serial and ssh, layer by layer"],
    ["/connections", "link", "连接中心", "Connection center", "连进来的台架 agent 和 adb / 串口转发", "bench agents dialed in, adb / serial forwards"],
  ];
  return (
    <article className="card span-5">
      <CardHead title={t("常用操作", "Common tasks")} sub={t("每项打开对应页面", "each opens its page")} />
      <div className="quick">
        {links.map(([to, ic, zh, en, dz, de]) => (
          <Link className="qa" to={to} key={zh}>
            <span className="qa__ic"><Icon name={ic} /></span>
            <b>{t(zh, en)}</b>
            <small>{t(dz, de)}</small>
          </Link>
        ))}
        <button type="button" className="qa" onClick={() => openSheet(null)}>
          <span className="qa__ic"><Icon name="spark" /></span>
          <b>{t("问助手", "Ask the assistant")}</b>
          <small>{t("能读日志、调用工具", "it can read logs and run tools")}</small>
        </button>
      </div>
    </article>
  );
}
