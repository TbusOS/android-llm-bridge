# performance-auditor 报告 · DEBT-022 batch · 2026-05-02

## 摘要

- 评审范围：DEBT-022 batch (PR-A/B/C.a/C.b/D/E/F/G/H · 9 commits 2026-04-30~05-02)
- 主要瓶颈：xterm.js 全量打进主 bundle (~80 KB gzip) + 6 hooks 漏 background gate + UiDumpTab render-time flatten
- 严重度：**high=2 / mid=3 / low=1**

## bundle 现状

- 当前：`docs/app/assets/index-_hlwuQOg.js` = 725,553 B 原始 / **205 KB gzip**
- 预算：500 KB gzip → 41% 占用，**还在预算内**；vite 警告基于"原始 500 KB"非 gzip，可放宽
- 真实膨胀来源（grep 主 bundle）：`BufferLine×34` `Viewport×18` `Decorations×6` `RenderService` `SelectionService` `UnicodeService` —— xterm.js 完整入主 chunk
- node_modules 体量：`@xterm/xterm/lib/xterm.mjs` 345 KB raw（贡献 ~80 KB gzip ≈ 40% bundle）
- lucide-react 19 个 icon 命名导入 → tree-shaking 良好（~25-35 KB gzip）
- 其他大头：tanstack router + query + react 18 = ~80 KB gzip
- 近期增量：DEBT-022 之前 ~120 KB gzip 估，9 PR 加了 xterm.js + 8 个 tab 组件 ≈ +85 KB

## 发现

### 1. **[high]** `web/src/features/inspect/{UartLiveStream,ShellTab}.tsx` + `vite.config.ts` — xterm.js 全量进主 bundle

**测量**：xterm.mjs 345 KB raw / ~80 KB gzip，~40% 主 bundle。仅 ShellTab + UartLiveStream 用，dashboard / chat 路径完全不需要。
**原因**：`InspectPage` 静态 `import { ShellTab }` → 静态 `import { Terminal } from "@xterm/xterm"`，rollup 把 xterm 摊进 entry chunk。
**建议**：InspectPage 8 tabs 全部 `React.lazy` + `Suspense`：
```tsx
const ShellTab = lazy(() => import("./ShellTab"));
const UartTab = lazy(() => import("./UartTab"));
// ...
{tab === "shell" ? <Suspense fallback={<Spinner/>}><ShellTab/></Suspense> : null}
```
另在 `vite.config.ts` `rollupOptions.output.manualChunks` 把 `@xterm/*` 单独 chunk，只在需要 tab 加载。
**预估收益**：首屏 bundle **-80 KB gzip → ~125 KB gzip (25% 预算)**。Inspect 内首次进 UART/Shell tab +1 RTT (~50-150 ms)。
**预估成本**：1 commit / +5 行 / 0 风险。

### 2. **[high]** `web/src/features/dashboard/{useSessions,useDeviceDetails,useMetricsSummary,useTools,useAudit,useDevices}.ts` — 6 hook 漏 `refetchIntervalInBackground:false`

**测量**：dashboard 打开 + 切到别的浏览器 tab/窗口失焦 → 6 hooks 仍按 30 s 间隔轮询。每分钟 ≈ 12 HTTP requests + per-card device-details 30s × N 设备 (PR-A 引入)。`useMetricsSummary` 每次扫全量 `events.jsonl` (DEBT-008 已知)。**只有 `useBackends` 有这个 gate**（line 126/144）—— 显示是有意识做过，但 PR-A 新加的 `useDeviceDetails` 没沿用。
**原因**：PR-A reviewer 把 ADR-029 (a) 拍板"per-card 30s polling"时漏了 background gate；其他 5 个 hook 是 D 档遗留。
**建议**：6 个 hook 全加：
```ts
refetchIntervalInBackground: false,
refetchOnWindowFocus: false,  // useDeviceDetails / useMetricsSummary 已有 refetchInterval，不需要 focus 再叠
```
特别 `useDeviceDetails`：N 个 device card 各自 30s polling，N=4 设备 + 隐藏窗口 1 小时 = 480 次无效 fetch。
**预估收益**：隐藏窗口零 polling；活跃窗口 -0 改变；DEBT-008 events.jsonl 扫全量降 50%（用户切走时不扫）。
**预估成本**：1 commit / +12 行 / 沿用 useBackends pattern。

### 3. **[mid]** `web/src/features/inspect/UiDumpTab.tsx:48-54` — render-time 全树 flatten + filter

**测量**：每次 keystroke 输入 filter → `flattenNodes(dump.root, 0)` 递归全树（用户场景 500-3000 nodes）+ `nodes.filter(...)` 又一次全表扫。500 nodes × 4 字段 string toLowerCase + includes ≈ 1-3 ms × 每键 → 输入卡顿可见，>2000 nodes 时 >50 ms 主线程阻塞。
**原因**：function body 内每次 render 跑，没 useMemo cache。
**建议**：
```tsx
const nodes = useMemo(() => dump?.root ? flattenNodes(dump.root, 0) : [], [dump]);
const visibleNodes = useMemo(() =>
  filter ? nodes.filter(n => nodeMatch(n.node, filter.toLowerCase())) : nodes,
[nodes, filter]);
```
+ filter `useDeferredValue` 让 input 立即响应，列表过滤异步追上：
```tsx
const deferredFilter = useDeferredValue(filter);
```
**预估收益**：keystroke 主线程占用 -2 ms（500 nodes）/ -10 ms（2000 nodes）。
**预估成本**：1 commit / +6 行 / 0 风险。

