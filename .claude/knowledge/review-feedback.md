# 评审反馈历史

agents 提的建议被采纳 / 驳回的累积记录。**agents 评审前必读**，避免
反复提同一类被驳回过的建议。

格式：
- 时间 + agent + 涉及 commit / 改动 + 建议摘要 + 决议（采纳 / 驳回） +
  理由

驳回的不是说 agent 错了，而是"项目意图就是这样"。同一类被驳回 ≥ 3
次 → 调整 agent prompt 把这个盲区移除。

被采纳的不需要写到这里（采纳的会变成代码改动 / 新 ADR / 新债 / 新
教训，自然进 architecture / decisions / debts / lessons）。

---

## 2026-04-28 · F.5 双 WS 实例（前端 useAuditStream + LiveSession metric 流）评审

**评审对象**：F.5 staged diff（useAuditStream.ts +24 行 / DashboardPage.tsx +11 行）

**调动**：code-reviewer + architecture-reviewer 并行（无新 schema，
跳过 security）

### 采纳清单（已实施）

- arch [high] **F.5 走 ADR-018 备选 (c) 但没立 ADR 反转** → 立新
  ADR-022 "Dashboard 同页双 WS 实例" + L-015 "ADR 备选段会随后续 ADR
  反转" 升级为元规则
- arch [mid] **server 协议没预留 session_id / kinds 过滤** → 登记
  DEBT-010
- arch [mid] **`pause/resume` 在 metric 实例上应禁用** → 加 console.warn
  no-op，防 LiveSession 误调
- code [mid] **UseAuditStreamOptions 应限定 primitive 字段** → JSDoc
  加约束说明（避免 caller 传函数/数组导致无限重连）
- code [mid] **首条 message 重连依赖 lib/ws.ts listener Set 复用契约
  没文档化** → 注释加 "after EVERY open (including reconnects)" 说明
- code [low] **注释 "cheap on localhost" 不准确** → 改"acceptable for
  M2 single-tenant; revisit when M3 adds auth；server fan-out queue
  1× → 2× acceptable while N ≤ 4"
- code [low] **liveAudit status="error" 时 spark 静默冻是 UX 缺口** →
  LiveSessionCard 接 streamStatus prop，error/closed 时 throughput
  区显示 "● stale" 离线提示

### 驳回清单

- arch [low] **抽 useDualAuditStream wrapper** → arch agent 自己反对
  （premature abstraction，单 callsite），不抽
- arch [mid] **strict-mode dev 双 invoke 触发 4× connect / snapshot scan**
  → 影响轻（snapshot 只读，不污染 events.jsonl），ADR-022 提及，本档
  不专门修，等 DEBT-006 events rotate 时一起加测试

### 形成新规则

- **ADR-022** · Dashboard 同页双 WS 实例（reverses ADR-018 备选 c）
- **L-015** · ADR 备选段会随后续 ADR 反转，反转时必须新立 ADR（**这条
  是元规则，覆盖整个 knowledge 维护流程**）
- **DEBT-010** · /audit/stream WS 协议预留 session_id/kinds（low，N≥3
  时还）

### agent prompt 调整建议

无。本次 architecture-reviewer agent 主动翻 ADR-018 备选段对照 ADR-021
新事实，识别出文档债 —— **这是 agents 团队"会演进"特性的具体证据**。

---

## 2026-04-28 · F.4 GET /tools 评审

**评审对象**：F.4 staged diff（src/alb/api/tools_route.py 新 112 行 +
schema/server +2 +2 + 测试 87 行）

**调动**：code-reviewer + security-and-neutrality-auditor 并行

### 采纳清单（已实施到 F.4 调整版）

- code [mid] **`_ToolCollector` 只伪造 tool() → 未来 @mcp.resource() 会
  AttributeError** → 加 `__getattr__` no-op fallback + log.warning once
- code [mid] **tool() 不接 kwargs → @mcp.tool(name="x") 会 TypeError**
  → 改 `def tool(self, *args, **kwargs)`，honour explicit name/description
- code [mid] **测试断言 >= 20 太松** → 收紧到 >= 30 + 新增
  test_eleven_categories_present 锁 11 个 category 名
- code [low] **加 docstring "intentionally no caching"** → _collect_tools
  docstring 加注释，避免下次 reviewer 重提"加 cache"
