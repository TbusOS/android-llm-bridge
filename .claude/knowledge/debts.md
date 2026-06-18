# 已知技术债清单

不是 bug，是当时为了快速 ship 做的妥协。agents 评审时**不要重复提**
已经在这里登记的债（除非建议升级 severity 或建议立刻还）。

格式：
- 每条债一段
- severity：high（影响功能 / 安全）/ mid（影响维护 / 体验）/ low（small）
- 引入时间 + commit
- 是否计划修：是 / 否 / 视情况

---

## 索引（按状态 + 编号 · 37 项 / 35 关 + 2 候选 · 2026-05-09）

**候选未关：2** —— DEBT-036 (session listing N=2→N=3 触发抽) + DEBT-037 (config.toml 原子写 + 0600)

**未关 backlog（视触发条件）**
- DEBT-036 · session listing helpers cross-surface drift（N=3 surface 触发抽）
- DEBT-037 · config.toml 写入非原子 + 0644 模式（加 secret 字段或下次 security-audit 触发）
- DEBT-005 · workspace/sessions 没自动清理
- DEBT-006 · workspace/events.jsonl 没 rotation
- DEBT-007 · ts_approx 字段语义已无用
- DEBT-009 · Vite base URL 硬编码风险
- DEBT-010 · /audit/stream WS 协议没预留 session_id / kinds 过滤
- DEBT-012 · web/ reducer 纯函数无单测
- DEBT-013 · 前端 METRIC_KINDS 与后端 _DEFAULT_METRIC_KINDS 双写不同步
- DEBT-016 · vite base 在 GH Pages 部署不正确，SPA shell 资源加载 404
- DEBT-022 (mid) · device card 信息薄 · 缺刷新机制 + 多维元数据
- DEBT-082 · event_bus.publish 每事件 to_thread + open/close（round11 PERF-5 · 低事件率无影响）

**已关（按时间倒序 · 28 项）**

5/09：033（4-step mockup v3 ship）
5/08：032 034 035
5/07：021 031
5/06：018 029（含 MID-6 4-commit batch）
5/02：023 024 025 026 027 028 030 + 4-agent 联合 audit 9 HIGH
5/01：020 015（mechanism）
4/30：017 019
4/29：001 002 003 004 011

（详见各 DEBT 段落正文 — Ctrl-F "## DEBT-NNN" 跳转）

---
- 还债条件：什么情况下应该停下来还

---

## DEBT-001 · LiveSession tps 退化为整段平均（spark 空）—— **CLOSED 2026-04-29**

- **severity**：mid（用户体感"实时"被削弱，但不影响功能）
- **引入**：C.5（commit a03cbab，2026-04-28）
- **关闭**：F.6 ship + 端到端验证（2026-04-29）
- **位置**：`web/src/features/dashboard/useLiveSession.ts` `tpsSpark: []`
- **解决路径**：
  1. F.1 后端 TokenSampler 1Hz 发 tps_sample（ADR-021）
  2. F.5 前端双 WS 实例订阅 metric 流（ADR-022）
  3. F.6 reducer 加 tps_sample 分支 + scaleSparkPoints
  4. **2026-04-29 端到端验证**发现并修一个 P0 bug：`audit_route._project()`
     一直把事件的 `data` 字段 silently drop，导致前端 reducer 拿不到
     `rate_per_s` / `total_tokens` / tool_call `id`/`name` / done `usage`
     —— 修法是 `_project()` 加 `data` 字段 + TS AuditEvent 类型同步
- **验证证据**：`.claude/reports/visual-2026-04-29-debt001.md` —— 真实
  ollama gemma4:e4b 跑 chat，reducer 拿到 tpsSamples=[3,12,11,12,12,11,
  12,12,12,12,9]（真实生成曲线），spark scale 后正确分布 0..27（peak
  normalize），prompt/turn/modelName/totalTokens 全部正确显示
- **后续**：F.8 阶段补 Playwright 视觉截图（不阻塞本次关闭）

---

## DEBT-002 · MOCK_BACKENDS 仍占位 —— **CLOSED 2026-04-29**

- **severity**：low
- **引入**：D 档（commit 6e5b12b，2026-04-27）
- **关闭**：G 档（2026-04-29）—— 新 `useBackends` hook 调 GET
  /playground/backends；DashboardPage 改用 hook，backendMeta 动态
  caption "1 registered · 3 planned"。LlmBackendCards 改 latencyMs/
  tps/errors undefined 时显示 "—"（避免假数据 0）
- **范围拆分**：本档只关 "mock → 真注册表数据"，runtime health
  缺口（latency/tps/errors 永远 "—"）拆 **DEBT-017** follow-up
- **agents 评审**：5 建议，4 采纳 + 1 follow-up（empty placeholder 不阻塞）

---

## DEBT-003 · KPI MCP tools 写死 21 —— **CLOSED 2026-04-29**

- **severity**：low
- **引入**：D step 4（commit 2af137c，2026-04-28）
- **关闭**：F.7 ship（2026-04-29）—— useTools hook 接 GET /tools，
  KpiStrip 显示真实 33 + 11 categories。验证报告
  `.claude/reports/visual-2026-04-29-f7.md`

---

## DEBT-004 · KPI LLM throughput 显示 "—" —— **CLOSED 2026-04-29**

- **severity**：mid
- **引入**：D step 4（commit 2af137c）
- **关闭**：F.7 ship（2026-04-29）—— useMetricsSummary hook 接 GET
  /metrics/summary?window_seconds=300，KpiStrip 显示真实 mean=11.4
  tok/s + "5m avg · N samples" label。LiveCard 同步标 "tok/s now /
  现"区分瞬时 vs 窗口均值（落实 F.6 arch review #4 强制要求）

---

## DEBT-005 · workspace/sessions 没自动清理

- **severity**：low
- **引入**：M1（ChatSession.create 写盘起）
- **位置**：`workspace/sessions/<sid>/`
- **原因**：每个 chat 创一个 dir，没有 TTL / count cap，长期运行会膨胀
- **是否计划修**：M3 / 视情况
- **还债条件**：用户报告"workspace 占盘"或 GET /sessions 响应慢

---

## DEBT-006 · workspace/events.jsonl 没 rotation

- **severity**：mid
- **引入**：C.1（commit 36537d5）
- **位置**：`workspace/events.jsonl`
- **原因**：append-only 单文件，长期跑会 GB 级，GET /audit 扫全量会慢
- **是否计划修**：M3
- **还债 sketch**：按月 rotate（events.jsonl → events-2026-04.jsonl），
  GET /audit 默认只读最新月，跨月查询走 archive

---

## DEBT-007 · ts_approx 字段语义已无用

- **severity**：low
- **引入**：C.1（保留兼容前端 useAudit）
- **位置**：`src/alb/api/audit_route.py` `_project()` 永远 false
- **原因**：旧实现用 messages.jsonl mtime 做近似 ts，新实现每条都有真
  ts，但前端 schema 里有这字段，删掉会破坏 schema
- **是否计划修**：API_VERSION 大版本时一起清
- **还债条件**：API_VERSION 从 "1" 升到 "2" 时

---

## DEBT-008 · GET /metrics/summary 缺 short-TTL cache —— **CLOSED 2026-06-17 (ADR-049)**

- **关闭**：ADR-049 物化读模型（2026-06-17）—— `/metrics/summary` 热路径改读内存
  `MetricStore`（O(窗口)），全量扫 events.jsonl 只剩冷启动 / 长窗口（> 容量 900s）
  兜底这条罕见路径。short-TTL cache 不再需要（内存投影本身就是缓存）。
- **severity**：~~low~~ → **mid**（2026-04-29 升级）
- **引入**：F.3（commit 5dcc018，2026-04-28）
- **位置**：`src/alb/api/metrics_summary_route.py` 每次请求全量扫
  `events.jsonl`
- **原因**：`window_seconds` 上限 24h + events.jsonl 全量遍历。
- **2026-04-29 升级理由**：F.7 ship `useMetricsSummary` 30s refetch +
  refetchOnWindowFocus，是 DEBT-008 第一个稳定消费者。F.3 时假设
  "events.jsonl 还没积累，问题不显" 已经废了。
- **细化触发条件**：
  - events.jsonl 行数 ≥ 10k（约 3 小时连续 chat 1Hz tps_sample 即达）
  - 单机 ≥ 2 个 dashboard tab 持续打开 ≥ 1 小时
  - 任一满足 → 还债优先级提到 M2（不再延后到 M3）
- **是否计划修**：是（M2 候选，触发条件满足前可不阻塞）
- **还债 sketch**（细化）：
  1. 进程级 `functools.lru_cache(maxsize=8)` + TTL 60s，按
     `(window_seconds, session_id)` cache 上次结果（首选，简单）
  2. 或文件 mtime + size 校验，如果不变直接返 cache（更精准）
  3. 客户端缓解：useMetricsSummary `refetchOnWindowFocus: false`
     + refetchInterval 拉到 60s（不解决根因，但减压）
- **备注**：security-and-neutrality-auditor agent 在 F.3 评审中提出
  作为 low 风险；F.7 后由 architecture-reviewer 升级为 mid。


## DEBT-010 · /audit/stream WS 协议没预留 session_id / kinds 过滤

- **severity**：low
- **引入**：F.5（commit pending，2026-04-28）
- **位置**：`src/alb/api/audit_route.py` 首条 message 只读
  `minutes` / `include_metrics`，无 `session_id` / `kinds` 过滤
- **原因**：F.5 阶段只需 metric vs business 两路；未来 SessionDetailPage /
  DiagnoseFollow / 第三个消费者想"单 session 全流"需要 break 协议或
  自己客户端过滤。当前 N=2 没问题，N≥3 + 跨页面消费时需要扩协议
- **是否计划修**：是（视情况，触发条件见下）
- **还债 sketch**：首条 message schema 扩为
  `{minutes, include_metrics, session_id?, kinds?}`，server 全 None 时
  行为不变（向后兼容）；同步 bump web/lib 协议版本注释
- **还债条件**：第 3 个消费者出现 / 同时 N ≥ 3 条 WS 都连 /audit/stream


## DEBT-009 · Vite base URL 硬编码风险

- **severity**：low
- **引入**：M2 Web Tier 1（约 commit f757cb7 起）
- **位置**：`web/index.html` 内嵌 link 不带 `/app/` 前缀（依赖 Vite
  base 自动拼接，详见 lessons.md "Vite base 路径不能在 link 里手写"）
- **原因**：Vite 重复加 base 会让 CSS 全 404
- **是否计划修**：否（这是 Vite 的正确用法，不是债）
- **还债条件**：—（保持当前用法，记入 lessons 防再踩）

> 注：这条其实是 lessons 不是 debt，写这里只是因为偶尔有 reviewer
> 误判它"硬编码可疑"。下次 reviewer 看到自动跳过。

---

## DEBT-011 · useAuditStream MAX_EVENTS 不分类型 —— **CLOSED 2026-04-29**

- **severity**：mid
- **引入**：F.5（commit c135816，2026-04-28）+ F.6 暴露
- **关闭**：F.7 ship（2026-04-29）—— dual buffer 落地：business cap 200 +
  metric cap 60（与 SPARK_WINDOW 对齐），useMemo merge 出 newest-first
  rawEvents 给 reducer。模拟验证：合成 50 biz + 500 metric 事件，
  旧单 cap 200 → biz 仅存活 18/50（丢 32），新 dual cap → biz 50/50
  完整。报告 `.claude/reports/visual-2026-04-29-f7.md`

---

---

## DEBT-016 · vite base 在 GH Pages 部署不正确，SPA shell 资源加载 404

- **severity**：low（GH Pages 上 SPA 完全不可用，但 landing 没指向 /app/，
  零真实用户场景；主用户走 alb-api dev/local 不受影响）
- **引入**：M2 Web Tier 1（commit `b07b930`，2026-04-23 起，6 天前）
- **位置**：`web/vite.config.ts:55` `base: process.env.VITE_BASE ?? "/app/"`
- **症状**：
  - GH Pages 部署在 `/android-llm-bridge/app/`（自定义域名 `doc.tbusos.com/android-llm-bridge/app/`）
  - vite build 出 `docs/app/index.html` 含 `<link href="/app/anthropic.css">`
    `<script src="/app/assets/index-XYZ.js">` 等绝对路径
  - 浏览器在 `doc.tbusos.com` 下解析这些绝对 path → `doc.tbusos.com/app/anthropic.css`
    缺 `/android-llm-bridge/` 前缀 → 404
  - 实际资源在 `doc.tbusos.com/android-llm-bridge/app/anthropic.css` HTTP 200
  - 结果：GH Pages 上 SPA shell 启动失败，root div 空白
- **DEBT-015 prod 验证暴露过程**：DEBT-015 修 SPA fallback 协议后做
  prod verify，跑 Playwright `/app/dashboard` chain 还原后看 React 没
  渲染 → 调试 console errors 看到 4 个 404（fonts.css / anthropic.css /
  index-XYZ.js / index-XYZ.css）→ 检查 vite base 配置发现错配
- **为什么之前没暴露**：landing page (`docs/index.html`) 没真实 link
  指向 `/app/`，只指向 `webui-preview.html` mockup。没人主动访问
  `/app/` 深链所以一直没炸
- **是否计划修**：视情况
- **不阻塞条件**：
  - 主用户走 alb-api dev/local（base="/app/" 正确）
  - GH Pages 用作"项目方法论展示 + landing"，不强求 SPA 真实可用
- **修法选项**（trade-off 重）：
  - **A**. GitHub Actions CI build with `VITE_BASE=/android-llm-bridge/app/`
    → publish to gh-pages branch。**违反 offline-first 原则**（项目
    README + memory 都强调 docs/ commit 进仓不依赖 CI）
  - **B**. 改 vite base 为 `/android-llm-bridge/app/` + alb-api mount
    path 也改。破坏 alb-api 默认用法（URL 变长难看）
  - **C**. 两份 docs/app/ 各 commit 一份 base（仓库膨胀，git diff 噪音）
  - **D**. **接受 GH Pages 不支持 SPA**，调整 landing 文案"Web UI 需
    本地运行 alb-api"，移除任何指向 GH Pages /app/ 的链接（事实上现
    状已经没有真实入口指向 SPA）—— **可能是最 pragmatic 选项**
- **触发还债条件**：用户明确报告"想在 GH Pages 上看 SPA 截图分享给同事"，
  或 dev-team.html 等展示页需要嵌入 SPA iframe

- **severity**：mid（生产 UX 问题）
- **引入**：M2 Web Tier 1（约 commit b07b930，2026-04-23）
- **关闭**：commit pending（2026-04-29）—— `SPAStaticFiles(StaticFiles)`
  子类 override get_response，404 时如果 path tail 没扩展名就 fallback
  到 index.html；含点的 path（asset）让真 404 propagate（不 silently
  改写避免白页 debug 噩梦）。+2 unit test + 真浏览器 Playwright
  deep-link/refresh/nested 3/3 pass。
- **范围拆分**：本档只修 alb-api（dev/local）。GH Pages prod 同问题
  拆作新 **DEBT-015**（spa-github-pages 套路：404.html + query-encoded
  redirect script）。
- **正面 case 引用**：见 lessons.md L-017 — F.8 端到端 Playwright
  `page.goto(/app/dashboard)` 直接拍到 FastAPI 404 JSON 暴露 SPA fallback
  缺失。code review / typecheck / unit test 都看不出。

---

## DEBT-015 · GH Pages prod 同 SPA fallback 缺失 —— **CLOSED (mechanism) 2026-04-29**

- **severity**：low（影响"分享深链"少数场景，主用户走 alb-api）
- **引入**：本仓 GitHub Pages 部署（最早 1f2522d，2026-04-19）
- **关闭范围**：**SPA fallback 协议层**（URL 跳转还原机制）已完整 ship +
  prod verify。**SPA shell 资源加载**层面的独立问题拆 DEBT-016（vite
  base 在 GH Pages 部署不正确，6 天前 commit `b07b930` 起一直存在）
