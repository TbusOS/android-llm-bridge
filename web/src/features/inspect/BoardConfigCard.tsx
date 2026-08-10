/**
 * "What the config partition holds right now" — the readback flashing cannot
 * give you.
 *
 * Sits on the Flash tab on purpose: the question it answers ("did that take?")
 * is asked immediately after a flash. It only works once the board is back in
 * Android though — in fastboot there is no shell to read a block device with —
 * so the card says that rather than looking broken.
 */
import { useState } from "react";

import { useApp } from "../../stores/app";
import { useConfigRead, useConfigScan } from "./useBoardConfig";

export function BoardConfigCard() {
  const lang = useApp((s) => s.lang);
  const [armed, setArmed] = useState(false);
  const scan = useConfigScan(armed);
  const read = useConfigRead();

  const candidates = scan.data?.candidates ?? [];
  const result = read.data;

  return (
    <section className="cfg-card flash-tab__wide">
      <header className="cfg-card__head">
        <h2 className="cfg-card__title">board config</h2>
        {result ? (
          <span className="cfg-card__node">
            {result.node} · {result.size_bytes} bytes
          </span>
        ) : null}
      </header>
      <p className="cfg-card__hint">
        {lang === "zh" ? (
          <>
            板子上这个分区现在实际是什么，从已启动的系统读回来的。烧录报告的是 fastboot
            认为它写了；只有这里能看到字节。分区靠内容认——头部能解析成 KEY="VALUE"
            的那个——不靠名字，名字每个产品都不一样。需要 root，且板子已进系统。
          </>
        ) : (
          <>
            What the partition holds right now, read back from the booted board. Flashing
            reports that fastboot believed it wrote; this is the only place that shows the
            bytes. The partition is found by content — the one whose head parses as
            KEY="VALUE" — not by a name, which differs per product. Needs root, and the
            board in Android.
          </>
        )}
      </p>

      <div className="flash-action-card__row">
        <button
          type="button"
          className="flash-btn flash-btn--ghost"
          disabled={scan.isFetching}
          onClick={() => {
            setArmed(true);
            void scan.refetch();
          }}
        >
          {scan.isFetching
            ? lang === "zh"
              ? "扫描中…"
              : "scanning…"
            : lang === "zh"
              ? "扫描配置分区"
              : "scan for config"}
        </button>
        {candidates.map((c) => (
          <button
            key={c.name}
            type="button"
            className="flash-btn flash-btn--ghost"
            disabled={read.isPending}
            onClick={() => read.mutate({ name: c.name })}
          >
            {lang === "zh" ? `读 ${c.name}` : `read ${c.name}`}
          </button>
        ))}
      </div>

      {scan.isError ? <p className="cfg-card__hint">{String(scan.error)}</p> : null}
      {scan.data && candidates.length === 0 ? (
        // Say the usual cause. "No config partition on this board" and "the
        // shell is not root, so every block device read returned nothing" are
        // the same observation from here.
        <p className="cfg-card__hint">{scan.data.hint}</p>
      ) : null}
      {read.isError ? <p className="cfg-card__hint">{String(read.error)}</p> : null}

      {result && result.parsed ? (
        <table className="cfg-table">
          <thead>
            <tr>
              <th>key</th>
              <th>value</th>
            </tr>
          </thead>
          <tbody>
            {result.entries.map((e) => (
              <tr key={e.key}>
                <td className="cfg-table__key">{e.key}</td>
                <td className="cfg-table__val">{e.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result && !result.parsed ? (
        <>
          {/* Never an empty table here. An empty one reads as "the config is
              empty"; the truth is almost always "that is not the config
              partition". */}
          <p className="cfg-card__hint">
            {lang === "zh"
              ? `${result.node} 的内容不是 KEY="VALUE"，原始字节如下：`
              : `${result.node} does not parse as KEY="VALUE". Raw bytes as read:`}
          </p>
          <pre className="cfg-raw">{result.raw.slice(0, 2000)}</pre>
        </>
      ) : null}
    </section>
  );
}
