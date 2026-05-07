# UI fluency audit 2026-05-07 · `3165699..bd054c4`

## 摘要

6 个 web 改动整体已遵守 L-028（Suspense fallback minHeight=480 显式守住）和
mockup baseline 风格；但**新加的 sidebar / preview / counter / per-row delete
按钮普遍缺 a11y 基线**：destructive delete 一击即删（无 confirm + 仅 hover
可见 + 键盘不可达）、selected 状态全无 `aria-current`、filter counter +
applying hint 无 `aria-live`、preview pane loading/error 三态高度不一致引
CLS。键盘导航在 inspect 子页面**完全缺**（无任何 onKeyDown / Esc / Enter
绑定）。

---

## HIGH (3)

### HIGH-1 · UART per-capture delete 一击即删，无 confirm，键盘几乎不可达
**位置**：`web/src/features/inspect/UartCaptureView.tsx:189-203` +
`web/src/styles/components.css:2031-2055`

**问题**：
- onClick 直接 `remove.mutate(c.name)`，无 `window.confirm` / 无 modal /
  无 undo（删除是不可逆 — 服务器端 capture 文件被 rm，文本永久丢失）
- CSS `.uart-tab__cap-delete { opacity: 0 }`，仅 `:hover` / `:focus-within`
  显形。键盘用户 Tab 焦点先落到 `<button>` 名称区，再 Tab 才到 trash
  按钮，但因为 trash 在 DOM 里**和选项 button 同级（兄弟）**，Tab 到
  trash 时按钮已 visible（`:focus` 命中），所以**键盘其实能到**——但因为
  视觉 0→1 突兀切换 + 按 Enter 立刻删，键盘用户 0 错容率
- L-029 / L-027 同源："危险动作必须有防误触安全余量"。这条 destructive
  action **既无视觉防呆（confirm）也无键盘防呆（focus 必触发显示，Enter
  立刻执行）**

**影响**：用户 Tab 浏览 capture 列表时按 Enter 误删；hover 状态下手抖点
错也立刻删

**建议修复**：
- 最低：第一次点 trash 把按钮换成"再次点击确认（3s）"两步式（无 modal
  + 自带 timeout 还原）
- 推荐：复用 `<HitlConfirmModal>`（N=2 → N=3，正好触发 L-020 base 抽提
  时机）。delete UART capture 是 destructive，文案"删除后无法恢复"
- 视觉防呆：trash 改用低饱和图标（不要红色 hover）+ 默认 opacity 30%（不
  全隐藏），让用户知道按钮存在并谨慎点击

---

### HIGH-2 · 新 sidebar / list 无 `aria-current` / `aria-selected`，screen reader 无法感知激活项
**位置**：
- `web/src/features/inspect/ScreenshotTab.tsx:188-199`
- `web/src/features/inspect/UartCaptureView.tsx:178-204`
- `web/src/features/inspect/FilesTab.tsx:512-535`

**问题**：所有 3 个新 sidebar/list 都用 `className={selected === x ? "is-active" : undefined}`
来标当前选中行，但**完全没有 `aria-current="true"` / `aria-selected="true"`
/ `aria-pressed="true"`**。CSS `is-active` 有 `border-left: 3px solid
orange`，纯视觉。

`grep -rn 'aria-current\|aria-selected\|aria-pressed' web/src/features/`
全仓 0 命中 — 这是新 sidebar pattern 的系统性问题，不只是这一处。

**影响**：盲用户 / 屏幕阅读器无法确认"我现在选的是哪一行"；切换选项后
焦点没有可识别的 announce

**建议修复**：每个 `<button>` 加 `aria-current={selected === x.name ? "true" : undefined}`
（文件列表）或 `aria-pressed={selected === x.name}`（toggle 语义）。统
一在新 sidebar list 里加上，将来如果抽 `<EntryList>` 组件这是基线 prop。

---

### HIGH-3 · FilesTab 内联 preview pane 三态高度不一致 → CLS 跳变
**位置**：`web/src/features/inspect/FilesTab.tsx:291-338` +
`web/src/styles/components.css:2563-2585`

**问题**：preview pane 三种 DOM 形态：
1. `data` 分支：head + 360px max-height pre/binary banner ≈ 80-400px 高度
2. `isLoading` 分支：纯 `.files-tab__preview--state` ≈ 30-50px（只有
   text + padding）
3. `isError` 分支：同 loading，30-50px

`.files-tab__preview` **无 `min-height`**。用户选一个文件，preview
loading 30px → preview data 400px **CLS 跳变 ~370px**，下面的
`.files-tab__status` 行被推下去，再选第二个文件时 loading 又把它撑回小，
来回闪。

L-028 教训直接同形态："loading 字看着没问题"掩盖布局跳变 — preview
pane 没有像 InspectPage Suspense 那样守住 minHeight。

**影响**：选文件预览时 status row + 下方布局闪跳；快速点列表里多个文件
时整页持续跳动

**建议修复**：`.files-tab__preview` 加 `min-height: 240px`（match data
分支的近似稳定高度，避开 binary 短文 vs 大文件 max-height-cap 的差异）。
或者**3 分支共用一个外壳**，head 总在，body 内部切换状态文案 — 彻底消
除 mount/unmount 跳变。

