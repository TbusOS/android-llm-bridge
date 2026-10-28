/**
 * Logs (v3): live logcat, kernel messages, the serial console, and search
 * across everything already recorded. Baseline: docs/webui-preview-v3-logs.html.
 */
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { Dot, PageHead, toast } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { LogcatPanel } from "./LogcatPanel";
import { useLogcat } from "./logcatModel";
import { DmesgPanel, SearchPanel, SerialPanel } from "./OtherPanels";
import "./logs.css";

type Tab = "logcat" | "dmesg" | "serial" | "search";
const TABS: readonly Tab[] = ["logcat", "dmesg", "serial", "search"];

export function LogsPage() {
  const t = useT();
  const device = useApp((s) => s.device);
  const search = useSearch({ strict: false }) as { tab?: string };
  const navigate = useNavigate();
  const initial = (TABS as readonly string[]).includes(search.tab ?? "") ? (search.tab as Tab) : "logcat";
  const [tab, setTab] = useState<Tab>(initial);
  const lc = useLogcat(device);

  const pick = (x: Tab) => {
    setTab(x);
    void navigate({ to: "/logs", search: { tab: x } as never, replace: true });
  };

  const save = () => {
    const text = lc.visible.map((e) => `${e.ts} ${e.pid} ${e.tid} ${e.level} ${e.tag}: ${e.msg}${e.cont.length ? `\n${e.cont.join("\n")}` : ""}`).join("\n");
    if (!text) {
      toast(t("现在没有可存的日志。", "Nothing to save yet."));
      return;
    }
    try {
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `logcat-${new Date().toISOString().replace(/[:.]/g, "-")}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast(t("保存失败。", "Save failed."));
    }
  };

  const tabs: { id: Tab; icon: string; label: string }[] = [
    { id: "logcat", icon: "logs", label: "logcat" },
    { id: "dmesg", icon: "cpu", label: "dmesg" },
    { id: "serial", icon: "plug", label: t("串口", "Serial") },
    { id: "search", icon: "search", label: t("搜索全部记录", "Search all recordings") },
  ];

  return (
    <>
      <PageHead
        eyebrow={<span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span>}
        title={t("日志", "Logs")}
        desc={t("实时 logcat、内核日志、串口输出,以及在所有已录的日志里搜索。", "Live logcat, kernel messages and the serial console, plus search across everything already recorded.")}
        actions={
          <>
            {tab === "logcat" ? (
              <>
                <button type="button" className="btn btn--outlined" disabled={!device} onClick={() => { if (lc.paused) lc.resume(); else { lc.pause(); toast(t("已暂停,新日志先缓存。", "Paused; new lines are buffered.")); } }}>
                  <Icon name={lc.paused ? "play" : "pause"} />
                  {lc.paused ? t("继续", "Resume") : t("暂停", "Pause")}
                </button>
                <button type="button" className="btn btn--outlined" onClick={save}>
                  <Icon name="download" />
                  {t("保存", "Save")}
                </button>
              </>
            ) : null}
            <Link className="btn btn--tonal" to="/timeline">
              <Icon name="timeline" />
              {t("在时间轴中看", "Open in timeline")}
            </Link>
          </>
        }
      />
      <article className="card card--flush">
        <div className="tabs" role="tablist" aria-label={t("日志来源", "log sources")}>
          {tabs.map((x) => (
            <button key={x.id} type="button" className="tab" role="tab" aria-selected={tab === x.id} aria-controls={`p-${x.id}`} onClick={() => pick(x.id)}>
              <Icon name={x.icon} size="sm" />
              {x.label}
              {x.id === "logcat" && lc.state === "ready" ? <Dot tone="live" /> : null}
            </button>
          ))}
        </div>
        {tab === "logcat" ? <LogcatPanel lc={lc} device={device} /> : null}
        {tab === "dmesg" ? <DmesgPanel device={device} /> : null}
        {tab === "serial" ? <SerialPanel device={device} /> : null}
        {tab === "search" ? <SearchPanel device={device} /> : null}
      </article>
    </>
  );
}