- **prod 验证**（2026-04-29，commit 64ad2e1 部署 4min 后）:
  - ✅ redirect chain：`/app/dashboard` → `?spa=1&p=dashboard` → `/app/dashboard`
  - ✅ URL 最终态干净（无 `?spa=1` 残留）
  - ✅ nested route 保留：`/app/sessions/abc-123` chain 正确
  - ✅ refresh on `/app/inspect` 正确还原
  - ✅ `/app/` 直访无回归
  - ❌ **SPA shell 资源加载 404**（DEBT-016，独立 issue）—— 真浏览器
    Playwright 看到 `<link href="/app/anthropic.css">` 在 `doc.tbusos.com`
    base 下加载 `doc.tbusos.com/app/anthropic.css` 而非
    `doc.tbusos.com/android-llm-bridge/app/anthropic.css`，所有 React
    bundle 也同样 404 → SPA shell 启动失败
- **关闭范围注释**：DEBT-015 关闭条件原文 "浏览器开 prod 深链能直达"。
  狭义看（fallback 机制本身正确）pass；广义看（SPA 在 GH Pages 真能用）
  fail（DEBT-016 阻塞）。机制层 PASS 已足够标 CLOSED，DEBT-016 单独
  跟进
- **关联产出**：
  1. `docs/404.html`（新）：GH Pages 自动服务 + 条件 redirect script，
     `/android-llm-bridge/app/<route>` → `/app/?spa=1&p=<encoded>&qs=<query>#hash`，
     非 /app 路径显示 anthropic-style 404 landing
  2. `web/index.html` + recovery inline script（vite build 进
     `docs/app/index.html`）：检测 `?spa=1`，`history.replaceState`
     还原原 URL → TanStack Router 接管
  3. 死循环防御：404.html 检测已 wrap 的 `?spa=1` URL 不再 wrap
  4. 残留参数清理：recovery script 检测 `?spa=1` 缺 `p` 时清掉
     query 让 URL bar 干净
- **测试**：`tests/web/spa_fallback_test.mjs` 12 case 持久化（node +
  vm.runInContext 跑两个脚本逻辑，含 trailing slash / qs / hash /
  loop guard / dev pathname=/ 边界）
- **prod 验证**：GitHub Actions Pages 部署 ~1-2min 后，主对话
  ScheduleWakeup 跑 curl `-IL https://tbusos.github.io/android-llm-bridge/app/dashboard`
  确认 redirect chain → 200 SPA shell（参考 L-018）
- **关联产出**：**ADR-023** 跨 surface 异构 SPA fallback / **L-018**
  静态托管 URL 闪现 + recovery 必须 inline 同步执行
- **共享不变量**（写入 architecture.md）：
  - SPA route 路径段不能含 `?` `#` `&`（GH Pages 协议保留）
  - SPA route 不能以 `assets/` 开头（与 vite build 产物冲突）
  - GH Pages 协议保留 query 名 `spa` / `p` / `qs`

---

## DEBT-013 · 前端 METRIC_KINDS 与后端 _DEFAULT_METRIC_KINDS 双写不同步

- **severity**：low（候选，未触发）
- **引入**：F.7 dual buffer 落地（commit pending，2026-04-29）
- **位置**：
  - 客户端：`web/src/features/dashboard/useAuditStream.ts:36`
    `const METRIC_KINDS = new Set(["tps_sample"])`
  - 服务端：`src/alb/api/audit_route.py` `_DEFAULT_METRIC_KINDS`
- **原因**：前端要按 kind 分桶 cap（business 200 + metric 60，DEBT-011
  关闭物），但 metric 类目集合在两端各持一份独立 truth。当前 N=1，
  双改成本可控；ADR-021 提示未来加 cmd_rate / push_rate，到 N≥3 时
  漏改一边会让"server 推过来但前端当 business 处理"，挤掉真 user 事件。
- **是否计划修**：视情况
- **还债 sketch**：把 metric kinds set 由 server 在 audit/stream 首条
  message 推下来；客户端先收再分桶。代价：增加首条 message schema
  + 客户端 ready 状态机
- **还债条件**：metric kinds ≥ 3 类，或前端测试发现"加了 metric 但
  spark 没响应"

---

## DEBT-012 · web/ reducer 纯函数无单测

- **severity**：low
- **引入**：C.5（a03cbab）至 F.6（pending）累积
- **位置**：`web/src/features/dashboard/useLiveSession.ts` 的 reduceSessions /
  selectActiveSession / toLiveSessionData / scaleSparkPoints 全部纯函数
- **原因**：web/ 没装测试框架（约束：M2 Tier 1 不引 vitest 保 bundle 小）。
  reducer 是数据正确性核心，下次 fallback 逻辑改动 / 新事件 kind 加分支
  没有回归网。
- **是否计划修**：是
- **还债 sketch**：web/ 引入 vitest 时一起补 6-8 case：单 sample / 多 sample /
  done 后续 sample / NaN 守卫 / SPARK_WINDOW cap / 跨 session 切换；
  **+ G 档 mapApiBackendToCard 5 case**（beta / planned / 未知 status /
  description 空走 requires / requires 空走 ""）
- **还债条件**：web/ 引入测试框架（候选 G/H 档）

---

## DEBT-017 · LLM backend runtime health 缺口 —— **CLOSED 2026-04-30**

- **severity**：mid
- **引入**：D 档 BackendCardData type 定义 runtime 字段但无数据源
- **关闭**：commit `67c0820` (主) + `63a10c2` (ADR-024 重构) + `8662027`
  (chat_cli envvar 隔离 follow-up)。新增 `GET /playground/backends/{name}/health`
  6-reason 枚举端点 + useQueries 双层并行 + 6-state UI + ADR-024
  capability via class attr + ADR-025 polling 分层。L-017 真浏览器
  4 cards / 0 console errors 验证。
- **agents 评审**：4 并行（mockup-baseline / code / arch / perf）·
  24 条建议 · **92% 采纳率**（18 采纳 + 4 部分采纳 + 2 follow-up）
- **新登记**：DEBT-018（DashboardPage placeholder 重复）+ DEBT-019
  （httpx.AsyncClient 实例复用）

---

## DEBT-018 · DashboardPage section placeholder loading/error 重复 —— **CLOSED 2026-05-06**

- **severity**：mid（结构债，本身不影响功能；DashboardPage 已 380+ 行，
  每加一个 hook 段涨 ~30 行 boilerplate）
- **引入**：D 档（device strip 加 isError/isLoading 分支）
- **暴露**：DEBT-017 给 backends 段加 isError/isLoading 时，arch
  reviewer 发现 4 处（device / sessions / backends / audit）各有自己的
  placeholder 实例
- **位置**：`web/src/features/dashboard/DashboardPage.tsx`（loading /
  error inline 在 4 段各写一份）
- **关闭**：commit (待提)。抽 `<SectionPlaceholder
  styleKey={"dev-strip"|"be-card"|"sess-card"} kind={"loading"|"error"|
  "empty"}>` 组件落到 `web/src/features/dashboard/SectionPlaceholder.tsx`
  ，DashboardPage 4 段（devices 4 分支 + backends 2 分支 + sessions 3
  分支 + timeline 3 分支 = 12 个 inline div）全数迁移。CLASS_MAP 内化
  3 套 BEM family（`dev-strip-state` dashed / `be-card--empty` solid /
  `sess-card--state` 继承）输出 class 完全照搬现有 BEM，**视觉零变化**
  。typecheck 0 / build 3.62s / 主 bundle 110.27 KB gzip 持平 / 841
  pytest pass。
- **设计决策**：DEBT-018 sketch 原写"沿用 .be-card--empty"，落地时改为
  "三 BEM family 共存 + CLASS_MAP 单一 mount 点"。原因：mockup
  baseline 仅定义 `.be-card--empty`（solid），React 自加 `.dev-strip-state`
  （dashed） 和 `.sess-card--state`（继承父）属于已偏离 baseline 的
  分支；统一到任一一种都需改 mockup baseline + 三道闸视觉验证，超出
  DEBT-018 抽组件范围。**视觉统一另登 follow-up**（见 DEBT-031 草稿）
- **行数**：DashboardPage diff +25/-24（净 +1） + SectionPlaceholder
  +70 = 净增 71 行；不是 boilerplate 减量胜利，是 **类型化 + 单一
  mount 点** 胜利（styleKey/kind 是 union type，BEM 拼错编译期挡掉；
  未来视觉统一只改 CLASS_MAP 一处）
- **来源**：DEBT-017 arch reviewer 发现 #4，主对话登记不阻塞合入

---

## DEBT-031 · Dashboard 3 套 placeholder BEM family 视觉不统一 —— **CLOSED 2026-05-07**

- **severity**：low（视觉一致性，不影响功能；不阻塞 ship）
- **引入**：D 档（`.dev-strip-state` 加入）+ DEBT-017（`.be-card--empty`
  入档） + sessions 段（`.sess-card--state` 继承父）
- **暴露**：2026-05-06 落 DEBT-018 抽 SectionPlaceholder 时，CLASS_MAP
  必须保留 3 套 BEM family，因为 mockup baseline `.be-card--empty`
  是 solid border + 默认 align，React 自加的 `.dev-strip-state` 是
  dashed border + center align，视觉风格不同
- **关闭**：commit (待提) 2026-05-07。抽 `.section-placeholder` base
  + `--dashed / --solid / --inset / --err` 4 个 variant modifier，
  每个 variant 复刻原视觉 ⇒ DashboardPage 4 段视觉零变化但 BEM
  block 从 3 → 1。落地：
  - `docs/webui-preview-v2.html`：删 `.be-card--empty`（10 行）+
    line 1264 inline 用法改 `.section-placeholder.section-placeholder--solid`
    ；加 base + 4 variant 定义（25 行 CSS + 9 行注释 DEBT-031）；
    三道闸全过（verify ✓ / visual-audit 0 error ✓ / screenshot 视觉
    零回归 ✓）
  - `web/src/styles/components.css`：删 `.dev-strip-state` /
    `--err`（13 行）+ `.be-card--empty`（10 行 + 4 行注释）+
    `.sess-card--state` / `--err`（10 行）；加 `.section-placeholder`
    + 4 variants（24 行 + 6 行注释 DEBT-031 引）
  - `web/src/features/dashboard/SectionPlaceholder.tsx`：CLASS_MAP
    从 3-key Record → VARIANT_CLASS 单层映射 + ERR_CLASS 单字符串
    + sess-card 仍包外层 `.sess-card` chrome（保边框上下相连）；
    styleKey/kind 接口不变 ⇒ DashboardPage callsite 零改动
- **设计决策**：3 BEM family 共存路径（DEBT-018 时的折中）走到 N=3+
  时清理；选用 base + variants 而非"统一一种风格"，因为：
  1. 3 段视觉风格各为业务表意（dashed=没数据状态强信号 /
     solid=card-style 收尾 / inset=融入父 list chrome），统一一种
     反丧失语义；
  2. base 单一 mount 点已实现"未来真要统一只改 .section-placeholder"
     的目标；
  3. 视觉零变化避免触发其他 audit 维度的回归
- **位置**：`web/src/styles/components.css:788-832`（新 base + variants）
  + `web/src/features/dashboard/SectionPlaceholder.tsx`（重写）
  + `docs/webui-preview-v2.html:557-590`（mockup baseline 扩展）
- **来源**：DEBT-018 关闭时承诺的视觉统一 follow-up

---

## DEBT-019 · httpx.AsyncClient 实例复用 —— **CLOSED 2026-04-30**

- **severity**：low → mid（M3 step 1 触发条件满足后升级）
- **引入**：M2 早期 OllamaBackend 实现
- **关闭**：commit `121106b`（M3 step 1 follow-up）。OllamaBackend +
  OpenAICompatBackend 加 lazy `_client` + `aclose()` + alb-api FastAPI
  shutdown 调 `close_probe_cache()` 集中关。chat/stream/health/list_models
  4 路全覆盖。新增 `tests/agent/test_backend_registry.py` 锁 6 行为测试 +
  各 backend 加 2 个 client_reused / aclose_idempotent 测试 = +10 tests。
- **agents 评审**：M3 step 1 arch-reviewer #1 推动（"不能让 DEBT-019
  静默留成忘记修，与 L-019 sentinel 反模式同源"）
- **性能影响**：N=4 backend × 4 r/min health = 16 r/min 现在共享 16 个
  keep-alive 连接，单 backend 减 ~1ms setup × 16 = 16ms/min CPU 省 +
  消除 N TCP TIME_WAIT 积累

---

## DEBT-020 · alb-api backend health 端点不读 ALB_*_URL / ALB_*_MODEL env —— **CLOSED 2026-05-01**

- **severity**：mid（dashboard 显示永远是 manifest 默认值，不反映用户 env 配置 —— 用户体感为"模型/URL 配错了"）
- **引入**：M3 step 1（OpenAICompatBackend 加 health 端点时，沿用 OllamaBackend 的 health 端点路径，两者都不读 env）
- **关闭**：commit `fe92583`（DEBT-022 PR-A 同期）。`src/alb/agent/backends/__init__.py:78` `_construct()`
  在 ollama 分支注入 `ALB_OLLAMA_URL` / `ALB_OLLAMA_MODEL` env override（caller kwargs > env > default
  同 `src/alb/api/chat_route.py:245-246` 已有 pattern）。3 测试覆盖：env 注入 / kwargs 优先 / 无 env 默认。
- **真机验证**：probe 之前 `reachable=false reason=down model=qwen2.5:3b latency=2ms` →
  现在 `reachable=true latency=186ms model=qwen2.5:7b`（env 配的）
- **同源问题预防**：openai-compat 同样 pattern 待 M3 step 3 (Anthropic) 落地时一并加（用户尚未报问题）

---

## DEBT-021 · 历史 tracked 文件含敏感词 · CI `--all` 模式会挂 —— **CLOSED 2026-05-07**

- **severity**：mid（CI 全量扫挂；staged 模式不影响日常 commit）
- **位置**：
  - `.claude/reports/visual-2026-04-29-debt001.md` 含 `<llm-host>`
  - `.claude/reports/preflight-2026-04-29-f-dock.md` 含 `<llm-host>`（同 IP，后期登记）
  - `scripts/f8_screenshots.mjs` 含 hardcoded 个人家目录路径 + 真实用户名字面
- **现象**：`bash scripts/check_sensitive_words.sh --all` 5 处命中（exit 1）
- **关闭**：commit (待提) 2026-05-07。落地：
  - `scripts/f8_screenshots.mjs:12` `import "/home/<user>/claude-tools/.../playwright"`
    → `import "../web/node_modules/playwright/index.mjs"`（web 已 dev-dep playwright 1.59）
  - `scripts/f8_screenshots.mjs:19` hardcoded `/home/<user>/...` 输出路径 →
    `path.resolve(SCRIPT_DIR, "../.claude/reports/screenshots/2026-04-29-f8")` 用
    `import.meta.url` + `fileURLToPath` 推导 `__dirname`
  - `.claude/reports/preflight-2026-04-29-f-dock.md:36` `<llm-host>:11434` →
    `<llm-host>:11434`
  - `.claude/reports/visual-2026-04-29-debt001.md:10` `ollama@<llm-host>:11434` →
    `ollama@<llm-host>:11434`
  - 验证：`./scripts/check_sensitive_words.sh --all` exit 0 命中 0；smoke check
    脚本 import 路径解析成功（实际跑通 playwright 加载）
- **来源**：2026-04-30 commit `0ef2d87` 前 `--all` 扫描发现，2026-05-07 4-agent
  audit security 复发现并最终关闭

---

## DEBT-022 · device card 信息薄 · 缺刷新机制 + 多维元数据

- **severity**：mid（功能缺失。当前 device card 只显示 serial / product / model / transport
  几个浅字段 + 空 cpu/温度，工程师视角"完全看不见板子")
- **引入**：D 档 device strip（dashboard 早期）
- **位置**：
  - `src/alb/api/<devices_route>` 当前 `/devices` 端点只返回 adb 层基础元数据
  - `web/src/features/dashboard/DeviceStrip.tsx`（或同等）—— 渲染薄字段
  - `web/src/features/inspect/`（如有）—— 详情页占位
