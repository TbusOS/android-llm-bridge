/**
 * Problems (v3): what the previous boot left in pstore, plus the v2 Diag
 * tab (bugreport / ANR / tombstone pulls and the collected artefacts).
 */
import { lazy, Suspense, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { CardHead, Empty, PageHead, Pill } from "../../../components/v3/ui";
import { pullPstore, type PstoreRecord } from "../../../lib/api3";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";

const DiagTab = lazy(() => import("../../inspect/DiagTab").then((m) => ({ default: m.DiagTab })));

export function ProblemsPage() {
  const t = useT();
  const device = useApp((s) => s.device);
  const openSheet = useShell((s) => s.openSheet);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ records: PstoreRecord[]; note: string; saved_dir: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const read = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await pullPstore(device);
      if (r.ok && r.data) setResult({ records: r.data.records, note: r.data.note, saved_dir: r.data.saved_dir });
      else setError(`${r.error?.code ?? "ERROR"}: ${r.error?.message ?? ""}${r.error?.suggestion ? ` — ${r.error.suggestion}` : ""}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        eyebrow={<span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span>}
        title={t("问题", "Problems")}
        desc={t("上一次开机的内核崩溃记录、ANR、tombstone 和 bugreport。", "The previous boot's kernel crash record, ANRs, tombstones and bugreports.")}
      />
      <div className="stack">
        <article className="card">
          <CardHead
            title={t("上一次开机留下的(pstore)", "Left by the previous boot (pstore)")}
            sub={t("内核崩溃时写进 ramoops 的日志,重启后还在;多数板子要 root 才能读", "what the kernel wrote to ramoops when it crashed, kept across the reboot; most boards need root to read it")}
            actions={
              <button type="button" className="btn btn--tonal btn--sm" onClick={() => void read()} disabled={!device || busy}>
                {busy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="download" size="sm" />}
                {t("读取并保存", "Read and save")}
              </button>
            }
          />
          {error ? (
            <div className="banner banner--error"><Icon name="error" /><p>{error}</p><span /></div>
          ) : !result ? (
            <Empty icon="bug" title={t("还没读", "Not read yet")}>{t("读取后会存到工作区的 pstore/ 下,并列出每份记录的第一行。", "Reading saves the records under pstore/ in the workspace and lists the first line of each.")}</Empty>
          ) : result.records.length === 0 ? (
            <Empty icon="check-circle" title={t("没有记录", "No records")}>{result.note}</Empty>
          ) : (
            <ul className="list">
              {result.records.map((r) => (
                <li className="list__item" key={r.name}>
                  <span className="list__lead"><Icon name={r.has_panic ? "alert" : "logs"} /></span>
                  <span className="list__title mono">{r.name}</span>
                  <span className="list__trail">
                    {r.has_panic ? <Pill tone="bad">{t("内核崩溃", "kernel panic")}</Pill> : <Pill>{r.kind}</Pill>}
                    <button type="button" className="btn btn--text btn--sm" onClick={() => openSheet({ zh: `pstore · ${r.name}`, en: `pstore · ${r.name}`, detail: `Context: pstore record ${r.name} saved at ${r.saved_path}; first line: ${r.summary}` })}>
                      <Icon name="spark" size="sm" />
                      {t("解释", "Explain")}
                    </button>
                  </span>
                  <span className="list__meta mono">{r.summary || r.error} · {r.size} B · {r.saved_path}</span>
                </li>
              ))}
            </ul>
          )}
        </article>
        <div className="legacy">
          <Suspense fallback={<div className="skeleton" style={{ height: 240 }} />}>
            <DiagTab />
          </Suspense>
        </div>
      </div>
    </>
  );
}
