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
import type { ApiDeviceSystem } from "../../lib/api";
import { useDeviceSystem } from "./useDeviceSystem";

function fmtKb(kb: number | undefined): string {
  if (!kb || kb <= 0) return "—";
  if (kb >= 1024 * 1024) return `${(kb / 1024 / 1024).toFixed(1)} GB`;
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb} KB`;
}

export function SystemInfoTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const q = useDeviceSystem(device);

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
          <RefreshCw size={12} style={{ verticalAlign: "-2px" }} />{" "}
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

      {q.data?.system && <Snapshot system={q.data.system} lang={lang} />}
    </>
  );
}

function Snapshot({ system, lang }: { system: ApiDeviceSystem; lang: string }) {
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

      <KvCard
        title={lang === "zh" ? "存储用量" : "Storage usage"}
        rows={Object.entries(system.storage).map(([mount, info]) => [
          mount,
          `${info.use_pct} · used ${fmtKb(Number(info.used_kib))} / avail ${fmtKb(Number(info.avail_kib))}`,
        ])}
      />

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
