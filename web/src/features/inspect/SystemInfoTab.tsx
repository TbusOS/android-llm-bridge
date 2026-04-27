/**
 * Inspect → System Info — 6 sys-cards in a sys-grid.  Five of them
 * are KV tables (System / CPU / Memory / Network / Battery); the
 * sixth (Storage) renders partition rows with progress bars.
 *
 * Lifted from docs/webui-preview-v2.html .sys-grid section.
 */
import { Fragment } from "react";
import { useApp } from "../../stores/app";
import { MOCK_STORAGE, MOCK_SYS_CARDS } from "./mockData";
import type { PartitionRowData, SysCardData } from "./types";

export function SystemInfoTab() {
  const lang = useApp((s) => s.lang);

  return (
    <div className="sys-grid">
      <KvCard data={MOCK_SYS_CARDS[0]} lang={lang} />
      <KvCard data={MOCK_SYS_CARDS[1]} lang={lang} />
      <StorageCard data={MOCK_STORAGE} lang={lang} />
      <KvCard data={MOCK_SYS_CARDS[2]} lang={lang} />
      <KvCard data={MOCK_SYS_CARDS[3]} lang={lang} />
      <KvCard data={MOCK_SYS_CARDS[4]} lang={lang} />
    </div>
  );
}

function KvCard({ data, lang }: { data: SysCardData | undefined; lang: string }) {
  if (!data) return null;
  return (
    <div className="sys-card">
      <h3>{lang === "zh" ? data.titleZh : data.title}</h3>
      <dl className="sys-kv">
        {data.entries.map((entry) => (
          <Fragment key={entry.key}>
            <dt>{lang === "zh" && entry.keyZh ? entry.keyZh : entry.key}</dt>
            <dd>{entry.value}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

function StorageCard({
  data,
  lang,
}: {
  data: typeof MOCK_STORAGE;
  lang: string;
}) {
  return (
    <div className="sys-card">
      <h3>{lang === "zh" ? data.titleZh : data.title}</h3>
      {data.partitions.map((p) => (
        <PartitionRow key={p.name} data={p} />
      ))}
    </div>
  );
}

function PartitionRow({ data }: { data: PartitionRowData }) {
  const fillClass =
    data.color === "green"
      ? "part-fill part-fill--green"
      : data.color === "blue"
        ? "part-fill part-fill--blue"
        : data.color === "red"
          ? "part-fill part-fill--red"
          : "part-fill";
  return (
    <div className="part-row">
      <span className="part-name">{data.name}</span>
      <div className="part-bar">
        <div className={fillClass} style={{ width: `${data.pct}%` }} />
      </div>
      <span className="part-pct">{data.pct}%</span>
    </div>
  );
}

