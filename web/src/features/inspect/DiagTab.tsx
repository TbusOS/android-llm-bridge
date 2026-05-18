/**
 * DiagTab — Inspect sub-tab for forensic artefacts.
 *
 * Three action cards (bugreport / ANR / tombstone) wired to
 * `/api/diag/*` plus a "collected" archive section that lists what's
 * already in the workspace under `devices/<serial>/{bugreports,anr,
 * tombstones}/`.
 *
 * Mockup baseline: `docs/webui-preview-v2-diag.html` — class names
 * here must mirror it (L-028).
 *
 * Bugreport is slow (60-180 s on real devices). The button stays
 * disabled with a "collecting…" label while the mutation is in flight;
 * after success we re-query the artifacts list so the new zip shows
 * up at the top.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApp } from "../../stores/app";
import {
  fetchDiagArtifacts,
  postDiagAnr,
  postDiagBugreport,
  postDiagTombstone,
  type DiagArtifactBundle,
  type DiagArtifactFile,
} from "../../lib/api";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function shortTime(mtimeSec: number): string {
  if (!mtimeSec) return "—";
  const d = new Date(mtimeSec * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().slice(11, 16);
}

function formatEnv(
  data:
    | { ok: boolean; data?: any; error?: { code: string; message: string } }
    | undefined,
): { text: string; status: "ok" | "err" | "idle" } {
  if (!data) return { text: "", status: "idle" };
  if (!data.ok || !data.data) {
    return {
      text: `${data.error?.code ?? "ERROR"}: ${data.error?.message ?? ""}`,
      status: "err",
    };
  }
  return { text: "", status: "ok" };
}

function BugreportCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: () => postDiagBugreport(device),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["diag-artifacts", device] }),
  });
  const env = formatEnv(mut.data);

  return (
    <section className="diag-card">
      <header className="diag-card__head">
        <h2 className="diag-card__title">bugreport</h2>
        <span className="diag-card__cost">~60-180s</span>
      </header>
      <p className="diag-card__hint">
        {lang === "zh"
          ? "触发 bugreportz · 系统 + logcat + dumpsys + tombstones 全装一个 zip。"
          : "Pulls a full bugreportz zip — system + logcat + dumpsys + tombstones in one archive."}
      </p>
      <div className="diag-card__row">
        <button
          type="button"
          className="diag-btn"
          disabled={!device || mut.isPending}
          onClick={() => mut.mutate()}
        >
          {mut.isPending
            ? lang === "zh"
              ? "采集中…"
              : "collecting…"
            : lang === "zh"
              ? "采集"
              : "collect"}
        </button>
      </div>
      {mut.data?.ok && mut.data.data && (
        <div className="diag-result" data-status="ok">
          ok · {mut.data.data.zip_path} ·{" "}
          {mut.data.data.duration_ms.toLocaleString()} ms
        </div>
      )}
      {env.status === "err" && (
        <div className="diag-result" data-status="err">
          {env.text}
        </div>
      )}
    </section>
  );
}

function AnrCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const qc = useQueryClient();
  const [clearAfter, setClearAfter] = useState(false);
  const mut = useMutation({
    mutationFn: () => postDiagAnr(device, { clear_after: clearAfter }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["diag-artifacts", device] }),
  });
  const env = formatEnv(mut.data);

  return (
    <section className="diag-card">
      <header className="diag-card__head">
        <h2 className="diag-card__title">anr</h2>
        <span className="diag-card__cost">{lang === "zh" ? "快" : "fast"}</span>
      </header>
      <p className="diag-card__hint">
        {lang === "zh"
          ? "从 /data/anr/ 拉所有 trace_*.txt 到时间戳目录。"
          : "Pulls /data/anr/*.txt trace files into a timestamped bundle."}
      </p>
      <div className="diag-card__row">
        <label>
          <input
            type="checkbox"
            checked={clearAfter}
            onChange={(e) => setClearAfter(e.target.checked)}
            disabled={mut.isPending}
          />{" "}
          {lang === "zh" ? "拉完清空" : "clear after pull"}
        </label>
        <button
          type="button"
          className="diag-btn"
          disabled={!device || mut.isPending}
          onClick={() => mut.mutate()}
        >
          {mut.isPending
            ? lang === "zh"
              ? "拉取中…"
              : "pulling…"
            : lang === "zh"
              ? "拉取"
              : "pull"}
        </button>
      </div>
      {mut.data?.ok && mut.data.data && (
        <div className="diag-result" data-status="ok">
          ok · {mut.data.data.count} {lang === "zh" ? "个" : "file(s)"}
          {mut.data.data.files.length > 0
            ? ` · ${mut.data.data.files[0]?.replace(/[^/]+$/, "")}`
            : ""}
        </div>
      )}
      {env.status === "err" && (
        <div className="diag-result" data-status="err">
          {env.text}
        </div>
      )}
    </section>
  );
}

function TombstoneCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const qc = useQueryClient();
  const [limit, setLimit] = useState(10);
  const mut = useMutation({
    mutationFn: () => postDiagTombstone(device, { limit }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["diag-artifacts", device] }),
  });
  const env = formatEnv(mut.data);

  return (
    <section className="diag-card">
      <header className="diag-card__head">
        <h2 className="diag-card__title">tombstone</h2>
        <span className="diag-card__cost">{lang === "zh" ? "快" : "fast"}</span>
      </header>
      <p className="diag-card__hint">
        {lang === "zh"
          ? "从 /data/tombstones/ 拉最新 N 个 native crash dump。"
          : "Pulls newest N tombstones (native crash dumps) from /data/tombstones/."}
      </p>
      <div className="diag-card__row">
        <label>
          {lang === "zh" ? "上限" : "limit"}{" "}
          <input
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value) || 10)}
            disabled={mut.isPending}
          />
        </label>
        <button
          type="button"
          className="diag-btn"
          disabled={!device || mut.isPending}
          onClick={() => mut.mutate()}
        >
          {mut.isPending
            ? lang === "zh"
              ? "拉取中…"
              : "pulling…"
            : lang === "zh"
              ? "拉取"
              : "pull"}
        </button>
      </div>
      {mut.data?.ok && mut.data.data && (
        <div className="diag-result" data-status="ok">
          ok · {mut.data.data.count} {lang === "zh" ? "个" : "file(s)"}
        </div>
      )}
      {env.status === "err" && (
        <div className="diag-result" data-status="err">
          {env.text}
        </div>
      )}
    </section>
  );
}

function BundleRow({ bundle }: { bundle: DiagArtifactBundle }) {
  const previewNames = bundle.files
    .slice(0, 3)
    .map((f) => f.name)
    .join(" · ");
  const more = bundle.files.length > 3 ? ` · +${bundle.files.length - 3}` : "";
  return (
    <li className="diag-archive__item">
      <span className="diag-archive__item-name">
        {bundle.bundle} — {bundle.count}{" "}
        {bundle.count === 1 ? "file" : "files"}
      </span>
      <span className="diag-archive__item-meta">{previewNames}{more}</span>
    </li>
  );
}

function FileRow({ file }: { file: DiagArtifactFile }) {
  return (
    <li className="diag-archive__item">
      <span className="diag-archive__item-name">{file.name}</span>
      <span className="diag-archive__item-meta">
        {humanSize(file.size_bytes)} · {shortTime(file.mtime)}
      </span>
    </li>
  );
}

function ArchiveSection({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["diag-artifacts", device],
    enabled: !!device,
    staleTime: 15_000,
    queryFn: ({ signal }) => fetchDiagArtifacts(device!, signal),
  });

  if (!device) {
    return (
      <section className="diag-archive">
        <header className="diag-archive__head">
          <h2 className="diag-archive__title">collected</h2>
        </header>
        <div className="diag-archive__empty">
          {lang === "zh"
            ? "未选设备 —— 先在顶栏选一个设备。"
            : "No device selected — pick one from the top-bar."}
        </div>
      </section>
    );
  }

  const bug = data?.data.bugreports ?? [];
  const anr = data?.data.anr ?? [];
  const tomb = data?.data.tombstones ?? [];

  return (
    <section className="diag-archive">
      <header className="diag-archive__head">
        <h2 className="diag-archive__title">collected</h2>
        <button
          type="button"
          className="diag-archive__refresh"
          disabled={isFetching}
          onClick={() => {
            void refetch();
          }}
        >
          {isFetching ? (lang === "zh" ? "刷新中…" : "refreshing…") : "refresh"}
        </button>
      </header>

      <div className="diag-archive__section">
        <h3 className="diag-archive__section-title">
          bugreports ({bug.length})
        </h3>
        {bug.length === 0 ? (
          <div className="diag-archive__empty">
            {lang === "zh" ? "暂无" : "none yet"}
          </div>
        ) : (
          <ul className="diag-archive__list">
            {bug.map((f) => (
              <FileRow key={f.path} file={f} />
            ))}
          </ul>
        )}
      </div>

      <div className="diag-archive__section">
        <h3 className="diag-archive__section-title">
          anr bundles ({anr.length})
        </h3>
        {anr.length === 0 ? (
          <div className="diag-archive__empty">
            {lang === "zh" ? "暂无" : "none yet"}
          </div>
        ) : (
          <ul className="diag-archive__list">
            {anr.map((b) => (
              <BundleRow key={b.path} bundle={b} />
            ))}
          </ul>
        )}
      </div>

      <div className="diag-archive__section">
        <h3 className="diag-archive__section-title">
          tombstone bundles ({tomb.length})
        </h3>
        {tomb.length === 0 ? (
          <div className="diag-archive__empty">
            {lang === "zh" ? "暂无" : "none yet"}
          </div>
        ) : (
          <ul className="diag-archive__list">
            {tomb.map((b) => (
              <BundleRow key={b.path} bundle={b} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export function DiagTab() {
  const device = useApp((s) => s.device);
  return (
    <div className="diag-tab">
      <div className="diag-actions">
        <BugreportCard device={device} />
        <AnrCard device={device} />
        <TombstoneCard device={device} />
      </div>
      <ArchiveSection device={device} />
    </div>
  );
}
