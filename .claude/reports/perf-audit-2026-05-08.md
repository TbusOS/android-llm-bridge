# perf audit 2026-05-08 · 5/06~5/08 累积 15 commits

## 摘要（3 句话）

15 commits（acc747e..9a70342）累积 +1039 行 web / +5 backend endpoint，主 bundle 110.32 KB gzip 比 5/02 baseline 205 KB **降 95 KB**，xterm 已分离 (84.74 KB shared chunk)，DEBT-022 lazy-load + DEBT-024/025 background-gate 全守住。
新增 5 endpoint 中 `workspace_preview` / `read_capture` / `list_screenshots` 用同步 IO 跑在 async 路径——单调用 microseconds 级，但高 QPS 或大文件下可能阻塞 event loop（**1 MID + 2 LOW**）。
WS push/pull stream（MID-6 commit 90）outer-finally + 双 task cancel race 处理扎实，符合 L-026；UiDump react-virtual 落地（estimateSize=24 + measureElement）合理；**0 HIGH，bundle 预算守住**。

## bundle 现状

| chunk | 当前 raw | 当前 gzip | 5/02 baseline gzip | 趋势 |
|---|---|---|---|---|
| **index-BBz3XyuU.js** (主) | 349,154 | **110,320** | 205,000 | **-95 KB** ✓（DEBT-022 lazy-load 落地）|
| index-LYWBlxcY.css | 41,821 | 6,710 | (含在 index 里估) | 稳定 |
| **xterm-3VOAfa_q.js** | 334,208 | **84,381** | (主 bundle 内) | 单独 chunk · 仅 UART/Logcat/Shell 用时加载 |
| UiDumpTab-LGcSyHvT.js | 20,653 | **7,029** | 1,600 | +5.4 KB（react-virtual MID-4）|
| UartTab-CmlL37KD.js | 11,569 | 4,253 | 4,000 | 稳定（+delete 按钮）|
| FilesTab-DB2brfza.js | 13,803 | **4,901** | 3,200 | +1.7 KB（preview + transfer-stream UI）|
| ScreenshotTab-BBCi5bfy.js | 4,444 | 1,967 | 1,200 | +0.8 KB（history sidebar）|
| LogcatTab-6TcNnOSD.js | 4,868 | 2,025 | 1,800 | +0.2 KB（debounced auto-reconnect）|
| ShellTab / SystemInfoTab / ChartsTab / xterm-css / 其他小 chunk | — | <3 KB ea | — | 稳定 |
| **总 gzip（全部 assets）** | — | **233,893** | — | 主 bundle ≤120 KB 预算守住 ✓ |

**关键观察**：
- 5/02 audit 留下的 "主 bundle ≤120 KB" 预算被守住（110.32 KB · 92% 占用）
- xterm.js 单独 chunk 设计如预期：dashboard / chat / playground 路径不付 84 KB 代价
- 新加的 SectionPlaceholder 组件 + DEBT-031 BEM 重写没引入 CSS 膨胀（components.css 2844 行，3 处 legacy class 引用全是注释，无 ruleset 残留）
- UiDumpTab +5.4 KB 是 react-virtual 必要代价（DEBT-022 PR-G 之后 estimateSize+overscan 12 + measureElement，符合预期）

## HIGH (0) - 阻塞

无。

## MID (1) - 入 backlog

### 1. **[mid]** `web/api/files_route.py:583-585` — `workspace_preview` 同步 read 在 async 路径

**测量**：用户每次点 workspace 文件 → `target.open("rb").read(64*1024)` 走在 async endpoint 里。单次 64 KB read 从 page cache 出来 ≈ 50-200 µs（NVMe）/ 几 ms（cold disk），**event loop 阻塞**。
hot-path 触发场景：用户 UI 浏览 workspace 大目录连点 N 个文件 → preview query 滚动重新触发；隐式键 `["workspace-preview", path]` 每次 path 变就 re-fetch。
**原因**：FastAPI async 路径里直接 sync FS 调用（违反 ADR-026 / L-014 的 IO 协程化原则）。
**建议**：包 `asyncio.to_thread`：
```python
data = await asyncio.to_thread(_read_preview_bytes, target, max_bytes)
```
同样 pattern 也应用到 `target.stat()`（line 576）和 NUL-byte sniff（line 589 `_looks_binary` 走在 async 上下文）。
**预估收益**：高 QPS 下 P99 latency 改善（多用户连点不互相阻塞）；正常使用基本无感知。
**预估成本**：1 commit / +6 行 / 0 风险。
**为什么 mid 不 high**：单文件 64 KB cap + workspace 通常在 NVMe，常态阻塞窗口 < 1 ms；只有 cold cache + 多并发才显著。

## LOW (2) - 可忽略 / 已知

### 2. **[low]** `web/api/uart_route.py:171-174` — `read_capture` 用 `f.read_text()` 同步读全文件

**测量**：UART capture 文件 30 s × 1.5 Mbaud ≈ 5 MB；5 min × 1.5 Mbaud ≈ 50 MB（HTTP cap 5 min）。`f.read_text()` 同步加载到内存 + `errors='replace'` UTF-8 decode 全文件 → 50 MB 时 200-500 ms event loop 阻塞 + memory spike。
**原因**：5/06 之前已有，本 audit 范围内未改动。属"5/02 audit 漏检"——同源 IO 协程化债。
**建议**：包 `asyncio.to_thread` + 考虑 streaming 大文件（>10 MB 用 `StreamingResponse`）。
**预估收益**：极少触发（5 min 满速 UART 是边界场景），但触发时 server 单线程 stall 可见。
**预估成本**：1 commit / +5 行；标 follow-up backlog（不阻 ship）。
**为什么 low**：实际使用 UART capture 通常 30-60 s（1-3 MB），decode 30-50 ms 可接受。

