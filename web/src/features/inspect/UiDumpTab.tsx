/**
 * Inspect → UI Dump — uiautomator dump as a flat tree (PR-G).
 *
 * v1: button + flat indented node list with key fields (class /
 * resource_id / text / bounds). Bounds-on-screenshot overlay left for
 * v2 (would re-fetch a screenshot then position absolute divs over
 * each clickable region).
 */

import { useDeferredValue, useMemo, useState } from "react";
import { ScanSearch } from "lucide-react";
import { useMutation } from "@tanstack/react-query";

import { useApp } from "../../stores/app";
import {
  captureUiDump,
  type UiDumpResponse,
  type UiNode,
} from "../../lib/api";

export function UiDumpTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [last, setLast] = useState<UiDumpResponse | null>(null);
  const [filter, setFilter] = useState("");

  const m = useMutation({
    mutationFn: () => {
      if (!device) throw new Error("no device");
      return captureUiDump(device);
    },
    onSuccess: (data) => setLast(data),
  });

  if (!device) {
    return (
      <div className="mock-card">
        <h1 style={{ fontSize: 22 }}>{lang === "zh" ? "UI 树" : "UI Dump"}</h1>
        <p className="section-sub">
          {lang === "zh"
            ? "顶栏选一台设备再回这里。"
            : "Pick a device from the top-bar picker, then come back."}
        </p>
      </div>
    );
  }

  const dump = last?.ui_dump;
  // Cache the flattened tree so the keystroke path doesn't re-walk a
  // 2000-node tree on every input character. flattenNodes is pure
  // over `dump.root`, so memo by reference identity.
  const nodes = useMemo(
    () => (dump?.root ? flattenNodes(dump.root, 0) : []),
    [dump?.root],
  );
  // Deferred filter: input updates are eager (typing stays snappy),
  // the actual list filter runs at lower priority — React 18 will
  // skip intermediate frames if the user is still typing.
  const deferredFilter = useDeferredValue(filter);
  const visibleNodes = useMemo(() => {
    if (!deferredFilter) return nodes;
    const q = deferredFilter.toLowerCase();
    return nodes.filter((n) => nodeMatch(n.node, q));
  }, [nodes, deferredFilter]);

  return (
    <div className="uidump-tab">
      <div className="uart-tab__bar">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => m.mutate()}
          disabled={m.isPending}
        >
          <ScanSearch size={12} style={{ verticalAlign: "-2px" }} />{" "}
          {m.isPending
            ? lang === "zh" ? "抓取中…" : "Dumping…"
            : lang === "zh" ? "抓 UI" : "Dump"}
        </button>
        <input
          type="text"
          placeholder={lang === "zh" ? "过滤 class / id / 文本" : "filter class / id / text"}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ width: 240, padding: "4px 8px", fontSize: 12 }}
        />
        {dump && (
          <span className="uart-tab__last">
            {dump.node_count} nodes · {dump.top_activity || "?"}
          </span>
        )}
        {last?.ok === false && (
          <span className="uart-tab__last uart-tab__last--err">{last.error}</span>
        )}
      </div>

      <div className="uidump-tab__list">
        {nodes.length === 0 && (
          <div className="uart-tab__empty">
            {lang === "zh" ? "点上方「抓 UI」按钮" : "Press Dump above"}
          </div>
        )}
        {visibleNodes.map(({ node, depth }, i) => (
          <div
            key={`${i}-${node.index}`}
            className="uidump-tab__row"
            style={{ paddingLeft: 8 + depth * 14 }}
          >
            <span className="uidump-tab__cls">
              {shortClass(node.class)}
            </span>
            {node.resource_id && (
              <span className="uidump-tab__id">#{node.resource_id.split("/").pop()}</span>
            )}
            {node.text && <span className="uidump-tab__text">"{node.text}"</span>}
            {node.content_desc && (
              <span className="uidump-tab__desc">[{node.content_desc}]</span>
            )}
            <span className="uidump-tab__bounds">
              {node.bounds[0]},{node.bounds[1]} → {node.bounds[2]},{node.bounds[3]}
            </span>
            {node.clickable && <span className="uidump-tab__pill">click</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

function flattenNodes(
  node: UiNode,
  depth: number,
): { node: UiNode; depth: number }[] {
  const out: { node: UiNode; depth: number }[] = [{ node, depth }];
  for (const c of node.children) out.push(...flattenNodes(c, depth + 1));
  return out;
}

function shortClass(cls: string): string {
  const i = cls.lastIndexOf(".");
  return i >= 0 ? cls.slice(i + 1) : cls;
}

function nodeMatch(n: UiNode, q: string): boolean {
  return (
    n.class.toLowerCase().includes(q) ||
    n.resource_id.toLowerCase().includes(q) ||
    n.text.toLowerCase().includes(q) ||
    n.content_desc.toLowerCase().includes(q)
  );
}
