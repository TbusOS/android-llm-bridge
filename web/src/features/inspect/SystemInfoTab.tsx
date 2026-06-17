/**
 * Inspect → System Info — real-data variant (DEBT-022 PR-B).
 *
 * Replaces the previous mock-driven `.sys-grid` with a live snapshot
 * of `GET /devices/{serial}/system`:
 *
 *   System (props subset) · CPU (props + meminfo MemTotal)
 *   Memory (full meminfo KV) · Storage (df + block devices)
 *   Network (interfaces) · Battery (dumpsys battery)
 *   Partitions (/dev/block/by-name) · Mounts (/proc/mounts)
 *   Thermal zones (/sys/class/thermal)
 *
 * Empty states render inline so missing data doesn't blank the tab.
 */

import { Fragment } from "react";
import { RefreshCw } from "lucide-react";
import { useApp } from "../../stores/app";
import type {
  ApiDeviceSystem,
  ApiGpuInfo,
  ApiProcessesInfo,
  ApiSecurityInfo,
} from "../../lib/api";
import { useDeviceSystem } from "./useDeviceSystem";
import { useDeviceInfoPanels, type DeviceInfoPanels } from "./useDeviceInfoPanels";

function fmtKb(kb: number | undefined): string {
  if (!kb || kb <= 0) return "—";
  if (kb >= 1024 * 1024) return `${(kb / 1024 / 1024).toFixed(1)} GB`;
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb} KB`;
}

function fmtHz(hz: number | undefined): string {
  if (!hz || hz <= 0) return "—";
  if (hz >= 1e9) return `${(hz / 1e9).toFixed(2)} GHz`;
  if (hz >= 1e6) return `${(hz / 1e6).toFixed(0)} MHz`;
  if (hz >= 1e3) return `${(hz / 1e3).toFixed(0)} kHz`;
  return `${hz} Hz`;
}

function yesNo(b: boolean | undefined, lang: string): string {
  return b ? (lang === "zh" ? "是" : "yes") : lang === "zh" ? "否" : "no";
}

/** "46%" → 46 (clamped 0..100); non-numeric → 0. */
function parsePct(raw: string | undefined): number {
  const n = Number.parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n));
}

/** Usage → part-fill colour, cool→warm by severity (mirrors the mockup
 * baseline): blue (very low) → green (healthy) → orange (filling) → red
 * (critical). Default `.part-fill` is orange (no modifier). */
function fillTier(pct: number): string {
  if (pct >= 85) return "part-fill part-fill--red";
  if (pct >= 60) return "part-fill";
  if (pct >= 30) return "part-fill part-fill--green";
  return "part-fill part-fill--blue";
}

export function SystemInfoTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const q = useDeviceSystem(device);
  const panels = useDeviceInfoPanels(device);

  if (!device) {
    return (
      <div className="sys-grid">
        <div className="sys-card">
          <h3>{lang === "zh" ? "未选设备" : "No device"}</h3>
          <p className="section-sub" style={{ marginBottom: 0 }}>
            {lang === "zh"
              ? "在顶栏的设备选择器选一个，再回这里。"
              : "Pick one from the top-bar device picker, then come back."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="sys-toolbar">
        <button
          type="button"
          className="link-arrow link-arrow--btn"
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          aria-label={lang === "zh" ? "刷新系统信息" : "Refresh system info"}
        >
          <RefreshCw size={12} className="icon-inline" />{" "}
          {q.isFetching
            ? lang === "zh" ? "刷新中…" : "Refreshing…"
            : lang === "zh" ? "刷新" : "Refresh"}
        </button>
        {q.data?.system && (
          <span className="sys-toolbar__meta">
            {lang === "zh" ? "字段统计" : "fields"}:
            {" "}{Object.keys(q.data.system.props).length} props ·
            {" "}{q.data.system.partitions.length} partitions ·
            {" "}{q.data.system.mounts.length} mounts ·
            {" "}{q.data.system.thermal.length} thermal zones
          </span>
        )}
      </div>

      {q.isLoading && (
        <div className="sys-grid">
          <div className="sys-card">
            <h3>{lang === "zh" ? "加载中…" : "Loading…"}</h3>
          </div>
        </div>
      )}

      {q.isError && (
        <div className="sys-grid">
          <div className="sys-card">
            <h3>{lang === "zh" ? "加载失败" : "Failed to load"}</h3>
            <p className="section-sub" style={{ marginBottom: 0 }}>
              {String(q.error ?? "")}
            </p>
          </div>
        </div>
      )}

      {q.data && !q.data.ok && (
        <div className="sys-grid">
          <div className="sys-card">
            <h3>{lang === "zh" ? "采集失败" : "Snapshot failed"}</h3>
            <p className="section-sub" style={{ marginBottom: 0 }}>{q.data.error}</p>
          </div>
        </div>
      )}

      {q.data?.system && (
        <Snapshot system={q.data.system} lang={lang} panels={panels} />
      )}
    </>
  );
}

function Snapshot({
  system,
  lang,
  panels,
}: {
  system: ApiDeviceSystem;
  lang: string;
  panels: DeviceInfoPanels;
}) {
  const p = system.props;
  return (
    <div className="sys-grid">
      <KvCard
        title={lang === "zh" ? "系统" : "System"}
        rows={[
          ["Model", p["ro.product.model"] || "—"],
          ["Brand", p["ro.product.brand"] || "—"],
          ["Manufacturer", p["ro.product.manufacturer"] || "—"],
          ["Android", p["ro.build.version.release"] || "—"],
          ["SDK", p["ro.build.version.sdk"] || "—"],
          ["Build", p["ro.build.fingerprint"] || "—"],
          ["Security patch", p["ro.build.version.security_patch"] || "—"],
        ]}
      />

      <KvCard
        title={lang === "zh" ? "CPU / 硬件" : "CPU / Hardware"}
        rows={[
          ["SoC",
            p["ro.boot.soc.product"]
            || p["ro.hardware.chipname"]
            || p["ro.board.platform"]
            || "—"],
          ["ABI", p["ro.product.cpu.abi"] || "—"],
          ["Hardware", p["ro.hardware"] || "—"],
          ["Bootloader", p["ro.bootloader"] || "—"],
          ["MemTotal", fmtKb(system.meminfo.MemTotal)],
          ["MemAvailable", fmtKb(system.meminfo.MemAvailable)],
        ]}
      />

      <KvCard
        title={lang === "zh" ? "内存（详细）" : "Memory (detailed)"}
        rows={Object.entries(system.meminfo).slice(0, 14).map(([k, v]) => [k, fmtKb(v)])}
      />

      <BlockCard
        title={lang === "zh" ? "块设备" : "Block devices"}
        rows={system.block_devices.map((b) => [b.name, fmtKb(Number(b.size_kib) || 0)])}
        empty={lang === "zh" ? "无" : "none"}
      />

      <BlockCard
        title={lang === "zh" ? "分区表（by-name）" : "Partitions (by-name)"}
        rows={system.partitions.map((part) => [part.name, part.target])}
        empty={lang === "zh" ? "无" : "none"}
      />

      <BlockCard
        title={lang === "zh" ? "挂载点" : "Mounts"}
        rows={system.mounts.map((m) => [m.mount_point, `${m.device} (${m.fstype})`])}
        empty={lang === "zh" ? "无" : "none"}
      />

      <StorageCard storage={system.storage} lang={lang} />

      <BlockCard
        title={lang === "zh" ? "网络接口" : "Network"}
        rows={system.network.map((n) => [
          n.iface,
          [n.ipv4, n.ipv6, n.mac].filter(Boolean).join(" · ") || "—",
        ])}
        empty={lang === "zh" ? "未拿到接口" : "no interfaces"}
      />

      <KvCard
        title={lang === "zh" ? "电池" : "Battery"}
        rows={Object.entries(system.battery).slice(0, 12)}
      />

      <BlockCard
        title={lang === "zh" ? "温度（thermal zones）" : "Thermal zones"}
        rows={system.thermal.map((t) => [`${t.zone} · ${t.type}`, `${t.temp_c}°C`])}
        empty={lang === "zh" ? "无温度传感器读数" : "no thermal readings"}
      />

      {/* ARCH-2: panels previously CLI/MCP-only — security / gpu / processes. */}
      <SecurityCard env={panels.security} lang={lang} />
      <GpuCard env={panels.gpu} lang={lang} />
      <ProcessesCard env={panels.processes} lang={lang} />
    </div>
  );
}

type PanelEnv<T> = { ok: boolean; data?: T; error?: { message: string } } | undefined;

function PanelError({ env, lang }: { env: PanelEnv<unknown>; lang: string }) {
  return (
    <p className="section-sub" style={{ marginBottom: 0 }}>
      {env && !env.ok
        ? env.error?.message || (lang === "zh" ? "采集失败" : "failed")
        : lang === "zh"
          ? "加载中…"
          : "loading…"}
    </p>
  );
}

function SecurityCard({
  env,
  lang,
}: {
  env: PanelEnv<ApiSecurityInfo>;
  lang: string;
}) {
  const s = env?.ok ? env.data : undefined;
  if (!s) {
    return (
      <div className="sys-card">
        <h3>{lang === "zh" ? "安全 / 启动" : "Security / Boot"}</h3>
        <PanelError env={env} lang={lang} />
      </div>
    );
  }
  return (
    <KvCard
      title={lang === "zh" ? "安全 / 启动" : "Security / Boot"}
      rows={[
        ["Verified boot", s.verified_boot_state || "—"],
        ["AVB version", s.avb_version || "—"],
        ["Verity mode", s.verity_mode || "—"],
        ["Crypto", [s.crypto_state, s.crypto_type].filter(Boolean).join(" · ") || "—"],
        ["File encryption", s.file_encryption || "—"],
        ["SELinux", s.selinux_mode || "—"],
        ["SELinux policy", s.selinux_policy_version || "—"],
        ["OEM unlock allowed", yesNo(s.oem_unlock_allowed, lang)],
        ["adb secure", yesNo(s.adb_secure, lang)],
      ]}
    />
  );
}

function GpuCard({ env, lang }: { env: PanelEnv<ApiGpuInfo>; lang: string }) {
  const g = env?.ok ? env.data : undefined;
  if (!g) {
    return (
      <div className="sys-card">
        <h3>GPU</h3>
        <PanelError env={env} lang={lang} />
      </div>
    );
  }
  return (
    <KvCard
      title="GPU"
      rows={[
        ["Name", g.name || "—"],
        ["Vendor", g.vendor || "—"],
        ["Renderer", g.renderer || "—"],
        ["Governor", g.governor || "—"],
        ["Freq (cur)", fmtHz(g.freq_hz_current)],
        ["Freq (max)", fmtHz(g.freq_hz_max)],
        ["Util", g.util_pct >= 0 ? `${g.util_pct}%` : "—"],
      ]}
    />
  );
}

function ProcessesCard({
  env,
  lang,
}: {
  env: PanelEnv<ApiProcessesInfo>;
  lang: string;
}) {
  const pr = env?.ok ? env.data : undefined;
  if (!pr) {
    return (
      <div className="sys-card">
        <h3>{lang === "zh" ? "进程（CPU Top）" : "Processes (top CPU)"}</h3>
        <PanelError env={env} lang={lang} />
      </div>
    );
  }
  return (
    <BlockCard
      title={
        (lang === "zh" ? "进程（CPU Top）· 共 " : "Processes (top CPU) · ") +
        pr.count
      }
      rows={pr.top_cpu
        .slice(0, 8)
        .map((proc) => [`${proc.name} (${proc.pid})`, `${proc.cpu_pct}% · ${fmtKb(proc.rss_kb)}`])}
      empty={lang === "zh" ? "无进程数据" : "no process data"}
    />
  );
}

/** MBC-4: storage usage as per-partition progress bars (mirrors the
 * docs/webui-preview-v2.html `part-row` / `part-bar` / `part-fill`
 * baseline). The `use_pct` data already exists — this replaces the
 * plain-text KV row with a bar coloured by usage tier. The bar width is
 * the one legitimately-dynamic inline style (data-driven, same as the
 * mockup); the colour is a `part-fill--*` class, not inline hex. */
function StorageCard({
  storage,
  lang,
}: {
  storage: ApiDeviceSystem["storage"];
  lang: string;
}) {
  const entries = Object.entries(storage);
  return (
    <div className="sys-card sys-card--block">
      <h3>{lang === "zh" ? "存储用量" : "Storage usage"}</h3>
      {entries.length === 0 ? (
        <p className="section-sub" style={{ marginBottom: 0 }}>
          {lang === "zh" ? "无" : "none"}
        </p>
      ) : (
        entries.map(([mount, info]) => {
          const pct = parsePct(info.use_pct);
          return (
            <div
              className="part-row"
              key={mount}
              title={`used ${fmtKb(Number(info.used_kib))} / avail ${fmtKb(Number(info.avail_kib))}`}
            >
              <span className="part-name">{mount}</span>
              <div className="part-bar">
                <div className={fillTier(pct)} style={{ width: `${pct}%` }} />
              </div>
              <span className="part-pct">{info.use_pct}</span>
            </div>
          );
        })
      )}
    </div>
  );
}

function KvCard({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div className="sys-card">
      <h3>{title}</h3>
      <dl className="sys-kv">
        {rows.map(([k, v]) => (
          <Fragment key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

function BlockCard({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: [string, string][];
  empty: string;
}) {
  return (
    <div className="sys-card sys-card--block">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="section-sub" style={{ marginBottom: 0 }}>{empty}</p>
      ) : (
        <dl className="sys-kv sys-kv--mono">
          {rows.map(([k, v], i) => (
            <Fragment key={`${k}-${i}`}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </Fragment>
          ))}
        </dl>
      )}
    </div>
  );
}
