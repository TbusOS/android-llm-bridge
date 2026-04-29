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

## ADR-021 · token 不广播但聚合 tps_sample（F.1 实施时定）

- **日期**：2026-04-28
- **状态**：accepted；extends ADR-019

**上下文**：ADR-019 决定 chat token 事件不广播到 bus（密度太高）。但
LiveSession 的 spark / KPI 的 LLM throughput 都需要 tps 数据源（DEBT-001
/ DEBT-004 登记）。F.1 要补这个数据源。

**决策**：
1. token 事件**仍然不广播**到 bus（ADR-019 不变）
2. 引入 `MetricSampler`（src/alb/infra/metric_sampler.py · TokenSampler 类），
   每 chat session 一个，1Hz 聚合 + publish `tps_sample` 事件到 bus
3. `tps_sample` 是新加的 **metric kind**（第 6 种 bus event kind）。
   bus event kinds 从此分两类：
   - **business kinds**：user / assistant / tool_call_start / tool_call_end /
     done / error / command / deny / hitl_*
   - **metric kinds**：tps_sample（未来可加 cmd_rate / push_rate 等）
4. 订阅方默认**只收 business kinds**。`/audit/stream` 通过首条 message
   `{include_metrics: true}` opt-in；`GET /audit` 通过 `?include_metrics=true`
   opt-in
5. token 数从 backend ABC 的 token 事件携带（`{"type":"token","delta":"...",
   "tokens": 1}`），AgentLoop 加 `on_raw_token` 回调把真实 token 数喂给
   sampler。**不**走"chars/4 估算"，避免 2-3× 精度偏差

**备选**：
- (a) 让 done 事件带完整 tps spark 数组 — 一次性，非流式，UI 看不到滚动
- (b) chars/4 估算 token — 中文 / emoji 偏差 2-3×，spark 失真
- (c) sampler 内联到 chat_route — 失去未来复用（terminal 命令速率等）

**Trade-off**：
- 放弃：bus event schema 极简（5 类 → 6 类），多一个回调路径
- 获得：实时 spark / KPI 真实 throughput 数据源 / metric 流可独立扩展
  / 不污染 timeline UI（默认过滤）

**何时推翻**：metric kinds 多到 ≥ 5 类时，应抽 `MetricBus` 独立通道。

---

## ADR-022 · Dashboard 同页双 WS 实例（F.5 实施时定）

- **日期**：2026-04-28
- **状态**：accepted；**reverses ADR-018 备选 (c) under ADR-021 conditions**

**上下文**：ADR-018（C 档 audit 升级为事件总线）当时把"两个 WS 各连各
的"作为备选 (c) 否决，理由是"浪费连接"。ADR-021 引入 metric kinds +
`include_metrics` opt-in 后，business 流的 pause/resume（user 控
timeline）和 metric 流（永远 live，喂 LiveSession spark）的 lifetime
语义已经不一致 —— 共享同一连接做客户端 demux 会让 timeline pause 冻结
metric 流，违反"metric 跟随设备运行"的设计意图。trade-off 反转。

**决策**：DashboardPage 同时持有两个 useAuditStream 实例：
1. `useAuditStream({includeMetrics: false})` —— ActivityTimeline 用
2. `useAuditStream({includeMetrics: true})` —— useLiveSession 喂数据用

Hook 不抽 useDualAuditStream（callsite 单一时是 premature abstraction，
按 architecture-reviewer 维度 1 的"两个实际场景才抽象"原则）。

`useAuditStream({includeMetrics: true}).pause/resume` 加运行时
console.warn 防止误用（metric 流不应 user-pausable）。

**备选**：
- (a) 单 WS 共享 + 客户端 demux + 独立 pause 状态机 —— 复杂度高，且
  当前规模 N=2 时双连接更直白
- (b) 抽 useDualAuditStream wrapper —— premature，等第二个 page 也要
  双流时再抽

**Trade-off**：
- 放弃：单连接简洁
- 获得：独立 pause 语义 / 独立重连 / hook API 不变 / server 端 fan-out
  queue 1× → 2×（可接受）

**何时推翻**：
- (a) 同页连接数 ≥ 4
- (b) SessionDetailPage 等带 sessionId 过滤的消费者出现
- 任一触发 → 评估方案 (a)（多消费者 + 客户端独立 pause 状态机）

**反思 ADR-018 的备选 c**：当时否决理由"浪费连接"在 localhost
单租户 + N=2 时不成立；隐性优势"独立 pause 语义"在 ADR-021 引入
metric 流后变成必须。**这次反转给后续 reviewer 重要信号**：ADR
备选段不是永久判决，新事实出现时应主动反转 + 立新 ADR（见 L-015）。

---

## ADR-023 · SPA fallback 跨部署 surface 异构实现

**Status**：accepted (2026-04-29，DEBT-014/015 关闭物)

**Context**：
项目有两个 Web UI 部署 surface，TanStack Router HTML5 history 模式
要求服务端在深链 / 刷新时 fallback 到 SPA shell：

- **alb-api dev/local**（FastAPI + StaticFiles）：用户主路径
- **GH Pages prod**（静态托管）：方法论展示 + offline-first 演示

DEBT-014 / DEBT-015 是同一不变量（"SPA route 直访不能 404"）的两
个部署 surface 实例化。

**Decision**：**两 surface 异构实现**：

| surface | 机制 | 实现 | 跳转 |
|---|---|---|---|
| alb-api | server-side intercept | `SPAStaticFiles(StaticFiles)` 子类 override get_response，404 + path tail 无扩展名 → 服务 index.html | 1 hop |
| GH Pages | client-side roundtrip | `docs/404.html` redirect script + `docs/app/index.html` recovery script（spa-github-pages 套路） | 2 hops + history.replaceState 静默还原 |

**不统一的原因**：GH Pages 静态托管无 server-side hook，server-side
intercept 不可行。这是部署 surface 硬约束，不是设计偏好。

**共享不变量**（写入 architecture.md 关键不变量段）：
- SPA route 路径段不能含 `.`（DEBT-014 启发式：`tail.includes(".")` 判
  为 asset）
- SPA route 路径段不能含 `?` `#` `&`（DEBT-015 用作 spa-github-pages
  协议保留）
- SPA route 不能以 `assets/` 开头（与 vite build 产物冲突）
- 任一违反 → 后端启发式 / GH Pages redirect 误判，浏览器深链 / 刷新
  404

**备选**：
- (a) 两 surface 都用 client-side roundtrip：alb-api 也走 404.html，
  统一一套机制 —— **否决**：alb-api 有 server hook 用之，多一次
  redirect 是无谓的 user-perceived latency
- (b) 项目迁出 GH Pages（Cloudflare Pages / Vercel / Netlify 都支持
  `_redirects` server-side rewrite）—— **暂不**：GH Pages 是项目方法
  论展示用，迁移收益边际

**Trade-off**：
- 放弃：两 surface 一套实现，需双写双测
- 获得：每个 surface 用最适合的机制（alb-api 一次 200 / GH Pages 走
  社区标准 spa-github-pages 协议），用户体感都是"深链直达"

**何时推翻**：
- 迁出 GH Pages → 备选 (b) 触发，client-side roundtrip 可删
- 共享不变量被新需求打破（比如某天非要支持 `.` 路由名）→ 重审两
  surface 的 fallback 协议

**关联**：DEBT-014 / DEBT-015 / L-017 端到端验证铁律 / **L-018**
静态托管 SPA URL 闪现。

---

（后续 ADR 在主对话决策时按此格式追加）