### 3. **[low]** `web/api/screenshots_route.py:118-133` — `list_screenshots` 全同步 stat + glob + PNG dims peek

**测量**：每个 PNG 1 次 `stat()` + 1 次 24 B 的 `f.read(24)`（dim peek）。N=20 history 时 ≈ 40 syscall × 几 µs = ~200 µs total。常规情况无感。
**原因**：5/05 commit `05bbdae` 新增 endpoint 时漏 `to_thread` 包装。
**建议**：N=20 已是体面历史上限（用户清理）；优先级低。如未来 history 不限上限会变 hot path。
**预估收益**：N<50 无意义；标 follow-up。
**预估成本**：暂不修。

## 没问题区域（明确）

- **xterm-3VOAfa_q.js shared chunk**：84.38 KB gzip，DEBT-022 lazy-load 设计如预期工作；dashboard / chat / playground 路径不加载。
- **dashboard 6 polled hooks**：全部经 `useDashboardQuery` 包装（`web/src/lib/dashboardQuery.ts`），自动 `refetchIntervalInBackground:false` + `refetchOnWindowFocus:false`。L-025 结构修复落地干净——5/02 audit HIGH-2 收口。
- **inspect 4 query hooks**（useFileBrowser × 3 + useScreenshots + useUartCaptures × 2 + useDeviceSystem）：全部 `refetchOnWindowFocus:false` + 无 `refetchInterval`，按需 fetch，无 background 累积。
- **useFileTransferStream WS lifecycle**：`useEffect(() => () => cleanup(), [cleanup])` + `wsRef` cleanup 严密，open/message/error/close 四 listener 状态机覆盖完整；server 侧 outer-finally + producer/recv 双 task cancel race 符合 L-026 "exactly one close frame" 不变量。
- **useLogcatStream debounced auto-reconnect**（commit a1dde70）：`lastAppliedFilter` ref 防 state-churn 自循环，`state==='ready'` 才触发 reconnect（避 reconnect loop）；600 ms debounce 抑制连接抖动。设计扎实。
- **UiDumpTab 虚拟化**（commit 9a70342）：useMemo + useDeferredValue + react-virtual estimateSize=24 + measureElement + overscan=12。2000 节点首屏只 mount ~30 行，filter keystroke 不再走 2000 行 layout。+5 KB gzip 是设计取舍合理。
- **CSS 增量**：components.css 2844 行 +274 行（PR-period 累计），SectionPlaceholder unify + DEBT-031 BEM 重写，0 legacy ruleset 残留（5 处 legacy class 引用全是注释）。
- **WS push/pull stream**（commit 90，fb_push_stream/pull_stream）：协议 ready/progress/closed 单方向单 close-frame；`asyncio.Queue` 协调 producer + recv + main loop；cancel 路径走 `gen.aclose()` → SIGTERM 子进程 finally 链，无 zombie 风险。
- **events.jsonl 全量扫**（DEBT-008）：未在本范围内引入新 hot path 调用（5/02 audit 已知，不重复提）。

## 结论

- **bundle 预算守住** ✓（110.32 KB 主 / 117.03 KB 主+css，<120 KB 预算）
- **必须立即修：0 HIGH**
- **入 backlog：1 MID（workspace_preview to_thread）+ 2 LOW（read_capture / list_screenshots 同源 IO 债）**
- 累积 15 commits 整体质量稳——reviewer agents 在主路径上盯得严（DEBT-024/025 background gate 结构修复 + L-026 outer-finally 模式 + L-032 a11y 三件套），未引入 HIGH。
- 三个 IO 协程化建议属同一类债（async endpoint 里 sync FS call），可打包做一个 follow-up commit "io_to_thread sweep · web/api 5 endpoint"，单独入 DEBT 候选。

## 建议加入 knowledge

- **debts.md** 新候选条目：
  - DEBT-032 · `workspace_preview` / `read_capture` / `list_screenshots` 同步 IO 在 async 路径（severity mid，5/05~07 commit 90/05bbdae 引入；同源建议 sweep `web/api/` 找全部 `f.open / f.read_text / f.stat / glob` 走在 async def 上的 endpoint 一次性补 `asyncio.to_thread`）
- **lessons.md** 候选 L-033：
  - "async endpoint 写 FastAPI 必须 grep `f\.\(open\|read\|stat\|glob\)` 走 sync 路径，包 `asyncio.to_thread`"——和 L-014/L-025 同源（"新 hook / endpoint 必须 sweep 一组 flag"模式），加进 backend-reviewer agent 自动 grep checklist
- **decisions.md** 不需要新 ADR（属 IO 协程化债，已有 ADR 覆盖）

## 不在范围

- 未跑实际 lighthouse / web-vitals（需主对话起 `npm run dev` + Playwright performance API）
- 未 benchmark `workspace_preview` 真实 P99（建议主对话起 wrk + 多并发 64 KB read 验证 to_thread 收益是否值得）
- DEBT-008 events.jsonl 全量扫已知 / 不重复提
- WS subscriber Queue 释放路径已读过 cleanup OK（5/02 audit 已验），不重复提
- xterm 终端实例复用（5/02 audit DEBT-026 候选）属 trade-off 重大 / 暂不动
