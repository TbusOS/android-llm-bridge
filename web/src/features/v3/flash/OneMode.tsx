/**
 * Write one partition from an image in the workspace (or one just uploaded).
 * Baseline: docs/webui-preview-v3-flash.html (#m-one).
 */
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { CardHead, Confirm, ErrorBanner, toast } from "../../../components/v3/ui";
import { flashOp } from "../../../lib/api3";
import { useT } from "../../../lib/i18n";
import { uploadImage } from "../../inspect/useFlash";
import { failureOf, fmtBytes, outputOf, walkWorkspace } from "./flashApi";
import type { FlashRun } from "./useFlashRun";

const MAX_UPLOAD = 2 * 1024 ** 3;

export interface OneModeProps {
  run: FlashRun;
  available: boolean;
  board: string | null;
  partitions: string[];
  /** Non-empty = nothing may be written (no board chosen, or no list for it). */
  partitionsProblem: string;
  disabledReason: string;
}

export function OneMode({ run, available, board, partitions, partitionsProblem, disabledReason }: OneModeProps) {
  const t = useT();
  const files = useQuery({ queryKey: ["flash-images-all"], queryFn: ({ signal }) => walkWorkspace("flash-images", 2, signal), retry: false, refetchOnWindowFocus: false });
  const [partition, setPartition] = useState("");
  const [image, setImage] = useState("");
  const [uploaded, setUploaded] = useState<{ path: string; size: number }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const mine = run.label === "flash";
  const blocked = !!partitionsProblem;
  // An empty list without a problem is "the agent set none": any well-formed name.
  const freeText = !blocked && partitions.length === 0;
  const part = freeText ? partition.trim() : partitions.includes(partition) ? partition : (partitions[0] ?? "");
  const options = [...uploaded, ...(files.data ?? []).map((f) => ({ path: f.path, size: f.size }))];
  const img = image || options[0]?.path || "";
  const canGo = available && !blocked && !run.running && !!part && !!img;

  const send = async (file: File | undefined | null) => {
    if (!file) return;
    if (file.size > MAX_UPLOAD) {
      setUploadError(t("文件超过 2 GiB,网页传不了。放到工作区 flash-images/ 下再选。", "The file is over 2 GiB; the browser cannot upload it. Put it under flash-images/ in the workspace."));
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const path = await uploadImage(file);
      setUploaded((u) => [{ path, size: file.size }, ...u.filter((x) => x.path !== path)]);
      setImage(path);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const go = () => {
    setConfirm(false);
    void run.start("flash", (on) => flashOp("flash", { partition: part, image: img }, on)).then((d) => {
      if (d.ok) toast(t(`${part} 已写入。`, `${part} written.`));
    });
  };

  const done = mine ? run.done : null;
  const failure = done && !done.ok ? failureOf(done) : null;
  const pct = run.frac !== null ? Math.round(run.frac * 100) : 0;

  return (
    <div id="m-one">
      <article className="card">
        <CardHead title={t("写一个分区", "Write one partition")} />
        {blocked ? (
          <ErrorBanner>
            {t("现在不能写任何分区:", "No partition may be written right now: ")}
            <span className="mono">{partitionsProblem}</span>
          </ErrorBanner>
        ) : null}
        <div className="form-grid">
          <label className="field">
            <span className="field__label">{board ? t(`分区(${board} 的名单)`, `Partition (${board}'s list)`) : t("分区", "Partition")}</span>
            {freeText ? (
              <input className="input" value={partition} placeholder="boot_a" onChange={(e) => setPartition(e.target.value)} disabled={!available} />
            ) : (
              <select className="select" value={part} onChange={(e) => setPartition(e.target.value)} disabled={blocked || !available}>
                {partitions.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            )}
          </label>
          <label className="field">
            <span className="field__label">{t("工作区里的镜像", "Image in the workspace")}</span>
            <select className="select" value={img} onChange={(e) => setImage(e.target.value)} disabled={options.length === 0 || !available}>
              {options.length === 0 ? <option value="">{files.isLoading ? t("正在扫描…", "Scanning…") : t("没有可选的镜像", "No image to pick")}</option> : null}
              {options.map((o) => (
                <option key={o.path} value={o.path}>{o.path} · {fmtBytes(o.size)}</option>
              ))}
            </select>
          </label>
        </div>
        {files.isError ? <ErrorBanner>{t("读不到工作区目录。", "Cannot read the workspace.")}</ErrorBanner> : null}
        <div
          className="dropzone"
          role="button"
          tabIndex={0}
          aria-label={t("上传镜像", "Upload an image")}
          style={drag ? { borderColor: "var(--md-sys-color-primary)" } : undefined}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") input.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            void send(e.dataTransfer.files[0]);
          }}
        >
          {uploading ? <span className="spinner" /> : <Icon name="upload" size="lg" />}
          <span>{uploading ? t("上传中…", "Uploading…") : t("或把镜像拖到这里(不超过 2 GiB)", "Or drop an image here (up to 2 GiB)")}</span>
          <input ref={input} type="file" hidden onChange={(e) => void send(e.target.files?.[0])} />
        </div>
        {uploadError ? <ErrorBanner>{uploadError}</ErrorBanner> : null}

        {mine && run.running ? (
          <div className="run-head">
            <span><b className="mono">flash {part} ← {img}</b></span>
            <span className="t-muted num">{pct}%</span>
            <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></div>
          </div>
        ) : null}
        {done ? (
          done.ok ? (
            <div className="banner banner--ok"><Icon name="check-circle" /><p>{t(`${part} 已写入,用时 ${Number(done.duration_s ?? 0).toFixed(1)} 秒。`, `${part} written in ${Number(done.duration_s ?? 0).toFixed(1)} s.`)}</p><span /></div>
          ) : (
            <>
              <div className="banner banner--error" role="alert"><Icon name="error" /><p><b>{failure?.code || "FAILED"}</b>{failure?.message ? <> · <span className="mono">{failure.message}</span></> : null}</p><span /></div>
              {outputOf(done) ? <pre className="term" style={{ fontSize: 12, maxHeight: 140 }}>{outputOf(done)}</pre> : null}
            </>
          )
        ) : null}

        {confirm ? (
          <Confirm
            open
            message={t(
              `把 ${img.split("/").pop()} 写到${board ? ` ${board} 的` : ""} ${part}。写错分区在这里撤不回。`,
              `Write ${img.split("/").pop()} to ${part}${board ? ` on ${board}` : ""}. A wrong partition cannot be undone from here.`,
            )}
            confirmLabel={t("写入", "Write")}
            cancelLabel={t("取消", "Cancel")}
            busy={run.running}
            onConfirm={go}
            onCancel={() => setConfirm(false)}
          />
        ) : (
          <div className="row">
            <button type="button" className="btn btn--danger" disabled={!canGo} title={!available ? disabledReason : undefined} onClick={() => setConfirm(true)}>
              <Icon name="bolt" />
              {t("写入", "Write")}
            </button>
          </div>
        )}
      </article>
    </div>
  );
}