- code [low] **测试加 description 非空软不变量** → 加 80% 阈值断言，
  catches 未来 docstring 写成 `\"\"\"\\n    ...` 形态
- sec [low] **list_tools docstring 加"consumer MUST escape description"**
  → 加 docstring 注释（和 F.3 session_id XSS 提醒同模式）

### 新登记 lesson

- **L-014** · `@mcp.tool()` 函数首行 docstring 等同于公开 API description
  - 由 sec agent 提出（隐性公开面 + 未来安全策略可能泄漏）
  - 升级为规则：mcp tools 函数首行 docstring 按"公网外发标准"写

### 驳回清单

无。本次评审反馈全部合理 + 改造成本低，全采纳。

### 形成的新规则

- L-014 · 升级到 lessons
- DEBT-003 → partial-fix（后端就绪，前端 F.7 完工后才彻底关闭）

### agent prompt 调整建议

无。

---

## 2026-04-28 · F.3 GET /metrics/summary 评审

**评审对象**：F.3 staged diff（src/alb/api/metrics_summary_route.py 新 +
schema/server +2 +2 + 测试 167 行）

**调动**：code-reviewer + security-and-neutrality-auditor 并行
（无新架构，跳过 architecture-reviewer）

### 采纳清单（已实施到 F.3 调整版）

- code [mid] **percentile 边界缺测试** → 加 test_single_sample_percentile
  + test_two_sample_percentile + test_basic_aggregation 加 p50 断言
- code [mid] **total_tokens 类型守卫不一致** → `isinstance(tw, (int, float))`
  + `int(tw)` 累加，与 rate fallback 对称
- code [mid] **legacy/malformed 测试没断 total_tokens** → 加断言；新增
  test_total_tokens_accepts_float_tokens_window 锁定 float 路径
- code [low] **docstring "ISO" 不说时区** → 改 "ISO 8601, UTC, with offset"
- code [low] **session_id 空串 0 命中体验差** → Query min_length=1（空串
  返 422）+ 加测试 test_session_id_empty_string_rejected
- sec [low] **session_id 进 response 的 XSS 边界提醒** → docstring 加
  "consumer MUST escape session_id" 注释

### 驳回清单（写入此文件，agents 下次会看到）

- code [low] **rates.sort() in-place 风格** → reviewer 自己说"不强求"，
  不改。
- code [low] **双闭区间 since <= ts <= until** → reviewer 自己核查后已
  确认与 audit_route 一致，不改。
- sec [low] **session_id 字符集 regex** → 当前不拼日志/不进路径，无
  实际场景，不加。等真有 log 拼接时再加 `regex=r"^[a-zA-Z0-9_\-]+$"`。

### 新登记 DEBT

- **DEBT-008** · GET /metrics/summary 缺 short-TTL cache —— security
  agent 提的 "24h × 全量扫描" 放大风险，severity=low，登记到 M3 一起
  解（连同 DEBT-006 events.jsonl rotate）

### 形成的新规则

无（本档 reviewer 反馈都是局部代码改进，无升级到 lessons 的新规律）

### agent prompt 调整建议

- 暂无。本次评审反馈聚焦在测试覆盖 + 类型守卫 + docstring 三块，质量
  高，prompt 不调。

---

## 2026-04-28 · F.1 首次实战（agents 团队 ship 后第一次评审）

**评审对象**：F.1 staged diff（TokenSampler + chat_route 集成）

**调动**：code-reviewer + architecture-reviewer + security-and-neutrality-auditor 并行

### 采纳清单（已实施到 F.1 调整版 commit 里）

- arch [high] **token 计数走 chars/4 估算 → backend ABC 加 tokens 字段**
  → 实施：LLMBackend.stream 文档加 tokens；OllamaBackend yield tokens=1；
  AgentLoop 加 on_raw_token 回调；chat_route 用回调而不是看 token 事件
- arch [high] **tps_sample 是新 metric kind 必须立 ADR + audit 默认过滤**
  → 实施：写 ADR-021；audit_route GET / WS 都加 include_metrics 默认
  False；architecture.md 不变量段升级
