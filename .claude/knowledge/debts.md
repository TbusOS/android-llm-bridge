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

## DEBT-001 · LiveSession tps 退化为整段平均（spark 空）

- **severity**：mid（用户体感"实时"被削弱，但不影响功能）
- **引入**：C.5（commit a03cbab，2026-04-28）
- **位置**：`web/src/features/dashboard/useLiveSession.ts` `tpsSpark: []`
- **原因**：C.2 决定 token 事件不广播（ADR-019），导致 LiveSession 没有
  逐字粒度的 tps 数据。tps 只能在 done 时取整段平均，spark 没采样可放。
- **状态（2026-04-28 update）**：**partial-fix shipped**
  - 后端就绪 ✓（F.1 ship · ADR-021 实施）：tps_sample 1Hz 流上 bus
  - 前端待做（F.5/F.6）：useAuditStream 加 include_metrics + useLiveSession
    reducer 识别 tps_sample → tpsSpark
- **彻底关闭条件**：F.6 ship 后，LiveSessionCard 实际渲染滚动 spark

---

## DEBT-002 · MOCK_BACKENDS 仍占位

- **severity**：low
- **引入**：D 档（commit 6e5b12b，2026-04-27）
- **位置**：`web/src/features/dashboard/DashboardPage.tsx` 使用
  `MOCK_BACKENDS`
- **原因**：D 档优先把 sessions / devices / audit 三大数据区接通；LLM
  backend cards 留 mock 暂用
- **是否计划修**：是（候选 G 档）
- **还债 sketch**：建一个 useBackends hook 调 `/playground/backends`，
  把现状 mock 替换。后端端点已存在。

---

## DEBT-003 · KPI MCP tools 写死 21（partial-fix shipped）

- **severity**：low
- **引入**：D step 4（commit 2af137c，2026-04-28）
- **位置**：`web/src/features/dashboard/DashboardPage.tsx` `buildKpis`
  里 MCP tools KPI value 写死 "21"
- **原因**：现有后端没有"列 MCP tool"端点；写死避免接 21 这数字到 21
  以外时还要改前端
- **状态（2026-04-28 update）**：**partial-fix shipped**
  - 后端就绪 ✓（F.4 ship · GET /tools 列 33 个 tool / 11 categories）
  - 前端待做（F.7）：useTools hook + KpiStrip 用 hook 数据替代写死 21
- **彻底关闭条件**：F.7 ship 后，KpiStrip MCP tools KPI 显示真实数（33
  或后续）

---

## DEBT-004 · KPI LLM throughput 显示 "—"

- **severity**：mid
- **引入**：D step 4（commit 2af137c）
- **位置**：同上，LLM throughput KPI value="—"，deltaText="待 /metrics"
- **原因**：tps 数据源没就绪（chat session 的 token usage 没全局聚合）
- **状态（2026-04-28 update）**：**unblocked**
  - 数据源就绪 ✓（F.1 ship · tps_sample 持续流到 events.jsonl）
  - 后端 GET /metrics/summary 待加（F.3）
  - 前端 useMetricsSummary 待加（F.7）
- **还债 sketch**：F.3 加 `GET /metrics/summary?window=300s` 聚合最近 5 min
  events.jsonl 的 tps_sample → mean / p50 / p95 / max。F.7 加前端 hook 接 KPI

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

## DEBT-008 · GET /metrics/summary 缺 short-TTL cache

- **severity**：low
- **引入**：F.3（commit pending，2026-04-28）
- **位置**：`src/alb/api/metrics_summary_route.py` 每次请求全量扫
  `events.jsonl`
- **原因**：`window_seconds` 上限 24h + events.jsonl 全量遍历。alb-api
  默认 bind 127.0.0.1，但单机内同源前端循环刷新 / bug 死循环可放大
  IO 压力。F.3 阶段 events.jsonl 还没积累，问题不显；DEBT-006 events.jsonl
  rotate 落地前是廉价缓解点。
- **是否计划修**：M3 一起（连同 DEBT-006 events rotate）
- **还债 sketch**：进程级 `functools.lru_cache(maxsize=8)` + TTL 60s，
  按 (window_seconds, session_id) cache 上次结果。或直接走 DEBT-006
  rotate 后扫描量本身就小。
- **备注**：security-and-neutrality-auditor agent 在 F.3 评审中提出
  作为 low 风险，不阻塞 ship。


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

## DEBT-011 · useAuditStream MAX_EVENTS 不分类型，metric 流挤掉 business

- **severity**：mid
- **引入**：F.5（commit c135816，2026-04-28）+ F.6 暴露
- **位置**：`web/src/features/dashboard/useAuditStream.ts:31` `MAX_EVENTS = 200`
- **原因**：双 WS 实例方案下，`liveAudit = useAuditStream({includeMetrics:true})`
  同时收 business + metric。tps_sample 1Hz 推送，**约 200 秒**后最早
  的 user / tool_call_start 事件被 `slice(0, MAX_EVENTS)` 挤出 rawEvents。
  reducer 看不到 user 事件就拿不到 prompt / turn，长 session（> ~3 min）
  LiveSessionCard 显示 "(no prompt yet)" 但 spark 继续滚动。F.6 没修。
- **状态**：登记，F.7 一并改
- **是否计划修**：是
- **还债 sketch**：useAuditStream 改成按 kind 分桶：metric 桶 cap 60
  （≈ 60s @ 1Hz，和 SPARK_WINDOW 对齐）+ business 桶 cap 200。或者
  让 useLiveSession 在 reducer 上游就把两类拆开各自 cap。后者改动
  范围更小。
- **还债条件**：F.7 落地（同步动 useAuditStream / useLiveSession）

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
  done 后续 sample / NaN 守卫 / SPARK_WINDOW cap / 跨 session 切换
- **还债条件**：web/ 引入测试框架（候选 G/H 档）

---

（新债由主对话评估后追加；agents 不直接写）
