/**
 * Single fastboot commands: getvar (read-only), oem, set_active, reboot.
 * Only what the bench agent's operation table offers. Baseline:
 * docs/webui-preview-v3-flash.html (#m-cmd).
 */
import { useMemo, useState } from "react";

import { CardHead, Confirm, ErrorBanner } from "../../../components/v3/ui";
import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { useBenchOps } from "../bench/useBenchOps";
import { type NdjsonEvent, flashOp, runBenchOp } from "../../../lib/api3";
import { useT } from "../../../lib/i18n";
import { failureOf, outputOf } from "./flashApi";
import { OpenLargeButton, linesOf } from "./OutputBlock";
import type { FlashRun } from "./useFlashRun";

type Pending = { kind: "oem" | "set-active" | "reboot"; value: string } | null;

export function CommandsMode({ run, available, disabledReason }: { run: FlashRun; available: boolean; disabledReason: string }) {
  const t = useT();
  const ops = useBenchOps();
  const table = ops.data?.ops ?? [];
  const find = (name: string, param: string) => table.find((o) => o.name === name)?.params.find((p) => p.name === param);
  const oemChoices = find("oem", "command")?.choices ?? [];
  const slotChoices = find("set-active", "slot")?.choices ?? [];
  const rebootChoices = ["", ...(find("reboot", "target")?.choices ?? [])];
  const has = (n: string) => table.some((o) => o.name === n);

  const [name, setName] = useState("all");
  const [oem, setOem] = useState("");
  const [slot, setSlot] = useState("");
  const [target, setTarget] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [term, setTerm] = useState("");
  const [large, setLarge] = useState(false);
  const termLines = useMemo(() => linesOf(term), [term]);

  const busy = run.running;
  const off = !available || busy;

  const exec = (label: string, shown: string, fn: (on: (ev: NdjsonEvent) => void) => Promise<NdjsonEvent>) => {
    setTerm((s) => `${s}$ ${shown}\n`);
    void run.start(label, fn).then((d) => {
      const f = failureOf(d);
      const body = outputOf(d) || (d.ok ? "OKAY" : "");
      setTerm((s) => `${s}${body ? `${body}\n` : ""}${d.ok ? "" : `${f.code || "FAILED"}${f.message ? `: ${f.message}` : ""}\n`}`);
    });
  };

  const commit = () => {
    const p = pending;
    setPending(null);
    if (!p) return;
    if (p.kind === "oem") exec("cmd", `fastboot oem ${p.value}`, (on) => flashOp("oem", { command: p.value }, on));
    else if (p.kind === "set-active") exec("cmd", `fastboot set_active ${p.value}`, (on) => runBenchOp("set-active", { slot: p.value }, on));
    else exec("cmd", `fastboot reboot ${p.value}`.trim(), (on) => flashOp("reboot", { target: p.value }, on));
  };

  const message = (p: NonNullable<Pending>) =>
    p.kind === "oem"
      ? t(`向板子发 oem ${p.value}。有的 oem 命令不可逆(例如解锁)。`, `Send oem ${p.value} to the board. Some oem commands cannot be undone (unlocking, for one).`)
      : p.kind === "set-active"
        ? t(`把活动槽位切到 ${p.value}。下次启动从这个槽位起。`, `Make slot ${p.value} the active slot. The next boot starts from it.`)
        : t(`重启板子${p.value ? `到 ${p.value}` : "回系统"}。fastboot 连接会断开。`, `Reboot the board${p.value ? ` to ${p.value}` : " into Android"}. The fastboot connection drops.`);

  return (
    <div id="m-cmd">
      <article className="card">
        <CardHead title={t("fastboot 命令", "fastboot commands")} sub={t("只能用台架 agent 操作表里有的", "only what the bench agent's operation table allows")} />
        {ops.isError ? <ErrorBanner>{t("读不到台架 agent 的操作表,oem / 槽位 / 重启先不可选。", "Cannot read the bench agent's operation table, so oem, slot and reboot are unavailable.")}</ErrorBanner> : null}
        <div className="form-grid">
          <label className="field">
            <span className="field__label">getvar</span>
            <div className="row">
              <input className="input grow" value={name} onChange={(e) => setName(e.target.value)} aria-label={t("getvar 变量名", "getvar name")} />
              <button type="button" className="btn btn--tonal btn--sm" disabled={off} title={!available ? disabledReason : undefined} onClick={() => exec("cmd", `fastboot getvar ${name || "all"}`, (on) => flashOp("getvar", { name: name.trim() === "all" ? "" : name.trim() }, on))}>
                {t("读取", "Read")}
              </button>
            </div>
          </label>
          <label className="field">
            <span className="field__label">oem</span>
            <div className="row">
              <select className="select grow" value={oem || oemChoices[0] || ""} onChange={(e) => setOem(e.target.value)} disabled={!has("oem") || oemChoices.length === 0} aria-label="oem">
                {oemChoices.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <button type="button" className="btn btn--outlined btn--sm" disabled={off || !(oem || oemChoices[0])} onClick={() => setPending({ kind: "oem", value: oem || oemChoices[0] || "" })}>{t("执行", "Run")}</button>
            </div>
          </label>
          <label className="field">
            <span className="field__label">set_active</span>
            <div className="row">
              <select className="select grow" value={slot || slotChoices[0] || ""} onChange={(e) => setSlot(e.target.value)} disabled={!has("set-active") || slotChoices.length === 0} aria-label="set_active">
                {slotChoices.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <button type="button" className="btn btn--outlined btn--sm" disabled={off || !(slot || slotChoices[0])} onClick={() => setPending({ kind: "set-active", value: slot || slotChoices[0] || "" })}>{t("设置", "Set")}</button>
            </div>
          </label>
          <label className="field">
            <span className="field__label">reboot</span>
            <div className="row">
              <select className="select grow" value={target} onChange={(e) => setTarget(e.target.value)} disabled={!has("reboot")} aria-label="reboot">
                {rebootChoices.map((c) => <option key={c} value={c}>{c === "" ? t("系统", "Android") : c}</option>)}
              </select>
              <button type="button" className="btn btn--outlined btn--sm" disabled={off || !has("reboot")} onClick={() => setPending({ kind: "reboot", value: target })}>{t("重启", "Reboot")}</button>
            </div>
          </label>
        </div>
        <Confirm
          open={!!pending}
          message={pending ? message(pending) : ""}
          confirmLabel={t("确认执行", "Run it")}
          cancelLabel={t("取消", "Cancel")}
          onConfirm={commit}
          onCancel={() => setPending(null)}
        />
        <div className="row">
          {busy && run.label === "cmd" ? <span className="t-muted"><span className="spinner" style={{ width: 14, height: 14, borderWidth: 2, display: "inline-block", verticalAlign: "middle" }} /> {t("执行中…", "Running…")}</span> : null}
          <span className="grow" />
          {term ? <OpenLargeButton onClick={() => setLarge(true)} /> : null}
          <button type="button" className="btn btn--text btn--sm" disabled={!term} onClick={() => setTerm("")}>
            <Icon name="close" size="sm" />
            {t("清空输出", "Clear output")}
          </button>
        </div>
        <pre className="term" style={{ height: 180 }} aria-label={t("命令输出", "command output")} onDoubleClick={() => term && setLarge(true)}>{term || t("还没有输出。", "No output yet.")}</pre>
        <LogDialog
          open={large}
          onClose={() => setLarge(false)}
          title={t("fastboot 命令输出", "fastboot command output")}
          sub={busy && run.label === "cmd" ? t("还在执行,输出会继续追加。", "Still running; output keeps arriving.") : undefined}
          lines={termLines}
          loading={busy && run.label === "cmd"}
          fileName="fastboot-commands.log"
        />
      </article>
    </div>
  );
}
