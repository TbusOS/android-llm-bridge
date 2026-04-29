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
  done 后续 sample / NaN 守卫 / SPARK_WINDOW cap / 跨 session 切换
- **还债条件**：web/ 引入测试框架（候选 G/H 档）

---

（新债由主对话评估后追加；agents 不直接写）
