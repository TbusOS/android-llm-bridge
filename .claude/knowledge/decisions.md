# 决策档案（ADR 风格）

每个重大决策一篇，agents 评审时用作"为什么这么设计" 的权威来源。
新决策由主对话写入，agents 不写。

格式：
- 标题 / 日期 / 状态（accepted / superseded by N / rejected）
- 上下文：当时面临什么问题
- 决策：选了什么
- 备选：考虑过的其他方案
- Trade-off：放弃了什么、获得了什么
- 后续可能推翻条件：什么情况下应该重新评估这个决策

---

## ADR-001 · Transport 抽象 + 4 实现

- **日期**：M1 早期（2026-04 初）
- **状态**：accepted

**上下文**：alb 要支持多种连接 Android 板子的方式（USB adb / WiFi adb /
SSH / 串口）。

**决策**：抽 `Transport` ABC，4 个实现（adb / ssh / serial / hybrid）。
`build_transport()` 工厂按 `ALB_TRANSPORT` 环境变量 / config / device
profile 选。HybridTransport 智能路由（先 adb 再 ssh 再 serial）。

**备选**：每种方式独立 CLI（`alb-adb` / `alb-ssh`）。

**Trade-off**：放弃 CLI 简洁性 / 获得统一 capability layer。

**何时推翻**：新增第 5 种连接方式（如 Wi-Fi USB tethering）需要 fork
出新 ABC 时。

---

## ADR-007 · long log 不进 LLM context

- **日期**：M1 早期
- **状态**：accepted

**上下文**：logcat / dmesg 等日志可能数 MB；如果整段塞进 chat 给 LLM，
context window 爆 + 成本失控。

**决策**：长日志一律落盘 `workspace/devices/<serial>/logs/<file>.txt`，
LLM 只看到 `{ok, summary, artifact: "/path/to/log"}`。Agent 想读细节
要主动调 read_artifact tool 拿摘要。

**Trade-off**：增加一次 tool call 往返 / 节省 90%+ context。

**何时推翻**：模型 context window 普遍 ≥ 1M tokens 时可重新评估。

---

## ADR-016 · LLMBackend ABC

- **日期**：M2 早期（2026-04 中）
- **状态**：accepted

**上下文**：M2 要支持 Ollama / OpenAI-compat / Llama.cpp / Anthropic
四类 backend。每个 SDK 风格不同。

**决策**：抽 `LLMBackend` ABC（`chat` / `stream` / `health`），统一
`Message` / `ToolCall` / `ChatResponse` 类型。`get_backend(name)` lazy
工厂。

**备选**：直接用 LiteLLM / OpenAI-compat 协议归一化。

**Trade-off**：自己维护 ABC 和 4 个实现 / 不引第三方依赖（offline-purity
原则）。

---

## ADR-017 · Web Tier 1 技术栈

- **日期**：2026-04-23
- **状态**：accepted

**上下文**：M2 step 4 起前端 Tier 1 — Chat UI + 设备看板 + HITL + 产物
栏。要选技术栈。

**决策**：React 19 + Vite + TS strict + TanStack Router + TanStack Query
+ shadcn/ui + Radix + lucide。**不引 Tailwind**（沿用 anthropic.css
token + class-based css）。

**Trade-off**：抛弃 Tailwind 的开发速度 / 保持品牌视觉一致 + offline
bundle 干净（不引大量未用的工具类）。

**何时推翻**：UI 复杂度上升到需要 design system level（不再是 dashboard
+ 几个表单）的时候。

---

## ADR-018 · audit 升级为事件总线（C 档重构）

- **日期**：2026-04-28
- **状态**：accepted；replaces "GET /audit 扫盘 + 10s 轮询"

**上下文**：D 档 step 3 用 GET /audit + 10s 轮询给前端 Timeline 数据。
但要做 LiveSessionCard（"系统中正在跑的 session"），轮询撑不住。

**决策**：把 audit 从"事后扫 messages.jsonl + terminal.jsonl"重构为：
1. in-process `EventBroadcaster`（fan-out + 持久化）
2. `workspace/events.jsonl` 全局事件日志（schema 固化）
3. `WS /audit/stream` snapshot + 实时增量 + pause/resume
4. chat_route / terminal_route 都是 producer
5. 前端 useAuditStream 替代轮询；useLiveSession 共享同一个 stream，
   纯函数 reduce 派生 LiveSession 视图

**备选**：
- (a) 给前端轮询加快频率 — 治标不治本
- (b) 引第三方 message bus（Redis / NATS）— 违反 offline-purity
- (c) 两个 WS 各连各的 — 浪费连接
- (d) 同一 WS 多消费者过滤 — pause 语义耦合

**Trade-off**：
- 放弃：实现复杂度（broadcaster + jsonl + WS 协议）
- 获得：实时性 first-class / 单一数据源 / 不引第三方依赖 / 多 UI 区共享

**何时推翻**：单进程容量不够（多 worker / 多机）时迁移到 Redis pub-sub。

---

## ADR-019 · token 事件不广播

- **日期**：2026-04-28（C.2 实施时定）
- **状态**：accepted

**上下文**：chat stream 里 `token` 事件密度 ~ 50-200 Hz。如果都进 bus
fan-out，慢消费者队列必爆。

**决策**：token 事件**不**广播。bus 只收 user / tool_call_start /
tool_call_end / done / error 这 5 类关键事件。

**Trade-off**：
- 放弃：UI 实时按字滚动的能力（要的话用 chat ws 直连，不用 audit ws）
- 获得：bus 队列稳定 / 不需要复杂背压机制

**遗留问题**：LiveSession tps 退化为整段平均，spark 没数据。这是
F 档要解决的（用 1Hz 聚合的 tps_sample 事件代替 token 事件）。

---

## ADR-020 · agents 团队工程方法论（本次）

- **日期**：2026-04-28
- **状态**：accepted

**上下文**：项目要进入更高质量节奏（F 档 + 后续 A/G/E）。需要独立
视角的代码 / 架构 / 性能 / UI 评审，不能只靠主对话。

**决策**：
1. 7 个项目专属 agents（code-reviewer / architecture-reviewer /
   performance-auditor / ui-fluency-auditor / mockup-baseline-checker /
   visual-audit-runner / security-and-neutrality-auditor）
2. 6 个 slash commands（review / arch / perf / ui-check / security /
   preflight）
3. `.claude/knowledge/` 团队记忆（agents 必读，主对话写）
4. agents 默认只读，写权限严格分层（5/7 完全只读，2/7 只能写
   `.claude/reports/<agent>-<ts>.md`）
5. 写权限互斥靠 timestamp + agent name 命名（永不撞）
6. agents 要有"质疑能力"（不只是"对当前规则打分"，还要质疑规则本身）
7. 知识库随项目演进，"越用越聪明"（review-feedback.md 累积反馈 →
   调 prompt / 升 lessons / 立新 ADR）

**备选**：不建团队，每次评审手写 prompt。

**Trade-off**：放弃灵活性 / 获得一致性 + 可复用 + 可对外展示。

**何时推翻**：发现 7 个 agents 角色边界混乱（频繁互相重叠）时合并；
或某 agent 一直没用上时移除。

---

（后续 ADR 在主对话决策时按此格式追加）