---

## MID (4)

### MID-1 · LogcatTab "applying…" hint + UiDumpTab "M of N" counter 无 `aria-live`
**位置**：
- `web/src/features/inspect/LogcatTab.tsx:146-150`（applying… 在 filter
  edit 时出现）
- `web/src/features/inspect/UiDumpTab.tsx:98-104`（"M of N matches"）

**问题**：两个动态状态变化都纯视觉（普通 `<span class="uart-tab__last">`）。
filter 改变 → counter 数字变 → 屏幕阅读器**完全沉默**。"applying…"
debounced 600ms 后消失，盲用户**根本不知道刚发生了什么**。

`grep -rn 'aria-live' web/src/features/inspect/` 只在 FilesTab 进度条命中
1 次。

**建议修复**：counter `<span>` 加 `aria-live="polite"` + `role="status"`。
applying… span 同样加 aria-live="polite"（debounced 反馈正合适
polite — 不会插队）。

---

### MID-2 · FilePane 图标按钮用 `title` 不用 `aria-label`
**位置**：`web/src/features/inspect/FilesTab.tsx:473-493`

**问题**：FilePane 内 `<FolderUp>` `<RefreshCw>` 两个 icon-only 按钮：

```tsx
<button title="parent dir"><FolderUp .../></button>
<button title="refresh"><RefreshCw .../></button>
```

只有 `title`。`title` 不是 a11y accessible name 的可靠来源（多数 screen
reader 默认不读，VoiceOver 要长 hover 才读）。WCAG 4.1.2 要求按钮有可
访问名。其它新加的图标按钮（ScreenshotTab refresh / UartCapture refresh
/ uidump filter-clear / preview close）**都用 aria-label** ✓ — 这两个
是漏网。

**建议修复**：`aria-label={lang==="zh" ? "上一级" : "Parent directory"}` +
`aria-label={lang==="zh" ? "刷新" : "Refresh"}`，保留 title 兼容。

---

### MID-3 · `useScreenshots` / `useDeviceFiles` 缺显式 `refetchOnWindowFocus` 决定
**位置**：
- `web/src/features/inspect/useScreenshots.ts:24-34`
- `web/src/features/inspect/useUartCaptures.ts:32-39`

**问题**：L-025 上下文是 `refetchInterval`，但精神延伸 — 这两个 list
hook **既没设 `refetchOnWindowFocus` 也没设 `staleTime`**。TanStack 默认：
- `refetchOnWindowFocus: true` — 切回 tab 自动 refetch
- `staleTime: 0` — 任何时间都视为 stale

useFileBrowser 三个 hook 显式都设了（`staleTime: 10_000` +
`refetchOnWindowFocus: false`）。screenshots / uart 两个 list hook 漏了，
导致 alt+tab 回浏览器**每次都触发 list refetch**（不像 useFileBrowser
有 staleTime gating）。

**建议修复**：补 `staleTime: 10_000` + `refetchOnWindowFocus: false`（与
useFileBrowser pattern 一致）。或者趁机抽
`useDashboardQuery(key, fn, opts)` wrapper 把这 3 flag 集中默认（L-025
后半段建议）。

---

### MID-4 · UiDump 大树 filter 时 row mount/unmount 卡顿 + 滚动位置丢失
**位置**：`web/src/features/inspect/UiDumpTab.tsx:60-64, 128-149`

**问题**：`useDeferredValue(filter)` 已经把 typing 和 filter 解耦（好），
但 `visibleNodes` 变化时**整个 list 重新 render**（无 virtualization），
2000 节点的 dump 在 filter 改字时浏览器 layout cost 高（每行 paddingLeft
inline 计算 + flex-wrap）。

更严重的是用户改 filter 后**列表滚动位置丢失**（重新从顶 0 开始）；
clear filter 时也丢。如果用户在大树里"找到一个，clear filter，看上下
文"，滚动条被重置 = 找不到刚才那个节点。

**影响**：> 1000 节点的 UI dump 上 filter 体验明显卡 + 上下文丢

**建议修复**（择一）：
- 短期：filter 应用后保留 selected node id（或 row index），filter 清后
  scrollIntoView 回去
- 中期：list 改 virtualization（react-window 或 @tanstack/react-virtual）
  — alb 已用 TanStack 生态，引入零成本

---

## LOW (3)

### LOW-1 · FilesTab 空文件夹 empty 文案硬编码英文 + 不可操作
**位置**：`web/src/features/inspect/FilesTab.tsx:510`

```tsx
<div className="files-tab__empty">empty</div>
```

只 "empty" 一个字，无 i18n 切换（其它 ScreenshotTab/UartCapture 都做了
zh/en 双语 + 操作引导"点上方按钮"）。设备空文件夹时用户看到"empty"，
不知道"我能做什么"。

**建议**：`{lang==="zh" ? "目录为空 · 上一级或换路径" : "Empty directory — go up or change path"}`。

---

### LOW-2 · LogcatTab "applying…" hint 没有显式区分"已应用"和"应用失败"
**位置**：`web/src/features/inspect/LogcatTab.tsx:146-150`