- **是否计划修**：是
- **用户诉求（2026-04-30）**：
  1. **dashboard summary 卡片**：补 SoC 具体型号 / RAM 用量 / 存储用量 / 电池 / Android 版本
  2. **inspect 详情页**：分区表（`ls /dev/block/by-name/` + `/proc/partitions` + `/proc/mounts`）
     + 内存布局（`/proc/meminfo` + `dumpsys meminfo` + `/proc/iomem`） + flash 布局
     （`lsblk` + `/proc/mtd` + `/sys/block/*/size`） + 网络接口 + 温度 + 全 props
  3. **手动刷新按钮** + 自动 polling（refetchInterval）
  4. **明确不依赖 LLM**：alb_devinfo 已经在做确定性 RPC 拉数据，alb-api 只需 surface 成 endpoint
- **还债 sketch**（拆 2 PR）：
  - PR-A · dashboard summary card（~2-3h）：alb-api `GET /devices/{serial}/details` →
    内部调 `alb_devinfo` + 多 1-2 个 grep（SoC / cores / 屏幕） → frontend 多字段渲染 +
    RefreshCw 按钮 + useQuery refetchInterval(30000)
  - PR-B · inspect 详情页（~4-5h）：alb-api `GET /devices/{serial}/system` 返回完整
    partition / memory / flash 三视图 → frontend `/inspect` 页面表格 + 手动刷新
- **PR-A 关闭 2026-05-01**：commit `fe92583`。devinfo() 加 7 extras 字段
  (soc/cpu_cores/cpu_max_khz/ram_total/avail/display/temp_c) + alb-api
  `GET /devices/{serial}/details` endpoint + frontend useDeviceDetails
  hook + DeviceCard 子组件（per-card 30s polling）+ DashboardPage 顶层
  RefreshCw 按钮（invalidateQueries 触发全 cards 重 fetch）+ CSS。
  ADR-028 (a) + ADR-029 (a) 拍板正式（见 decisions.md）。+13 tests 全过。
  真机验证：dashboard 显 SOC/CPU/RAM/DISPLAY/BATTERY 5 行 + temp 真值。
- **PR-C.a 关闭 2026-05-01**：commit `70ba2a4`。alb-api 加 `/uart/capture`
  POST + `/uart/captures` GET list + `/uart/captures/{name}` GET read 三
  endpoint（ADR-028 (a) 同 pattern：summary endpoint + read endpoint）+
  inspect 第 6 个 tab UartTab + useUartCaptures 三 hook（list / read /
  trigger mutation）+ vite.config.ts 加 /uart proxy（应用 L-022 lesson
  主动 grep）。+13 tests 全过。真机验证：playwright click UART tab +
  Capture(3s) → 截图右侧暗 viewer 满屏真实 UART logd 行 + 0 console
  errors + 2 captures 列表项。**用户能在 web 上看 UART 打印了（事后翻账
  模式）**。
- **PR-B 仍 OPEN**：inspect 详情页 partition/memory/flash 三视图 + 全 props 表格
  · 待 next session（独立 PR）
- **PR-C.b 关闭 2026-05-01**：commit `96a539a`。alb-api `WS /uart/stream`
  endpoint（pump task 推 SerialTransport.stream_read('uart') binary frames
  + recv task 监 client close 帧 + asyncio.wait FIRST_COMPLETED 双协程）
  + frontend useUartStream hook（state machine idle→connecting→ready→
  ended/error，不 auto-reconnect）+ UartLiveStream 组件（xterm.Terminal
  + FitAddon + Connect/Disconnect/Clear + state pill）+ UartTab 拆 mode
  toggle（Capture/Live segment）。+5 tests 全过。真机验证：playwright
  UI 截图 + python websockets 直连后端 收到 ready JSON + binary 3359 bytes
  真实 SE Linux audit 行。**用户能在 web 上实时看 UART 打印（现场观察
  模式）**。
- **PR-D 关闭 2026-05-01**：commit `14f2e00`。alb-api `WS /logcat/stream`
  + frontend useLogcatStream hook + LogcatTab 组件 · 加 filter input
  ("*:E"/"Tag:V *:S") + tags 短语展开 · ADR-028 (a) 模式扩展到 adb 线 ·
  +6 tests · python websockets 直连后端收 ready frame 验证
- **PR-E 关闭 2026-05-01**：commit `fea4c26`。frontend ShellTab +
  useTerminalSession hook · 双向 WS（sendBytes/sendResize）· 后端
  /terminal/ws 在 M2 已 ship · HITL prompt v1 自动 deny + console.warn ·
  N=3 stream 组件落地，触发 ADR-030 抽象时机评估（不立抽，等 N=4）
- **PR-B 关闭 2026-05-01**：commit `d109a6a`。alb-api 加
  `GET /devices/{serial}/system` endpoint + `device_system()` capability
  拉 10 字段（props/partitions/mounts/block_devices/meminfo/storage/
  network/battery/thermal）· 任一 collector 失败 fallback 不影响其他 ·
  frontend SystemInfoTab 重写接真数据 10 cards · +4 tests
- **PR-G 关闭 2026-05-01**：commit `a1ef214`。alb-api POST 2 endpoint
  /devices/{serial}/screenshot (PNG base64 inline) + /ui-dump (UINode
  tree) · 复用 capabilities/ui.py screenshot/ui_dump · frontend
  ScreenshotTab (img + Download) + UiDumpTab (filter + 缩进 tree) ·
  替换 inspect 2 个 PendingTab · +6 tests
- **PR-F 关闭 2026-05-01**：commit `1e82760`。frontend useMetricsStream
  hook 接现有 /metrics/stream WS（M2 ship 时已有，1Hz device telemetry）
  · ChartsTab 重写 6 charts (CPU/CPU温度/Mem%/GPU/Bat温度/Net rx) 真时序
  · pause/resume/disconnect 控制 · ADR-030 评估升 N=4，但 metrics 协议
  差异化（JSON sample + history snapshot + control_ack）共有逻辑反少，
  继续不抽，等 N=5
- **PR-C.c 候选**：双向 UART 输入（让 web 终端打字到 UART，进 u-boot /
  sysrq）· v1 read-only 留出的 follow-up
- **PR-E.v2 候选**：HITL approve / deny modal UI（v1 auto-deny，遇到
  rm -rf 这种命令直接静默拒绝不友好）· 多 shell session tab strip
- **PR-H 关闭 2026-05-01**：commit `00cc532`。alb-api 加 5 endpoint
  files_route.py（GET /devices/{s}/files ls 解析 / GET /workspace/files
  本地 ls / POST .../files/pull / POST .../files/push / GET
  /workspace/files/download/{path} FileResponse 流式）· HITL 命中
  /system /vendor /data /dev /proc /sys /persist /oem /boot /recovery
  /metadata 返回 requires_confirm=true，前端 modal 二次确认后 force=true
  重提（/data/local/tmp 例外）· frontend FilesTab 双栏 + useFileBrowser
  hook（query+mutation 共用 invalidate）· +22 tests · DEBT-022 batch
  9/9 完成 · inspect 8 tabs 全数接真数据
- **PR-H follow-up 2026-05-02**（code-review + perf-audit 修）：
  - `bd49156` toybox 兼容（去 `--time-style=long-iso` flag）
  - `f64a10c` 安全 + UX：HITL `..` bypass 修（`_is_safe_remote_path` 拒
    `..` + `_is_sensitive_remote` `posixpath.normpath`）/ FilesTab path
    input `useDebouncedValue(300ms)` 防 adb shell 雪崩 / sort-before-truncate /
    transport 字段补全 / +5 regression test = 27 总计
  - `0c74b2c` perf：lazy-load 8 inspect tabs + `manualChunks` 拆 xterm /
    6 dashboard hook 加 `refetchIntervalInBackground:false` / UiDumpTab
    `useMemo`+`useDeferredValue`。主 bundle 722 KB → 346 KB raw（gzip
    206 KB → 110 KB，**-46% 首屏**）· vite >500KB warning 消失
- **关联 ADR**：ADR-028 / ADR-029 (PR-A 落地拍板，PR-B 二 endpoint 模式
  完成) · PR-C.a/b/D/E/F 同 stream pattern · ADR-030 seed (stream hook
  base 抽象，N=5 时再评估) · ADR-031 seed (filesync HITL 在 endpoint 层
  vs PermissionEngine · M1 engine 只识 shell cmd，PR-H 路径前缀 HITL 写
  endpoint，待 engine 加 filesync 规则后下沉)
- **来源**：2026-04-30 user UX 反馈（device 信息）+ 2026-05-01 user 追加
  "现在能显示 uart 打印的内容在 web 上吗" + "uart 调试 adb 调试 web 全部
  开发完全" → PR-A/C.a/C.b/D/E/B/G/F/H 全 ship · DEBT-022 batch ✅
- **PR-C.c 关闭 2026-05-02**：commit `cef3d1f`。原候选"双向 UART 输入"
  ship 完。serial.py 加 `open_session/close_session` 公开 API（共享物理
  UART link，避免两次 _open EBUSY/single-client 拒）· uart_stream_route
  支持 `write:true` 首帧升级 · UartLiveStream 加 [Allow input] checkbox
  + WRITE 警示 pill + xterm.onData → ws.send_bytes（仅 writeEnabled 挂订阅）
  · +3 测试（共 8 个 uart_stream 测试）· 真机部分 smoke OK（协商 +
  read pump + close），write→物理 UART 端到端验证留待板子在 u-boot
  prompt 或启用 sysrq 时再做（当前 Android 无 console getty 不响应）
- **PR-C.c follow-up 2026-05-02**：commit `8a98dfd`。code-review 4 finding
  修：HIGH 1 close-frame race（pump/recv 各发 closed → 加 _CloseState
  shared，outer finally 唯一发，参考 terminal_route 同 pattern）/ MID 2
  close_session docstring 改 "best-effort idempotent" 与实际 swallow
  行为对齐 / MID 3 删 `except (CancelledError, WebSocketDisconnect):
  raise` dead code / MID 4 +2 OSError 路径 regression 测试（10 测试）。
  LOW 5 capability ABC vs hasattr 留 ADR-033 seed
- **PR-E.v2 关闭 2026-05-02**：commit `14fa208`。原候选"PR-E.v2 HITL
  approve/deny modal" ship 完。抽 web/src/components/HitlConfirmModal.tsx
  共享组件（N=2：ShellTab + FilesTab，L-020 抽象时机正好）·
  useTerminalSession 加 onHitl 订阅 + respondHitl 方法（无订阅 fallback
  auto-deny 保兼容）· ShellTab 接 modal 替换 v1 silent auto-deny ·
  FilesTab refactor 用共享 modal · CSS 加 .hitl-modal__* + .btn--danger
  variant · 主 bundle 110 KB 持平（chunk 自然合并）
- **PR-E.v2 follow-up 2026-05-02**：commit `75a07d7`。security-audit
  发现 PR-E.v2 引入的 HITL bypass：approve "eval \$X" for session 后用
  户/agent 改 \$X 内容 → 再敲同字面命中 _session_allowed 直通绕开
  deny-list。修：terminal_guard 加 _has_shell_metachars 检查，allow_session
  路径前命中 metachar 拒绝晋升（仍 approve 一次）+ audit 留痕 + 1
  regression test。L-027 入库

---

## DEBT-023 · xterm.js 全量入主 bundle —— **CLOSED 2026-05-02**

- **severity**：mid（性能 · 主 bundle 首屏 +80 KB gzip 浪费 · 非热路径
  所有用户必须下载 xterm 才能看 dashboard / chat）
- **症状**：`docs/app/assets/index-_hlwuQOg.js` 722 KB raw / 206 KB gzip，
  vite 警告 "chunks > 500 KB"。perf-audit 反查发现 `BufferLine×34` /
  `Viewport×18` / `RenderService` 等 xterm 全量符号入主 entry chunk，
  仅 ShellTab + UartLiveStream 用
