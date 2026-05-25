# performance-auditor 报告 · 20260525T113326 · Y~AG commit batch

## 摘要

- 评审范围：bf9d1dc^..22dbb4b（9 commits, Y/Z/AA/AB/AC/AD/AE/AF/AG）
- 主要瓶颈：**0 个本批引入的退化**。本批 perf 改动方向正确（Y stale-closure 修对、AD UiDumpTab O(N×D)→O(N+M×D) 算法对、AB live-pulse 用 transform+opacity GPU-friendly）。已发现的 perf 嫌疑全是**本批之前就在的债**（DEBT-047 path-keyed WS dedup 待修；DashboardPage `devices.devices.filter/map` 每 render 重建）。
- 优化建议数：4（2 MID · 1 LOW · 1 信号）
- bundle 影响：0（vitest/jsdom/testing-library 在 devDependencies 没漏进 src import；本批 `index-*.js` 117.4 KB gzip = 上一版同量级，未恶化）

## bundle 现状

- `docs/app/assets/index-HKq1gfTt.js` = **117.4 KB gzip**（约 23 % 预算 < 500 KB）
- `docs/app/assets/index-DFkXjD8I.js`（router/main 入口) = 5.1 KB gzip
- `docs/app/assets/index-BAxl-kbw.css` = 10.8 KB gzip（+ 12 行 CSS：1 keyframe 兜底 + 1 sessions list + 1 playground responsive + 1 reduced-motion · 体积忽略）
- `docs/app/assets/xterm-3VOAfa_q.js` = 82.4 KB gzip（手动 manualChunks 切出 · 非首屏 cost）
- 主入口三件套合并 gzip = 133.2 KB
- vite.config.ts / 入口 import 链 0 命中 vitest|jest|testing-library|jsdom|playwright（grep 已跑）

## 9 commit 改动维度速览

| commit | 性能维度看点 | 结论 |
|---|---|---|
| Y `bf9d1dc` usePlaygroundChat 2 stale-closure | gotDoneRef 替 stale status；`setDelta((d)=>d+δ)` functional updater | **OK**。修对了，未引入额外 setState |
| Z `8225609` Playground a11y + prefers-reduced-motion | CSS animation 加 `@media` 兜底 | **OK** |
| AA `6e2c40f` useDevices/useAuditStream lib/hooks/ | 仅路径迁移；queryKey + connect path 不变 | **OK**，react-query cache key 没坏 |
| AB `d2549ef` AuditPage live pulse + DevicePicker focus | CSS keyframes `transform: scale + opacity`（合成层） + DevicePicker `wasOpenRef` 防初次 mount 抢焦点 | **OK**。无 setInterval / RAF 常驻，纯 CSS |
| AC `7987009` ScreenshotTab functional setter + diag try/except | 减少 stale closure，不增加 render | **OK** |
| AD `2afd470` UiDumpTab filter ancestor 算法 | flat all[] 一遍 O(N) + 匹配项 climb parentMap O(M×D) | **OK**，算法正确（详见 finding #1） |
| AE `b4298e6` ScreenshotZoom wheel non-passive | imperative addEventListener + `{passive:false}` + 完整 cleanup | **OK**（详见 finding #2） |
| AF `9bd84cc` vitest 起步 + spec 8 测 | devDependency 隔离正确 | **OK** |
| AG `22dbb4b` 21 测 + DevicePicker effect bug 修 + spec | effect deps `[open, device]` 排除 devices 数组（避免 react-query 新数组刷 focusIdx） | **OK**（详见 finding #4） |

## 发现

### finding #1：UiDumpTab 算法改写正确 · 微小残余优化可做（LOW）

**位置**：`web/src/features/inspect/UiDumpTab.tsx:121-135`（`effectiveExpanded` useMemo）

**实测算法**：
- 旧（git blame O 这段是 5/22 之前）：`buildAncestors(root)` 全树 DFS 每节点 alloc 一份 ancestors 数组，filter 阶段 O(N) 遍历全部节点 × O(D) 测试 ancestor 是否含 match
- 新（commit AD）：`buildTreeIndex` 单遍 walk 产出 `ids: Map`, `parents: Map`, `all: UiNode[]` ——所有 perf 关键结构一次建好；filter 阶段 `for (node of allFlat) if matches → climb parentMap`
- 复杂度：O(N) 匹配扫描 + O(M×D) ancestor 链回填 → 总 O(N + M×D)，M ≪ N 时显著少

**正确性**：parent map 在 `buildTreeIndex` 里 set 完整（`parents.set(node, parent)`，root 是 null），climb 循环 `while (cur) { ... cur = parentMap.get(cur) ?? null; }` 正确终止于 root。