debounced 600ms 后 connect() 触发，state 进 connecting → ready。新
filter 应用成功**没有任何视觉反馈**（applying… 文本只是消失）。用户改
filter 等不到 600ms 看不到有动作；600ms 后文本消失但屏幕**没有刷新**
（旧 logcat 行还在），心智上"是不是没生效？"

**建议**：applying 完成后短暂（1-2s）显示"已应用 filter: <new>" pill，
然后淡出。或者 connect 时给 xterm clear 一行 marker：`--- filter changed
to <new> ---`。

---

### LOW-3 · ScreenshotTab 切换 selected 时 viewer `<img>` URL 立刻变 → 闪白 → 新图加载
**位置**：`web/src/features/inspect/ScreenshotTab.tsx:204-214`

`<img src={imgSrc}>` 直接绑 URL。点列表里另一张图，浏览器先把当前 img
卸掉显示 alt 区域 / 白底，再 fetch 新 URL，显示新图。500KB 截图 + 慢
adb 拉取场景**白屏 100-300ms 闪烁**。

**建议**：用 `loading="lazy"` 没用（这是 above-fold），但可以包一层
`<div style={{position:"relative"}}>` 加 spinner overlay + 新 `<img>`
`onLoad` 后才 opacity:1，老图 onLoad 前保持。或最简单：`<img>` 加
CSS `transition: opacity 150ms`，URL 改时给 inline `key={imgSrc}` +
`onLoad={() => setLoaded(true)}` 控制 opacity。

---

## grep checklist 自动跑结果

| Lesson | Pattern | 命中 | 结论 |
|---|---|---|---|
| **L-028** | `Suspense fallback={` 后 `minHeight` | 1/1 命中且有 minHeight=480 ✓ | clean |
| **L-029** | `role="dialog"` | 1 命中（`HitlConfirmModal.tsx:84`，N=2 已审过） | clean — 本批未新增 modal |
| **L-025** | `useQuery.*refetchInterval` 后 `refetchIntervalInBackground` | 0 命中 refetchInterval（本批新 hook 无 polling） | N/A — 但配套 `refetchOnWindowFocus`/`staleTime` 显式设 → 见 MID-3 |
| 危险 action 防呆 | destructive `mutate` 无 `confirm(` / 无 modal | 1 命中 `UartCaptureView.tsx:192 remove.mutate` | **HIGH-1** |
| `aria-current` / `aria-selected` | sidebar selected 标识 | 0 命中 | **HIGH-2** |
| `aria-live` 动态状态 | filter counter / hint | 0 命中（FilesTab 进度条 1 命中不算 inspect 改动） | **MID-1** |
| 图标按钮 `aria-label` | 仅 `title` 不算 | 2 命中（FilePane FolderUp/RefreshCw） | **MID-2** |
| 键盘 onKeyDown / Esc | 4 个 inspect tab + Files | 0 命中 | 全 0 命中是 inspect 子模块系统性问题，本批不引入新债但也未还 |
| Suspense fallback minHeight | InspectPage | 1/1 守住 ✓ | clean |

---

## 实际跑过的验证

- `web/scripts/web_check.mjs /app/`（dashboard） + `/app/inspect/` 跑通
  - Playwright 截图 + aria_snapshot 落 `.claude/reports/screenshots/uifluency-2026-05-07/` 和
    `.../uifluency-2026-05-07-inspect/`
  - dashboard 无 console error / 0 page error；inspect tab 无 console error
- alb-api **未连真机**，所以 Inspect 子页面（Screenshot / UART / Files /
  UI Dump）**未能实际渲染数据态** — 三态切换的视觉跳变（HIGH-3 preview
  pane CLS、LOW-3 img 闪白）以静态代码 + CSS 推断为主
- 无法跑 axe-core 完整 a11y 扫（aria_snapshot 已能确认 sidebar 缺
  current 标识 / counter 缺 live region）

---

## 结论

- **必须立即修：3 HIGH**（destructive delete 防呆 + sidebar aria-current
  + preview CLS）
- **入 backlog：4 MID + 3 LOW**（aria-live counter / aria-label icon /
  query flag 显式 / virtualization / empty 文案 / applying 反馈 / img 切换闪白）

建议把 HIGH-1（destructive delete confirm 模式）+ HIGH-2（sidebar
aria-current pattern）抽成 follow-up commit；这两个修完后 inspect 子模块
a11y 基线和 dashboard 持平。

## 建议加入 knowledge

- `lessons.md` 新立 L-032：**新 sidebar / list 抽出时 a11y 三件套基线**
  （aria-current 标识 / aria-live 状态变化 / destructive button 防呆 +
  键盘可达），类比 L-029 modal 三件套但针对 list pattern
- `debts.md` 登记：preview pane CLS（HIGH-3）/ UiDump 大树 virtualization
  （MID-4）入技术债

## 不在范围

- 视觉风格 / token 偏离（→ visual-audit-runner）
- mockup baseline 偏移（→ mockup-baseline-checker）
- 性能数字（→ performance-auditor）