### 4. **[mid]** `useUartStream.ts:101-103` / `useLogcatStream.ts:111-113` — 高频 byte 推送无 batch

**测量**：UART 高速口（1.5 Mbaud）峰值可达 150 KB/s，server frame 可能 50-100 frames/s。每 frame → `subsRef.forEach(cb => cb(chunk))` → `term.write(new Uint8Array(chunk))`。xterm.js write 内部已 batch，但 React state / 外部观察者每 frame 走一次。当前只有 1 sub 所以无热点；**N=2 sub 后**（如未来 UI overlay 监听 byte）会成本暴露。
**原因**：observer pattern 设计 OK，但缺 server 端 batching 上限。
**建议**：低优先（不在热路径）。Server 端 `uart_route.py` 把 1ms 内的 byte coalesce 成单 frame（目前可能已经是 OS pipe boundary，先 instrument 再决定）。客户端 sub callback 不动。
**预估收益**：N=1 sub 时可忽略；N≥2 + 高速 UART 时 -CPU 30%。
**预估成本**：暂不修；标 follow-up "PR-C.b' batching"。

### 5. **[mid]** `InspectPage.tsx:97-104` — 8 tabs 切换 = 完全 unmount/remount + WS 重建

**测量**：用户在 UART live → Shell → 回 UART 流程，每次 re-mount 都跑 `term.dispose()` + `new Terminal()` + WS 重连 + history replay（metrics tab 60 s history snapshot ≈ 60 frames）。切 tab 一次 ≈ 50-200 ms blocking + WS handshake ~50 ms。**WS cleanup 已 OK**（4 个 stream hook 全部 `useEffect(() => () => cleanup(), [])` 验证过，无泄漏）。
**原因**：tab 状态本地 useState + 三元 mount/unmount，没 keepAlive。
**建议**（trade-off 重）：
- **A**. 接受现状：用户切 tab 不频繁，重建成本可控；优点是 WS resource 释放彻底
- **B**. ChartsTab + UART/Logcat/Shell live tabs 加 `display:none` 保持 mount（hidden 时仍占 WS）；切回零延迟但隐藏的 stream 仍在收数据耗带宽
- **C**. 折中：每个 stream tab 卸载时把 buffer/sample 数组写到 Zustand，re-mount 时回填快照。复杂度高
**建议选 A**，标 lessons "tab 切换 ≈ 100-200 ms 是设计取舍，不优化"。
**预估收益**：A=0；B=切 tab 0 ms 但 hidden tab 仍占 WS 带宽；C=切 tab 50ms 但代码 +200 行
**预估成本**：A=0；B=2 commits；C=1 周

### 6. **[low]** `useFileBrowser.ts:37/50` — 缺 `refetchIntervalInBackground` 但已 `refetchOnWindowFocus:false` + 无 interval

**测量**：只在挂载时跑，`staleTime:10s`，`refetchOnWindowFocus:false`。无周期 polling → 实际无背景压力。
**原因**：只是规范不齐，不影响行为。
**建议**：不修（pattern 已经是"按需 fetch"，加 gate 无收益）。
**预估收益**：0。

## Top 3 wins（impact / effort）

| # | 收益 | 成本 | ratio |
|---|---|---|---|
| **1** Lazy-load 8 tabs + xterm 单 chunk | -80 KB gzip 主 bundle (40%) | 1 commit / 5 行 | **极高** |
| **2** 6 hook 加 `refetchIntervalInBackground:false` | 隐藏窗口零无效请求 + DEBT-008 events.jsonl 扫减半 | 1 commit / 12 行 | **极高** |
| **3** UiDumpTab `useMemo` + `useDeferredValue` | -2~10 ms keystroke 主线程占用 | 1 commit / 6 行 | 高 |

## 不在范围

- 未跑实际 lighthouse / web-vitals（需主对话起 `npm run dev` + Playwright performance API）
- 未 benchmark UART 高速口真实 byte 频率（PR-C.b 验证只 3 KB 真实 audit log）
- DEBT-008 events.jsonl 全量扫已知 / 不重复提
- WS subscriber Queue 释放路径已读过 4 个 stream hook，cleanup OK 不重复提

## 建议加入 knowledge

- **debts.md** 新条目（候选）：
  - DEBT-023 · xterm.js 全量入主 bundle（severity mid，PR-C.b/E 引入）
  - DEBT-024 · 6 dashboard hook 漏 background gate（severity mid，D 档遗留 + PR-A 没沿用 useBackends pattern）
- **decisions.md** 候选 ADR：ADR-032 "Inspect 8-tab 切换走 unmount/remount，不做 keepAlive"（记录 trade-off 为什么不优化）
- **lessons.md**：L-022 候选"新 hook 必须 sweep refetchIntervalInBackground / refetchOnWindowFocus 两 flag，sentinel 反模式同源"