**残余优化**（LOW，不建议本批做）：
- `effectiveExpanded` 在 filter 为空时直接返回 `expanded`（line 122 `if (!deferredFilter) return expanded`）；但**有 filter** 时每次 `expanded` 变（用户点 chevron）也会重算整棵匹配——这是必要的（widened set 依赖最新 expanded），不是 bug
- `nodeMatch(node, q)` 每次都 `.toLowerCase()` × 4 字段；千节点树 × 4 = 4000 次 toLowerCase。可在 buildTreeIndex 时预算一份小写 cache（每节点 1 个 string），匹配只 `includes`。**预估收益**：1 万节点 dump 上每次 keystroke 省 ~10ms。**建议**：不做（千节点是少数 dump，且 deferredValue 已分摊感受），登记为 LOW backlog

**建议**：暂不动；如未来真遇到大 dump 卡顿再做。

### finding #2：ScreenshotZoom wheel listener mount-only（OK · 不是 leak）

**位置**：`web/src/features/inspect/ScreenshotZoom.tsx:58-75`

**疑点（已排除）**：
- useEffect deps `[]` 空 → 仅 mount 一次 addEventListener
- handler 闭包内 `setScale((s) => ...)` 用 functional updater，**不读 state**，所以 deps 空不会有 stale-state bug
- cleanup `el.removeEventListener("wheel", handler)`：`el` 是 effect 闭包变量（首次 mount 时的 DOM），即使 `wrapRef.current` unmount 时变 null，cleanup 也不需要它（用闭包变量）。每次 component mount/unmount 配对完整，**不 leak**

**Strict Mode mount→unmount→mount 行为**：第一个 effect 加 listener、立刻清理、第二个 effect 再加——也只剩一个，没 leak。

**结论**：OK。

### finding #3：MID · 本批未恶化但 DEBT-047 未关 · WS connect path 多开（背景债）

**位置**：`web/src/lib/ws.ts:41` `connect(path, opts)` 无 dedup

**现状**（本批改动无关，但 review-feedback 必须提）：
- DashboardPage 同时挂 2× `useAuditStream`（`includeMetrics:false` + `includeMetrics:true`，line 55-56）→ 2 个 `/audit/stream` socket，server snapshot 各发一份
- AuditPage 挂 1× `useAuditStream({includeMetrics, minutes})` → 又 1 socket
- 用户从 Dashboard 跳到 AuditPage（router 不卸载 Dashboard，因 `<Outlet/>` 切换），如果同屏可见会有 **3 socket** 同时 connected to `/audit/stream`
- 每条 socket 都重发 snapshot（~40 KB），server-side bus fan-out N×；前端每 socket 各自 reducer setState

**实测**：未跑（需要 dev server）；代码层确认 connect() 没 Map 池。

**本批是否恶化**：**否**。AA 只是把 hook 从 `features/dashboard/` 提到 `lib/hooks/`，**没改 connect() 调用次数**。AuditPage 已存在的双 socket 行为不变。

**建议**：DEBT-047 是已登记债。本批 9 commit 不强求修，但下次 perf audit 仍标记为存量风险。

**预估收益**：3 socket→1 socket，snapshot 带宽 −66%（≈ 80 KB / page transition）；reducer setState 频率 3×→1×；M3 加 auth 时握手成本 3×→1×。

**预估成本**：3-4 commit（`lib/ws.ts` 加 Map 池 + shareKey + refcount + snapshot replay；3 处 hook 调用点改 shareKey；测试）。

### finding #4：MID · DevicePicker effect deps 修法正确，但 useDevices 数组身份不稳定（背景债）

**位置**：
- `web/src/components/DevicePicker.tsx:76-81`（修对）
- `web/src/lib/hooks/useDevices.ts:99-107`（根因，非本批引入）

**AG commit deps `[open, device]` 修法**：
- 老 deps `[open, device, devices]` 会因 `devices` 数组每次 hook return 新身份（line 100 `.map(...)`）而每次 render 都重 fire effect → `setFocusIdx(0)` 把用户键盘 nav 状态清零
- 修法：deps 排除 `devices`，加 `eslint-disable-next-line` + 注释说明"react-query 新数组身份会刷 focusIdx"
- **正确性**：device 列表内容变化时 user 看不到旧 focus 也合理（设备进出会清 focus）；open / device 变化才 re-sync——精确表达"何时该同步"

**遗漏的场景**（用户场景）：menu open 时设备列表后台 refetch（5 s tick）→ 新 device 加进来但 focusIdx 不更新 → 用户按 ArrowDown 可能跳过新 device。**实际影响极小**（5 s 内一次 refetch + 用户正在键盘 nav 的窗口很窄）。

