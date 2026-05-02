# 已知技术债清单

不是 bug，是当时为了快速 ship 做的妥协。agents 评审时**不要重复提**
已经在这里登记的债（除非建议升级 severity 或建议立刻还）。

格式：
- 每条债一段
- severity：high（影响功能 / 安全）/ mid（影响维护 / 体验）/ low（small）
- 引入时间 + commit
- 是否计划修：是 / 否 / 视情况
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

## DEBT-008 · GET /metrics/summary 缺 short-TTL cache —— severity 升 low → mid

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

## DEBT-018 · DashboardPage section placeholder loading/error 重复

- **severity**：mid（结构债，本身不影响功能；DashboardPage 已 380+ 行，
  每加一个 hook 段涨 ~30 行 boilerplate）
- **引入**：D 档（device strip 加 isError/isLoading 分支）
- **暴露**：DEBT-017 给 backends 段加 isError/isLoading 时，arch
  reviewer 发现 4 处（device / sessions / backends / audit）各有自己的
  placeholder 实例
- **位置**：`web/src/features/dashboard/DashboardPage.tsx`（loading /
  error inline 在 4 段各写一份）
- **是否计划修**：是
- **还债 sketch**：抽 `<SectionPlaceholder kind="loading"|"error"|"empty">`
  组件，沿用已加的 `.be-card--empty` BEM class，DashboardPage 每段从
  ~30 行降到 1 行
- **还债条件**：dashboard 加第 5 个 hook 段时（M3 上 OpenAI-compat
  + 第二个真 backend 后，可能需要 backend-by-backend metrics 段）
- **来源**：DEBT-017 arch reviewer 发现 #4，主对话登记不阻塞合入

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

## DEBT-021 · 历史 tracked 文件含敏感词 · CI `--all` 模式会挂

- **severity**：mid（CI 全量扫挂；staged 模式不影响日常 commit）
- **位置**：
  - `.claude/reports/visual-2026-04-29-debt001.md` 含 `<llm-host>`
  - `scripts/f8_screenshots.mjs` 含 hardcoded 个人家目录路径 + 真实用户名字面
- **现象**：`bash scripts/check_sensitive_words.sh --all` 4 处命中
- **是否计划修**：是
- **还债 sketch**：
  - `f8_screenshots.mjs`：把 hardcoded `/home/&lt;user&gt;/...` 路径改 `process.env.HOME` /
    相对路径（参考新加的 `web/scripts/web_check.mjs` 模式 —— 用 web/node_modules 标准解析，
    不绝对路径 import）
  - `visual-2026-04-29-debt001.md`：把 `<llm-host>` 改 `<llm-host>` 占位
- **还债条件**：next session（不阻塞当前 work）
- **来源**：2026-04-30 commit `0ef2d87` 前 `--all` 扫描发现

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

（新债由主对话评估后追加；agents 不直接写）
