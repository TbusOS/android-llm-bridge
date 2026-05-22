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

/** Synthetic id for tree state: the node's flatten-index path is unique
 *  per dump. Using a global counter on each (re)dump avoids collisions
 *  when the same uiautomator class repeats. */
function assignIds(root: UiNode): Map<UiNode, string> {
  const ids = new Map<UiNode, string>();
  let n = 0;
  const walk = (node: UiNode, prefix: string) => {
    const id = `${prefix}${n++}`;
    ids.set(node, id);
    node.children.forEach((c, i) => walk(c, `${id}.${i}.`));
  };
  walk(root, "");
  return ids;
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
        const ids = assignIds(data.ui_dump.root);
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
  // Assign stable ids once per dump payload (memo by root identity).
  const idMap = useMemo(
    () => (dump?.root ? assignIds(dump.root) : new Map<UiNode, string>()),
    [dump?.root],
  );
  // Cache the flattened tree so the keystroke path doesn't re-walk a
  // 2000-node tree on every input character. flattenNodes is pure
  // over `dump.root`, so memo by reference identity.
  const allNodes = useMemo(
    () => (dump?.root ? flattenNodes(dump.root, 0) : []),
    [dump?.root],
  );
  // Deferred filter: input updates are eager (typing stays snappy),
  // the actual list filter runs at lower priority — React 18 will
  // skip intermediate frames if the user is still typing.
  const deferredFilter = useDeferredValue(filter);
  // Active expansion set: when filtering, the visible set widens to
  // include every match's ancestor chain so the match is reachable.
  const effectiveExpanded = useMemo(() => {
    if (!deferredFilter) return expanded;
    const q = deferredFilter.toLowerCase();
    const widened = new Set(expanded);
    // Walk the tree, marking ancestors of any matching node as expanded.
    const walk = (n: UiNode, ancestors: string[]): boolean => {
      const id = idMap.get(n)!;
      let hasMatch = nodeMatch(n, q);
      for (const c of n.children) {
        if (walk(c, [...ancestors, id])) hasMatch = true;
      }
      if (hasMatch) {
        for (const a of ancestors) widened.add(a);
        widened.add(id);
      }
      return hasMatch;
    };
    if (dump?.root) walk(dump.root, []);
    return widened;
  }, [expanded, deferredFilter, dump?.root, idMap]);

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
        {filter && allNodes.length > 0 && (
          <span
            className="uart-tab__last"
            role="status"
            aria-live="polite"
          >
            {lang === "zh"
              ? `${visibleNodes.length} / ${allNodes.length} 显 (含 ancestor 链)`
              : `${visibleNodes.length} of ${allNodes.length} shown`}
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
        {allNodes.length === 0 && (
          <div className="uart-tab__empty">
            {lang === "zh" ? "点上方「抓 UI」按钮" : "Press Dump above"}
          </div>
        )}
        {allNodes.length > 0 && visibleNodes.length === 0 && filter && (
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