**根因建议**（不要本批做）：
- `useDevices()` line 100 `devices: data?.devices.map((d) => mapToDeviceCard(data.transport, d)) ?? []` → 每次 hook call return 新数组
- 用 `useMemo` 包：`const devices = useMemo(() => data?.devices.map(...) ?? [], [data])` ——react-query `structuralSharing` 默认保 data 引用稳定，memo 命中
- 类似的 `return { devices, transportName, ... }` 整对象每次新身份——但消费方都是直接解构读字段，影响小

**预估收益**：DevicePicker / DashboardPage 减少 1-2 次不必要 re-render per 5s refetch tick；DevicePicker effect deps 可以加回 `devices` 不踩坑

**预估成本**：1 commit，影响面 `useDevices.ts` + 验证 4 处消费方

### finding #5：信号（不算 finding） · usePlaygroundChat streaming 期间整页重 render

**位置**：`web/src/features/playground/PlaygroundPage.tsx`（PlaygroundPage 整体）+ `usePlaygroundChat.ts:87` `setDelta((d) => d + msg.delta)`

**现象**：streaming 时每个 token 一次 `setDelta` → PlaygroundPage 重 render（含左侧 sampling 面板、message log、input bar）。30 tok/s = 30 render/s = 33ms/render budget。

**实测**：未跑 benchmark（需 dev server + 真 LLM 流）。**代码层看 X 嫌疑，但没跑 benchmark 验**。

**为什么不立刻做**：
- 单个 PlaygroundPage 子树很小（左 rail 5-10 个 form input + 右 chat log + textarea）
- React 18 reconciler 对没变的子树 bail-out 很激进
- 真问题应该等用户报"30 tok/s 时 typing 卡" 或 Playwright FPS 测出再说

**如果优化**：把 streaming `<pre>{chat.delta}<span>▍</span></pre>` 区单独抽 `<StreamingMessage delta={chat.delta} />` 组件，外层用 React.memo + 把 sampling/log 包在 memoised wrapper——但 `chat.delta` 仍然每 token 变。真正的解：用 `useImperativeRef` + DOM 直写文本（textContent），跳过 React reconciliation。这违反 React 范式但是 streaming 的标准优化。**建议留 backlog**。

### finding #6：背景观察 · AuditPage allEvents.reverse() 每次 vm.rawEvents 变都重建

**位置**：`web/src/features/audit/AuditPage.tsx:92`

**现状**：`useAuditStream` 用 `setBusinessRaw((prev) => [raw, ...prev].slice(...))` 已经在头部 prepend（newest first）。AuditPage 又 `[...vm.rawEvents].reverse()` 强行复制 + 反转。

**冗余**：`useAuditStream.rawEvents` 内部 sort 已经按 ts desc（`a.ts < b.ts ? 1 : -1`，merged 路径 line 232；单 buffer 路径 prepend 保证 desc）。AuditPage 的 reverse 把 desc 变成 asc → table 显示是最早的在最上？这看着是 bug 不是 perf 问题。**移交 code-reviewer**（不在 perf 范围）。

如果纯 perf 视角：删掉 reverse 省一次 array spread + reverse（200 元素 × 每次 event 到达 = 几十次 reverse/s 在 audit 高频时），但相比 mapAuditToTimeline 的 map 开销可忽略。

## 不在范围

- 没跑实际 benchmark（Lighthouse / Playwright FPS / Chrome perf profile）—— 需要主对话起 `npm run dev` + 真 LLM stream / 真 ADB 设备
- 后端 `/audit/stream` snapshot 序列化耗时未测（要看 `audit_route._project()` 实际 latency 数字）
- `usePlaygroundChat` 实测 token 频率下的 render 次数（finding #5 仅代码层）—— 需要主对话起 dev + 真 Ollama

## 建议加入 knowledge

**debts.md** 不新增（DEBT-047 已盖 finding #3；finding #4 根因是 useDevices 数组身份债，建议合并入 DEBT-047 同一 backlog 或新开 DEBT-052 · useDevices/useAuditStream return value 身份稳定化）。

**decisions.md** 不新增（无新 ADR 决策点）。

**lessons.md 可选新增 1 条**：
> L-046 候选：useEffect deps 排除 react-query 返回的"数组/对象"字段时，必须验证消费方"刷新动作"是否走另一条路径（DevicePicker 用 `[open, device]` 排除 devices 数组，但牺牲了"menu 打开期间设备列表更新时 focusIdx 同步" — 评估后判定可接受，但建议加单测覆盖）。
（这条更适合 review-feedback.md 而不是 lessons.md，由主对话裁定）

## 结论

本批 9 commit perf 维度**没有引入退化**。Y 的 stale-closure 修法、AD 的算法改写、AB 的 CSS 动画选择、AG 的 effect deps 排除，4 处技术选择都对。剩余优化机会全部是**预存的债**（DEBT-047 WS dedup · useDevices 数组身份），本批没让它们更糟。建议放过本批，把 DEBT-047 + useDevices memo 化作为下一个 perf sweep 的候选。
