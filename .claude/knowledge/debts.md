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
- **是否计划修**：是（F 档）
- **还债 sketch**：F.1 加 TokenSampler 1Hz 聚合 → publish tps_sample
  → useLiveSession reducer 识别 → tpsSpark 60 个采样

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

## DEBT-003 · KPI MCP tools 写死 21

- **severity**：low
- **引入**：D step 4（commit 2af137c，2026-04-28）
- **位置**：`web/src/features/dashboard/DashboardPage.tsx` `buildKpis`
  里 MCP tools KPI value 写死 "21"
- **原因**：现有后端没有"列 MCP tool"端点；写死避免接 21 这数字到 21
  以外时还要改前端
- **是否计划修**：是（候选 E.1）
- **还债 sketch**：新增 `GET /tools` 后端端点列出所有 mcp.tool() 名 +
  category；前端 useTools hook 调用之

---

## DEBT-004 · KPI LLM throughput 显示 "—"

- **severity**：mid
- **引入**：D step 4（commit 2af137c）
- **位置**：同上，LLM throughput KPI value="—"，deltaText="待 /metrics"
- **原因**：tps 数据源没就绪（chat session 的 token usage 没全局聚合）
- **是否计划修**：是（候选 E.2，等 F.1 完成 tps_sample 后做）
- **还债 sketch**：`GET /metrics/summary?window=300s` 聚合最近 5 min
  events.jsonl 的 tps_sample → mean / p50 / p95 / max。前端 useMetricsSummary。

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

## DEBT-008 · Vite base URL 硬编码风险

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

（新债由主对话评估后追加；agents 不直接写）
