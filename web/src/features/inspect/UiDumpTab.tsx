/**
 * Inspect → UI Dump — uiautomator dump as a collapsible tree (PR-G).
 *
 * v2 (DEBT-043 first slice): per-node expand/collapse. The tree is
 * computed from `dump.root` once, then re-flattened on every
 * expand/collapse using the current `expanded` set. Search bypasses
 * collapse — when filtering, all nodes that match (and their ancestor
 * chain) are auto-expanded so matches are visible.
 *
 * Bounds-on-screenshot overlay still pending (DEBT-043 second slice).
 */

import { useDeferredValue, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ScanSearch, X } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";

import { useApp } from "../../stores/app";
import {
  captureUiDump,
  type UiDumpResponse,
  type UiNode,
} from "../../lib/api";
import { NoDeviceCard } from "../../components/NoDeviceCard";

// Row height matches mono font 11px × line-height 1.7 ≈ 18.7px + 1px
// border-bottom; estimate slightly above to avoid resize jitter when
// rows wrap onto a second line (rare — usually only the bounds field
// is wide). useVirtualizer measures real heights at runtime via
// `measureElement`, so this estimate is a hint not a hard cap.
const _UIDUMP_ROW_ESTIMATE = 24;

/** Synthetic id for tree state + flat parent index, built in one walk.
 *  Per-dump, so used to be O(N) every render — now memoised against
 *  `dump.root` reference identity. The flat `parents` map lets the
 *  filter ancestor-chain walk be O(matches × depth) instead of the
 *  previous O(N × depth) full-tree scan with per-node array alloc. */
function buildTreeIndex(root: UiNode): {
  ids: Map<UiNode, string>;
  parents: Map<UiNode, UiNode | null>;
  all: UiNode[];
} {
  const ids = new Map<UiNode, string>();
  const parents = new Map<UiNode, UiNode | null>();
  const all: UiNode[] = [];
  let n = 0;
  const walk = (node: UiNode, parent: UiNode | null, prefix: string) => {
    const id = `${prefix}${n++}`;
    ids.set(node, id);
    parents.set(node, parent);
    all.push(node);
    node.children.forEach((c, i) => walk(c, node, `${id}.${i}.`));
  };
  walk(root, null, "");
  return { ids, parents, all };
}

