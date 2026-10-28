/**
 * Console (v3): adb shell and the serial console in the browser, with
 * saved commands, the recent-command list from the audit log and the adb
 * root / unroot / remount buttons. Baseline: docs/webui-preview-v3-console.html.
 */
import { useCallback, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { useBenchBoard } from "../../../components/v3/TopBar";
import { CardHead, Confirm, Empty, ErrorBanner, Menu, PageHead, Pill, toast } from "../../../components/v3/ui";
import { type AuditEvent, fetchAudit } from "../../../lib/api";
import { adbAction } from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import "./console.css";
import { AdbPane, type PaneApi, SerialPane } from "./Panes";
import { loadSnippets, saveSnippets, type Snippet } from "./snippets";

type Kind = "adb" | "serial";
interface Tab {
  id: string;
  kind: Kind;
}

export function ConsolePage() {
  const t = useT();
  const device = useApp((s) => s.device);
  const board = useBenchBoard();
  const serial = board.data?.serial;
  const [tabs, setTabs] = useState<Tab[]>([
    { id: "adb-1", kind: "adb" },
    { id: "serial-1", kind: "serial" },
  ]);
  const [activeId, setActiveId] = useState("adb-1");
  const seq = useRef(1);
  const panes = useRef(new Map<string, PaneApi>());
  const register = useCallback((id: string, api: PaneApi | null) => {
    if (api) panes.current.set(id, api);
    else panes.current.delete(id);
  }, []);

  const addTab = (kind: Kind) => {
    seq.current += 1;
    const tab = { id: `${kind}-${seq.current}`, kind };
    setTabs((l) => [...l, tab]);
    setActiveId(tab.id);
  };
  const closeTab = (id: string) => {
    setTabs((l) => {
      const next = l.filter((x) => x.id !== id);
      if (id === activeId) setActiveId(next[next.length - 1]?.id ?? "");
      return next;
    });
  };

  const audit = useDashboardQuery({
    queryKey: ["console-audit"],
    queryFn: ({ signal }) => fetchAudit(24 * 60, 300, signal),
    refetchMs: 10_000,
  });

  const run = (cmd: string) => {
    const pane = panes.current.get(activeId);
    if (!pane || !pane.send(`${cmd}\r`)) {
      toast(t("当前终端没有连上,命令没发出去。", "The current terminal is not connected; the command was not sent."));
      return;
    }
    toast(t(`已发送:${cmd}`, `Sent: ${cmd}`));
    window.setTimeout(() => void audit.refetch(), 1500);
  };

  const tabLabel = (tab: Tab) => {
    const same = tabs.filter((x) => x.kind === tab.kind);
    const n = same.length > 1 ? ` #${same.indexOf(tab) + 1}` : "";
    return tab.kind === "adb" ? `adb shell${n} · ${device ?? "—"}` : `${t("串口", "Serial")}${n} · ${serial?.com ?? "—"}`;
  };
  const activeTab = tabs.find((x) => x.id === activeId);

  return (
    <>
      <PageHead
        eyebrow={<span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span>}
        title={t("控制台", "Console")}
        desc={t("浏览器里的 adb shell 和串口控制台。每条命令都记进操作记录。", "adb shell and the serial console in the browser. Every command lands in the audit log.")}
        actions={
          <Menu
            align="end"
            trigger={(p) => (
              <button type="button" className="btn btn--filled" {...p}>
                <Icon name="plus" />
                {t("新建终端", "New terminal")}
                <Icon name="chevron-down" size="sm" />
              </button>
            )}
            items={[
              { key: "adb", icon: "terminal", label: "adb shell", desc: t("需要 adb 能看到板子", "needs adb to see the board"), onSelect: () => addTab("adb") },
              { key: "serial", icon: "plug", label: t("串口控制台", "Serial console"), desc: t("bootloader 里、adb 不通时都能用", "works in the bootloader and when adb is down"), onSelect: () => addTab("serial") },
            ]}
          />
        }
      />
      <section className="grid">
        <article className="card card--flush span-8 console">
          {tabs.length === 0 ? (
            <Empty
              icon="terminal"
              title={t("没有打开的终端", "No terminal open")}
              action={
                <div className="row">
                  <button type="button" className="btn btn--tonal btn--sm" onClick={() => addTab("adb")}>adb shell</button>
                  <button type="button" className="btn btn--tonal btn--sm" onClick={() => addTab("serial")}>{t("串口控制台", "Serial console")}</button>
                </div>
              }
            >
              {t("用右上角的「新建终端」,或者点下面的按钮。", "Use New terminal at the top right, or the buttons below.")}
            </Empty>
          ) : (
            <>
              <div className="tabs" role="tablist" aria-label={t("终端", "Terminals")}>
                {tabs.map((tab) => (
                  <button key={tab.id} type="button" className="tab" role="tab" aria-selected={tab.id === activeId} aria-controls={`t-${tab.id}`} onClick={() => setActiveId(tab.id)}>
                    <Icon name={tab.kind === "adb" ? "terminal" : "plug"} size="sm" />
                    {tabLabel(tab)}
                  </button>
                ))}
              </div>
              {tabs.map((tab) =>
                tab.kind === "adb" ? (
                  <AdbPane key={tab.id} id={tab.id} active={tab.id === activeId} register={register} />
                ) : (
                  <SerialPane
                    key={tab.id}
                    id={tab.id}
                    active={tab.id === activeId}
                    register={register}
                    info={{ com: serial?.com ?? null, baud: serial?.baud ?? null, readers: serial?.readers ?? null }}
                  />
                ),
              )}
              <div className="console__foot">
                <button type="button" className="btn btn--text btn--sm" onClick={() => closeTab(activeId)} disabled={!activeTab}>
                  {t("关闭这个终端", "Close this terminal")}
                </button>
              </div>
            </>
          )}
        </article>
        <div className="stack span-4">
          <SnippetsCard onRun={run} />
          <RecentCard q={audit} />
          <AdbAccessCard />
          <div className="banner">
            <Icon name="info" />
            <p>{t("板子上的命令这里不拦、不弹确认,撤不回的命令请先想清楚。Windows 那边的操作只能走台架操作表。", "Commands on the board are not blocked or confirmed here; think twice before ones you cannot undo. Windows-side actions only run through the bench operation table.")}</p>
            <span />
          </div>
        </div>
      </section>
    </>
  );
}

/* ---------------- saved commands ---------------- */

function SnippetsCard({ onRun }: { onRun: (cmd: string) => void }) {
  const t = useT();
  const [list, setList] = useState<Snippet[]>(() => loadSnippets());
  const [adding, setAdding] = useState(false);
  const [cmd, setCmd] = useState("");
  const [risky, setRisky] = useState(false);
  const [pending, setPending] = useState<Snippet | null>(null);

  const update = (next: Snippet[]) => {
    setList(next);
    saveSnippets(next);
  };
  const add = () => {
    const c = cmd.trim();
    if (!c) return;
    if (list.some((s) => s.cmd === c)) {
      toast(t("这条命令已经在列表里。", "That command is already in the list."));
      return;
    }
    update([...list, { id: `s${Date.now()}`, cmd: c, risky }]);
    setCmd("");
    setRisky(false);
    setAdding(false);
  };

  return (
    <article className="card">
      <CardHead
        title={t("常用命令", "Saved commands")}
        sub={t("只存在这个浏览器里", "stored in this browser only")}
        actions={
          <button type="button" className="icon-btn icon-btn--sm" aria-label={t("添加", "Add")} aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
            <Icon name="plus" size="sm" />
          </button>
        }
      />
      {adding ? (
        <form
          className="snips__add"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <label className="visually-hidden" htmlFor="snip-cmd">{t("命令", "Command")}</label>
          <input id="snip-cmd" className="input mono" value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder={t("要保存的 shell 命令", "Shell command to save")} />
          <label className="snips__risky">
            <input type="checkbox" checked={risky} onChange={(e) => setRisky(e.target.checked)} />
            {t("会改动板子,运行前先问", "changes the board; ask before running")}
          </label>
          <div className="row">
            <button type="submit" className="btn btn--tonal btn--sm" disabled={!cmd.trim()}>{t("保存", "Save")}</button>
            <button type="button" className="btn btn--text btn--sm" onClick={() => setAdding(false)}>{t("取消", "Cancel")}</button>
          </div>
        </form>
      ) : null}
      <Confirm
        open={pending !== null}
        message={t(`「${pending?.cmd ?? ""}」会改动板子。要发到当前终端运行吗?`, `"${pending?.cmd ?? ""}" changes the board. Send it to the current terminal?`)}
        confirmLabel={t("运行", "Run")}
        cancelLabel={t("取消", "Cancel")}
        onConfirm={() => {
          if (pending) onRun(pending.cmd);
          setPending(null);
        }}
        onCancel={() => setPending(null)}
      />
      {list.length === 0 ? (
        <Empty icon="terminal" title={t("还没有常用命令", "No saved commands")}>
          {t("点右上角的加号,把常用的 shell 命令存在这里。", "Use the plus button to keep frequent shell commands here.")}
        </Empty>
      ) : (
        <ul className="list snips">
          {list.map((s) => (
            <li className="list__item" key={s.id}>
              <span className="list__lead">
                <Icon name={s.risky ? "alert" : "terminal"} size="sm" />
              </span>
              <code className="list__title">{s.cmd}</code>
              <span className="list__trail">
                <button type="button" className="btn btn--text btn--sm" aria-label={`${t("运行", "Run")} ${s.cmd}`} onClick={() => (s.risky ? setPending(s) : onRun(s.cmd))}>
                  {t("运行", "Run")}
                </button>
                <button type="button" className="icon-btn icon-btn--sm" aria-label={`${t("删除", "Remove")} ${s.cmd}`} onClick={() => update(list.filter((x) => x.id !== s.id))}>
                  <Icon name="close" size="sm" />
                </button>
              </span>
              <span className="list__meta">{s.risky ? <Pill tone="warn">{t("改动板子", "changes the board")}</Pill> : t("只读", "read-only")}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

/* ---------------- recent commands ---------------- */

function hhmm(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts.slice(11, 16) : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function RecentCard({ q }: { q: ReturnType<typeof useDashboardQuery<{ ok: boolean; events: AuditEvent[]; since: string; until: string }>> }) {
  const t = useT();
  const events = (q.data?.events ?? [])
    .filter((e) => e.source === "terminal" && (e.kind === "command" || e.kind === "deny" || e.kind === "hitl_deny"))
    .slice(-8)
    .reverse();
  return (
    <article className="card">
      <CardHead title={t("最近命令", "Recent commands")} sub={t("来自操作记录,最近 24 小时", "from the audit log, last 24 hours")} />
      {q.isError ? (
        <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void q.refetch()}>{t("重试", "Retry")}</button>}>
          {t("读不到操作记录:", "Cannot read the audit log: ")}
          {q.error?.message}
        </ErrorBanner>
      ) : q.isLoading ? (
        <div className="row"><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("读取中…", "Loading…")}</div>
      ) : events.length === 0 ? (
        <Empty icon="history" title={t("最近没有命令", "No recent commands")}>
          {t("在网页终端里敲的命令会出现在这里。", "Commands typed in the web terminal show up here.")}
        </Empty>
      ) : (
        <ul className="list">
          {events.map((e, i) => {
            const line = typeof e.data?.line === "string" && e.data.line ? e.data.line : e.summary.replace(/^\$ /, "");
            const denied = e.kind !== "command";
            return (
              <li className="list__item" key={`${e.ts}-${i}`}>
                <span className="list__lead mono t-muted">{hhmm(e.ts)}</span>
                <code className="list__title">{line}</code>
                <span className="list__trail">{denied ? <Pill tone="bad">{t("被拒", "denied")}</Pill> : null}</span>
                <span className="list__meta">{t("网页 · 终端", "web · terminal")}</span>
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}

/* ---------------- adb root / unroot / remount ---------------- */

type AdbKind = "root" | "unroot" | "remount";

const ADB_COPY: Record<AdbKind, [string, string, string, string, string, string]> = {
  root: ["让 adbd 以 root 重启,当前的 adb shell 会断开,之后重新连接。", "Restart adbd as root. The current adb shell drops; reconnect afterwards.", "切到 root", "Switch to root", "切换到 root", "Switch to root"],
  unroot: ["让 adbd 回到普通用户重启,当前的 adb shell 会断开。有些固件不理会这个请求。", "Restart adbd as the shell user. The current adb shell drops. Some builds ignore the request.", "回到普通用户", "Back to shell user", "回到普通用户", "Back to shell user"],
  remount: ["把 /system、/vendor 等分区重新挂成可写。需要先是 root;这会改动板子。", "Remount /system, /vendor and others read-write. Needs root first; this changes the board.", "重新挂载", "Remount", "重新挂载为可写", "Remount read-write"],
};

/** Plain-words meaning of the result codes the three actions can return. */
const ADB_CODES: Record<string, [string, string]> = {
  ADB_UNROOT_NO_EFFECT: [
    "adbd 重启了,但回来还是 root:这版固件不管 adb unroot 怎么要求都保持 root。不是出错,重试和等待都没用;想以普通用户跑一条命令,在前面加 su 2000。",
    "adbd restarted but came back as root: this build keeps adbd root whatever adb unroot asks. Not an error; retrying or waiting changes nothing. To run one command as the shell user, prefix su 2000.",
  ],
  ADB_ROOT_REFUSED: [
    "固件拒绝了 root(正式版固件通常如此)。单条命令可以用 su 0 前缀;要整个 shell 是 root,需要 userdebug 或 eng 固件。",
    "The build refused root (normal on user builds). Prefix single commands with su 0; a root shell needs a userdebug or eng build.",
  ],
  ADB_REMOUNT_FAILED: [
    "分区挂不上可写。先确认已经是 root;开着 dm-verity 的固件还要先关掉校验再重启一次。",
    "The partitions would not mount read-write. Make sure adbd is root first; with dm-verity on, disable verification and reboot once.",
  ],
  ADB_COMMAND_FAILED: ["adb 命令本身失败了,详细原因见下面的原文。", "The adb command itself failed; the original message is below."],
  ADB_SERVER_UNREACHABLE: ["连不上 adb server。检查台架代理和 adb 隧道是否在线。", "Cannot reach the adb server. Check the bench agent and the adb tunnel."],
  ADB_BINARY_NOT_FOUND: ["这台主机上找不到 adb 程序。", "The adb binary is not installed on this host."],
  DEVICE_NOT_FOUND: ["adb 里没有这块板子。它可能在 bootloader 或没上电;串口页签通常还能用。", "adb does not list this board. It may be in the bootloader or off; the serial tab usually still works."],
};

interface AdbResult {
  kind: AdbKind;
  ok: boolean;
  code?: string;
  message?: string;
  suggestion?: string;
}

function AdbAccessCard() {
  const t = useT();
  const device = useApp((s) => s.device);
  const [pending, setPending] = useState<AdbKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AdbResult | null>(null);

  const go = async (kind: AdbKind) => {
    setBusy(true);
    try {
      const r = await adbAction(kind, device);
      setResult({ kind, ok: !!r.ok, code: r.error?.code, message: r.error?.message, suggestion: r.error?.suggestion });
    } catch (e) {
      setResult({ kind, ok: false, code: "NETWORK", message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  const explain = result?.code ? ADB_CODES[result.code] : undefined;
  return (
    <article className="card">
      <CardHead title={t("adb 权限", "adb access")} sub={t("root / unroot / remount", "root / unroot / remount")} />
      <div className="row">
        {(["root", "unroot", "remount"] as const).map((k) => (
          <button key={k} type="button" className="btn btn--outlined btn--sm" disabled={!device || busy} onClick={() => setPending(k)}>
            {k === "root" ? "adb root" : k === "unroot" ? "adb unroot" : "adb remount"}
          </button>
        ))}
      </div>
      {!device ? <p className="t-body-small t-muted" style={{ margin: 0 }}>{t("没有选中的设备。", "No device selected.")}</p> : null}
      {pending ? (
        <Confirm
          open
          busy={busy}
          message={t(ADB_COPY[pending][0], ADB_COPY[pending][1])}
          confirmLabel={t(ADB_COPY[pending][2], ADB_COPY[pending][3])}
          cancelLabel={t("取消", "Cancel")}
          onConfirm={() => void go(pending)}
          onCancel={() => setPending(null)}
        />
      ) : null}
      {result ? (
        result.ok ? (
          <div className="banner banner--ok" role="status">
            <Icon name="check-circle" />
            <p>{t(`adb ${result.kind} 成功。`, `adb ${result.kind} succeeded.`)}</p>
            <span />
          </div>
        ) : (
          <div className="banner banner--warn" role="status">
            <Icon name="alert" />
            <p>
              <b className="mono">{result.code ?? "ERROR"}</b>
              <br />
              {explain ? t(explain[0], explain[1]) : result.message}
              {explain && result.message ? (
                <>
                  <br />
                  <span className="t-muted">{result.message}</span>
                </>
              ) : null}
            </p>
            <span />
          </div>
        )
      ) : null}
    </article>
  );
}
