/**
 * FlashTab — Inspect sub-tab for fastboot: import an image, write one
 * partition, and get back out of fastboot.
 *
 * Mockup baseline: `docs/webui-preview-v2-flash.html` — the BEM class names
 * here mirror that file (L-028 mockup-baseline rule).
 *
 * Three deliberate shapes:
 *   - The partition is a SELECT, never a free text box. Writing the wrong
 *     partition is not undoable from this page and the board may not come
 *     back to report it, so a typo must not be reachable.
 *   - `flash` is armed: the first click explains the consequence, the second
 *     performs it. The hub and the agent both re-check on their side; this
 *     is the usability half, not the safety boundary.
 *   - "reboot to system" gets its own card. It is the way out of a state alb
 *     itself can put a board into, and burying it under the write controls
 *     would hide the remedy behind the hazard.
 */
import { useEffect, useRef, useState } from "react";

import { useApp } from "../../stores/app";
import {
  uploadImage,
  useFlashJob,
  useFlashStatus,
  type FlashLine,
} from "./useFlash";

// Fallback only. The agent has the authoritative allowlist and now ships it
// in `status.partitions`; this list is what we offer when the agent has
// configured none — which means "any well-formed name", not "none allowed".
// Hard-coding it as THE list was a real defect: on a bench whose allowlist is
// a single custom partition, none of these four is accepted, so every flash
// from the web page ended at FLASH_PARTITION_REJECTED while the CLI worked.
const FALLBACK_PARTITIONS = ["vendor_cfg", "boot", "dtbo", "recovery"];

function StateCard() {
  const lang = useApp((s) => s.lang);
  const { data, isLoading, isError, refetch, isFetching } = useFlashStatus();

  const state = !data?.available ? "unavailable" : data.busy ? "busy" : "ready";
  const verdict =
    state === "ready"
      ? lang === "zh"
        ? "就绪"
        : "ready"
      : state === "busy"
        ? lang === "zh"
          ? "忙"
          : "busy"
        : lang === "zh"
          ? "不可用"
          : "unavailable";

  const why =
    state === "ready"
      ? lang === "zh"
        ? "已连接的 agent 报告了 fastboot 能力，当前没有作业在跑。"
        : "The connected agent advertises fastboot and no job is running."
      : state === "busy"
        ? lang === "zh"
          ? `另一个作业正在跑：${data?.job || "未知"}。设备一次只能服务一个作业。`
          : `Another job is running: ${data?.job || "unknown"}. The device serves one at a time.`
        : lang === "zh"
          ? "没有 agent 报告 fastboot 能力 —— 在设备侧 agent.conf 里设 fastboot_path 后重启 agent。"
          : "No agent advertises fastboot — set fastboot_path in the agent's agent.conf and restart it.";

  return (
    <section className="flash-state-card">
      <header className="flash-state-card__head">
        <h2 className="flash-state-card__title">fastboot</h2>
        <button
          type="button"
          className="flash-state-card__refresh"
          onClick={() => void refetch()}
          disabled={isFetching}
        >
          {lang === "zh" ? "刷新" : "refresh"}
        </button>
      </header>
      <p className="flash-state-card__verdict" data-state={state}>
        <span className="flash-state-card__dot" />
        <span>{isLoading ? (lang === "zh" ? "查询中" : "checking") : verdict}</span>
      </p>
      <p className="flash-state-card__why">
        {isError
          ? lang === "zh"
            ? "读不到 hub 的 fastboot 状态。"
            : "Cannot read the hub's fastboot status."
          : why}
      </p>
      {data?.job ? (
        <div className="flash-state-card__field">
          <span className="flash-state-card__field-label">job</span>
          <span className="flash-state-card__field-value">{data.job}</span>
        </div>
      ) : null}
    </section>
  );
}

function TimelineLine({ line }: { line: FlashLine }) {
  return (
    <div className="flash-line" data-src={line.src}>
      <span className="flash-line__t">{line.t.toFixed(2)}</span>
      <span className="flash-line__src">{line.src}</span>
      <span className="flash-line__text">{line.text}</span>
    </div>
  );
}