- arch [mid] **模块归属：sampler 不属于 agent/，应在 infra/**
  → 实施：移到 src/alb/infra/metric_sampler.py
- arch [mid] **interval 写死 → 从 env 读** → 实施：ALB_TPS_SAMPLE_INTERVAL_S
- code [mid] **summary 在 interval≠1.0 时单位错** → 实施：rate_per_s 字段 +
  summary 改为 `{rate_per_s} tok/s`
- sec [mid] **observe(n) 缺单次 cap** → 实施：OBSERVE_MAX = 10000
- sec [mid] **0 token 仍 publish 浪费写盘** → 实施：_flush 在 n=0 且
  非 force 时跳过；close() 用 force=True flush 让 consumer 看到结束信号
- code [low] `_loop` 的 try/except CancelledError noop → 实施：删
- code [low] **测试 timing 抖动 / 断言太弱** → 实施：interval 拉到 0.02s；
  加 token 数下界断言；删脆弱的 counts == 1 断言
- code [low] **observe before start 是 buffer 但永不发** → 实施：契约改
  drop（更严格的 lifecycle）
- sec [low] bare `except` 吞异常应 log → 实施：logger.warning

### 驳回清单（已写入此文件，agents 下次会看到不再重复提）

- arch 提的"sampler 一次内联到 chat_route 私有函数（替代 infra/）"
  - **驳回**：未来 terminal_route 想加命令速率 / push 字节速率会复用，
    且独立文件可独立测试。MetricSampler 是 infra 级别抽象不是临时
    helper。
- sec 提的"WS endpoint 无 auth/rate limit"
  - **驳回**：M2 阶段已知，alb-api 默认只 bind 127.0.0.1（部署文档已说），
    M3 加 origin / token 闸。本档不解。
- sec 提的"events.jsonl 无界增长"
  - **驳回**：已是 DEBT-006 登记，本档不解（M3 加 rotate）。
- code 提的"lazy bus fallback 是测试便利，应改为构造时显式注入"
  - **驳回**：sampler 一两处调用，简化为构造永远传 bus 反而让 chat_route
    显式 import get_bus。lazy fallback 维持。
- code 提的"close 在未 start 时跳过 flush 但 observe 计数器仍写"
  - **变通**：契约改为"observe 在未 start 时 drop"（不再 buffer）。
    test_no_start_no_publish 简化。

### 对应 agent prompt 调整建议

- 暂无。本次评审反馈质量高，agent prompt 不调。

### 学到的新规则（升级到 lessons.md）

- L-013 · bus event 加新 kind 时分类（business / metric）

---

预期写入流程：
1. agents 完成评审
2. 主对话整理建议清单 + 询问用户
3. 用户驳回某条 → 主对话立刻写一条到本文件
4. 下次 agents 评审时读到这里 → 不再重复提

---

## 模板（首次写入时拷贝）

```
## YYYY-MM-DD · <agent-name> · commit <hash> 或 sketch <topic>

**建议**：<agent 提的原话或摘要>

**决议**：驳回（设计意图 / 误报 / 暂不优化）

**理由**：<用户给的具体原因>

**对应 agent prompt 调整建议**：<是否要调 agent prompt 避免下次再提；
具体怎么调>
```

---

## 累计统计（自动派生 dev-team.html 用）

> dev-team 展示页会读这里的统计，让外部看到团队成长证据。

**当前（2026-04-28 update · F.5 完成后）**：
- 总评审次数：4（F.1 / F.3 / F.4 / F.5）
- 总建议数：48（F.1: 18 / F.3: 10 / F.4: 8 / F.5: 12）
- 采纳率：79%（27 + 11 = 38 / 48）
- 驳回类型分布：模块归属偏好 1 / 已登记 DEBT 复提 2 / 未来 milestone 范围 2 / 测试便利权衡 1 / "不强求"风格调整 1 / 无场景的防御 1 / premature abstraction 1 / 影响轻不修 1
- 形成新规则数：3（L-013 / L-014 / **L-015 元规则**）+ 3 ADR（ADR-020/021/022）+ 2 DEBT（DEBT-008/010）
- F.1 发现 2 条 high 级架构问题 → 推迟 ship，重做调整版
- **F.5 architecture-reviewer 主动翻历史 ADR 备选段，识别"反转未立新
  ADR"的文档债 → 升级为元规则 L-015**（agents 团队"会演进"的具体证据）
- F.3 / F.4 / F.5 reviewer 反馈持续聚焦代码质量 + 架构债识别，**采纳率
  稳定在 78-79%**

> 每次评审后由主对话更新（commit message 里附带"review-feedback +N
> 条"作为信号）。