- **关闭**：commit `0c74b2c` (perf-audit HIGH #1)。改：
  - `web/vite.config.ts` `rollupOptions.output.manualChunks: {xterm:
    ["@xterm/xterm","@xterm/addon-fit"]}`
  - `web/src/features/inspect/InspectPage.tsx` 8 tabs 全 `React.lazy`
    + `<Suspense>` fallback "loading…"
- **效果**：主 bundle 722 KB → **346 KB raw / 110 KB gzip**（-46% 首屏）·
  xterm 独立 chunk 334 KB / 84 KB gzip 按需加载 · vite warning 消失 ·
  各 tab 独立 4-9 KB lazy chunk
- **来源**：performance-auditor 报告 2026-05-02 finding HIGH #1
- **关联**：ADR-032 (8 tab unmount/remount 不做 keepAlive)

## DEBT-025 · useDashboardQuery wrapper 缺，每个 polled hook 重复 4 flag —— **CLOSED 2026-05-02**

- **severity**：mid（结构性 quality · L-025 lesson 写完仍是"code-reviewer
  检查"，没结构性预防）
- **症状**：N=7 hook 每个都手填 staleTime / refetchInterval /
  refetchIntervalInBackground:false / refetchOnWindowFocus:false ·
  M2 ship 时 useBackends 写对了 pattern，PR-A 加 useDeviceDetails 漏
  了 2 个 flag · 5 天后才被 perf-audit 发现
- **关闭**：commit `ee68887`。新 web/src/lib/dashboardQuery.ts ·
  DashboardQueryOptions interface refetchMs 必填 · useDashboardQuery
  内置 bg gate · 7 hook (Sessions/Tools/MetricsSummary/Audit/Devices/
  DeviceDetails/Backends.manifest) 全 refactor 用 wrapper · useBackends.
  healthQueries 留 useQueries（dynamic refetchInterval 按 error state
  不适合 wrapper API）
- **效果**：未来加 polled hook 用 useDashboardQuery 自动有 bg gate 无法忘 ·
  L-025 enforcement 从"人查"升级到"接口约束"
- **来源**：L-025 (新 hook 必须 sweep bg gate) · perf-audit 2026-05-02
  HIGH #2 fix 后 L-020 N=3 阈值 N=7 早过载
- **关联**：L-025 (sweep refetchIntervalInBackground 规则) · L-020 (N=3
  抽象时机) · DEBT-024 (6 hook 漏 gate · 已修)

## DEBT-026 · UART /uart/stream write 无 size cap —— **CLOSED 2026-05-02**

- **severity**：low（DoS 面 · 误操作放大）
- **关闭**：commit `004962b`。`uart_stream_route.py` 加
  `_MAX_WRITE_FRAME_BYTES = 64 * 1024` · `_recv_loop` 收 binary frame
  时 `if len(data) > cap` 直接 drop + 发 `{type:"write_dropped",
  reason:"frame_too_large", max_bytes, got_bytes}` 通知前端不撕 WS ·
  schema.py 加 write_dropped S→C 帧文档 · +1 regression 测试
- **来源**：security-audit 2026-05-02 finding LOW 4

## DEBT-028 · 4-agent 联合 audit 找到 9 HIGH —— **CLOSED 2026-05-02**

- **severity**：critical（本来该在 PR-H/C.c/E.v2 ship 时被发现的）
- **背景**：用户提"agent 团队该用上 + 审查者要真查出问题 + 越用越聪明"，
  并行派 4 reviewer agent (architecture / code / ui-fluency / functional)
  扫今日 21 commits。共 9 HIGH + 多 MID/LOW
- **关闭**：2 commit
  - `b33c1c4` backend 5 fix：terminal_guard audit log 用 effective_session
    + _SHELL_METACHARS 加 \n\r + files_route Pull/Push timeout 300s +
    _FILE_OP_LOCKS per-serial lock + metrics_route _send_loop 不吞异常 +
    outer 发 server_error closed 帧
  - `53e984d` frontend 4 fix：HitlConfirmModal cancelRef autoFocus +
    capture-phase ESC + Enter→Cancel + tabIndex=-1 + InspectPage Suspense
    fallback minHeight:480 + WRITE pill 红底白字 + UART helper text 替
    title + ShellTab WS 断 modal 自动关
- **效果**：HITL session forensic 留痕一致 / Pull/Push 无穷挂死消除 /
  并发 push 撞 adb 消除 / Charts WS server crash 前端能看到原因 / modal
  键盘 a11y 通过基线 / Suspense 切 tab 不再 480px CLS / WRITE 警示
  视觉对比度过 WCAG AA / Shell 断后用户不会点 silent no-op
- **DEBT-NEW-A** (arch-reviewer 提的 HitlConfirmModal a11y focus trap)
  作为 HIGH 2/3 一并修，不再单独立条
- **来源**：4 agent 输出 `.claude/reports/{perf,functional,security}-audit-*.md`
  + arch/ui-fluency/code-reviewer 走 agent 直接输出
- **关联**：L-027 / L-028 / L-029 / ADR-033 method 名固化补 / DEBT-NEW-A
  并入修

## DEBT-027 · UART/PTY → xterm.js OSC 注入面文档化 —— **CLOSED 2026-05-02**

- **severity**：low（受信源前提下 · 当前 xterm 默认 config 已安全）
- **关闭**：commit `004962b`。UartLiveStream + ShellTab 在 `new
  Terminal({...})` 上方加 SECURITY 注释块：
  - 禁 `allowProposedApi: true`（启用 OSC 52 clipboard write =
    UART/PTY 输出可写浏览器剪贴板）
  - 禁 `linkHandler` 自动跳转外部 URL
  - 当前默认 config 是审过的安全集合
- 代码无运行时变化（默认就安全），加注释防回归 · code-reviewer 后续
  grep `allowProposedApi|linkHandler` 出现即标
- **来源**：security-audit 2026-05-02 finding LOW 5

---

## DEBT-024 · 6 dashboard hook 漏 `refetchIntervalInBackground:false` —— **CLOSED 2026-05-02**

- **severity**：mid（性能 · 浏览器 tab 切走时仍 30s polling，每分钟 12
  无效 HTTP 请求 + DEBT-008 events.jsonl 全量扫被放大）
- **症状**：6 hook (`useSessions`/`useTools`/`useMetricsSummary`/
  `useAudit`/`useDeviceDetails`/`useDevices`) 全部缺 `refetchIntervalInBackground:false`，
  只有 `useBackends`（M2 ship 时写）有。新 hook 按"copy useSessions
  pattern"思路写，bug 等比例传染
- **关闭**：commit `0c74b2c` (perf-audit HIGH #2)。6 hook 全加 flag，
  对齐 useBackends pattern
- **效果**：隐藏窗口 zero-value polling 累计 ~720 req/h 浪费 → 0 ·
  events.jsonl 全量扫频率减半
- **来源**：performance-auditor 报告 2026-05-02 finding HIGH #2
- **关联**：L-025 (新 useQuery hook 必须 sweep refetchIntervalInBackground /
  refetchOnWindowFocus 两 flag) · DEBT-008 (events.jsonl 全量扫已知)

---

## DEBT-029 · functional audit MID 收头 8 项 —— **CLOSED 2026-05-06**（含 MID-6 4-commit batch）

- **severity**：mid（无 user-visible weirdness，但用户感知边角缺口 +
  极端输入下沉默/慢）
- **背景**：2026-05-02 functional-audit 报告了 8 MID + 5 LOW，HIGH 9 项
  早班 ship 修完后晚班再起一轮收头。挑 4 项独立 / 低风险 / 用户感知强
  的先做，剩 4 MID + 5 LOW 进 backlog 不强求一次清完
- **关闭**：commit `dbf5dca`（fix: functional MID 收头 4 项 #17 part 77/N）
  - **MID-1 logcat invalid filter** — `_validate_filter_spec` 校验
    `<TAG>:<LEVEL>` 格式，LEVEL ∈ V D I W E F S；坏 spec 直接发
    `closed/bad_filter` 关帧，不再静默"看似成功 logcat 启不起来"。
    前端 useLogcatStream 把 `bad_filter` 也归入 error 显错
  - **MID-2 UART stream_error stale bytes** — error/ended 状态加 "Clear
    & reconnect" 按钮，先 xterm.clear() 再重连，避免残留字节被误读为
    新输出。普通 Clear 按钮保留
  - **MID-7 metrics set_interval pathological values** — setter 显式
    reject NaN（**触发 L-030**）；control_ack 加 `clamped: true` +
    `requested_s` 让前端可知"你的值被钳到 [0.1, 60]"。inf / -inf /
    1e9 / 'not-a-number' 全测覆盖。**注**：同日 retroactive grep 实测
    发现 Python `max(LO, min(HI, nan))` 在这个特定顺序下实际返回 HI（不
    传染 NaN），所以 NaN reject 不是"修 bug"而是"防御性补强"（避免反向
    顺序重构 / 跨语言移植被坑）；UX `clamped` flag 才是真改进。L-030 v2
    已修订规则按语言+顺序分级
  - **MID-3 workspace iterdir** — files_route /workspace/files 改用
    `os.scandir`，DirEntry 缓存 is_dir/is_symlink 元信息；先排序 +
    截断到 _MAX_ENTRIES 再 stat 保留项。50k 文件目录省 48k stat()
    调用（每个 stat 是 syscall，省下来非常显著）
- **测试**：780 pass（767→+13: 8 logcat + 3 metrics + 2 files）/ typecheck 0 /
  sensitive 0 / build 5s / 主 bundle 110 KB gzip 持平
- **效果**：4 个边角隐患修完 — 用户拼错 logcat filter 立刻看到原因 /
  UART 错恢复一键 / metrics 病态值有反馈 / workspace 大目录响应快
- **MID 全数关闭**（**CLOSED 2026-05-06** · MID-6 真 GAP 修完 · 其余 retroactive 锁定）：
  - **MID-6 Files Pull/Push 无 Cancel 无 progress**（4-commit batch 89-92 ship）：
    - commit 89 `ec79795`: AdbTransport push_stream/pull_stream + 10 测试 ·
      解析 adb stderr `[N%]` 进度行 + stdout 末尾 `(NNNN bytes)` summary ·
      cancel via aclose() + spawn_stream finally SIGTERM/SIGKILL adb · 触发
      L-031（嵌套 async generator outer aclose 不传染 inner，需显式 inner.aclose）
    - commit 90 `7b9afc0`: WS endpoints `/devices/{s}/files/push/stream` +
      `/pull/stream` + 10 测试 · L-026 单 close-frame outer-finally · L-022
      vite proxy `/devices` ws:true 同步 · 触发 L-031（suppress(Exception)
      不抓 CancelledError 在 3.11+）
    - commit 91 `6afe3be`: L-031 lesson + code-reviewer grep checklist
      升级（reviewer "越用越聪明" 第 8 条 grep）
    - commit 92 `01fe778`: 前端 useFileTransferStream hook + FilesTab 集成
      进度条 + Cancel 按钮 · 删旧 useFileTransfers HTTP mutations · CSS
      确定/不确定双模式动画
  - retroactive corrections（2026-05-05）：
    - MID-4 Range：Starlette FileResponse 1.0 原生支持 Range（206/416/
      Accept-Ranges/Content-Range），regression 锁在 test_files_route.py（commit `ead4e90`）
    - MID-5 PTY exit：stdout bytes 实时 send_bytes，close-frame 带 exit_code，
      rationale 走 xterm 不在 JSON。regression 锁在 test_terminal_route.py
    - MID-8 WS heartbeat：uvicorn 默认 ws_ping_interval=20s / ws_ping_timeout=20s
      已开，alb-api 不 override。regression 锁在 test_meta_route.py
- **测试 841 pass**（5/02 780 → +61：MID 收头 + retroactive regression + Anthropic）
- **未关 LOW**（5 项进 backlog · 见 functional audit 报告）
- **来源**：`.claude/reports/functional-audit-2026-05-02.md` MID 1-8
- **关联**：L-030（NaN 钳位反模式 · 同 commit 触发）/ L-031（suppress 不抓
  CancelledError + 嵌套 async generator aclose 不传染 · MID-6 触发）/
  DEBT-028（早班 HIGH 9 关 · 本条是同 audit 的 MID 收头）

---

## DEBT-030 · useLiveSession scaleSparkPoints 无 isFinite filter · L-030 retroactive grep 唯一真发现 —— **CLOSED 2026-05-02**

- **severity**：low（依赖后端 reducer 不发 NaN 的隐性契约 · 当前后端
  实际不会发，但 contract 没显式守护）
- **关闭**：commit pending（M3 step 2 收官后开新 work）。3 行修：
  `samples.filter(Number.isFinite)` 在算 peak 前先净化，empty 化为 []
  返回 [];scaleSparkPoints 注释加 L-030 v2 引用解释为啥 JS Math.max
  不能用 Python 那套"max(LO, min(HI, x)) 顺序安全"假设
- **位置**：`web/src/features/dashboard/useLiveSession.ts:175-181`
  ```ts
  function scaleSparkPoints(samples: number[]): number[] {
    if (samples.length === 0) return [];
    const peak = Math.max(SPARK_MIN_CEILING, ...samples);
    return samples.map((rate) => {
      const norm = peak > 0 ? rate / peak : 0;
      return Math.max(0, Math.min(SPARK_HEIGHT, SPARK_HEIGHT * (1 - norm)));
    });
  }
  ```
- **风险**：`samples` 来自 WS 帧 reducer，若任何一项是 NaN：
  - `Math.max(SPARK_MIN_CEILING, ...NaN)` = NaN（JS Math 真传染）
  - `peak = NaN` → `rate / peak` = NaN → `1 - NaN` = NaN
  - 最后 `Math.max(0, Math.min(SPARK_HEIGHT, NaN))` = NaN
  - SVG `<polyline points="x,NaN x,NaN ...">` 不渲染 / 报错
- **修法**（一行 filter）：
  ```ts
  const finiteSamples = samples.filter(Number.isFinite);
  if (finiteSamples.length === 0) return [];
  const peak = Math.max(SPARK_MIN_CEILING, ...finiteSamples);
  ```
- **来源**：2026-05-02 L-030 retroactive grep 全仓扫 7 命中中唯一
  真问题（其他 6 个 SAFE：5 个 Python 顺序安全 / 1 个 JS 上游
  `Number() || DEFAULT` 兜底）
- **是否计划修**：低优先 · 现在没爆（reducer 不发 NaN）；防御性补强
  时一并 ship。**不阻塞下一票**
- **关联**：L-030 (NaN 钳位语言 + 顺序分级 · 本条是 retroactive 唯一
  真发现) / DEBT-029 (同日早班 metrics NaN reject 是同形态防御)

---

## DEBT-032 · `_safe_resolve_*` × 3 同形 path-resolve helper —— **CLOSED 2026-05-08**

- **severity**：mid（架构 N=3 抽 base 阈值教科书触发；arch-reviewer
  2026-05-08 finding · 三处 helper docstring 互相点名"Mirror X 同
  pattern"）
- **位置**：`src/alb/api/screenshots_route.py:_safe_resolve_screenshot`
  + `src/alb/api/uart_route.py:_safe_resolve_capture` +
  `src/alb/api/files_route.py:_resolve_workspace_path`（参考实现）
- **关闭**：commit `ee1de9c`（2026-05-08）。抽 `src/alb/infra/safe_path.py
  ::resolve_under(base, name, *, is_valid_name, ...)` 公共 helper，三
  层防御（name gate + symlink reject + resolve.relative_to）。
  screenshots_route + uart_route 缩到 3 行调用，删 50 行同形代码。
  `files_route._resolve_workspace_path` 保持现状（语义不同——多段 rel
  path vs 单 filename 卡槽）
- **来源**：architecture-reviewer 2026-05-08 跑 5/06~5/08 累 16 commits
  audit · finding 1（5 维评审中"模块边界"维度）
- **关联**：L-020 (N=2 不抽 base，N≥3 才抽 · 本条是教科书触发条件)

---

## DEBT-033 · mockup v3 扩 inspect 4 子 tab baseline —— **CLOSED 2026-05-09**

- **severity**：high（mockup-baseline-checker 标 high）但**非紧急**——
  inspect 子 tab 的 BEM class（uart-tab__* / screenshot-tab__* /
  uidump-tab__* / files-tab__*）从 PR-C/F/G 时期就在 React 单边推
  进，mockup v2 只覆盖 dashboard + inspect subnav。已是历史欠债的
  延续，不是 5/06~5/08 新增违规
- **位置**：`docs/webui-preview-v2.html`（原仅画 dashboard + inspect
  subnav + sys-grid + charts-grid）
- **关闭**：2026-05-08 part 123 + 2026-05-09 part 124~126 共 4 commit
  逐 step ship：
  - step 1 ScreenshotTab（commit `dccf80a` · 2026-05-08）—— sidebar +
    Android phone placeholder SVG viewer + .uart-tab__* 共享骨架立
    template
  - step 2 UART capture（commit `1156dfa` · 2026-05-09）—— duration
    input + sidebar with delete trash + .uart-tab__viewer 黑底 mono
    pre 文本（21 行 kernel printk demo）
  - step 3 UI Dump（commit `7caf1f6` · 2026-05-09）—— filter input
    带 ✕ + counter aria-live + 14 row 缩进 tree（class/id/text/
    bounds/click pill 颜色编码）
  - step 4 Files（commit (待提) · 2026-05-09）—— dual-pane (device +
    workspace) + Pull/Push/Download 按钮 + .files-tab__preview 内联
    预览（last_kmsg 风格 stack trace demo · min-height 240 防 CLS）
- **设计决策**：mockup 不重复 React side 所有 modifier（hover-only
  delete / armed two-step / virtualized list 等），只画 baseline
  视觉骨架 + 注释引用 components.css 完整 modifier 位置——避免
  mockup 与 React side 双向同步债
- **三道闸**：4 step 各自 verify.py / visual-audit.mjs / screenshot.mjs
  全过 · 截图分别存
  `.claude/reports/screenshots/debt033-step{1,2,3,4}-mockup.png`
- **来源**：mockup-baseline-checker 2026-05-08 audit · 主 finding
- **关联**：feedback `feedback_react_ui_design_baseline.md`（"先
  mockup 走三闸 → React 照搬 class"原则） · L-001 (React UI 必须以
  mockup HTML 为视觉基线)

---

## DEBT-034 · architecture.md REST envelope 三态约定缺文档化 —— **CLOSED 2026-05-08**

- **severity**：low（架构层规范化债，不阻塞功能）
- **位置**：`screenshots_route.py:list_screenshots/read_screenshot`
  + `uart_route.py:read_capture/delete_capture` +
  `files_route.py:list_device_files/workspace_files/workspace_download
  /workspace_preview` 等多 endpoint
- **现象**：当前 REST endpoint 三种 response envelope 形态并存：
  (a) `{ok, ..., entries|screenshots|...}` 200 + ok-flag
  (b) `FileResponse` 直接抛 `HTTPException(4xx)`
  (c) `device_files` 永远返 200 + `ok: false + error` 字段
  同一文件 files_route 三种都用过；前端 useQuery 行为不同（
  HTTPException 触发 onError，200+ok=false 走 isSuccess+渲染 error
  文案）
- **关闭**：commit (待提) 2026-05-08。`.claude/knowledge/architecture.md`
  加 "REST envelope 三态约定（DEBT-034 close 2026-05-08）" 段，明确：
  - (a) 200 + `{ok: true, ...payload}` 用于成功 read/list
  - (b) 200 + `{ok: false, error}` 用于 device-side / 上游失败
  - (c) HTTPException(4xx/5xx) 用于输入不合法 / 真错
  - 例外：FileResponse 二进制流 / WebSocket 长连接 envelope 不适用
  - 5xx detail 不能 leak 路径（generic message + server log 留 trace）
  - 各类已知 endpoint 实例
- **来源**：architecture-reviewer 2026-05-08 finding 2

---

## DEBT-035 · L-meta-001 候选 · reviewer 新增类规则必带 grep pattern —— **CLOSED 2026-05-08**

- **severity**：low（meta-lesson 归档级，不影响代码）
- **位置**：`.claude/knowledge/lessons.md`
- **现象**：L-014 / L-025 / L-032 / L-033 都是"新 X 时漏一组 sweep
  规则"形态（mcp tool docstring / useQuery bg gate / sidebar a11y /
  async io-to-thread）。这 4 条已经有共同骨架："新增 X 时必跑 sweep
  checklist {a, b, c}"。但 meta-pattern 没正式入档，新 lesson 写时
  容易漏 grep pattern（变成纯描述规则随项目 N 增长稀释成"依赖人
  记忆"）
- **关闭**：commit (待提) 2026-05-08。`.claude/knowledge/lessons.md`
  加 L-meta-001："新增类规则" lesson 必带四件套（可执行 grep pattern
  + 反面教材 + 正例 + agent checklist 同步），5/06~5/08 part 91/105/
  109/113 实操经验正式入档
- **来源**：architecture-reviewer 2026-05-08 finding · meta-观察

---

## DEBT-036 · session listing helpers 跨 surface drift 风险

- **severity**：low（v1 复制是有意识决策，N=3 时必抽）
- **位置**：`src/alb/cli/session_cli.py:43-95` vs `src/alb/api/sessions_route.py:28-65`
- **现象**：part 130 (`c39ccbe`) 加 `alb session list/show/replay` 时，
  4 个文件系统 helper（`_count_lines` / `_mtime_iso` / `_load_meta` /
  `_summarize_session`）与 `api/sessions_route.py` 字节级同形。当时
  按 L-020 决定不抽 base，理由：API 端是 `async def` 调 sync FS（要
  `to_thread` wrapper），CLI 端是 sync 直调，两边职责差异让 N=2 抽出来
  反而在异步边界变复杂
- **触发关闭**（任一）：
  - MCP `session_list` tool 出现（N=3 surface），抽 `agent/session_index.py`
    暴露 `scan_sessions() -> list[SessionSummary]` (dataclass，非 dict)
  - 两端字段定义首次 drift（`last_event_ts` schema 偏离 / 新字段单边添加）
- **修法（关闭时）**：抽 `agent/session_index.py` · CLI 直调 · API 包
  `await asyncio.to_thread(scan_sessions)` 满足 L-033
- **来源**：architecture-reviewer 2026-05-09 self-audit finding 3 ·
  part 134 时点 N=2 hold

---

## DEBT-037 · `~/.config/alb/config.toml` 写入非原子 + 0644 模式

- **severity**：low（reliability + future-secret hardening）
- **位置**：`src/alb/cli/setup_cli.py:_persist_serial_config`（part 128 `77c22a0`）
- **现象**：
  - 写入流程 `path.exists() → tomllib.load → mutate → mkdir → open(wb) →
    tomli_w.dump`。`tomli_w.dump` 中途崩溃（disk full / SIGTERM / OOM）
    会留下 truncated TOML，下次跑 `--save` 触发 `tomllib.TOMLDecodeError`
    被 `typer.BadParameter` 拦截要求"manually fix"，用户原配置已损坏
  - 默认 0644 权限，当前 `[transport.serial]` 不含 secret 但同文件
    可能未来加 `[transport.adb] server_socket` / `[backends.openai] api_key` /
    ssh key path，team server 多人共用场景同机其他用户能读
- **修法**：
  - 原子写：`tempfile.mkstemp(dir=path.parent) → write → os.replace(tmp, path)`（POSIX 原子）
  - 权限：`os.umask(0o077)` 包写入 + `path.chmod(0o600)` + `path.parent.chmod(0o700)`
- **触发关闭**（任一）：
  - 任何 secret 字段加入 config.toml（api_key / ssh_key_path 等）
  - 用户报告 `--save` 中断后 config 损坏
  - 下次 security-audit batch
- **来源**：security-and-neutrality-auditor 2026-05-09 self-audit findings 2+3 ·
  part 134 时点低优 hold

---

## DEBT-038 · mockup v2 主基线落后 React 12 tab —— **CLOSED 2026-06-17**

> 关闭（MBC-1）：缺基线的主交互页补了 5 个 per-page mockup
> docs/webui-preview-v2-{playground,audit,sessions,inspect-console,screenshot}.html，
> 各过 verify/visual-audit/screenshot 三闸 + 人眼审；9 个受影响 React 文件的
> app-class 全量 grep 收口，均在 docs/*.html 命中。见 L-058 / [[round11-sweep-findings]]。

- **现象**：`docs/webui-preview-v2*.html` 系列 mockup 是 v2 时代（dashboard
  + 4 tab：System / Charts / UART / Logcat），React 实际已扩展到 12 tab
  （+Shell/Screenshot/UiDump/Files/Power/LogSearch/Diag/App）+ activity
  bar Doctor。mockup-baseline-checker 现在审 React 时找不到对应 mockup,
  漏掉视觉一致性检查。
- **影响**：
  - L-028 规则（class 名照搬 mockup）失去校准源
  - 新 tab 视觉风格全靠 reviewer 现场拍 · 没基线
  - 用户改设计想从 mockup 看效果, 拿不到完整图
- **建议方案**：mockup v3 sweep —
  - 起新文件 `docs/webui-preview-v3-inspect-{shell,screenshot,ui-dump,
    files,power,log-search,diag,app}.html`
  - 复用 anthropic.css design tokens
  - 跑 sky-skills 三道闸（verify.py / visual-audit.mjs / screenshot.mjs）
  - 用 mockup-baseline-checker 重审 React 与新 mockup 的一致性
- **触发关闭**（任一）：
  - 主对话单批 sweep · 估 1-2 天
  - 下一个 visual audit batch 命中"无 mockup baseline"
- **来源**：5/18 batch 8 agent audit 间接发现（mockup-baseline-checker
  没找对应 v2 文件）· 2026-05-21

---

## DEBT-039 · `/sessions/{id}` 全 load `messages.jsonl` · 长 session 内存峰值高

- **现象**：`SessionDetailPage` 当前一次 fetch 整个 `messages.jsonl`,
  长 session（>1000 turn / 数十 MB）会让 endpoint 一次性 read + parse
  全部, 然后前端一次性 render 全部 `<MessageNode>`。
- **影响**：
  - 后端: O(N) 内存 + O(N) JSON parse · 100 MB jsonl 跑 OOM 风险
  - 前端: VirtualScroller 没用, 渲染 1k+ message 元素的 layout 慢
- **建议方案**：
  - 后端 add `GET /sessions/{id}?offset=N&limit=M` (cursor / line offset)
  - 前端 SessionDetailPage 改用 `@tanstack/react-virtual`（同 PackageList
    模式 commit C `1d18299`）+ 增量 fetch
  - 拿 `match_count` / `total_turns` 作 pagination 元数据
- **触发关闭**（任一）：
  - 实测某 session > 50 MB 触发 endpoint 慢 / 浏览器卡
  - 用户报告"打开会话卡很久"
  - 下次 perf-audit batch
- **来源**：5/18 batch architecture-reviewer LOW finding · 2026-05-21

---

## DEBT-040 · activity bar 缺 Doctor / Power 顶层入口

- **现象**：`ActivityBar.tsx` 8 个顶层入口 `Dashboard / Chat / Terminal
  / Inspect / Playground / Sessions / Files / Audit`, Doctor 只能从
  Dashboard quick-action 进, Power 只能 `/inspect/power` 嵌套两层进。
  常用功能埋深, 操作冷启动慢。
- **影响**：
  - 用户首次想看 doctor / power 找不到
  - 快捷键 / URL 直跳路径不直观
- **建议方案**：activity bar 重排:
  - 把 Doctor 升顶层（已有 `/doctor` route, 只缺图标 + 排序）
  - Power 因属于 device-scoped 操作, 保留 Inspect 子 tab, 但 Dashboard
    quick-action 新增 "Reboot device"
  - 顺手考虑 SubNav-style "secondary nav" 让常用 inspect tab 也能挂顶
- **触发关闭**（任一）：
  - 用户实际反馈 "找不到 Doctor"
  - 下次 ui-fluency-audit batch
- **来源**：5/18 batch ui-fluency MID + 2026-05-21 实战

---

## DEBT-041 · ScreenshotTab 缺 delete · 服务端缺 DELETE endpoint

- **现象**：`/inspect/screenshot` 截图历史 sidebar 只能查看 / 下载, 没
  删除按钮。同行 UartCaptureView 早就有 2-step delete 模式 (commit
  D `f887198` 的 useArmedAction 抽象用上了)。
- **影响**：
  - 抓久了一台设备能堆几百张, sidebar 翻页慢
  - 用户没法清理失败 / 老旧截图, 只能 ssh 进 workspace 手 rm
  - workspace 占盘没上限 (PNG ~MB 级)
- **建议方案**：
  1. server 加 `DELETE /devices/{serial}/screenshots/{name}` (走
     `is_safe_device` + `_resolve_screenshot_path` 复用现有 validation)
  2. `lib/api.ts` 加 `deleteScreenshot(serial, name)` helper
  3. `useScreenshots.ts` 加 `useDeleteScreenshotMutation` (成功后
     invalidate ["screenshots", device])
  4. ScreenshotTab sidebar 每行加 useArmedAction 2-step delete 按钮
     (与 UartCaptureView 同模式 · DRY 复用)
- **触发关闭**（任一）：
  - 用户报告 workspace 占盘 / sidebar 慢
  - 下次 ui-fluency-audit batch
- **来源**：5/22 audit Web/UART/ADB 盘点 MINOR · 服务端无 endpoint
  是阻塞原因 (commit T 时点放弃)

---

## DEBT-042 · ScreenshotTab 缺 tap-zoom / crop / annotate

- **现象**：截图 viewer 是固定 `<img>`, 既不 click-to-zoom (无法看
  细节像素), 也无 crop 工具 (无法只截图中关键 UI 元素), 也无 annotate
  (无法画框标注 bug)。
- **影响**：
  - 调 UI bug 时为了看清像素得 ssh download + 本地查看
  - 团队协作分享时手机截图 + 标注是基操, UI 没给
- **建议方案**：
  - tap-zoom: 第一档 `<img>` 加 onClick → modal 显原图 + 滚轮 zoom
  - crop: 加 lasso select + "save crop as new entry" (POST 新文件)
  - annotate: tldraw / Excalidraw 嵌入式画板, save 成 SVG overlay
- **触发关闭**（任一）：
  - 用户主动 ask (低优 hold)
  - 团队跨人协作场景需求
- **来源**：5/22 audit · 优先级低于 D-041 因不影响阻塞场景

---

## DEBT-043 · UiDumpTab 平 list · 无 tree 展开 · 无截图叠加

- **现象**：UI dump 服务端返结构化 view hierarchy, 客户端只显平铺
  node list, 没 tree 展开/折叠, 也不在 screenshot 上叠加边框可视
  化。doc 自己写 "Bounds-on-screenshot overlay 留 v2"。
- **影响**：
  - 大 UI 树 (几百 node) 全列出来翻不动
  - 找 button 的 bounds 时, 用户拿坐标手算, 看不到 "这是哪个矩形"
  - 与 Android Studio Layout Inspector 体验差太多
- **建议方案**：
  - 第一档: tree expand/collapse + 当前 node 高亮父链 (类似
    DevTools elements panel)
  - 第二档: 在 screenshot 上叠 SVG `<rect>` 边框, 点 rect 跳 tree
    节点 (双向同步)
  - 第三档: click-node-to-tap (右键发 `input tap x y` shell command)
- **触发关闭**（任一）：
  - 用户调真机 UI bug 时痛点反馈
  - Files 重构后顺手 attack
- **来源**：5/22 audit · UI 已可用但远未达 Layout Inspector 水准

---

## DEBT-044 · FilesTab no rsync · server 也无 rsync endpoint

- **现象**：FilesTab 当前只 push / pull (per-file). v1 router stub
  描述说 "push / pull / rsync", 但 rsync **不在 server 实现**, 是
  doc 口误 / 未来 promise。
- **影响**：
  - 大目录批量传输只能 N×push / N×pull, 慢 + 无 resume
  - 跨设备 sync (镜像 /sdcard 到主机) 走 adb rsync 是常用模式, web
    没接
- **建议方案**：
  - server 加 `POST /devices/{serial}/rsync` (subprocess wrap adb +
    rsync · 或纯 Python paramiko_rsync 实现 · 注意 path-traversal)
  - WS `/devices/{serial}/rsync/stream` 流 stderr/stats 给前端
  - 客户端 FilesTab 加 rsync tab/section · 选 src/dst + dry-run /
    delete / preserve flags + 实时进度
- **触发关闭**（任一）：
  - 用户跨多 GB workspace sync 需求
  - 团队"我要镜像我手机 /sdcard 整个 DCIM" 类型场景
- **来源**：5/22 audit · v1 stub doc 撒谎 (router 写有但 server 没有)
  是 audit 翻出来的

---

## DEBT-045 · `_terminal /files` activity-bar redirect 长期归宿

- **现象**：commit S `c829895` 把 `/terminal` `/files` 改成 redirect
  到 `/inspect/$tab`, activity bar 两栏成"幽灵路由"。点完 URL swap
  到嵌套路径; 浏览器后退键 + redirect 可能死循环。
- **影响**：当前可用, 但 URL 体验奇怪 (用户点 "Terminal" → URL 变
  `/inspect/shell` 后退按钮把人弹回 `/terminal` 又 redirect)。
- **建议方案** (二选一明确入档):
  - (a) 删 `/terminal` `/files` 路由本身, activity bar 两栏直接指
    `/inspect/shell` `/inspect/files`
  - (b) 接受 redirect 是临时态; 触发条件 = Terminal 要 multi-tab /
    Files 要拖拽上传时拆独立页
- **触发关闭**：用户报告后退键 bug / 主对话决策拆独立页
- **来源**：5/22 audit arch MID#2

---

## DEBT-046 · PlaygroundPage 右 metrics column 待落地

- **现象**：commit R 落 Playground 时 docstring 承诺 3-column 实际
  实现 2-column · 右 metrics rail (累计 tokens/s / 耗时 / 错误率
  实时 chart) 缺。当前 metrics 内嵌 done message 下方, 看不到累计
  趋势。
- **影响**：调采样参数 (temp / top_p) 对吞吐影响不直观; 多 turn 后
  看不到 per-backend 累计 metrics。
- **建议方案**：grid-template-columns 加第 3 列 (~240px), 内嵌
  `<MiniSparkline>` 显近 60 次 done 事件的 tokens/s 趋势。Backend
  health 也在这里 (调 useBackendHealth)。
- **触发关闭**：用户实际 Playground 多 turn 调参时反馈 / 下次 Playground
  feature 改动
- **来源**：5/22 ui-fluency LOW#6 (注释 vs 实现)。commit AE 改了
  docstring 但 UI 没补

---

## DEBT-047 · lib/ws.ts path-keyed dedup + late-joiner snapshot replay —— **CLOSED 2026-05-26 (AP-1 / AP-2)**

- **现象**：注释撒谎 (commit Y 已订正), 真问题: AuditPage + Dashboard
  同屏开 2-3 条 `/audit/stream` socket · snapshot bandwidth ×N · 服务
  端 bus fan-out N 次。
- **影响**：localhost / LAN 下不阻塞 (snapshot ~40 KB/socket), 但
  setState 频率 N 倍, 50+ event/s deltas 时浪费 CPU。
- **落地** (AP-1 / AP-2 · ADR-045 / L-055)：
  - `lib/ws.ts` 加 `Map<path|shareKey, PoolEntry>` · `connect(path,
    {shareKey})` 同键复用一条 underlying WebSocket · view.close()
    refcount-- · 至 0 才真 close + 删 entry
  - 缺 `shareKey` 走 `soloConnect()` · 保留 useWsChatStream 单 turn
    隔离不池化的契约
  - 新 subscribe 在 underlying 已 OPEN 时排 microtask · 给 listener
    回放 `{kind:"open"}` + 缓存的 `{type:"snapshot"}` JSON 事件 ·
    晚到 view 不靠服务器再吐就能 converge
  - useAuditStream 走 AM-3 已 pre-wire 的 `shareKey =
    JSON.stringify({minutes, includeMetrics})` · 0 caller diff
  - 17 spec @ `web/src/lib/ws.test.ts` 钉契约 (solo / pool / refcount
    / late-joiner / unsubscribe-race / snapshot-only-overwrites-snapshot)
- **来源**：5/22 audit code-r MID#3 + perf HIGH#1

---

## DEBT-048 · usePlaygroundChat / DevicePicker / useElapsedSeconds / ScreenshotZoom 缺测试

- **现象**：5/22 batch 新增 4 个新 hook + 1 个新 modal 组件, 全没单测。
  特别 usePlaygroundChat 含 WS lifecycle + done/error/close race +
  之前 commit Y 修了 stale closure bug, 没测覆盖 = 下次复发只能靠
  audit。
- **影响**：再修同款 bug 时无回归保护; 重构 connect/usePlaygroundChat
  风险高。
- **建议方案**：先起 vitest + @testing-library/react · 然后:
  - `usePlaygroundChat`: mock connect → emit token×3 + done → 验
    delta/done/status · close-before-done → status=error
  - `useArmedAction`: arm → trigger fire / 8s auto-disarm / disarm
    timer cleanup
  - `useElapsedSeconds`: active true → tick 0,1,2... · false 停
  - `DevicePicker`: keyboard ArrowDown 改 focusIdx · Enter setDevice
  - `ScreenshotZoom`: wheel zoom 钳位 · Esc close
- **触发关闭**：vitest setup 完成 + 至少 usePlaygroundChat 测覆盖
- **来源**：5/22 audit code-r MID#6 + arch 间接

---

## DEBT-049 · /api/diag/artifacts API_VERSION / docs note 漏

- **现象**：commit O 改 diag `path` 字段从绝对路径变 workspace-rel
  POSIX, 是 web API breaking · 没 bump API version 也没在
  `docs/api/` 写 changelog。
- **影响**：任何外部脚本 / Web UI 早期版本 / 第三方集成读
  `data.bugreports[0].path` 当绝对路径用的会找不到文件。MCP /
  CLI 不走该端点 (确认)。
- **建议方案**：
  - (a) `docs/api/diag.md` (或类似入口) 加 "since 2026-05-22:
    `path` is workspace-relative POSIX" 注释
  - (b) 若项目有 API_VERSION 字段, bump minor
  - (c) `schema.py` REST 列表 `/diag/artifacts` 的 description 加
    "path field is workspace-rel"
- **触发关闭**：docs 入档 / 下次 API breaking change 一起做
- **来源**：5/22 audit code-r MID#4 + arch MID#4

---

## DEBT-050 · DELETE pattern N=2/3 抽 `idempotent_delete` helper

- **现象**：screenshots DELETE (commit V) 显式 try/except 走 idempotent;
  uart `DELETE /uart/captures/{name}` (`uart_route.py`) 不 catch
  FileNotFoundError, 抛 500。第 3 处 DELETE 出现时立刻抽。
- **影响**：API 语义不一致; 用户双击 delete uart capture 会得 500,
  delete screenshot 得 ok。
- **建议方案**：`infra/safe_path.py` 加 `idempotent_delete(base,
  name, name_validator) -> bool`:
  - 复用 `resolve_under` 的路径校验
  - FileNotFoundError → return False (removed=False)
  - 其它 OSError → 500
- **触发关闭**：N=3 DELETE 端 (logcat captures / 任意 third 端) 出现
- **来源**：5/22 audit arch MID#3

---

## DEBT-051 · ScreenshotZoom promotion to `components/`

- **现象**：commit W ScreenshotZoom 留 `features/inspect/`, N=1。
  DEBT-043 二档 (UiDumpTab screenshot overlay) 需要复用 zoom modal,
  那时 N=2 → 抽 `components/ImageZoom.tsx`。
- **影响**：当前 N=1 不抽是对的 (避免 premature abstraction)。
- **建议方案**：等 UiDumpTab overlay 落地时一起 `git mv` + sed
- **触发关闭**：DEBT-043 二档 (overlay) 开工时同步
- **来源**：5/22 audit arch LOW#5

---

（新债由主对话评估后追加；agents 不直接写）

## DEBT-052 · React Test StrictMode wrapper baseline

- **现象**：5/25 code-r MID-6 / arch HIGH-6 · DevicePicker /
  useArmedAction / useElapsedSeconds / usePlaygroundChat 4 spec 都用
  普通 `renderHook` / `render` · prod 在 `main.tsx:32` 有
  `<StrictMode>` 包整个 app · dev 模式 effect 跑两遍 · spec 跑一遍 ·
  effect cleanup / timer 重创 / useEffect 双 invoke 副作用永远测不到
- **影响**：DevicePicker focus effect 在 StrictMode 下 wasOpenRef 是否
  被错改 true 触发误归位 / useArmedAction 8s timer 是否在 cleanup
  re-effect 后重复创建 / useElapsedSeconds setInterval 是否泄露 —
  4 个 race candidate 都 dark
- **建议方案**：
  - test/setup.ts 加 `import { StrictMode } from "react"`
  - 在 `renderHook(hook, { wrapper: StrictMode })` / `render(<Comp />,
    { wrapper: StrictMode })` 全局默认包
  - 或最低门槛：给每个 effect-heavy hook 加 1 个 `it("survives
    StrictMode double-invoke", ...)` spec
- **触发关闭**：StrictMode 下任一现有 spec fail / 第 5 个 hook
  spec 落地时一起做
- **来源**：5/25 audit code MID-6 + arch HIGH-6

---

## DEBT-053 · 测试 mock helper 抽 (vi.hoisted N=2 触发)

- **现象**：5/25 arch MID-6 · DevicePicker.test.tsx 用 `vi.hoisted`
  factory mock useDevices + useApp 两层 · 含一堆 mutable closures over
  activeDevice / devicesList / isLoading / isError · pattern 强但
  hard-to-read · 第 2 个组件 spec 大概率会复制 4-5 个 let-state +
  setDeviceFn = vi.fn() 模式 · typo / 漏 reset 在 beforeEach 风险高
  · 当前 setActiveDevice helper 已经是 dead code (spec 里 0 引用)
- **影响**：N=4-5 spec 后 helper 设计已散 · 回头抽代价高
- **建议方案**：
  - 抽 web/src/test/mock-hooks.ts · `mockUseDevices(initial)` /
    `mockUseApp(initial)` 工厂返 `{ mock, set: { devices, device,
    loading, error } }`
  - decisions.md 加 ADR "test mock 优先 helper · 不直接 vi.hoisted"
  - 第一个 helper 在 N=2 (下一个组件 spec 出来) 时抽
- **触发关闭**：第 2 个组件 spec 用 vi.hoisted mock 两个以上 hook
- **来源**：5/25 arch MID-6

---

## DEBT-054 · audit event_id 字段

- **现象**：5/25 code-r MID-5 / AI-6 修 row key 抓的 fallback
  · server 端 AuditEvent 缺 unique id · client 端只能拼
  `${ts}|${sid}|${kind}|${source}|${summary[0..30]}` 当 key ·
  亚毫秒同 ts+sid+kind+source+summary 全撞概率极低但理论存在
- **影响**：row 重挂载 / animation flicker / focus 丢
- **建议方案**：
  - server 端 src/alb/audit/__init__.py emit event 时加 `event_id` =
    ulid / uuid4 · append-only log 兼容（旧 jsonl 无 event_id · client
    保留 fallback 拼 key）
  - client 端 row key 改 `event_id ?? fallback`
- **触发关闭**：row key 实测撞 (基本不会) / 或下次 audit 改 schema 时
- **来源**：5/25 code-r MID-5

---

## DEBT-055 · `mapAuditToTimeline` migration to wrapper · CLOSED 2026-05-25

- **关闭于**：AL-2 commit
- **关闭方式**：useAudit polling hook 0 caller · 直接删除 ·
  mapAuditToTimeline + dotFor + timeOf 搬到 `features/dashboard/
  mappers.ts` 纯函数文件 · useAuditTimeline wrapper 同步撤回（ADR-043
  N=1 consumer 直接 useMemo inline）
- **原现象**：mapAuditToTimeline 留 useAudit.ts · cross-import 散
- **关联**：ADR-043 (wrapper hook 抽取临界)

---

（新债由主对话评估后追加；agents 不直接写）

## DEBT-056 · SessionsList 改 table semantic + 子链接 button (aria-label 不再 1:1 复述)

- **现象**：5/25 第二轮 ui-f LOW-2 · AI-8 commit 把 6 列内容全编进
  aria-label · NVDA / VoiceOver 顺序朗读 ~110 字符 = 6s/行 · 30 行
  = 3 分钟 · SR 用户基本会放弃
- **影响**：a11y · 信息密度
- **建议方案**：拆 `<Link>` 包整行 → 用 `<table><tr><th scope="col">
  / <td>` 语义 + 子链接 button · SR 可独立朗读列名 + 单元格 ·
  aria-label 简化成 "Open session abc123" 类
- **触发关闭**：a11y 合规审 / Sessions 列表改大改时
- **来源**：5/25 第二轮 ui-f audit LOW-2

---

## DEBT-057 · mockup webui-preview-v3 补 Playground / Inspect / Audit / Sessions / Screenshots zoom 全套 —— **CLOSED 2026-06-17**

> 关闭（MBC-1）：见 DEBT-038 关闭说明。playground/audit/sessions/screenshot-zoom/
> inspect-console 全套 BEM 已画进 docs/webui-preview-v2-*.html 并过三闸。

- **现象**：5/25 mockup MID-1 · `.playground-chat__*` / `.audit-page__*`
  / `.screenshot-zoom__*` / `.sessions-table__*` 全套 BEM 都未画进
  mockup v2 · React 直接 ship 不走 L-React-UI-baseline 流程
- **影响**：React UI 没有视觉基线锚 · 下次 audit 易因 React 改动
  漂移 mockup（H2 `.live-pulse` 橙色就是这类）
- **建议方案**：
  - 建 docs/webui-preview-v3.html (或 v2 单页扩展 docs/
    webui-preview-v2-playground.html / -inspect.html / -audit.html
    / -sessions.html / -screenshots-zoom.html)
  - 跑 sky-skills/design-review 三闸 (verify / visual-audit / screenshot)
  - mockup-baseline-checker agent 加 grep checklist 验 React class
    都在 mockup 出现
- **触发关闭**：下一个大批 audit cycle 前 / 或新 UI 工作前
- **来源**：5/25 第二轮 mockup MID-1

---

## DEBT-058 · DevicePicker combobox 模式正式化 (改 Radix Combobox / 标准 ARIA APG combobox-only)

- **现象**：5/25 第二轮 ui-f LOW-4 (arch LOW-2) · 当前 DevicePicker
  实际 DOM focus 在 `<ul>` + activedescendant 也挂 listbox · 这是
  ARIA listbox-with-active 模式 · 不是标准 combobox pattern
- **影响**：对 WAI-ARIA 1.2 合规审查不友好 · 实测 NVDA / VoiceOver
  能用 · 但下一个 UI 审计可能 flag · 选 Radix Combobox 等成熟库
  长期更稳
- **建议方案**：
  - 评估 Radix UI Combobox / cmdk 等 · 与 anthropic-design 视觉对齐
  - 或纯手实现 combobox-only：trigger 永远保持 focus · listbox 不
    tabIndex · 全 keyboard event 在 trigger 处理 + activedescendant
    挂 trigger
- **触发关闭**：a11y 合规审 / 第二个 combobox 出现 (model picker 等)
- **来源**：5/25 第二轮 ui-f LOW-4 + arch LOW-2

---

## DEBT-059 · TestClient + StrictMode wrapper baseline 化 (升 DEBT-052)

- **现象**：5/25 第二轮 code MID 隐含 · 现 7 file/59 spec 都跑普通
  render / renderHook · prod app 在 main.tsx:32 是 StrictMode 包 ·
  dev effect 跑两遍 · 任何 cleanup-then-re-effect 副作用 spec 测不到
- **影响**：DEBT-052 升级版 · 当前测试基础设施层面没强制 StrictMode
- **建议方案**：
  - 选项 A：test/setup.ts 全局 wrap StrictMode (所有 renderHook /
    render 自动套)
  - 选项 B：每个 effect-heavy hook 加 "survives StrictMode" 单元
    spec
  - 选项 C：自定义 customRender utility 默认 StrictMode + 各 spec
    显式 opt-out
- **触发关闭**：StrictMode 下任一现有 spec fail / Effect cleanup bug
  在 prod 被发现
- **来源**：5/25 第二轮 隐含 + DEBT-052 升级

---

## DEBT-060 · PlaygroundPage 3 处 eslint-disable → 抽 useStickToBottom + useChatLogProjection 双 hook

- **现象**：AN 收尾给 PlaygroundPage scroll/done/error effect 补了
  deps · 但因 PlaygroundPage 其他 state 故意不加 仍 eslint-disable
  3 处。AO-3 partial 收口又新加 1 处。当前 4 处局部抑制 lint。
- **影响**：lint 工具在这 4 处失效 · 下次 audit reviewer 会重复提
- **建议方案**：
  - 抽 useStickToBottom(scrollRef, [chat.delta, chat.status, log.length])
    · effect deps 自然封闭 · 1 处 disable 消失
  - 抽 useChatLogProjection(chat, log, setLog) · 把 terminal-path
    effect 搬进去 · 把 cancel/error stash 逻辑封 hook · 1 处 disable
    消失
  - 余下 2 处 (done useEffect / error input restore) 用同款 hook 化
- **触发关闭**：第二个 chat 页面（Inspect Logcat live tail / 任何
  streaming UI）需复用 scroll + log projection 行为时 · 或 lint 升级
  把 disable 不允许局部
- **来源**：5/25 第三轮 arch MID-5 + AO-3 partial 收口

---

## DEBT-061 · useWsChatStream API 替代设计备用

- **现象**：AO-1 已经把 5 enum → 单 discriminated union · 现 API 在
  双 consumer 上很顺。但未来如 M3 OpenAI-compat / Anthropic SSE bridge
  / Gemma 4 on-device stream 出现 · API 可能再调
- **建议方案**：保持 3 phase + WsChatSettled union 模式 · 新 consumer
  按现有 switch 模板抄即可 · 如果发现共同模式 (如所有 consumer 都
  需要 partial stash) 再抽 useWsChatStreamWithLog wrapper
- **触发关闭**：第三个 WS stream consumer 出现时复审
- **来源**：5/25 第三轮 arch HIGH-2 修后 follow-up

---

## DEBT-062 · mockup webui-preview-v3 真做 · 整 React UI baseline 锚 —— **CLOSED 2026-06-17**

> 关闭（MBC-1）：5 个 per-page mockup 落地（见 DEBT-038）。mockup MID-2 .live-pulse
> 分叉一并消除：docs/webui-preview-v2.html 的 .live-pulse 改用 currentColor +
> color-mix、.live-head 设 color:orange，与 components.css 完全一致。
> 注：.live-card.is-idle / live-empty / live-tps-stale 等 dashboard 状态形态仍归
> round10 MBC-6（独立 backlog），不在 MBC-1 范围。

- **现象**：DEBT-057 已立 · 整套 .playground-* / .audit-* / .screenshot-
  zoom-* / .sessions-table-* / .live-card.is-idle / .playground-msg
  --cancelled / .playground-msg--errored BEM 都未画 mockup · React
  改动易漂 (H2 .live-pulse 橙色就是反例) · mockup .live-pulse 写法
  vs React 不一致 (mockup MID-2)
- **影响**：每次大改后 mockup baseline 形同虚设 · DEBT-057 兜底循环
- **建议方案**：
  - 建 docs/webui-preview-v3.html 或 v2-playground.html / v2-inspect.html
    / v2-audit.html / v2-sessions.html / v2-screenshot-zoom.html
    分 5 单页 · 视觉对齐 React 实际渲染
  - 顺手统一 .live-pulse 写法 (mockup 也用 currentColor + color-mix)
  - 跑 sky-skills/design-review 三闸 (verify / visual-audit / screenshot)
  - mockup-baseline-checker agent 加 grep checklist 验 React class 都
    在 mockup 出现
- **触发关闭**：下次大批 audit cycle 前 / 或新 UI 工作前
- **来源**：5/25 第三轮 mockup MID-1+2 + DEBT-057 升级

---

## DEBT-063 · PlaygroundPage 组件 spec (terminal-path effect + LLM payload filter) —— **CLOSED 2026-05-26 (AQ-1)**

- **现象**：AO-3 加 llmMessagesFrom 纯函数 spec · 但 PlaygroundPage
  整体组件 (chat.settled effect / cancel push log / error push partial
  + error message / lastPromptRef 回填 input) 0 spec · 业务正确性
  核心 (HIGH-4 修复) 仅靠纯函数 spec + 手工验
- **落地** (AQ-1)：
  - `web/src/features/playground/PlaygroundPage.component.test.tsx`
    新增 · 18 spec · 7 describe block 全覆盖
  - 用 `vi.hoisted` 给 chatState 一个可变 ref · 测试 mutate 后
    `rerender()` 触发 chat.settled effect · 不要 react-query provider
    (mock 干净到 useBackends 直接返 `{data,isLoading,isError}`)
  - 覆盖 4 settled 路径: done w/ content / done w/ empty content /
    cancelled w/ partial / cancelled w/ empty / error w/ server / error
    w/ ws-close 双 entry
  - HIGH-4 业务正确性回归网 3 条 (cancelled / errored / done 后下一
    send payload assertion · 钉 llmMessagesFrom 过滤行为)
  - cancel / clear / Esc / input restore / handledSettledRef guard 5 条
  - jsdom 没 ResizeObserver · stub no-op class (PlaygroundPage 用 RO
    维护 --chat-bar-height CSS var)
- **触发关闭**：18 spec 全过 · 业务正确性回归保护就位
- **来源**：AO-3 commit 自承 + arch follow-up

---

## DEBT-064 · `lib/ws.ts` pool key 拼接迁 JSON.stringify (collision-proof)

- **现象** (5/26 第四轮 arch LOW)：`const key = \`${path}|${opts.shareKey}\`` 用裸 `|` 分隔。今天 caller 全是字面量 path (`/audit/stream`) + JSON shareKey · 0 collision · 实际无风险。但任何未来 caller 传动态 path (template literal 拼 query string / `|` 字符) 或 free-form shareKey 都会和分隔符碰撞悄悄混键 (例如 `path="/x|y"` + `shareKey="z"` 等价于 `path="/x"` + `shareKey="y|z"`)。
- **影响**：N/A 今天 · 未来可能 silent dedup bug
- **建议方案**：
  - 改 `const key = JSON.stringify([path, opts.shareKey])` · 0 字节实际开销 (key 只在 Map 里存) · shape 自解释 · 0 collision
  - 同时更新 `useAuditStream.shareKey.test.ts` 钉新字节序列 (顺手把 contract spec assertion 跟新 key 格式 align)
- **触发关闭**：第 2 个 connect() caller 使用 shareKey · 或任何 path 变量化 caller 出现 (lib/hooks/* 用 template literal 构造 path)
- **优先级**：LOW · 不阻塞 · YAGNI 当前
- **来源**：5/26 第四轮 architecture-reviewer LOW-1

---

## DEBT-065 · `lib/ws.ts` cachedSnapshot 永不过期策略

- **现象** (5/26 第四轮 ui-f MID-2 · 第五轮 arch HIGH-1 + ui-f MID-1 升级)：pool entry 长寿 (refcount > 0) · cachedSnapshot 可能持有数小时未更新的 snapshot (服务端只在显式 send config 时回 snapshot · 期间只发 delta)。late-joiner 用过期 snapshot bootstrap → 立刻收 live delta · view 短暂处于"过期 snapshot + 未来 delta"不一致态。
- **AR-1 后风险放大** (5/26)：AR-1 加了 send dedup-by-epoch (ADR-046) · 晚到 view 的 synth-open send 被 drop · **服务器不再重发 fresh snapshot** · 晚到 view 必走 cachedSnapshot 回放路径。此前是"罕见 visible 不一致" (服务器重发 fresh 兜底) · 现在是"每次 late-join 都吃 stale" (无兜底)。影响面 1 → N。
- **影响**：late-joiner 视觉短暂错乱 (用户开 N 个 tab 长时间挂的场景 · 每次新 tab bootstrap 都吃过期数据 + 几条最新 delta 拼接 1-3 帧)
- **建议方案** (建议 (a) · 不再"二选一"):
  - (a) **推荐 · AR-1 后从可选升级为推荐**: entry 加 `cachedSnapshotAt: number` · subscribe 时若 `Date.now() - cachedSnapshotAt > 5min` 则不回放 · 让 view 等真服务器响应 (代价: 用户看 5min 后开新 tab 要等几百 ms snapshot · 但 dedup 现在 drop 同 epoch 同 payload · 需要 force-send 跳 dedup · 或 caller 自己变更 payload 触发 fresh)
  - (b) ~~接受现状~~ — AR-1 后此选项不再合理
- **触发关闭**：AS-2 实施 · 或下次 audit 实测 stale 命中
- **优先级**：**MID** (从 LOW 升 · 第五轮 arch HIGH-1 提)
- **来源**：5/26 第四轮 ui-fluency-auditor MID-2 + 第五轮 arch HIGH-1 + ui-f MID-1

---

## DEBT-066 · pool refcount close→reconnect 同 tick 瞬时双 socket race

- **现象** (5/26 第四轮 ui-f MID-1)：viewA.close() 同步执行 → refCount 0 → `pool.delete(key)` + `entry.client.close()` (排到 ws onclose macrotask)。同 tick viewB connect() 见 pool 空 · 重建新 entry · 旧 underlying 还在 close 流程中 → **瞬时 2 个 underlying socket** (旧的几 ms 内 close · 新的已 connecting)。
- **影响**：服务端短暂 2 connections · snapshot 多发 1 次。不影响功能 · 只是 wire / 服务端负载边缘 cost。
- **建议方案**：
  - 等 entry.client.close() 真触发 close event 后才 pool.delete (用 close listener · 同步 close call 后挂监听)
  - 或在 ADR-045 trade-off 节加 "close→reconnect 同 tick 瞬时双 socket" 已知 limitation 段
- **触发关闭**：服务端 hot path 实测 2x 连接 spike / 用户路由切换抖动反馈
- **优先级**：LOW · 服务端负载 < 1% 增量
- **来源**：5/26 第四轮 ui-fluency-auditor MID-1

---

## DEBT-067 · 跨 React subtree fan-out 压测 + 同 root 假设钉死

- **现象** (5/26 第四轮 ui-f MID-3)：pool fan-out 是同步循环 N 个 listener · React 18 自动 batch 同 root 内的 setState · 但跨 root (portal / iframe / 微前端) 不 batch · 同 1 服务端 snapshot 触发 N 次独立 commit。
- **影响**：当前架构单 React root (单 App)·N/A 今天。未来引入 portal / 微前端时静默退化。
- **建议方案**：
  - 加 spec 钉 "fan-out 调用方必须在同 React root" 契约
  - lib/ws.ts JSDoc 明记此前提
  - 或主动包 `unstable_batchedUpdates` (React 18 内已默认 · 但被 polyfill 一层保险)
- **触发关闭**：项目引入 React portal-mount UI / micro-frontend 时
- **优先级**：LOW · YAGNI 今天
- **来源**：5/26 第四轮 ui-fluency-auditor MID-3

---

## DEBT-068 · pool dedup 真实命中率追踪

- **现象** (5/26 第四轮 perf MID-1)：pool 设计是 N→1 socket · 但实测 3 个 prod caller (Dashboard timeline + LiveSession + AuditPage) 用 3 个 unique shareKey (LiveSession 因 includeMetrics:true 不同 · AuditPage 因用户动态 minutes 不同) · 单 tab 单用户场景下 **dedup 实际命中 = 0**。基础设施投资是对的 (架构层防御 + 未来扩展) · 但当前 RoI 数据没记录。
- **影响**：开发者 / reviewer 看 ADR-045 期望"3 socket → 1 socket" · 实际不变。预期管理偏差。
- **建议方案**：
  - ADR-045 trade-off 节加 "实测命中率" 段 (3 caller / 3 unique shareKey / 单 tab 0 dedup)
  - 列出"真触发 dedup 的场景": 多 tab 同 config / 同页面 N 处复用同一 config 的 hook
  - 可加 dev-only 计数器 `pool.dedupHits` · 跑一周开发量化
- **触发关闭**：第 2 个 caller 用 shareKey · 或 dev-mode 计数实测 0
- **优先级**：LOW
- **来源**：5/26 第四轮 performance-auditor MID-1

---

## DEBT-069 · PlaygroundPage backend 切换 → model reset 副作用 spec 0 覆盖

- **现象** (5/26 第四轮 code LOW-6)：`PlaygroundPage.tsx:261` 用户切 backend → `setModel("")` · 然后 `useBackendModels(backend)` 触发新 model list · PlaygroundPage 的 useEffect 把 model 置默认。这一链 0 spec 覆盖。
- **影响**：改 backend onChange 逻辑 / model 默认选择策略时无回归保护
- **建议方案**：
  - PlaygroundPage.component.test.tsx 加 1 spec：模拟 backend select onChange → mock useBackendModels 返不同 list → 验 model 被 reset 到默认
- **触发关闭**：spec 加 1 条 · 或 backend/model 选择逻辑下次改动时
- **优先级**：LOW
- **来源**：5/26 第四轮 code-reviewer LOW-6

---

## DEBT-070 · PlaygroundPage 5 项流式 UI spec 仍未覆盖

- **现象** (5/26 第五轮 ui-f LOW-1)：AR-2 spec 覆盖了 4/9 上轮 ui-f 列的漏洞 (settled-ref / shortcuts / !backend / lang=zh basic) · 剩 5 项流式 UI 仍裸奔:
  - `.playground-msg--streaming` cursor `▍` 渲染 (line 421)
  - `.playground-chat__metrics` 指标条 + finish_reason 显示 (line 426-437)
  - `.playground-chat__jump` 按钮 + stickToBottom 联动出现/消失 (line 441-452)
  - 空态 + 流式空态瞬间 (`chat.status==="streaming" && log.length===0`)
  - `chat.reset` 后 `settled→null` → `handledSettledRef.current = null` 的 reset cycle (现在用 chatState.settled=null 模拟 · 没真调 chat.reset)
- **影响**：改流式 cursor / metrics / jump 联动 / reset 时无回归保护 · 改 i18n 文案 / 流式 placeholder 也是
- **建议方案**：1 commit 加 5 spec · 部分需 jsdom + ResizeObserver / IntersectionObserver mock (stickToBottom scroll 联动可能需 ImageData mock)
- **触发关闭**：下次 PlaygroundPage 改 streaming UI / metrics / jump 逻辑前
- **优先级**：LOW
- **来源**：5/26 第五轮 ui-fluency-auditor LOW-1

---

## DEBT-071 · PlaygroundPage lang='zh' i18n 完整覆盖 (4/8 → 8/8)

- **现象** (5/26 第五轮 ui-f LOW-2)：AR-2 lang=zh spec 只验 4 个中文 label (`发送` / `清空` / `输入消息` / `已取消（未发给模型）`) · 漏 4 个:
  - `错误（未发给模型）` (errored label · PlaygroundPage.tsx:409)
  - `生成中 · Esc 取消` (streaming placeholder · line 481)
  - `回到最新 ↓` (jump button · line 450)
  - `回到最新消息` (jump aria-label · line 447)
- **影响**：未来重构改文案静默英文化 0 测试 fire
- **建议方案**：lang=zh spec block 内加 4 spec · 或用 `it.each([['en'], ['zh']])` 参数化原 spec
- **触发关闭**：DEBT-070 一并修 / i18n 文案重构前
- **优先级**：LOW
- **来源**：5/26 第五轮 ui-fluency-auditor LOW-2

---

## DEBT-072 · `listenerErrorHandler` scoped helper (`__withListenerErrorHandlerForTests`)

- **现象** (5/26 第五轮 code MID-4)：`listenerErrorHandler` module-level mutable singleton · `__setListenerErrorHandlerForTests` 让 test 改 + restore 用 `afterEach` · 模式正确但 boilerplate (capturedListenerErrors 全局变量 + beforeEach 装 + afterEach 恢复 + 3 行 boilerplate × N spec)。
- **影响**：spec 写法脆弱 · 任何 spec 漏 restore 跨 spec 污染 · 现状 ws.test.ts 集中管理 OK · 新 spec 文件 import 该 hook 会复制 boilerplate
- **建议方案**：导出 `__withListenerErrorHandlerForTests(handler, fn)` scoped helper · 自动 setup + cleanup · spec 内 `__withListenerErrorHandlerForTests(myHandler, async () => { ... })` 无需手写 beforeEach/afterEach
- **触发关闭**：第 2 个 spec 文件需要捕获 listener error / 实测漏 restore 跨 spec 污染
- **优先级**：LOW
- **来源**：5/26 第五轮 code-reviewer MID-4 / arch LOW-4

---

## DEBT-073 · listener throw 后视觉降级 UI (window CustomEvent / global toast)

- **现象** (5/26 第五轮 ui-f MID-2 · 第六轮 arch MID-4 升级)：AR-3 listener throw 不再饿 sibling · 但该 view 自己处于"半 state" (setState 没跑完 · UI 卡 stale)。用户看到"A 面板冻结 · B 面板活的"会以为 A 坏了 · 0 降级反馈 (无 error banner / 无"重连"按钮 / 无 console 给非 dev)。
- **影响**：罕见 listener-bug 场景 · 用户可见 stale UI · prod 用户看 console 红字 + 卡组件 · 无 toast / 无 banner = 真用户可见 bug
- **建议方案**：`listenerErrorHandler` 默认行为加 `window.dispatchEvent(new CustomEvent("ws-listener-error", {detail: e}))` · App 顶层 ErrorBoundary 或全局 toast 订阅 · 给"实时数据已断 · 请刷新"降级 UI
- **触发关闭**：App 加全局 toast 系统 / 第 1 个真 listener throw 用户反馈
- **优先级**：**MID** (从 LOW 升 · 第六轮 arch MID-4 提 · 用户可见非 cosmetic)
- **来源**：5/26 第五轮 ui-fluency-auditor MID-2 + 第六轮 architecture-reviewer MID-4

---

## DEBT-074 · listener async throws bypass `listenerErrorHandler` · 加 runtime 检测

- **现象** (5/26 第五轮 code LOW-3)：AR-3 `try { l(ev) } catch` 只接同步 throw · async listener (`async (ev) => { throw ... }`) 抛错走 unhandled promise rejection · 不路由到 listenerErrorHandler。WsEvent listener 类型签名是 `(ev) => void` · TS 不强 ban async function。
- **影响**：consumer 写 async listener 抛错 · 不被 lib 捕获 · 难诊断
- **建议方案** (二选一):
  - (a) listener 调用包 `Promise.resolve(l(ev)).catch(handler)` · 也捕获 async throws · 但每 listener call 多 1 个 microtask · perf 微影响
  - (b) 仅 JSDoc 警告 "listener MUST be synchronous" (AS-3 已做) + lint rule 检测 async function 传 .subscribe()
- **触发关闭**：实测有 async listener consumer 出现 / 第 N 个 consumer 误用
- **优先级**：LOW
- **来源**：5/26 第五轮 code-reviewer LOW-3

---

## DEBT-075 · `lib/ws.ts` module-level mutable hook 拐点 watchdog (≥ 4 时评估 DI 重构)

- **现象** (5/26 第六轮 arch MID-3)：lib/ws.ts 累计了 3 个 module-level mutable state + 对应 test hook:
  - `pool: Map<string, PoolEntry>` (+`__resetPoolForTests`)
  - `listenerErrorHandler` (+`__setListenerErrorHandlerForTests`)
  - `nowMs` (+`__setNowProviderForTests`)
  test hook 命名越来越长 (`__setListenerErrorHandlerForTests` 31 字符) · 开始难看。
- **影响**：再加 1 个 hook (DEBT-073 全局 toast / DEBT-074 async listener) 到 4 个时 · spec 测试隔离越来越依赖"调用方记得 restore" · 模式脆弱
- **建议方案**：
  - 现在不做 (DI 重构 = `createPool({now, onListenerError, ...})` factory · 改 caller 签名 · 1 caller 成本 vs 3 hook 成本不对等)
  - watchdog 触发条件: lib/ws.ts module-level mutable hook ≥ 4 时立刻评估
  - 重构 sketch (作 watchdog 参考): lib/ws.ts 改 `createPool({...deps})` factory · caller 持 pool ref · test 直接 `new` pool 不共享 module-level
- **触发关闭**：第 4 个 hook 加入时启动 DI 重构 / 或 architecture-reviewer 第 N 轮再判
- **优先级**：LOW (watchdog 性 · 不修)
- **来源**：5/26 第六轮 architecture-reviewer MID-3

---

## DEBT-076 · features/*/__tests__/helpers.ts 通用 page spec mock-reset helper

- **现象** (5/26 第六轮 arch LOW)：AS-4 PlaygroundPage.component.test.tsx 抽 `DEFAULT_USE_*_IMPL` + `resetMockImpls()` 是好模式 · 但 spec 文件 643 行 · 接近"该拆"临界。同款模式其他 page spec (Dashboard / Audit / Inspect) 也该用 · 现状各自重复。
- **影响**：spec helper 重复 · 改 mock 默认形状要扫多文件
- **建议方案**：
  - 抽 `web/src/features/__tests__/spec-helpers.ts` · 暴露 `withDefaultHookMocks(mockMap, resetters)` 或类似 API
  - 各 page spec 文件 import helper · spec 文件回降至 < 500 行
  - 或更激进: vitest config `setupFilesAfterEach` 集中 reset 公共 mock
- **触发关闭**：第 2 个 page spec 用同款 default impl 模式 (DashboardPage spec 加 useApp/useBackends mock 时)
- **优先级**：LOW
- **来源**：5/26 第六轮 architecture-reviewer LOW + code-reviewer LOW-6 (PlaygroundPage spec 633 行临界)

---

## DEBT-077 · cachedSnapshot stale skip + 服务器静默 = UI 死锁兜底 watchdog

- **现象** (5/26 第六轮 ui-f MID-2)：AS-2 stale snapshot 触发 force-fresh send · 期望服务器回新 snapshot。但若服务器静默 (不响应 send config · 服务端 bug / 异常) · 晚到 view 永远 stuck 在"open + 0 行" UI · 用户看死。AR-1 之前不存在这 failure mode (cachedSnapshot 总有兜底 even if stale)。
- **影响**：罕见服务器异常路径 · 用户 0 反馈
- **建议方案**：
  - subscribe 触发 force-fresh 时启 watchdog: 5s 内仍 0 server snapshot → listener emit `{kind:"error", message:"snapshot timeout"}` 走现有 error UI · 不引入新视觉契约
  - 或: lib 不管 · useAuditStream 自加 timeout 兜底
  - 评估: lib 是 protocol-agnostic · 不应假设"snapshot 必到" · 建议 useAuditStream 实施
- **触发关闭**：服务器 snapshot 静默 bug 实测 / useAuditStream 加 status="timeout" 状态时
- **客观升级条件** (AU-6 / 5/26 第七轮 arch LOW)：**若 backend audit_stream timeout 实测 ≥ 1 例 · 立刻 LOW → MID** · 用户可见 UI 死锁优先级高于"理论 rare"
- **监控源** (AV-6 / 5/26 第八轮 code LOW)：
  - backend log: `grep -E 'audit_stream.*(timeout|silent)' workspace/sessions/*/server.log`
  - frontend console (一旦 DEBT-077 watchdog 实施): listener 收 `{kind:"error", message:"snapshot timeout"}` 计数
  - 用户反馈渠道: GitHub issue + "timeline 一直空白 / connecting" 关键词监控
  - **谁负责数**: 每月 release prep 时跑一次 backend log grep · ≥1 命中触发本 DEBT 升 MID
- **优先级**：LOW (rare 双条件: 5min idle + 服务器 bug)
- **来源**：5/26 第六轮 ui-fluency-auditor MID-2 + 第七轮 arch LOW (升级条件) + 第八轮 code LOW (监控源)

---

## DEBT-078 · `lib/ws.ts` 综合拐点 watchdog (4 维度任 2 +1 启重整) — **STATUS: ACTIVE (5/26 第八轮)**

- **现象** (5/26 第七轮 arch MID): lib/ws.ts 累积已可见 · 4 个维度同时压临界:
  - **Options 字段数**: 4 (maxBackoffMs / noReconnect / shareKey / staleSnapshotMs) · 第 5 个加入时该分 PoolOptions vs ClientOptions
  - **module-level mutable hook**: 3 (pool / listenerErrorHandler / nowMs) · DEBT-075 已立 ≥4 时评估 DI
  - **spec 文件**: ws.test.ts 685+ 行 / 42 spec / 9 describe · 接近 ADR-040 拆分临界 (600 行 / 30 spec)
  - **ADR-046 trade-off 叠加**: 原段 + AS-2 段 + AT-2 段 · 3 段累计 narrative 越来越复杂
- **影响**：任 1 维度单看 OK · 4 同时压 = 综合信号 · 下次大改前必整
- **触发**：上面 **4 维度任 2 同时 +1** (例如 spec 700 行 + Options 5 字段 / hook ≥4 + ADR-046 第 4 段修订)
- **STATUS 升级 ACTIVE** (AV-6 / 5/26 第八轮 arch LOW)：实测 ws.test.ts 已 806+ 行 (从 685 升) · 已触发 spec 维度 +1 (>700 行)。第 2 个维度 (Options 字段 5 / hook 4 / ADR-046 第 4 段) 任 1 +1 时**立即启动重整 sketch** · 不再 watchdog 待触发。
- **触发后启动重整 sketch**：
  1. Options 拆 `PoolOptions` (entry-level: shareKey/maxBackoffMs/noReconnect/staleSnapshotMs) vs `ClientOptions` (view-level: 未来扩展位)
  2. spec 按 concern 拆 ws.pool.test / ws.dedup.test / ws.cachedSnapshot.test / ws.lifecycle.test
  3. ADR-046 v2 rewrite narrative (合并 3 段修订成单条目 + "当前 state + 推翻条件 + 修订史 collapsed" 结构)
  4. module hook 走 DI factory (DEBT-075 sketch)
- **不重整代价**：每位 reviewer 必读 806 行 spec + 3 段叠加 ADR · 上下文成本指数增长
- **架构 agent 固化** (AV-6 / 5/26 第八轮 code LOW)：architecture-reviewer agent checklist 加 "每次 review web/src/lib/ws.ts diff 时数 4 维度计数 · 任 2 +1 必报 DEBT-078"
- **优先级**：LOW (watchdog 已 ACTIVE · 触发条件接近 · 暂不重整代价仍可接受)
- **来源**：5/26 第七轮 architecture-reviewer MID-4 + LOW + 第八轮 arch LOW 升 ACTIVE

---

## DEBT-079 · ADR-046 narrative v2 重写 (3 段累计修订 → 单条目)

- **现象** (5/26 第七轮 arch LOW): ADR-046 累计了 3 段修订 (原段 + AS-2 dedup 段 + AT-2 修正段) · reviewer 必须读全才能理解最终 state · 已被推翻的描述还在文中。
- **影响**：reviewer cognitive load · 后人误读"原 trade-off" 已不成立的部分
- **建议方案**：ADR-046 v2 重写 narrative · 结构 "**当前 state** + **推翻条件** + **修订史 (放底部 collapsed)**" · 把"AR-1 后已消除 / AS-2 修正 / AT-2 修正" 三段合到 v2 单条目里
- **触发关闭**：DEBT-078 综合拐点触发时一并做 / 下次 ADR-046 再有修订之前
- **优先级**：LOW
- **来源**：5/26 第七轮 architecture-reviewer LOW

---

## DEBT-080 · `lib/ws.ts` first-caller-wins 0 反馈 API · debug 难

- **现象** (5/26 第八轮 arch LOW): caller 传 `staleSnapshotMs=10min` 但是第 2 caller · 实际 entry 是别人 30min · caller 不知道自己被 silently 覆盖。0 API 让 caller 自检 "我是不是 first" 或 "我的 value 真生效了吗"。同款的 noReconnect / maxBackoffMs 都有此问题 (但当前 0 实战 hit · 见 ADR-047 修订)。
- **影响**：debug 难 — 用户反馈"我设了 X 但效果没生效" · 开发者要看 entry first-caller 是谁、什么时候 mount · 没工具支持
- **建议方案**：YAGNI 不现在做 · 入档备查。未来若 first-caller-wins 真踩坑:
  - (a) `connect()` 返 `{view, isFirstViewOfEntry: boolean}` · caller 可 assert / warn
  - (b) entry 创建时 dev-mode console.debug 标"entry X created by caller from path Y with value Z" · debug 友好
  - (c) `__debugPoolStatesForTests` test hook 暴露 entry 列表 · 方便 spec 验
- **触发关闭**：第 1 个 first-caller-wins 真冲突 case 出现 / 用户反馈"设了 X 不生效"
- **优先级**：LOW (debug 性 · 不影响功能)
- **来源**：5/26 第八轮 architecture-reviewer LOW

---

## DEBT-081 · 5 个 inspect 流式 hook 手写 raw WebSocket · 触发条件到时迁 lib/ws solo

- **现象** (round11 AR9-4): `useUartStream` / `useLogcatStream` / `useMetricsStream` /
  `useTerminalSession` / `useFileTransferStream` 各自 `new WebSocket` 手写 ~100 行
  生命周期，不走 `lib/ws.ts`。ADR-048 已立边界规则 + JSDoc 说明为何 raw，当前**有意
  保留**（协议各异 · 统一重写是 premature）。
- **影响**：lib 层容错改进（listener try/catch 不截断 fan-out / Blob→ArrayBuffer demux /
  统一 error）不覆盖这 5 个；stale-socket 守护（CR-1 + UI-3）要在每个 hook 各修一遍。
- **建议方案**：YAGNI 不现在统一重写。**触发迁移**（任一 hook 命中即迁该 hook 到
  `lib/ws.ts` solo `noReconnect` 模式，不再手写）：
  - 该 hook 需要 reconnect / backoff（raw 手写重连易错 · lib 已有）
  - 踩 listener-throw 截断 fan-out（lib 的 AR-3 容错能救 · raw 不能）
  - 出现第 6 个流式 hook 想复用前 5 个的某段生命周期逻辑（说明该抽象了）
- **触发关闭**：上述任一条件出现并完成迁移 / 或 5 个全迁完
- **优先级**：LOW（当前 raw 是有意决定 · ADR-048 已记录边界 · 不影响功能）
- **来源**：round11 architecture audit AR9-4

## DEBT-082 · event_bus.publish 每事件 to_thread + open/append/close · producer 串行等落盘

- **现象**：`src/alb/infra/event_bus.py` `publish()` 每条事件
  `await asyncio.to_thread(_append_jsonl, …)`，`_append_jsonl` 每次 `open("a")→write→close`。
  producer 协程要等这次磁盘写完才继续；每事件一次 open/close 系统调用。
- **为何现在不修**：`to_thread` 把写挪出事件循环（**loop 不阻塞**，只有 publisher 协程
  await ~1ms）；事件率低（1Hz tps + 零星 chat/tool/device），本地单用户桥。当前写路径
  正确 + 有序 + 持久 + 够用。round11 PERF-5（LOW）。
- **为何不盲改**：① fire-and-forget（不 await）→ 进程退出丢写 + 乱序 + 错误无人看；
  ② 持久句柄缓存 → 测试 per-test monkeypatch `ALB_WORKSPACE`，缓存句柄会写到上一个
  workspace（测试污染），需句柄失效生命周期；③ 单 writer task + 批量 I/O = 正经设计但
  要 lifecycle/flush/ordering/backpressure，且刚为 ADR-049 重构过这条写路径，不该 LOW 顺手叠。
- **触发修复**（任一）：事件率显著上升（多并发会话 / 更高频 metric）· profiling 实测此处
  是瓶颈 · 上多 worker（写路径要重做）。届时按 Option B（单 writer + 批量 append）立 ADR 正经做。
- **优先级**：LOW（本地单用户低事件率下无实际影响）
- **来源**：round11 PERF-5（2026-06-17 过方案后用户拍 defer）

## DEBT-083 · transport/factory 无 host/agent 维度 + serial 端口写死 config（多 host fleet 前置债）

- **现象**：`src/alb/transport/factory.py` `build_transport(device_serial=...)` **没有
  host/agent 维度**；serial transport 连**固定** `default_tcp_port`（`infra/config.py`）。
  `workspace.is_safe_device` 正则也不含 host 概念。
- **背景**：当年（M0/M1）假设"一台 Windows、一条隧道、固定端口（5037/19001）"，
  `ADB_SERVER_SOCKET` 间接寻址正为此设计。
- **为何现在不修**：远程 agent P0–P3 只做**单设备单 host**，固定端口（ADR-051 决定 5037
  adb + 19001 serial）下 keystone invariant 成立，core 零改，不需要 host 维度。
- **触发修复**：落 **多 host fleet（ADR-050 §代价）/ 多 COM 并发动态端口（P4）** 前必须先消化：
  ① `build_transport` 加 host/agent 维度（或 hub 端 per-agent 端口映射）；② serial 端口
  从"读固定 config"改成"接收 per-session 注入端口"；③ 设备寻址 `(agent_id, serial|COM)`。
- **为何不盲改**：`build_transport` 是 CLI/MCP/API 共用入口，加维度会 ripple 到所有调用点 +
  `is_safe_device` 校验 + MCP 工具签名；单设备阶段加了纯属空转。
- **优先级**：MEDIUM（多 host / 动态端口落地的硬前置，单设备阶段无影响）
- **来源**：ADR-050/051 架构评审（2026-06-18 opus）

## DEBT-084 · remote agent: cid 未绑定 agent_id + current_agent 单 agent 假设（多 agent 前置）

- **现象**：① `src/alb/remote/registry.py` `_pending` 全局只按 cid，`resolve_pending`
  只验 cid 存在 + token（`agent_route.py /agent/channel`），**不验回拨连接归属哪个 agent**。
  ② `registry.current_agent()` 返回 epoch 最大的单个 agent（P0 单 agent 假设）。
- **背景**：P0 范围是单设备单 agent。cid 不可猜（uuid4 122bit）+ token-gated 在单 agent +
  单信任域下足够；路由"当前 agent"也只有一个。
- **为何现在不修**：多 agent（P4）才暴露——届时 (a) 一个持有效 token 的进程拿到 live cid
  可能 resolve 到**别的 agent** 的 data channel（跨 agent 串流）；(b) current_agent 的
  "最近一个"语义不再够，要按设备/agent_id 选。
- **触发修复**：多 agent / 多 host fleet 落地前必须：register_pending 带 agent_id、
  resolve_pending 核验回拨连接的 agent 归属（data WS 需带 agent 身份或复用连接身份）；
  current_agent 改为按 (agent_id|device) 寻址（与 DEBT-083 一并）。
- **为何不盲改**：单 agent 阶段实现 per-agent 绑定要给 data WS 加鉴权身份维度，纯空转 +
  增复杂度；且 protocol/ADR 已据实标注"P0 不绑 agent_id"（不留未兑现安全声明）。
- **优先级**：MEDIUM（多 agent 的安全前置；单 agent 无实际暴露）
- **来源**：ADR-050/051 P0 实现后 architecture-reviewer（2026-06-18 opus）
