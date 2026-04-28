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

**当前（2026-04-28 update · F.1 首次实战后）**：
- 总评审次数：1（3 agents 并行 = 1 次）
- 总建议数：18（arch 6 + code 7 + sec 4 + 1 重叠不双计）
- 采纳率：65%（11 采纳 / 5 驳回 / 2 已知不修）
- 驳回类型分布：模块归属偏好 1 / 已登记 DEBT 复提 2 / 未来 milestone 范围 1 / 测试便利权衡 1
- 形成新规则数：1（L-013 · bus event 分类）+ 1 ADR（ADR-021）
- agents 团队首次实战发现 2 条 high 级架构问题 → 推迟原本的 ship，重做 F.1
  调整版。**首次实战已经创造价值**。

> 每次评审后由主对话更新（commit message 里附带"review-feedback +N
> 条"作为信号）。