export function FlashTab() {
  const lang = useApp((s) => s.lang);
  const { data: status } = useFlashStatus();
  const { lines, verdict, running, pct, run } = useFlashJob();

  const [image, setImage] = useState<{ path: string; size: number } | null>(null);
  const [partition, setPartition] = useState("");
  const [armed, setArmed] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  // The agent's list when it has one; ours only as a stand-in. Empty from the
  // agent means "no allowlist configured", so falling back is correct — it is
  // NOT the same as a bench that allows nothing.
  const partitions = status?.partitions?.length ? status.partitions : FALLBACK_PARTITIONS;
  const partitionKey = partitions.join(",");
  useEffect(() => {
    // The list arrives after the first render (and can change when a different
    // agent reconnects). Re-anchor the selection instead of leaving a value
    // this bench would refuse — which is exactly how the old hard-coded picker
    // failed, just quietly.
    if (!partitions.includes(partition)) setPartition(partitions[0] ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partitionKey, partition]);

  const busy = running || !!status?.busy;
  const canFlash = !!image && !!partition && !!status?.available && !busy;

  async function onPick(file: File | undefined) {
    if (!file) return;
    setUploadError("");
    try {
      const path = await uploadImage(file);
      setImage({ path, size: file.size });
      setArmed(false);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    }
  }

  function onFlash() {
    if (!image) return;
    if (!armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    void run("flash", { partition, image: image.path });
  }

  // No role="region" on <main>: it is already a landmark. The mockup carries
  // one only because it renders standalone with no surrounding page.
  return (
    <main className="flash-tab" aria-label={lang === "zh" ? "烧录面板" : "Flash tab"}>
      <StateCard />

      <div className="flash-actions">
        {/* ---- import ---- */}
        <section className="flash-action-card">
          <header className="flash-action-card__head">
            <h2 className="flash-action-card__title">image</h2>
          </header>
          <p className="flash-action-card__hint">
            {lang === "zh"
              ? "文件先传到 hub 的工作区，再从那里烧 —— 同一个镜像重烧一次不用再传一遍。"
              : "The file is uploaded to the hub workspace first, then flashed from there — so re-writing the same image costs no second upload."}
          </p>
          <div className="flash-drop">
            <p className="flash-drop__label">
              {lang === "zh" ? "选择要烧录的文件" : "Pick a file to write"}
            </p>
            <input
              ref={fileInput}
              type="file"
              hidden
              onChange={(e) => void onPick(e.target.files?.[0])}
            />
            <button
              type="button"
              className="flash-drop__button"
              onClick={() => fileInput.current?.click()}
            >
              {lang === "zh" ? "选择文件" : "choose file"}
            </button>
          </div>
          {image ? (
            <div className="flash-file">
              <span className="flash-file__name">{image.path}</span>
              <span className="flash-file__meta">{image.size} bytes</span>
            </div>
          ) : null}
          {uploadError ? (
            <div className="flash-file">
              <span className="flash-file__name">{uploadError}</span>
            </div>
          ) : null}
        </section>

        {/* ---- write ---- */}
        <section className="flash-action-card">
          <header className="flash-action-card__head">
            <h2 className="flash-action-card__title">write</h2>
          </header>
          <p className="flash-action-card__hint">
            {lang === "zh"
              ? "agent 会在动设备之前先核对摘要 —— 传坏了的代价是重传一次，不是写了一半的分区。"
              : "The agent verifies the digest before it touches the device — a damaged transfer costs a retry, not a half-written partition."}
          </p>
          <div className="flash-action-card__row">
            <label htmlFor="flash-partition">partition</label>
            <select
              id="flash-partition"
              value={partition}
              onChange={(e) => {
                setPartition(e.target.value);
                setArmed(false); // a changed target must be re-confirmed
              }}
            >
              {partitions.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="flash-btn flash-btn--danger"
            onClick={onFlash}
            disabled={!canFlash}
          >
            {lang === "zh" ? (armed ? "确认烧录" : "烧录") : armed ? "confirm flash" : "flash"}
          </button>
          {armed ? (
            <div className="flash-arm" role="alert">
              {lang === "zh"
                ? `写 ${partition} 在这里撤不回来，板子也可能起不来告诉你结果。再点一次确认。`
                : `Writing ${partition} cannot be undone from here, and the board may not come back to report it. Click again to confirm.`}
            </div>
          ) : null}
        </section>

        {/* ---- leave fastboot ---- */}
        <section className="flash-action-card">
          <header className="flash-action-card__head">
            <h2 className="flash-action-card__title">leave fastboot</h2>
          </header>
          <p className="flash-action-card__hint">
            {lang === "zh"
              ? "进了 fastboot 的板子已经从 adb 里消失。这是回去的路 —— 没有它，恢复就得有人到机器跟前。"
              : "A board in fastboot has dropped off adb. This is the way back — without it, recovering needs someone at the machine."}
          </p>
          <button
            type="button"
            className="flash-btn flash-btn--ghost"
            onClick={() => void run("reboot", { target: "" })}
            disabled={busy || !status?.available}
          >
            {lang === "zh" ? "重启回系统" : "reboot to system"}
          </button>
        </section>
      </div>

      {/* ---- timeline ---- */}
      <section className="flash-timeline flash-tab__wide">
        <header className="flash-timeline__head">
          <h2 className="flash-timeline__title">timeline</h2>
          {verdict?.artifacts ? (
            <span className="flash-timeline__path">{verdict.artifacts}/timeline.jsonl</span>
          ) : null}
        </header>
        <p className="flash-timeline__hint">
          {lang === "zh"
            ? "作业进度实时在下面；板子的 UART 与它用同一个时钟记进上面那个文件 —— 烧完起不来时，答案在那里。"
            : "Job progress streams below; the board's UART is recorded into the file above on the same clock — when a flash does not come back, that is where the answer is."}
        </p>
        {running || pct > 0 ? (
          <div className="flash-timeline__bar">
            <div className="flash-timeline__bar-fill" style={{ width: `${pct}%` }} />
          </div>
        ) : null}
        <div className="flash-timeline__log">
          {lines.length === 0 && !verdict ? (
            <div className="flash-line" data-src="meta">
              <span className="flash-line__text">
                {lang === "zh" ? "还没有作业。" : "No job yet."}
              </span>
            </div>
          ) : null}
          {lines.map((line) => (
            <TimelineLine key={line.seq} line={line} />
          ))}
          {verdict ? (
            <div className="flash-line" data-src={verdict.ok ? "job" : "meta"}>
              <span className="flash-line__t">{verdict.duration_s.toFixed(2)}</span>
              <span className="flash-line__src">{verdict.ok ? "ok" : "fail"}</span>
              <span className="flash-line__text">
                {verdict.ok
                  ? verdict.stdout.trim() || (lang === "zh" ? "完成" : "done")
                  : `${verdict.code || "error"}: ${verdict.error}`}
              </span>
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}