export function UiDumpTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [last, setLast] = useState<UiDumpResponse | null>(null);
  const [filter, setFilter] = useState("");
  // Set of node-ids that are explicitly expanded. Default: top-level
  // (root + depth=1) expanded so dump opens to a useful state without
  // forcing the user to click "expand all".
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const m = useMutation({
    mutationFn: () => {
      if (!device) throw new Error("no device");
      return captureUiDump(device);
    },
    onSuccess: (data) => {
      setLast(data);
      // Reset expansion: open root + its direct children only.
      if (data.ui_dump?.root) {
        const { ids } = buildTreeIndex(data.ui_dump.root);
        const next = new Set<string>();
        const rootId = ids.get(data.ui_dump.root);
        if (rootId) next.add(rootId);
        data.ui_dump.root.children.forEach((c) => {
          const id = ids.get(c);
          if (id) next.add(id);
        });
        setExpanded(next);
      }
    },
  });

  if (!device) {
    return <NoDeviceCard titleZh="UI 树" titleEn="UI Dump" />;
  }

  const dump = last?.ui_dump;
  // Single tree-index pass per dump: id map + parent map + flat array.
  // Memoised by `dump.root` reference; only walks once per uiautomator
  // call.
  const treeIndex = useMemo(
    () =>
      dump?.root
        ? buildTreeIndex(dump.root)
        : {
            ids: new Map<UiNode, string>(),
            parents: new Map<UiNode, UiNode | null>(),
            all: [] as UiNode[],
          },
    [dump?.root],
  );
  const { ids: idMap, parents: parentMap, all: allFlat } = treeIndex;
  const allNodesCount = allFlat.length;
  // Deferred filter: input updates are eager (typing stays snappy),
  // the actual list filter runs at lower priority — React 18 will
  // skip intermediate frames if the user is still typing.
  const deferredFilter = useDeferredValue(filter);
  // perf MID (5/22 audit M6): previously walked the entire tree per
  // keystroke + alloc'd a new ancestors array at every depth (O(N×D)
  // per filter character). Now iterate the flat all[] once to find
  // matches, then for each match climb the parent chain via parentMap
  // (O(M×D) where M = match count ≪ N). Total per keystroke
  // O(N + M×D) vs O(N×D).
  const effectiveExpanded = useMemo(() => {
    if (!deferredFilter) return expanded;
    const q = deferredFilter.toLowerCase();
    const widened = new Set(expanded);
    for (const node of allFlat) {
      if (!nodeMatch(node, q)) continue;
      let cur: UiNode | null = node;
      while (cur) {
        const id = idMap.get(cur);
        if (id) widened.add(id);
        cur = parentMap.get(cur) ?? null;
      }
    }
    return widened;
  }, [expanded, deferredFilter, allFlat, idMap, parentMap]);

  // Build the visible list by walking the tree and pruning collapsed
  // subtrees. Combines tree-walk + filter in one pass so we don't pay
  // O(N) twice.
  const visibleNodes = useMemo(() => {
    if (!dump?.root) return [] as { node: UiNode; depth: number; hasChildren: boolean }[];
    const q = deferredFilter ? deferredFilter.toLowerCase() : "";
    const out: { node: UiNode; depth: number; hasChildren: boolean }[] = [];
    const walk = (n: UiNode, depth: number) => {
      const matches = !q || nodeMatch(n, q);
      const id = idMap.get(n)!;
      // If filtering, only emit nodes whose subtree contains a match
      // OR self-matches; effectiveExpanded already tracks ancestor
      // chains of matches.
      if (!q || effectiveExpanded.has(id) || matches) {
        out.push({ node: n, depth, hasChildren: n.children.length > 0 });
      }
      if (effectiveExpanded.has(id)) {
        for (const c of n.children) walk(c, depth + 1);
      }
    };
    walk(dump.root, 0);
    return out;
  }, [dump?.root, deferredFilter, effectiveExpanded, idMap]);

  const toggleNode = (n: UiNode) => {
    const id = idMap.get(n);
    if (!id) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // ui-fluency MID-4 (2026-05-07): virtualize the row list. >1000 node
  // dumps used to render every row and pay layout cost on each filter
  // keystroke; only render visible rows now.
  const listRef = useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: visibleNodes.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => _UIDUMP_ROW_ESTIMATE,
    overscan: 12,
  });

  return (
    <div className="uidump-tab">
      <div className="uart-tab__bar">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => m.mutate()}
          disabled={m.isPending}
        >
          <ScanSearch size={12} className="icon-inline" />{" "}
          {m.isPending
            ? lang === "zh" ? "抓取中…" : "Dumping…"
            : lang === "zh" ? "抓 UI" : "Dump"}
        </button>
        <div className="uidump-tab__filter">
          <input
            type="text"
            placeholder={lang === "zh" ? "过滤 class / id / 文本" : "filter class / id / text"}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {filter && (
            <button
              type="button"
              className="uidump-tab__filter-clear"
              onClick={() => setFilter("")}
              aria-label={lang === "zh" ? "清空过滤" : "Clear filter"}
            >
              <X size={12} />
            </button>
          )}
        </div>
        {filter && allNodesCount > 0 && (
          <span
            className="uart-tab__last"
            role="status"
            aria-live="polite"
          >
            {lang === "zh"
              ? `${visibleNodes.length} / ${allNodesCount} 显 (含 ancestor 链)`
              : `${visibleNodes.length} of ${allNodesCount} shown`}
          </span>
        )}
        {dump && (
          <span className="uart-tab__last">
            {dump.node_count} nodes · {dump.top_activity || "?"}
          </span>
        )}
        {last?.ok === false && (
          <span className="uart-tab__last uart-tab__last--err">{last.error}</span>
        )}
      </div>

      <div className="uidump-tab__list" ref={listRef}>
        {allNodesCount === 0 && (
          <div className="uart-tab__empty">
            {lang === "zh" ? "点上方「抓 UI」按钮" : "Press Dump above"}
          </div>
        )}
        {allNodesCount > 0 && visibleNodes.length === 0 && filter && (
          <div className="uart-tab__empty">
            {lang === "zh"
              ? `没有节点匹配 "${filter}"`
              : `No nodes match "${filter}"`}
          </div>
        )}
        {visibleNodes.length > 0 && (
          <div
            className="uidump-tab__virt"
            style={{ height: rowVirtualizer.getTotalSize() }}
          >
            {rowVirtualizer.getVirtualItems().map((vRow) => {
              const item = visibleNodes[vRow.index];
              if (!item) return null;
              const { node, depth, hasChildren } = item;
              const id = idMap.get(node);
              const isOpen = !!(id && effectiveExpanded.has(id));
              return (
                <div
                  key={vRow.key}
                  className="uidump-tab__virt-row"
                  data-index={vRow.index}
                  ref={rowVirtualizer.measureElement}
                  style={{ transform: `translateY(${vRow.start}px)` }}
                >
                  <div
                    className="uidump-tab__row"
                    style={{ paddingLeft: 4 + depth * 14 }}
                  >
                    {hasChildren ? (
                      <button
                        type="button"
                        className="uidump-tab__chev"
                        onClick={() => toggleNode(node)}
                        aria-expanded={isOpen}
                        aria-label={
                          isOpen
                            ? lang === "zh"
                              ? "折叠子节点"
                              : "collapse"
                            : lang === "zh"
                              ? "展开子节点"
                              : "expand"
                        }
                      >
                        {isOpen ? (
                          <ChevronDown size={11} />
                        ) : (
                          <ChevronRight size={11} />
                        )}
                      </button>
                    ) : (
                      <span className="uidump-tab__chev uidump-tab__chev--leaf" />
                    )}
                    <span className="uidump-tab__cls">
                      {shortClass(node.class)}
                    </span>
                    {node.resource_id && (
                      <span className="uidump-tab__id">
                        #{node.resource_id.split("/").pop()}
                      </span>
                    )}
                    {node.text && (
                      <span className="uidump-tab__text">"{node.text}"</span>
                    )}
                    {node.content_desc && (
                      <span className="uidump-tab__desc">
                        [{node.content_desc}]
                      </span>
                    )}
                    <span className="uidump-tab__bounds">
                      {node.bounds[0]},{node.bounds[1]} → {node.bounds[2]},
                      {node.bounds[3]}
                    </span>
                    {node.clickable && (
                      <span className="uidump-tab__pill">click</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
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
