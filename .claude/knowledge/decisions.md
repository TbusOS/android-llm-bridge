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

## 索引（按主题 · 47 ADR + 5 seed · 2026-05-26 update）

**核心架构（transport / backend / event）**
- ADR-001 · Transport 抽象 + 4 实现
- ADR-016 · LLMBackend ABC
- ADR-024 · LLMBackend ABC capability 改用 class attribute（amends ADR-016）
- ADR-018 · audit 升级为事件总线（C 档重构）
- ADR-026 (seed) · backend 配置边界：CLI flag 还是 config 文件
- ADR-027 · BackendSpec.runs_on_cpu → host_compute_type 三态
- ADR-033 (seed) · transport capability 用 ABC class-attr 标，不 hasattr 检测
- ADR-035 · LlamaCpp 嵌入式 backend deferred · 改 step 顺序 step 2/3 互换

**流 / 协议（WS / SSE / chat / stream）**
- ADR-007 · long log 不进 LLM context
- ADR-019 · token 事件不广播
- ADR-021 · token 不广播但聚合 tps_sample
- ADR-022 · Dashboard 同页双 WS 实例
- ADR-030 (seed) · stream hook 抽象时机评估
- ADR-036 · 流式文件传输用 WS 而非 SSE / Job-Model（MID-6）
- ADR-037 (seed) · transport 配置常量（retry / timeout / backoff）用 class attribute
- ADR-045 · `lib/ws.ts` shareKey opt-in pool + late-joiner microtask replay（关 DEBT-047）
- ADR-046 · `lib/ws.ts` pool view.send() epoch-scoped payload dedup（5/26 AR-1 · 关 ui-f HIGH-1 reconnect storm）
- ADR-047 · `lib/ws.ts` Pool-level Options first-caller-wins 语义（5/26 AU-4 · 关 arch MID-4）

**Web Tier / 前端**
- ADR-017 · Web Tier 1 技术栈
- ADR-023 · SPA fallback 跨部署 surface 异构实现
- ADR-025 · per-backend 并行 useQueries · Dashboard 健康探测 polling 分层
- ADR-028 · device 信息分层 · dashboard summary vs inspect details
- ADR-029 · device 信息刷新策略 · auto polling vs button vs WS push
- ADR-032 · Inspect 8 tabs 走 unmount/remount，不做 keepAlive

**安全 / 部署**
- ADR-031 (seed) · filesync HITL 写在 endpoint 层 vs PermissionEngine
- ADR-034 (seed) · alb-api 默认 bind 127.0.0.1，0.0.0.0 须显式开启

**工程方法 / 团队**
- ADR-020 · agents 团队工程方法论

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

## ADR-024 · LLMBackend ABC capability 改用 class attribute（amends ADR-016）

**Status**: accepted, supersedes ABC default-method-with-sentinel-flag pattern
**Date**: 2026-04-30
**Context**: DEBT-017 主 commit `67c0820` 在 ABC 默认 `health()` 里加
`implemented: False` sentinel，由端点 `if not result.get("implemented")`
反查这个 dict key 来判定"未接探测"。arch reviewer / code reviewer
同时指出 3 处脆性：

1. OllamaBackend.health() 不返回 `implemented` 字段，端点靠"key 缺失
   == implemented=True"的隐式 fallthrough。下一个 backend 复制 ABC
   模板做基础时，留 `implemented: False` 又返回 reachable=True →
   端点把它判成 unprobed，明明在跑显示成"未探测"，**静默错读**。
2. dict-as-interface 没有 schema：endpoint 读 `result.get("model_present")`、
   `result.get("model")` 等，concrete backend 加字段 / 改字段 / 漏字段
   都不 type-check。
3. ChatResponse / ToolCall / Message 早已 dataclass 化，`health()`
   仍返回 dict，是孤儿。

**Decision**：

1. ABC 加 `class.has_health_probe: bool = False`，与
   `supports_tool_calls` / `supports_streaming` / `runs_on_cpu` 一组，
   显式 declare capability。
2. ABC 默认 `health()` 改 `raise NotImplementedError` —— "调用未接探测
   的 health()" 是 programmer error，loud failure 比 silent placeholder
   值得。
3. 新增 `HealthResult` dataclass（`reachable: bool | None` /
   `model: str | None` / `model_present: bool | None` / `error: str | None`），
   `health()` 返回 typed value。
4. endpoint 改读 `getattr(type(b), "has_health_probe", False)` 决定
   是否调 health()；调用后读 `result.reachable` / `result.model` /
   `result.model_present` / `result.error` typed 字段。

**Trade-off**：
- 放弃：dict-as-interface 的"加字段不破坏老调用方"灵活
- 获得：static type 校验 / IDE 补全 / capability 显式声明 / "忘了
  override" loud failure / ChatResponse-级别契约一致性

**备选**：
- (a) 保留 dict 但加 `TypedDict` schema —— **否决**：runtime 不强
  制，只骗 mypy，丢失"忘了 override 应失败"信号
- (b) 改 dict + 显式 enum field 替代 sentinel —— **否决**：本质还
  是约定胜过类型，下次评审还要重审
- (c) 当前选项 dataclass + class attr —— **采用**

**何时推翻**：
- 加 OpenAI-compat 后发现需要返回比 4 字段更灵活的 metadata（比如
  rate-limit headers）—— 扩 dataclass 而非回 dict
- 出现某 backend 需"探测能力随运行时配置改变"（has_health_probe 不
  是静态而是动态）—— 那时改 instance attribute / property，不是回
  dict

**测试覆盖**：`test_health_abc_default_raises`（直接 unit 验证 ABC
default raise）+ `test_health_no_probe_wired`（_FakeBackend 没 set
has_health_probe → endpoint 短路给 no_probe）+ `test_health_with_method`
（has_health_probe=True 走真探测分支）。

**关联**：
- ADR-016（LLMBackend ABC 设计原则）—— 本 ADR 是对它的 amendment
- DEBT-017（运行时 health 缺口）—— 主 commit `67c0820` 落，本 ADR
  在 follow-up 的 commit 里 supersede 掉 sentinel pattern
- L-019（待写）：ABC 默认方法用 sentinel flag 表达 capability 否定
  是反模式

---

## ADR-025 · per-backend 并行 useQueries · Dashboard 健康探测 polling 分层

**Status**: accepted（描述当前实现，未来 N≥6 时重审）
**Date**: 2026-04-30
**Context**: DEBT-017 给 Dashboard 加"每个 backend 独立 health
probe"。N=4 today（1 ollama beta + 3 planned）。两个设计选择需文档：

1. **per-backend useQuery（fan-out）vs single batch endpoint**：
   - 当前选 fan-out（`useQueries` 4 路并行 GET /playground/backends/<n>/health）
   - perf-auditor 测算 N=8 时 32 r/min idle，N=16 时 65 r/min
   - alternative: `GET /playground/backends/health` batch 一次返回
     所有 backend health
2. **polling 频率分层**：
   - 静态 manifest（注册表 / 描述）：60 s
   - 运行时 health：15 s（normal）/ 60 s（after error，TanStack
     refetchInterval 函数式 backoff）
   - 长期 metrics window：5 min（DEBT-008 cache 候选）
   - 真实时（audit / tps_sample）：WebSocket（ADR-022 双 WS 实例）

**Decision**：

1. fan-out 直到 N ≥ 6 backends 或 idle QPS > 1。理由：失败隔离（一个
   probe 挂死不影响其他）+ TanStack 单 query 状态独立 + 每 query
   各自 backoff。
2. 频率分层定调：60 s manifest / 15 s health / 60 s health-on-error
   / 5 min metrics / WS realtime。新增同档 polling 沿用此层级。
3. health useQueries 显式配置：
   - `enabled: api.status !== "planned"` —— planned 不浪费 round-trip
   - `refetchOnWindowFocus: false` —— 与 interval 重叠的 focus
     refetch 在 N 路 fan-out 时会雪崩
   - `refetchIntervalInBackground: false` —— hidden tab 不该烧
     daemon，10-100× 节省
   - `retry: 1` —— probe 失败本身就是要展示的信号，retry-storm 噪声

**Trade-off**：
- 放弃：N≥6 时 batch endpoint 的请求量收敛
- 获得：N<6 时 fan-out 简单 / 失败隔离 / 单 query 独立 backoff

**备选**：
- (a) batch endpoint —— 暂不（N=4 还在 fan-out 甜点；切换成本不高，
  M3 加 OpenAI-compat 时再评估）
- (b) WebSocket push（server 主动 push health change）—— **否决**：
  health 不是高频信号（变化≈daemon up/down 事件），polling 足够

**何时推翻**：
- N ≥ 6 backend 真上线 → 触发 batch endpoint 评估
- DEBT-006（events.jsonl rotate）期间发现 health polling 是 events
  写盘热点 → polling 频率重审
- M2.5 Windows standalone 上线后 idle 桌面 app 长开 → 进 visibility-
  aware refetch 也覆盖一阶段

**关联**：DEBT-017 / DEBT-NEW-C(httpx client 复用) / ADR-022 双 WS

---

## ADR-026 (seed) · backend 配置边界：CLI flag 还是 config 文件

**Status**: seed（M3 step 1 留下，待 M3 step 2 LlamaCpp 落地时拍板）
**Date**: 2026-04-30
**Context**: M3 step 1 (commit `344fb47`) 加 OpenAICompatBackend 时
chat_cli 加了 `--openai-url` + `--api-key` 两个 flag。当前 chat_cli
共 `--ollama-url` / `--openai-url` / `--api-key` 3 flag。M3 step 2
LlamaCpp 需 `--gguf-path` / `--n-ctx` / `--n-gpu-layers`，M3 step 3
Anthropic 需 `--anthropic-key` / `--system-prompt`。预计 N=4 backend
后 chat_cli 累积 8-10 个 backend-specific flag，CLI 助记可读性下降，
而且很多 flag 跨 backend 不通用（如 `--api-key` 既给 openai-compat 又
给 anthropic 用，但 anthropic 还另需 `--anthropic-version`）。

**Decision**：暂不决策，留 seed。M3 step 2 LlamaCpp 落地时拍板。

**3 备选**：

- (a) **保持 N×flag**（每个 backend 自己的 flag 集合）—— 当前模式，
  扩展性 O(N×params)，到 N=4 就 8-10 个 flag。优势：CLI 直观；劣势：
  flag 长尾 + 不复用。
- (b) **`--backend-arg key=value` 多次重复**（generic 通配）—— sklearn
  / mvn 风格，`alb chat --backend openai-compat --backend-arg base_url=
  http://localhost:1234/v1 --backend-arg api_key=sk-...`。优势：1 个
  flag 覆盖所有 backend；劣势：autocomplete 弱、help 文档拆碎、不能
  做类型校验。
- (c) **`~/.alb/config.toml` `[backends.openai-compat]` 段**（最规
  范）—— TOML 已是 pyproject 共识。优势：可分 profile / 每 backend
  独立 default / 易在 IDE 编辑；劣势：boilerplate 高、首次使用要先
  写 config。

**推荐方向**（待 M3 step 2 验证）：(c) 配 (a) tail-flags（少数 hot
override 用 flag，长尾走 config）。即 `--ollama-url` / `--openai-url`
/ `--api-key` 留作 hot override，但 `--n-ctx` / `--system-prompt` 等
长尾沉到 config。

**何时拍板**：M3 step 2 LlamaCpp PR 必含本 ADR 决策。本 seed 在该 PR
里被升为正式 ADR。

**关联**：DEBT-019 (httpx client 复用，独立维度) / L-020 (N≥3 抽象
原则在 CLI flag 维度同样适用)

---

## ADR-027 · BackendSpec.runs_on_cpu → host_compute_type 三态

**Status**: ✅ accepted 2026-05-02（M3 step 2 commit `332d743`）
**Date**: 2026-04-30 seed → 2026-05-02 升正式
**Context**: 原 `BackendSpec.runs_on_cpu: bool` 语义模糊：
- OllamaBackend `runs_on_cpu=True` —— 字面对（本机 CPU 推理）
- OpenAICompatBackend `runs_on_cpu=True` —— 字面**错**（上游可能 8×H100
  GPU server，alb-host 只发 HTTP）
- AnthropicBackend `runs_on_cpu=False` —— 字面**也错**（alb-host 根本
  不跑模型，是 SaaS API）

字段实际语义 = "alb-host 端的 CPU/GPU 需求"，但字面"runs on cpu" 误导
用户以为是"模型在 CPU 跑"。

**Decision**：选 **(b) `host_compute_type: 'cpu' | 'gpu' | 'remote'`** 三态
enum。M3 step 2 Anthropic 落地时一并改（早一步落，比 seed 提的"M3 step
3 Anthropic"更早，因为 step 2/3 顺序在 ship 前调换 —— LlamaCpp 嵌入式
有 4 个 footgun deferred，Anthropic 提到 step 2）。

**3 备选回顾 + 拒方理由**：

- ❌ (a) `host_gpu_required: bool`（反向布尔）—— 拒：仍只能表达 2 态，
  "remote" 概念表达不出（Anthropic / OpenAI-compat 用 False 还是 True？
  False 显然不准）。
- ✅ (b) `host_compute_type: 'cpu' | 'gpu' | 'remote'` 三态 —— 选这个。
  序列化 / 前端 enum 改动可控（实际只 1 个 TS interface + 1 个 surface
  方法），表达精确。
- ❌ (c) 拆 `host_cpu_only: bool` + `inference_remote: bool` —— 拒：
  两个布尔表达三态，4 组合用 3 个有歧义状态空间。

**ship 实际清单**（commit `332d743` cross-cut 9 文件 · 纯 rename）：

- `BackendSpec.host_compute_type: str` 替换 `runs_on_cpu: bool`
- 4 实例 reclassify：ollama=cpu / openai-compat=remote / llama-cpp=cpu /
  anthropic=remote
- `LLMBackend` ABC 类属性同步改名（默认 `"cpu"`）
- `OllamaBackend` / `OpenAICompatBackend` 改 `host_compute_type`
- `playground_route` 响应字段改 + `playground_cli` 表头从 "CPU?" 改 "host"
- `web/src/lib/api.ts` `ApiBackend` interface 改三态 union type
- `docs/agent.md` + `docs/web-api.md` 同步说明

**API 破坏性变更**（pre-1.0 + 仅 dashboard 展示用，无外部消费者）：
GET /playground/backends 响应 `runs_on_cpu` 字段消失，新增
`host_compute_type`。

**未做的事 / 遗留**：
- 前端 `LlmBackendCards` 当前不渲染 host_compute_type（仅 status 标签）。
  以后如果加"按 host type 过滤"功能，三态 enum 已就位
- ADR-026（CLI flag 还是 config 文件）仍 seed —— 等 LlamaCpp / 第 4
  backend 落地再拍。本步只新增 anthropic 2 个 flag (`--anthropic-key` /
  `--anthropic-url`)，N×flag 模式未到拐点

**关联**：M3 step 1 arch-reviewer #6 / architecture.md 字段语义条目 ·
  ADR-016 (LLMBackend ABC 设计原则) · M3 step 2 commit `332d743`

---

## ADR-028 · device 信息分层 · dashboard summary vs inspect details

**Status**: ✅ accepted 2026-05-01（DEBT-022 PR-A commit `fe92583`）
**Date**: 2026-04-30 / 2026-05-01 升正式
**Context**: 2026-04-30 真机验证暴露 device card 信息薄（只 serial / product /
model / transport 4 字段）。用户诉求要补 SoC / RAM / 存储 / 电池 / 分区表 /
内存布局 / flash 布局 / 网络 / 温度。`alb_devinfo` 工具已实现 9 字段，
追加 ~50 字段后 dashboard 单卡装不下，必须分层。

**Decision**：选 (a) **2 endpoint 分层**。`/devices/{serial}/details` 用于
dashboard summary（PR-A 已落地，commit `fe92583`），`/devices/{serial}/system`
留给 inspect 详情页（DEBT-022 PR-B）。

**3 备选回顾**：

- (a) ✅ **2 endpoint 分层**：summary + system 各自维护。优势：dashboard
  payload 小、刷新快；劣势：2 个 endpoint 维护，summary/full 字段定义需对齐
- (b) **1 endpoint + level 参数**：`?level=summary|full`。劣势：level=full
  时 payload 大，dashboard 误传 full 会拖慢
- (c) **GraphQL 风格 fields 选择**：劣势：本仓没 GraphQL 基础设施、字段名
  暴露 schema 锁死

**为什么 (a)**：
- summary 30s polling、full 按需手动拉，刷新频率不一样 → 2 endpoint 自然
- summary 字段稳定（PR-A 实际落 13 字段：model/brand/sdk/release/abi/
  hardware/serialno/uptime/battery/storage + extras{soc,cores,khz,ram_t,
  ram_a,display,temp_c}），full 字段会随时间扩张（PR-B 加 partition /
  memory layout / flash layout / 网络接口） → 解耦版本演进
- PR-A 实际落地后 dashboard payload ~600 bytes（vs full ~5-10 KB
  预估）—— 30s × N 设备 polling 成本可控

**关联**：DEBT-022 / ADR-029（refresh 策略，独立维度）

---

## ADR-029 · device 信息刷新策略 · auto polling vs button vs WS push

**Status**: ✅ accepted 2026-05-01（DEBT-022 PR-A commit `fe92583`，简化版）
**Date**: 2026-04-30 / 2026-05-01 升正式
**Context**: device summary 数据有些字段动（电池 / 温度 / RAM 用量 / 存储用量
/ 在线状态），有些静（model / SoC / build / Android 版本）。刷新机制
3 备选。

**Decision**：选 (a) **`refetchInterval: 30000` auto polling + manual refetch button**。
PR-A 简化版只用 1 个 useQuery（不拆静态/动态字段），N=1 设备实测 30s × 1 = 1
fetch/min 成本可忽略。N≥4 设备时再考虑拆 2 个 useQuery。

**3 备选回顾**：

- (a) ✅ **`refetchInterval: 30000` auto polling + 手动 refetch 按钮**：
  react-query 原生模式。优势：dev 简单 / 跟现有 useBackends pattern 一致；
  劣势：N 设备 × 30s polling 会累 / electron 后台 tab 浪费
- (b) **button-only 手动刷新**：劣势：电池/温度永远滞后，违反"实时面板"定位
- (c) **WebSocket push**：劣势：alb-api 要主动 polling 板子（成本转移）+ 设计
  WS event schema + 频繁数据触发 React 重渲染抖动

**为什么 (a) 简化版**：
- N=1 设备时拆 2 个 useQuery 是过度设计 —— polling 成本 = 30s × 1 = 1
  fetch/min，可忽略
- N≥4 设备的拆分动作留给 PR-B 或之后真出现 polling 拥塞时再做（"don't
  design for hypothetical future requirements" 原则）
- manual refetch button 走 `queryClient.invalidateQueries(['device-details'])`
  全 cards 同时重 fetch（DashboardPage 顶层 RefreshCw 按钮）

**升级路径**（N≥4 设备时）：
- DeviceCard 内部 useDeviceDetails 拆成 useDeviceDetailsStatic（不
  refetchInterval）+ useDeviceDetailsLive（refetchInterval=30000，只查
  battery/temp/ram_avail）
- ADR-025 (polling 分层) 模式可复用

**关联**：DEBT-022 / ADR-028（分层 endpoint，独立维度）/ ADR-025（polling
分层模式 · 已落地的 backend health polling 可复用）

---

## ADR-030 (seed) · stream hook 抽象时机评估（useUart/Logcat/Terminal Session）

**Status**: seed（DEBT-022 PR-D/E 落地观察 · N=4 出现时拍板）
**Date**: 2026-05-01
**Context**: PR-C.b/PR-D/PR-E 三个 stream 风格 hook 已落地：
- `useUartStream` (~110 行) · read-only · WS /uart/stream
- `useLogcatStream` (~125 行) · read-only · WS /logcat/stream + filter/tags
- `useTerminalSession` (~190 行) · bidirectional · WS /terminal/ws +
  sendBytes/sendResize/HITL

共有逻辑（~80 行 / hook）：
- WS lifecycle（open/message/error/close）
- state machine: idle → connecting → ready → ended/error
- onBytes 订阅者 Set + 派发
- cleanup 在 unmount + manual disconnect

差异：
- read-only vs bidirectional（terminal 加 sendBytes/sendResize）
- 协议 close 帧形态（uart/logcat: `{type:"close"}` · terminal:
  `{type:"control", action:"close"}`）
- ready 后续帧（terminal 有 hitl_request / closed.exit_code）

**Decision**：暂不抽，留 seed。N=4（PR-G adb screenshot 用 streaming
fb 抓 / 或 PR-F metrics chart 复用 stream pattern）出现时再评估，跟 ADR-024
"ABC 第 1 个非首例消费者 = 免费 stress test" 同思路。

**3 备选**（待 N=4 时拍板）：

- (a) **抽 useStreamWs(path, opts) base hook**：返回通用 state/error/
  onBytes/cleanup，sub-hook 调 base + 加自己的 sendBytes/HITL 处理。
  优势：dedup ~80 行 × N；劣势：base hook 不知所有协议变体（HITL /
  exit_code / control 帧形态），还得加 hooks/callback 注入
- (b) **共享 utility 函数**（不抽 hook）：`createWsStateMachine()` +
  `createOnBytesEmitter()` 当工厂，hook 内部组合。优势：每个 hook 还
  自治；劣势：抽不彻底
- (c) **不抽，3 个 hook 共存**：N=3 不抽是合理保守做法（参考 L-020
  "N=3 才是抽 base 的安全时机"）

**为什么 seed 不立刻拍**：N=3 处于"ABC 第 1 个非首例消费者"边界，
PR-E 落地后 ShellTab 的 bidirectional + HITL 让差异性显著，base hook
的接口设计还不清晰（多塞 callback 还是抽 protocol adapter 不明）。
等 N=4 出现，base 接口形状会被第 4 个消费者"压"出来。

**何时拍板**：DEBT-022 batch 内出现第 4 个 stream 消费者时（PR-F
metrics stream 复用 / PR-G screenshot streaming / PR-H file pull
progress stream 等候选）。

**关联**：L-020 (N=3 才抽 base class) · ADR-024 (ABC 第 1 个非首例
消费者 = 免费 stress test) · DEBT-022 PR-C.b/D/E

**N=4 落地复核 2026-05-01**：PR-F (`useMetricsStream`) 落地后评估为
"协议差异化（JSON sample + history snapshot + control_ack）共有逻辑反少"，
不抽 base hook，等 N=5。详见 `.claude/knowledge/debts.md` PR-F 关闭段。

**N=5 落地复核 2026-05-01**：PR-H (`useFileBrowser`) 落地为 useQuery+useMutation
组合，**不是 stream hook**，对 ADR-030 不构成新数据点。stream hook 方向 N=5
仍未出现，seed 维持。下一次评估等 PR-C.c (双向 UART 输入) 或 PR-G v2
(streaming framebuffer) 出现。

---

## ADR-031 (seed) · filesync HITL 写在 endpoint 层 vs PermissionEngine

**Status**: seed（PR-H 落地观察 · M2 PermissionEngine 加 filesync 规则后再拍）
**Date**: 2026-05-01
**Context**: PR-H push endpoint 命中 sensitive 路径前缀（/system /vendor
/data /dev /proc /sys /persist /oem /boot /recovery /metadata，
/data/local/tmp 例外）需要 HITL 二次确认。M1 `infra.permissions.default_check`
现在只识 shell `cmd` 字符串，不接 filesync action。两条路：

- **(a) endpoint 层 inline HITL**（PR-H 选）：`files_route.device_push`
  自己判 `_is_sensitive_remote(remote)` + `force` flag，命中返回
  `requires_confirm=true`。缺点：HITL 规则散落 in routing；优点：M1
  engine 不动，0 接口面变更
- **(b) 下沉到 PermissionEngine**：扩展 `default_check` 接 `filesync.push`
  action，从 `input_data["remote"]` 读路径前缀；endpoint 直接走
  `transport.check_permissions("filesync.push", ...)`。缺点：要扩 engine
  + 加配置层（user 能改名单）；优点：和 shell HITL 同进同退，policy 集中
- **(c) endpoint 层简版 + 标记 follow-up**：（当前 PR-H）

**Decision**：选 (c) 为 v1。等 M2 PermissionEngine 加 filesync.push /
filesync.pull action 类型 + multi-layer config（defaults < profile <
session）时下沉。届时 endpoint 改成纯转发：`r = await transport.
check_permissions("filesync.push", {"remote": ...})` 命中 `behavior=ask`
就返回 `requires_confirm`，跟 shell HITL 完全同形态。

**何时拍板**：M2 PermissionEngine 扩展 spec 出炉时（与 ADR-013 / 权限
engine M2 路线绑定）。届时这条 seed 升正式 + 改 endpoint。

**关联**：DEBT-022 PR-H · ADR-013（PermissionEngine 设计 ·
M1→M2 路线）· `infra/permissions.py` default_check

---

## ADR-032 · Inspect 8 tabs 走 unmount/remount，不做 keepAlive

**Status**: accepted（perf-audit 2026-05-02 显式 trade-off）
**Date**: 2026-05-02
**Context**: PR-A/B/C.a/C.b/D/E/F/G/H ship 后 inspect 页 8 tab 全部接
真数据。`InspectPage.tsx` 采用 `tab === "X" ? <XTab /> : null` 的
unmount/remount 模式：

- 切走 tab → 完全 unmount，hook 走 cleanup（WS 关 / fetch abort / xterm
  dispose）
- 切回 tab → 重新 mount，hook init（new WebSocket / new useQuery /
  new Terminal()）

代价：切 tab 一次 ≈ 50-200 ms blocking + WS 重连 ~50 ms。3 个 stream
hook（uart/logcat/shell）尤其重，xterm.js 实例化 + WS handshake +
history replay 串行。

**Decision**：**保持 unmount/remount，不做 keepAlive**。

**3 备选**：
- (a) 当前 unmount/remount —— 简单，无背景占用，切 tab 慢 100 ms 量级
- (b) keepAlive（隐藏 tab 仍挂载）—— 切 tab 0 ms，但隐藏 tab 持续
  占 WS 带宽 + xterm 仍渲染（用户切走时 UART 仍 byte 流入 = 静默浪费）
- (c) 选择性 keepAlive（只 stream tabs 保活） —— 切 tab 50 ms，但代码
  +200 行（双层 mount state），且与 React 18 Suspense lazy load 冲突

**为什么选 (a)**：
- 用户行为模式：debug 时长时间锁定一个 tab，切 tab 频率 < 1/min
- 100 ms 切换延迟在能接受边界（< 200 ms = "snappy" 心理阈值）
- (b)(c) 的隐藏 tab 资源占用是**累计**问题：开 4 tab 一晚，UART byte
  通道一直吃 USB serial 带宽 + xterm 一直 render，远比"切 tab 慢 100 ms"
  代价大
- 4 个 stream hook cleanup 已验证 OK（`useEffect(() => () => cleanup(), [])`），
  unmount 路径无泄漏，技术债 0

**何时反悔**：
- 用户报告"切 tab 卡" → 触 ui-fluency-auditor 实测延迟，> 250 ms 再考虑 (c)
- React 19 出新 keepAlive 原语（Activity 组件）成熟时，可零成本上 (b)
  for stream tabs

**关联**：perf-audit `.claude/reports/perf-audit-debt022-2026-05-02.md`
finding MID #5 · L-020 (N=3 才抽抽象 · keepAlive 抽象 N=1 不上)

---

## ADR-033 (seed) · transport capability 用 ABC class-attr 标，不 hasattr 检测

**Status**: seed（PR-C.c review 5/02 提出，N=2 transport 出现时升正式）
**Date**: 2026-05-02
**Context**: PR-C.c bidirectional UART 加 `SerialTransport.open_session()`
公开 API。`uart_stream_route._run_bidirectional` 用 `hasattr(transport,
"open_session")` 检测能力 —— 是 duck-typing 检测。

**问题**：未来 SSHTransport / HybridTransport 给一个 `open_session` 占位
返回 `NotImplementedError` → `hasattr` 通过 → init_failed 而非
write_unsupported（误报到客户端）。和 DEBT-017 / ADR-024 已修过的
"LLMBackend dict-sentinel reachable=False" 反模式同形态（L-019 sentinel
反模式）。

**3 备选**：
- (a) **保 hasattr**：v1 简单，只支持 SerialTransport 真实需求，1
  consumer 不构成 spec 设计动力
- (b) **加 class-attr 显式 capability**：`Transport.supports_bidirectional_uart:
  bool = False`，SerialTransport 重写 True，路由用
  `getattr(type(transport), "supports_bidirectional_uart", False)`。和
  ADR-024 LLMBackend `class_attr: has_health_probe` 同 pattern
- (c) **细化 ABC**：把 open_session 提到 Transport ABC 上 + 抛
  NotImplementedError 默认实现。但污染 ABC 表面 — 不是所有 transport
  都该有"双向 UART"概念

**Decision**：**v1 选 (a) 保 hasattr，等 N=2 transport 出现时升 (b)**。
理由：
- 当前 N=1 (SerialTransport)，扩 ABC 等于"为还没存在的需求设计接口"，
  违反 L-020 (N=3 才抽抽象，本场景 N≥2 即可考虑)
- (b) 升正式时 SerialTransport 改 1 行 + Transport ABC 加 1 默认 attr +
  路由改 getattr 即可，cost 低
- 何时升：第 2 个 transport（最可能是 HybridTransport）想接 bidirectional
  UART 时

**何时拍板**：HybridTransport 实现 bidirectional UART / 或第 2 个 capability
detection 用 hasattr 出现时（任一触发 spec 出炉）。

**升正式时方法名固化**（arch-reviewer 2026-05-02 补）：升 ABC class-attr
时，方法名同步标准化为 `open_session/close_session`（避免下个 transport
起名 `acquire_session/release_session` 等再制造 hasattr 多名混乱）。
capability slot + method 命名约定一并立。

**关联**：L-019 (sentinel 反模式 · dict reachable=False 是 hasattr 同
形态) · ADR-024 (LLMBackend has_health_probe class-attr capability 已落)
· DEBT-017 close (sentinel 反模式实例)

---

## ADR-034 (seed) · alb-api 默认 bind 127.0.0.1，0.0.0.0 须显式开启

**Status**: seed（security-audit 5/02 提出 · 等下 milestone 切默认）
**Date**: 2026-05-02
**Context**: `src/alb/api/server.py:110` 默认 `ALB_API_HOST="0.0.0.0"` —
绑所有网卡。今日 PR-H + PR-C.c + PR-E.v2 ship 后，alb-api 暴露的
write 类 endpoint 实质增多：

- `POST /devices/{s}/files/push` (sensitive 路径 HITL，但 /sdcard 直通)
- `POST /devices/{s}/files/pull` (任意设备路径 → workspace)
- `WS /uart/stream?write=true` (字节直写 UART · 可中断 u-boot · 可注 sysrq)
- `WS /terminal/ws` (adb shell + HITL · approve once / session)
- `POST /devices/{s}/screenshot` / `ui-dump` / `system` 信息泄露

无 auth。任意 LAN 内可达者全功能调用。security-audit `MID 2`。

**3 备选**：
- (a) **保 0.0.0.0 默认 + 加启动警告**（v1 当前选）：commit `75a07d7`
  已加 `[alb-api] WARNING: bound 0.0.0.0 with no auth ...`，不破坏现
  有部署
- (b) **改默认到 127.0.0.1 + LAN 暴露走 `ALB_API_HOST=0.0.0.0`**：安
  全默认（principle of least privilege），但破坏 dev/CI 部署 — 用户
  从公开 GitHub Pages → 内网 alb-api 走 95 ssh tunnel + alb-api 在 95
  侧 0.0.0.0 监听 + windows 这边 ssh -L 转发，需要额外 docs 提示
- (c) **加 token auth 默认 + 0.0.0.0 仍开**：M3 step 3 候选，复杂度高，
  当前 milestone 不做

**Decision**：v1 选 (a) · 下个 milestone 切 (b)。

**何时拍板**：
- M3 step 3 / 或 alb-api 开始外网部署（GitHub Pages 直连 host 而不是
  dev 隧道）时，必须 (b) 至少 + 文档化 `ALB_API_HOST=0.0.0.0` 启动
  required for LAN
- 或 security-auditor 后续 audit 发现 MID 2 仍 outstanding 时升正式

**关联**：security-audit `.claude/reports/security-audit-2026-05-02.md`
finding MID 2 · L-027 (HITL bypass 已修，0.0.0.0 暴露面是放大该问题
的运维维度)

---

## ADR-035 · LlamaCpp 嵌入式 backend deferred · 改 step 顺序 step 2/3 互换

**Status**: ✅ accepted 2026-05-02（M3 ship 决策）
**Date**: 2026-05-02
**Context**: M3 原计划：step 1 OpenAICompat → step 2 LlamaCpp 嵌入式 →
step 3 Anthropic。step 1 已 ship（commit `344fb47`，2026-04-30）。
step 2 LlamaCpp 嵌入式调研后发现 4 个真实工程 footgun，不应当下做。

**4 footgun 评估**：

1. **sync/async 桥接复杂度**：`llama-cpp-python` 是 sync 库（C++ binding）。
   alb 全 async。chat 可用 `asyncio.to_thread()` 一调；但 streaming 需要
   thread + asyncio.Queue 双向桥接，每个 token 跨线程推送，正确性 / 取消
   / 异常传播都是非平凡工程
2. **依赖体积**：50+ MB binary wheel · pip 装失败要本机编译（要 C++
   toolchain · macOS / Linux 各架构 wheel 矩阵庞大）
3. **测试不可行**：单测需 GGUF 文件（最小也是几百 MB）；只能 importorskip
   skip 大部分逻辑，CI 几乎无覆盖
4. **抽象验证强度低**：LlamaCpp 跟 Ollama 都是 CPU 推理同形态
   （`host_compute_type="cpu"`）。M3 真正要回答的设计问题是 ABC 能不能
   容纳"完全异构"的 backend —— Anthropic（云 SaaS · 计费 / API key /
   速率限制 / 协议差异 / cache_control）是更强的 ABC 验证

**Decision**：M3 step 2/3 顺序互换，先 ship Anthropic（commits 81~84），
LlamaCpp 嵌入式 deferred。registry 保留 `llama-cpp` 条目 status="planned"
作占位。

**用户替代方案**：用 `openai-compat` backend 接 llama.cpp 自带的 OpenAI
兼容 server：
```bash
# 一端：起 llama.cpp 内置 server
python3 -m llama_cpp.server --model path.gguf --port 8080

# 另一端：alb 用 openai-compat 接它
alb chat --backend openai-compat --openai-url http://localhost:8080/v1 \
         --model default
```
零额外代码，已经 work。

**何时重新评估**：
- 用户明确报告"必须嵌入式 / 不能起 daemon"的真实场景（移动 alb-host 单
  进程发布等）
- llama-cpp-python 出现纯 async API（项目层修了 sync 桥接 footgun）
- 有"alb-host 必须 GPU 推理"的 backend 需求（届时 LlamaCpp on CUDA 是
  自然选择，host_compute_type="gpu" 字段已就位）

**关联**：ADR-016 (LLMBackend ABC) · ADR-027 (host_compute_type 三态) ·
M3 step 2 commits 81~84 (Anthropic 实际 ship)

---

## ADR-036 · 流式文件传输用 WS 而非 SSE / Job-Model · MID-6 协议形态拍板

**Status**: ✅ accepted 2026-05-06（MID-6 step 1-3 ship · commits 89-92）
**Date**: 2026-05-06
**Context**: 2026-05-02 functional audit MID-6 报"Files tab Pull/Push 无
Cancel 无 progress"。前端 useMutation 走同步 POST，没法在传输中给反馈
也没法 cancel。多 GB push 卡住时用户只能干瞪眼。3 个协议形态选项：

- (a) **WS per-op** — 新 WS endpoint，配置帧 → progress 流 → done/cancelled。
  Cancel 走控制帧 + 浏览器关 tab WS 断 = 自动 cancel
- (b) **Job model** — POST 起 job 返 `{job_id}` · GET /jobs/{id} 轮询 ·
  DELETE /jobs/{id} 取消
- (c) **SSE on POST** — POST 直返 SSE event 流，AbortController 关连接

**Decision**：选 **(a) WS per-op**，2026-05-06 commits 89-92 ship。

**理由**：

1. **Infrastructure 复用**：项目已有 4 WS endpoint（uart/stream / terminal/ws /
   logcat/stream / metrics/stream），WS lifecycle 模式成熟（_CloseState
   outer-finally 单 close-frame · L-026），新加 2 endpoint 复用现有形态
2. **Cancel 语义最干净**：浏览器关 tab → WS 断 → recv_loop catch → 通过
   queue 通知 pump → cancel adb subprocess。**同 1 套 cancel 路径处理
   两种 cancel 来源**（显式控制帧 + 隐式 disconnect）。Job model 需要单
   独 DELETE endpoint + job lifecycle，多一倍状态空间
3. **单 endpoint 无状态**：每个 WS 是独立连接，结束即 GC。Job model 需
   server-side job registry（cleanup / TTL / lookup），多一层架构债
4. **进度推送即时**：WS 是 push 模型，server 解析 adb stdout 即可 send。
   Job model 是 pull，client 轮询有延迟 + 无效请求

**拒方理由**：

- ❌ (b) Job model：2 额外 endpoint + job 状态机 + cleanup TTL + lookup 索引。
  对 alb 这种 single-user dev tool 复杂度溢出。Job model 价值在多 client /
  跨 session 场景（task 提交完关浏览器，下次连回拿结果），alb 用户在线全程
- ❌ (c) SSE：cancel 不可靠（依赖 server 检测 client gone），AbortController
  在 fetch 上的 cancel 是 client-side 关连接，server 进程是否真停取决于
  其框架。WS 控制帧是显式 in-band 信号，更可靠

**Protocol 落地**（commits 89-92）：

```
C → S  config first-frame  {local?, remote, force?}
S → C  ready               {direction, local, remote}
S → C  progress * N        {percent, bytes_transferred, file}
C → S  cancel control      {type:"cancel"}
S → C  closed (terminal)   {reason, ok, bytes_transferred,
                            duration_ms, error?}
```

**附带架构收获**（commit 90 调试触发）：

- L-031 立项：Python 3.11+ `asyncio.CancelledError` 是 BaseException 子类，
  `contextlib.suppress(Exception)` 不抓。finally 清理 cancel 过的 task →
  CancelledError 漏出 → testclient 报"看似无关"错误
- 嵌套 async generator (push_stream → _stream_transfer) outer aclose 不
  自动传染 inner，标准模式：`inner = gen(); try: async for x in inner:
  yield x; finally: await inner.aclose()`（这个不立 lesson · 已包含在
  L-031 反面教材内）

**未做的事 / 遗留**：

- 真机 e2e 验证（M3 step 4 · 用户 ad-hoc 操作 · 待）
- 进度反馈精度：adb 在 pipe mode 不一定输出 [N%] 行（modern adb 35.x 输出，
  老版可能不输出）。退化模式：useFileTransferStream 显示不确定动画（30%
  宽度 + 1.4s slide），用户仍看到"在动"
- POST `/files/pull` + `/files/push` 旧端点保留（backend 仍可用，前端不再
  调用），下个清理 batch 再删

**Effect 测试**: 841 pass（5/02 780 → +61：MID 收头 + Anthropic + retroactive
regression）/ typecheck 0 / sensitive 0 / 主 bundle 110 KB gzip 持平 ·
FilesTab chunk +1.01 KB gzip（hook + 进度 UI 全在 lazy chunk）

**关联**：DEBT-029 (audit MID 8/8 关) · L-026 (WS 多 task close-frame race ·
本 ADR 应用) · L-031 (suppress + 嵌套 generator · 本 ADR 实施触发) ·
ADR-024 (LLMBackend ABC capability · 同形态用 class-attr 暴露能力) ·
functional-audit-2026-05-02.md MID-6

---

## ADR-037 (seed) · transport 配置常量（retry / timeout / backoff）用 class attribute，不 module-level

**Status**: seed（part 132 立 L-034 时部分论证，part 134 self-audit
architecture-reviewer 建议升 seed）
**Date**: 2026-05-09
**Decider**: architecture-reviewer self-audit + 主对话同意

**Context**：

part 131 (`fb236ac`) 在 `SerialTransport` 加 ECONNRESET 重试时，把 retry
触发异常集合 + backoff 序列两个常量放在了 class attribute
（`SerialTransport._TRANSIENT_CONNECT_ERRORS` / `_CONNECT_BACKOFF_S`），
而不是 module-level 常量。L-034 lesson 明确 transport retry 范围按角色
判定（per-connection 网关 vs daemon），本 ADR 把"放在 class 上"这个
落点决策正式约定下来。

**Decision**：

Transport 子类的连接行为常量（retry trigger 异常集合、backoff 序列、
timeout 上限、connect window 等）**放 class attribute，不放 module-level
constants**。

**Why class attribute**：

1. **不同 transport 角色需要不同策略**（L-034）：ser2net per-connection
   网关 retry RST 必要；adb / sshd listen-socket daemon retry RST 掩盖
   真 bug。module-level 常量会被错误复用
2. **测试可干净注入**：`monkeypatch.setattr(Cls, "_X", ...)` 隔离作用域，
   不污染其他 transport 测试
3. **子类显式 override**：未来 SshTransport 加自己的 retry 必须显式
   `_TRANSIENT_CONNECT_ERRORS = (...)`，比 module-level 静默继承更难错
4. **与 ADR-024 / ADR-033 一致**：capability via class-attr pattern 已
   立先例（LLMBackend / Transport ABC），retry policy 是同 pattern

**Consequences**：

- 第 N 个 transport 加 retry 时，复制粘贴一份 class attribute 而非 import
  shared module-level constant —— 看似 dup，实际语义独立
- module-level shared constant 会让"为什么 SshTransport 用了 ser2net 的
  backoff" 变成 latent bug
- 当前 N=1 (Serial) 远未到拐点

**Reverses if**：3 个以上 transport 复用了完全相同的 retry policy（说明
确实是通用 transport 行为），抽 mixin / module-level 才有意义。届时
立新 ADR override 本 seed。

**关联**：

- L-034（per-connection vs daemon · 本 ADR 的语义来源）
- ADR-024（LLMBackend ABC capability via class-attr · 同 pattern 先例）
- ADR-033 seed（transport capability via class-attr · 同 pattern 邻居）
- 实操 commit `fb236ac` part 131 + `a1612aa` part 134

---

## ADR-038 · 每个 feature page / inspect sub-tab 配 use<X>.ts hook · 数据层与 view 强制分层

**Date**: 2026-05-21 seed (`244111a` AppTab) · 2026-05-22 promoted to
formal ADR after Playground (commit R `8a61c3e`) implemented the same
shape as a third independent instance — 5/22 arch audit LOW#6 confirms
the pattern.

**Decision**: 每个 feature page (top-level activity-bar 入口) **或**
inspect sub-tab 的数据层（query / mutation / mutation invalidation）
**必须**抽到独立 `use<X>.ts` / `use<X>Actions.ts` / `use<X>Chat.ts`
hook 文件。Component 只 import 这些 hook 加 UI 组合，**不直接**调
`useQuery` / `useMutation` / `useQueryClient` / `fetch*` / `post*` /
`connect()`。

**Rationale**: N=3 (PowerTab/usePower + AppTab/useAppActions +
Playground/usePlayground + usePlaygroundChat) 已稳定:

- 数据层与 UI 强分层 → 单元测试可以单独 mock fetch* / connect() 跑 hook
- mutation invalidation 集中（如 `useAppInstallMutation` 自带
  `onSuccess: invalidate ["app-list"]`），不再在 component 里重复
- 抽出来后 useEffect 依赖能稳定（hook 返回的 mutation 对象 stable
  across re-render）—— 配合 hook 内 `useCallback([])` 写法
- 跨 feature 复用（如 DevicePicker 引 lib/hooks/useDevices · AuditPage
  引 lib/hooks/useAuditStream · 见 AA `6e2c40f` layering 修）

**Counter-argument**: 单 mutation / 单 query 抽 hook 是过度抽象。但
feature page / inspect tab 平均 3-6 mutation + 1-2 query, 不抽就
500+ 行平铺。**单 query/0 mutation 例外**: 如 SystemInfo,
ChartsTab — inline useQuery 即可。

**变种**:
- Chat 类长流: 主 hook + 同名 `Chat.ts` 拆 WS lifecycle (usePlayground
  + usePlaygroundChat)
- Shared 跨 feature: 升到 `lib/hooks/` 而不是 `features/<X>/`
  (见 AA `6e2c40f` · arch HIGH#6 修)

**Status**: formal · 14 feature 中:
- 用模式: Power / App / Playground (主+chat) / Devices (lib/hooks) /
  AuditStream (lib/hooks) / Files / Sessions
- 单 query 例外: SystemInfo / Charts / UART / Logcat / Shell /
  Screenshot / UiDump / LogSearch / Diag (其中部分有同名 use*.ts 但
  形态不一致 · 下次改动时同款重构)

**Reverses if**：某 feature 演化成纯 view-only (只读 single endpoint
poll), 抽 hook 反而增重 — 案例提交 ADR override 本 ADR。

**关联**：

- L-020（ABC 第 1 个非首例消费者 = 抽象设计的免费检验 · N=2 不抽象）
  —— 本 ADR 在 N=3 升正式
- L-025（新 useQuery hook 必 sweep refetchInterval/OnWindowFocus 两 flag）
- commit F `244111a` (AppTab) · usePower.ts (PowerTab 原型) ·
  commit R `8a61c3e` (Playground 双 hook) · commit AA `6e2c40f` (lib
  共享层抽出)

---

## ADR-039 (seed) · 危险 / 长操作通用 UX hook 三件套 · useArmedAction + useElapsedSeconds + useDeviceReset

**Date**: 2026-05-21（commit D `f887198` useArmedAction · commit H
`aacf691` useElapsedSeconds · commit G `0f6f439` device reset 模式
未抽 hook 但模式重复 4 处）

**Decision**: 危险或长操作的 UX 用通用 hook 三件套表达，不每个 card
inline:

1. **useArmedAction(onFire, opts)** — 两步确认（first click 武装 ·
   8s timeout / 二次 click 真触发）· `{ armed, trigger, disarm }`
2. **useElapsedSeconds(active)** — 任何 30s+ 长 op 的实时 elapsed
   counter（防"静默 spinner"）
3. **useDeviceReset(device, ...mutations)** —（**待抽** · 当前 4 处
   inline `useEffect(() => mutations.forEach(m=>m.reset()), [device])`）
   切设备 reset 之前的 mutation 结果

**Rationale**:

- "危险 op 需 HITL / 长 op 需进度反馈 / 切设备需 reset" 这三类问题
  在 ar7 之外的项目（Doctor / Sessions / Files / Playground）也
  会遇到, 抽 hook 防衰减
- L-029（共享 modal 三件套基线）+ 本 ADR 共同构成"危险/长操作"完整模式

**Status**: seed · #1 #2 已实现 · #3 待抽（part L 入档但代码留给后续
commit, 4 处 inline 模式稳定后再抽）。当用户触发"加新 mutation
忘 device reset"事故时升级为强制 ADR。

**关联**：

- L-029（destructive op a11y 三件套）· 本 ADR 是其行为补集
- L-037（elapsed timer 必要性）· #2 的根因
- commit D / H / G · 三个 hook 的实操

---

（后续 ADR 在主对话决策时按此格式追加）

---

## ADR-040 · vitest config 和 vite config 拆双文件 · 实测真因 + 维护契约

**Status**: accepted · 关 5/25 arch audit MID-5 + DEBT-053

**Context**:

5/25 arch audit 怀疑双 config 是误诊（"vitest 的 defineConfig 也是从
vite re-export，应该可单 config 解决"）。AH-5 实测验证：

- vite 的 `defineConfig`（from `"vite"`）的 `UserConfigExport` 类型
  **不包含 `test` 字段**。加 `/// <reference types="vitest" />` 三斜
  线指令也不行 —— 它只 augment 周围环境声明，不 augment
  `UserConfigExport` 联合。直接写 `test: { ... }` 报 TS2769。

- vitest 的 `defineConfig`（from `"vitest/config"`）**确实** 暴露
  `test` 字段，但其 `UserConfig` 中
  `build.rollupOptions.output.manualChunks` 类型 **narrow 成 array-
  of-output 形式**，拒绝我们生产构建用的
  `{ xterm: [...] }` record 形式。

两个都是真错。单 config 任何一种写法都要在生产构建侧（manualChunks
易读形式）或测试侧（test 段无法识别）牺牲一个。

**Decision**:

保持双 config：
- `vite.config.ts` 用 `defineConfig from "vite"` · 写 build / server /
  resolve / plugins · **不写 test 段**
- `vitest.config.ts` 用 `defineConfig from "vitest/config"` · 写
  plugins + resolve + test 段 · **不写 build 段**

**Maintenance contract**:

任何 plugins / alias / resolve / 任何 spec 也需要的 vite-side 配置
**必须**在两个文件里同步写。今天两份文件共享：
- `react()` plugin
- `@` → `./src` alias

新加任何条件时检查两边。

**Rationale**:

- 单 config 试过（AH-5 临时合并）· 实测 build 失败 · 已删验证分支
- 替代方案"as any 强转 test 字段"或"魔改 vite 类型"都更脏
- 双 config 显式 · 维护契约清晰 · 类型完全正确

**关联**：

- 5/25 audit arch MID-5（vitest 双 config 真因误诊）· 本 ADR 是对那
  条 finding 的"实测后反驳"
- DEBT-053（hook test mock pattern · vi.hoisted N=2 抽 helper）
- 任何升级 vite / vitest 主版本时重测：可能未来某个版本两边类型趋同

---

## ADR-041 · hook 组织规则 · lib/hooks/ vs lib/ vs features/<x>/

**Status**: accepted — slot-allocation rule (lib/hooks/ vs lib/ vs
features/<x>/) still authoritative. The "raw hook + feature wrapper
必须分离" 子条款 amended by ADR-043 (wrapper 抽取临界 N ≥ 2 consumer)
on 2026-05-25.

**Context**:

5/25 arch HIGH-3: lib/hooks/ 与 lib/ 命名分裂 — 4 hook 分 2 目录无
判定依据 (useDevices / useAuditStream 在 hooks/ · useArmedAction /
useElapsedSeconds 在 lib/ 裸放 · useDashboardQuery 在 lib/ 裸放)。
AH-1 commit 把 4 个业务 hook 统一到 lib/hooks/ · useDashboardQuery
留 lib/ 作 query factory 例外。

**Decision**:

强制 3 槽：

- `web/src/lib/hooks/<useX>.ts` = **多 feature 共享的业务 hook** ·
  数据 fetch / WS lifecycle / 状态 hook · 必须只返 raw (服务端原
  shape) · 不允许 import features/ (反向依赖触发 arch HIGH)
- `web/src/lib/<utility>.ts` = **非 hook 的纯 utility** · api.ts /
  ws.ts / format.ts / deviceFormat.ts / dashboardQuery.ts (薄封装
  TanStack Query) / types.ts (跨 feature union) 这类 · 不带 React
  state · 不带 useEffect
- `web/src/features/<x>/use<Y>.ts` = **单 feature 专属 hook** · view
  projection wrapper / 业务编排 hook · 可以 import lib/hooks/ +
  features/<x>/types · 不允许跨 feature import (除非通过 lib/hooks/)

**Amendment to "raw hook + wrapper" sub-clause** (added 2026-05-25 by
ADR-043, restated 5/26 AO-5 for clarity)：原 ADR-041 第 4 段 "raw
vs view-model 拆分必须在 boundary 上显式 · feature wrapper 做
projection (mapToDeviceCard / mapAuditToTimeline) · lib hook 不知道
feature view shape" **只在 N ≥ 2 consumer 时强制**。N=1 时直接在
consumer 内 useMemo + 纯映射函数（放 features/<x>/mappers.ts 或
lib/<thing>Format.ts），不抽 wrapper hook —— AL-2 commit 撤回
useDeviceCards / useAuditTimeline 两个 N=1 wrapper 验证了这条。
**槽位规则（3 槽 · lib/hooks/ 不准 import features/）保留。** 详见
ADR-043 + L-052。

**Why**:

- AA commit (5/22) 把 useDevices / useAuditStream 提到 lib/ 但留在
  hooks/ subfolder · AG commit (5/25) 又把 useArmedAction /
  useElapsedSeconds 直接放 lib/ — bikeshed-prone (作者要二选一无
  judge)
- raw vs view-model 拆分必须在 boundary 上显式 · feature wrapper
  做 projection (mapToDeviceCard / mapAuditToTimeline) · lib hook 不
  知道 feature view shape

**Enforcement**:

- 任何 lib/hooks/<X>.ts grep 自身 import features/ → audit HIGH
- 新加共享 hook 默认 lib/hooks/ · 默认 raw shape · feature wrapper
  自己做投影
- code-reviewer agent grep checklist 加: lib/hooks/*.ts 内
  `import.*features/` → flag

**Rationale**:

- 单一规则消除"该放哪"的判断分歧
- raw / view-model 分离让 lib hook 测试不依赖 feature types
- 跨 feature 共享只通过 lib · 不通过 sibling feature

**关联**：

- AH-1 commit · arch HIGH-3 fix
- AH-2 commit · arch HIGH-4 fix (raw / wrapper split 实操)
- L-048 (提层提一半 = 反向依赖 trap)
- DEBT-053 (test mock helper 抽 · 跟随同一 lib/hooks/ 不变量)

---

## ADR-043 · wrapper hook 抽取临界 = N ≥ 2 consumer · 否则 useMemo inline

**Status**: accepted · 关 5/25 第二轮 arch HIGH-2 · 修订 ADR-041

**Context**:

5/25 第二轮 arch HIGH-2 反例 (`useDeviceCards` / `useAuditTimeline`)：
ADR-041 第 1 槽规则把 "raw hook 不返 view-model" 当硬约束 · AH-2
commit 顺应规则建了 2 个 thin wrapper hook (每个 ~10-12 行有效代码 ·
只服务 DashboardPage 1 个 consumer) · arch reviewer 抓 "wrapper 是为
规则的规则" + `mapAuditToTimeline` 跨 sibling cross-import (DEBT-055
也认了 mapper 归宿没定)。

**Decision**:

wrapper hook 抽取的临界数：

- **N=1 consumer**：raw hook + 纯 mapping 函数（放 `features/<x>/
  mappers.ts` 或 `lib/<thing>Format.ts` 看归属）+ consumer 内
  `useMemo`。不抽 wrapper hook。
- **N=2 consumer**：抽 wrapper hook 到首消费 feature (`features/<x>/
  use<Y>.ts`)，第二消费者从兄弟 feature import 这个 wrapper。
- **N ≥ 3 跨 feature**：把 wrapper 升到 `lib/hooks/<X>.ts` raw +
  每个 feature 各自 wrapper · ADR-041 第 1 槽规则适用。

**Why**:

- **N=1 wrapper 是 over-design**：thin wrapper 加 render boundary +
  API surface + 测试 mock 层 · 但没换来任何复用收益
- **N=1 用 useMemo inline** 让 consumer 看见完整 view 投影路径 ·
  reviewer 一眼看清 · 不需要打开 wrapper 文件看里面是不是 thin
- **mapping 函数** 抽到 mappers.ts 后 N=2 consumer 出现时升 wrapper
  代价 ~10 行 · 不预先付

**Enforcement**:

- 任何新增 `features/<x>/use<Y>.ts` thin wrapper (只是 useMemo + map)
  → audit MID · 该考虑撤回 inline
- mappers.ts 文件命名约定：feature-specific projection functions
  (mapFooToBar / dotFor / formatRel)
- lib/types.ts 文件命名约定：跨 feature 共享的 domain union (Transport
  / DeviceStatus 等 atomic enum)

**Rationale**:

- AL-2 commit 撤回 useDeviceCards / useAuditTimeline 实测：DashboardPage
  inline `useMemo` 反而比 wrapper 直观 · 行数差不多 · 0 测试要改
- 关联 N=2 抽象规则（L-020 已有）的 hook 化版本

**关联**:

- AL-2 commit · arch HIGH-2 fix
- L-052 (thin wrapper hook 是规则压力的副产物)
- DEBT-055 (mapAuditToTimeline 归宿)  → 关 (mappers.ts 决定下来了)
- ADR-041 修订 (第 1 槽规则保留 · "必须 wrapper" 条款撤回)

---

## ADR-044 · `lib/hooks/*` 复合 viewModel 返回必须 useMemo 稳定 reference

**Status**: accepted · 关 5/25 第三轮 arch HIGH-1

**Context**:

5/25 第三轮 arch HIGH-1 实测发现：AM-2 给 `useDevices` 加了
`useMemo<DevicesRawViewModel>(...)` 包返回对象 · 但 `useAuditStream`
没加 · 返回裸 object literal。consumer DashboardPage 用
`useMemo(() => ({...auditStream, events: auditEvents}), [auditStream,
auditEvents])` 是 **假 memo** —— auditStream 每 render 新 ref ·
deps 永不命中 · 下游 ActivityTimeline / KpiStrip / 其他 React.memo
子组件全 invalidate。

同源问题影响所有 lib/hooks/* 复合 hook (return `{ field, fn, ... }`
而非 primitive)。useDevices 和 useAuditStream 写法不对称是 reviewer
+ 新人陷阱。

**Decision**:

`web/src/lib/hooks/<X>.ts` **复合 viewModel** 返回必须 `useMemo`
包裹 · deps 列稳定 reference 字段：

```ts
return useMemo<ViewModel>(
  () => ({
    field1: stableField1,
    field2: stableField2,
    callback1, // 已是 useCallback
  }),
  [stableField1, stableField2, callback1],
);
```

**例外**：
- 返回 primitive 或单值的 hook (useElapsedSeconds 返 number ·
  useArmedAction 返 single fn · useDashboardQuery 透传 react-query
  hook 结果) 不需要 · 反正 ref 本来就稳
- 内部 callback 必须用 useCallback (不要每 render 新函数)
- state 数组用 useState(()→[]) 初值化 · React 自身保证 ref 稳

**Enforcement**:

- code-reviewer agent grep checklist 加：`lib/hooks/*.ts` 的 return
  是裸字面量对象 `return { ... }` 而非 `return useMemo(...)` →
  flag MID
- 新加 lib/hooks/<X>.ts 必须从这条规则开始 · 不再"先 ship 后包"
- 现状审计 (5/26)：useDevices ✓ · useAuditStream ✓ (AO-2 修)
  · useWsChatStream 返 `{ phase, settled, start, cancel, reset }`
  · phase/settled 是 useState · start/cancel/reset 是 useCallback
  · ref 已稳但**未 useMemo 包** → DEBT-064 候选 (低优先 · 不破)

**Rationale**:

- 消除 useDevices vs useAuditStream 对称性 trap (reviewer 看其中一
  个会以为另一个也包了)
- consumer 可以放心 `useMemo([hookReturn])` · 不用拼字段级 deps
- 配合 ADR-043 (N≥2 才抽 wrapper) · raw hook 已经 stable · consumer
  inline useMemo 不需要再包字段级 deps (AO-2 DashboardPage 仍写
  字段级是冗余防御 · 历史代码可逐步收敛)

**关联**：

- AO-2 commit (useAuditStream useMemo wrap · 关 H1)
- AM-2 commit (useDevices useMemo wrap · ref 稳定立 baseline)
- DEBT-047 (WS pool dedup · 未来 connect() 也要 useMemo 包返回)
- L-054 (lib/hooks/* 返回 ref 契约不对称是 trap)

---

## ADR-045 · `lib/ws.ts` shareKey opt-in pool + late-joiner microtask replay

**Status**: accepted · 关 DEBT-047 (5/26 AP-1)

**Context**:

`/audit/stream` 同屏被 AuditPage + Dashboard timeline + LiveSession
spark 3 处 hook 独立 connect · 服务端 fan-out 3 倍 snapshot · 客户端
3 倍 setState。需要给 N→1 dedup · 但不能影响 useWsChatStream (chat
per-turn 必须独占 socket · 池化会跨 turn 喂错 token)。

且服务器协议是"open → 收到 client config → 发 snapshot → live deltas"
。第 N 个 view subscribe 时 underlying 早已 open + snapshot 发完 ·
那个 view 永远等不到 open / 等不到 snapshot · UI 卡 connecting。

**Decision**:

`connect(path, opts)` 的 `opts.shareKey` 控制行为：

- **缺省 (`shareKey === undefined`)**: 走 `soloConnect()` · 1 caller
  1 underlying socket · 完全保留 pre-DEBT-047 语义 · useWsChatStream
  / 任何不愿被池化的 caller 默认安全
- **提供 (`shareKey: string`)**: 进 `Map<path|shareKey, PoolEntry>`
  pool · 同键复用 · 不同键 / 不同 path 互不影响 · view.close()
  refcount-- · 0 时真 close + 删 entry

新 view subscribe 时若 underlying.readyState === OPEN · 排
`queueMicrotask` · 给 listener 顺次回放 `{kind:"open"}` +
`entry.cachedSnapshot` (若有) · 微任务前 view.close() / unsub 都
能 short-circuit (防 leak listener)。

cachedSnapshot 只在 JSON `data.type === "snapshot"` 时更新 · delta
不覆盖 · 保证晚到 view 总收"最近一次完整 snapshot" 而非中间帧。

**备选**:

1. ❌ **总是池化** — useWsChatStream 跨 turn 复用会喂错 token · 拒
2. ❌ **server-driven dedup** (按 session_id 让服务器记 subscriber
   list) — 后端复杂 + 跨进程更难 · 暂不动 backend
3. ❌ **subscribe 时 short-poll fetch snapshot** — 多 1 个 HTTP
   round-trip · 比 microtask replay 慢 100ms+
4. ❌ **同步 emit open in subscribe()** — 破坏"事件总是异步" 的
   listener 契约 · 容易和 listener 内 `clientRef.current = client`
   赋值争 · microtask 队列保证赋值已完成

**Trade-off**:

- 放弃 (历史 · 5/26 AR-1 后已消除)：~~晚到 view 第一次仍走
  `useAuditStream` 的 open handler · 触发 `client.send({minutes,
  include_metrics})` · 服务器**再发一次 snapshot 广播给所有 view** ·
  已有 view setState 重渲一次。冗余 1 帧。~~
- **5/26 AR-1 修正**：AR-1 给 pool 加 send dedup-by-payload-per-epoch
  (见 ADR-046)。晚到 view 的 synth-open handler 调 `client.send(config)`
  时 · 若同 epoch 内首 view 已发同 payload · 直接 drop · 服务器不重发
  snapshot · 那 1 帧冗余消除。**新 trade-off**: 晚到 view 必须靠
  `cachedSnapshot` 微任务回放 bootstrap · cachedSnapshot 可能已过期
  (数小时未刷新) · 引入"stale snapshot + 未来 delta" 视觉短暂不一致。
  风险面从"罕见"(原: 偶发 reconnect 风暴期) 升为"必然" (任何 N≥2
  shared view 的 bootstrap)。见 **DEBT-065** (5/26 第五轮 arch HIGH-1
  + ui-f MID-1 提) · LOW→MID 升级 · 建议加 `cachedSnapshotAt` +
  age check (e.g. >5min 不回放 · 强制 send 跳 dedup) 治本。
- 获得：N→1 socket / N→1 server fan-out / N→1 server-bound send
  (AR-1 加强) / 晚到 view 0 延迟 converge
- 协议 0 改 · backend 0 改 · useAuditStream 0 改 (AM-3 已 pre-wire)

**Enforcement** (写进 architecture-reviewer agent grep checklist):

- 任何新 `lib/hooks/*.ts` 用 `connect()` · 必须明确"是否能池化":
  - 一次性 turn-scoped / 上下文不能共享 → **缺 shareKey**
  - 多 caller 配置完全一致 / 共享视图合理 → **填 shareKey** ·
    用 `JSON.stringify(config-knobs)` 字符串
- `shareKey` 序列化字段顺序是 wire 契约 · 改要更新
  `useAuditStream.shareKey.test.ts` 钉住的字节序列
- `lib/ws.ts` cachedSnapshot 探测条件 (`data?.type === "snapshot"`)
  是 duck typing · 协议方加新 snapshot-shape 消息时需要扩这里

**触发推翻条件**:

- 出现"配置略有差异但还是想池化"的 caller (e.g. minutes=5 vs 30 想
  共享底层 socket) → 需要 server-side multiplex 或重设计 shareKey
  策略
- 微任务回放仍跟 useState 时序竞 (实测 race) → 改 task / 改 sync
- WS server 端引入 真 server-side dedup → 客户端 pool 退化为 no-op
  · 删 makeView 简化
- **DEBT-065 真实命中** (stale cachedSnapshot 被用户 visible) → 重
  evaluate dedup vs fresh snapshot 取舍 · 可能要把 dedup 改成
  payload-equality + age 双键

**关联**:

- DEBT-047 (CLOSED by AP-1 / AP-2 / AP-3)
- DEBT-065 (MID · cachedSnapshot 过期路径 · AR-1 后 risk 放大)
- ADR-022 (Dashboard 同页双 WS 实例 · pool 不能跨 metric 配置共享)
- ADR-041 + ADR-043 + ADR-044 (hook layer 三槽 + N≥2 wrapper + 复
  合 return useMemo · 同源工程化主题)
- ADR-046 (5/26 AR-1 send dedup-by-epoch 单独决策 · 备选 A/B/C +
  推翻条件)
- AM-3 commit (`useAuditStream` shareKey pre-wire) · L-055 (pre-wire
  让 DEBT 落地变 1-file diff)
- L-056 (lib 行为契约修改即使 caller 0 改也需 ADR · ADR-046 案例)
- `web/src/lib/ws.ts:80` (`PoolEntry`) / `web/src/lib/ws.test.ts`
  (28 spec)

---

## ADR-046 · `lib/ws.ts` pool view.send() epoch-scoped payload dedup

**Status**: accepted · 关 ui-f HIGH-1 (5/26 第四轮) · AR-1 落地

**Context**:

ADR-045 落地 (5/26 AP-1) 后 · 第四轮 ui-fluency-auditor 实测 reconnect
风暴: N view 共 1 pool entry · underlying socket 网抖 reconnect →
"open" event fan-out 给 N listener → N 个 `useAuditStream` listener
各自调 `client.send({minutes, include_metrics})` → 服务器收 N 次同
config → 回 N 次 snapshot → fan-out N×N = N² setState。

需要把 N 次冗余 send 收敛到 1 次 · 但不能改 caller 协议 · 也不能改
后端。

**Decision**:

`makeView.send(data)` 在 view 层做 **per-epoch payload dedup**:

- `PoolEntry` 加 `currentEpoch: number` · underlying 每次 "open"
  事件 (含 reconnect) bump 1
- `PoolEntry` 加 `lastSentPayload: {epoch, payload: string} | null`
  tracker
- view.send() 流程:
  1. binary 帧 (ArrayBuffer / typed array / Blob / SharedArrayBuffer)
     完全 pass through · 不 dedup
  2. text-shape (string / object) 序列化为 string · 与 tracker 比对:
     若 `tracker.epoch === currentEpoch && tracker.payload === payload`
     → drop
  3. 否则更新 tracker · forward 到 underlying.send()
- 新 epoch 来时 tracker.epoch !== currentEpoch · 自然 mismatch ·
  payload 被 re-send · 服务器拿到新 config

**备选**:

1. ✅ **A (当前 · 选)**: pool 内部记 epoch · view 透明 dedup · caller
   0 改 · 复杂度内聚在 ws.ts
2. ❌ **B**: 让 caller 显式标 `send(data, {idempotent: true})` · 更
   灵活但每个 caller 都要思考 idempotency · 增加心智负担 · YAGNI
   今天
3. ❌ **C**: 服务端 dedup 配置消息 (服务器侧改) · 后端复杂 + 多进程
   去重难 · 拒

**Trade-off**:

- 放弃: 灵活性 — 若未来 caller 需要"不同 payload 也算重复" (e.g.
  不同 minutes 都该幂等) 或"同 payload 跨 epoch 主动 force-send" ·
  A 不直接支持 · 需扩 send opt (升级备选 B)
- 获得: caller 0 改 · 复杂度全在 ws.ts · 1 文件 diff · 7 spec 钉契约
- 放弃: 极端 corner case · 若 caller 不慎传含 `{时间戳: now()}` 的
  payload · 每次序列化都不同 · dedup 永不命中 · 退化为无 dedup ·
  silent perf 退化 (但功能 0 影响)

**5/26 AT-2 修正 (第六轮 code/arch HIGH-2)**: 原 AS-2 实现把
"stale-snapshot 路径 force re-send" 信号塞到 `entry.lastSentPayload
= null` · 这是 **entry 级状态** · view A 触发 clear 把 dedup tracker
撕掉 · 影响所有 sibling view 的下一次 send · 等效于 N view × N 次
force-send → N² snapshot 重发 · **正好打掉本 ADR 想省的那部分**。
AT-2 改为 **per-view `forceNextSendFresh` flag** · 只让"观察到 stale
cache 的那个 view" 跳 dedup 一次 · sibling view 的 dedup tracker
不被破坏。dedup 不变量重新闭合: same `(epoch, payload)` 跨 view 仍只
到服务器 1 次 (除非有 view 显式 force-fresh)。

**Enforcement**:

- 任何 lib/ws.ts send dedup 行为变更必须先改本 ADR
- 新增 `send(opt)` 字段要在 Options interface JSDoc 标 "see ADR-046"
- ws.test.ts 7 spec 是契约 baseline · 删除任何一条要更 ADR-046 推翻
- **新增 entry-level mutable state 必须先评估 "view 间 side-effect"** ·
  AT-2 教训: pool entry 是共享的 · 单 view 操作不该 implicitly 影响
  sibling view 的决策状态 · force-flag 等"一次性触发器"必须 per-view

**触发推翻条件**:

- 出现真需"不同 payload 也想 idempotent"caller → 升级到备选 B 加
  `send(data, {idempotent: true | "same-payload-same-epoch"})` opt
- 出现真需"同 payload 跨 epoch force-send"caller → 加
  `send(data, {forceFresh: true})` opt
- 服务端引入 application-level dedup → 客户端 dedup 退化为 no-op
  · 删 send dedup 简化
  · **协调成本**: 撤销前需后端 PR 同步落地 + 前端发版滞后 ≥ 1 release ·
    避免窗口期"前后端双重 dedup" 或 "双不 dedup" 错位

**关联**:

- ADR-045 (shareKey opt-in pool · 上层决策 · trade-off 节已更新指向
  本 ADR)
- DEBT-065 (MID · cachedSnapshot 过期 · AR-1 后 risk 放大 · 本决策
  的副作用)
- DEBT-068 (LOW · pool dedup 真实命中率追踪 · 验证本决策实际收益)
- AR-1 commit (5/26 落地)
- L-056 (5/26 新写 · "lib 行为契约修改即使 caller 0 改也需 ADR"
  · 本 ADR 是案例)
- `web/src/lib/ws.ts` (`makeView.send` 实现) / `web/src/lib/ws.test.ts`
  (7 dedup spec)

---

## ADR-047 · `lib/ws.ts` Pool-level Options · first-caller-wins 语义

**Status**: accepted · 5/26 AU-4 (第七轮 arch MID-4)

**Context**:

`lib/ws.ts` 的 `Options` 接口 4 个 pool-level 字段分两类:

**Entry key 字段 (1)** — 参与 pool 寻址 · 不同值 → 不同 entry · 不在
同 entry 内"竞争":
- `shareKey` (since DEBT-047 AP-1) · 和 `path` 组成 entry key tuple

**Entry value 字段 (3)** — 同 entry 内只有 1 份值 · "first caller to
mint the pool entry wins · 后续 callers 的同字段值 silently ignored":
- `maxBackoffMs` (since 项目早期)
- `noReconnect` (since 项目早期)
- `staleSnapshotMs` (since AT-3)

这 3 个 value 字段各自 JSDoc 里散写 "this matches the existing
first-caller semantics" · 但**全 codebase grep 不到一个 ADR 立这条
规则**。后续 reviewer / 新 contributor 加第 5 个 pool-level 字段时
不知道该明示 first-caller-wins · 容易破。第七轮 arch MID-4 提:"4 个
JSDoc 文字契约 + 0 个 ADR 强制 = 一致性靠 reviewer 偶然发现"。

**当前观察 (AV-1 / 5/26 第八轮 arch MID-2 修订)**:
- `staleSnapshotMs` — **已实战验证**: useAuditStream 3 caller 共 entry
  · 验证过跨 caller 协调脆弱 (AU-3 commit 已统一 30min)
- `maxBackoffMs` — **已实战验证**: 所有 caller 用 lib default · 0 caller
  override · "first-caller-wins" 自然不冲突
- `noReconnect` — **0 场景验证**: 唯一传 `noReconnect:true` 的 caller
  是 `useWsChatStream` (chat 一次性 · 不要重连) · 但它**不传 shareKey ·
  走 soloConnect · 0 进 pool**。"first-caller-wins" 对 noReconnect
  当前**未经实战** · 若未来出现 `{noReconnect:true, shareKey:"X"}` 与
  `{noReconnect:false, shareKey:"X"}` 共 entry · first-caller-wins 立刻
  破 (chat 一次 ping 锁死 entry · 持续监听 view 拿不回 reconnect ·
  ADR-047 必须重审)

**未验证字段处置** (AW-2 / 5/27 第九轮 arch MID-2):
"0 场景验证"标注只是观察 · 不是结论。`noReconnect` 字段不能永远停在
这个状态 — **下一次 audit cycle · 或第一次真出现 chat+listener 共 entry
case (无论哪个先到)**，必须从以下 2 选 1 落槌:

- **方案 X (推荐 · 字段降级 ViewOptions)**: `noReconnect` 从
  pool-level Options 移除 · 改走 per-view state (chat 的真实语义就是
  per-view 一次性 · 不该是 entry 字段)。配合 DEBT-078 PoolOptions /
  ViewOptions 拆分一起做。
- **方案 Y (字段保留 + 显式拒绝合并)**: `noReconnect` 留在 Options ·
  但 connect 命中现有 entry 时若 `opts.noReconnect !== entry.noReconnect`
  立即 throw (不走 first-caller-wins silent ignore)。比 X 改动小 · 但
  违反 ADR-047 主条款 (entry value silently win) · 需要给 noReconnect
  开特例条款 · 复杂度增量真实。

**禁止**: 不允许在 noReconnect 字段上保持当前的"first-caller-wins 但
未经验证"状态超过一个 audit cycle。下一轮 (或触发事件) 必须落子。

**Decision**:

`lib/ws.ts Pool-level Options` 的 **value 字段** (`maxBackoffMs` /
`noReconnect` / `staleSnapshotMs`) 遵循 first-caller-wins:

- pool entry 由 (path, shareKey) tuple 创建 · entry 一旦创建 · 所有
  value 字段值锁死
- 后续 `connect(path, opts')` 同 (path, shareKey) 命中现有 entry →
  返新 view · 但 entry 内部 value 字段不变 · `opts'` 同字段值 silently
  被丢
- `shareKey` 是 entry key · 不参与 value 竞争 (不同 shareKey → 不同
  entry · 各自独立)
- view-level options (未来扩展) 应该走不同机制 (per-view state in
  makeView · 不存 entry · 见 AT-2 `forceNextSendFresh` 模式)

**备选**:

1. ✅ **A (当前 · 选)**: first-caller-wins · documented + enforced by ADR
2. ❌ **B (last-caller-wins)**: 第 N 个 connect 改 entry 字段 · 破坏
   稳定性 (Dashboard 设 5min · AuditPage 后到改 30min · Dashboard 静默
   收到行为变化)
3. ❌ **C (per-caller override)**: 每个 view 持本 view 的 options · 需
   makeView 拷一份 · 复杂度 + memory · 暂无 use case 证明值得
4. ❌ **D (assert 不一致即抛)**: 第 N caller 字段值跟 entry 不同 → throw
   · 太严 · 破坏 caller 独立性 (Dashboard 和 AuditPage 不该需要协调)

**Trade-off**:

- 放弃: 灵活性 — caller 无法保证自己的 options 真生效 · 必须依赖
  约定 (所有 caller 同字段传同值)
- 获得: pool entry 行为可预测 · 不会因 caller mount 顺序 silently 漂
- 获得: 0 复杂度增量 · 当前实现已是 first-caller-wins

**Enforcement** (写进 architecture-reviewer agent grep checklist):

- 任何新加 `Options` 字段必须在 JSDoc 标"first-caller-wins"且关联本
  ADR
- 新 caller 用 pool-level option 时 · 若同 shareKey 其他 caller 已存
  在 · 必须传相同值 (or assume 自己拿不到这个值的预期效果)
- 跨页面共享 pool entry 的 callers (Dashboard / AuditPage 同 /audit/
  stream) 应**统一选 1 个常量** · 避免各自传不同值后 first-caller-wins
  silently 失效
- spec 钉契约: `useAuditStream.shareKey.test.ts` 已示范怎么钉
  shareKey 不含 staleSnapshotMs

**触发推翻条件**:

- 出现真需 per-view override 的 caller (e.g. 同 entry · view A 想
  noReconnect=true · view B 想 false) → 该字段从 Options 升级为
  per-view state · 走 makeView 闭包模式 (AT-2 forceNextSendFresh
  case)
- pool-level 字段数 ≥ 6 → 考虑拆 `PoolOptions` (entry-level) vs
  `ClientOptions` (view-level) interface (DEBT-078 综合拐点 watchdog
  的触发条件之一)

**关联**:

- ADR-045 (shareKey opt-in pool · entry 创建机制)
- ADR-046 (send dedup · 也是 entry-level 状态 · 但 view 可以 force
  跳一次 = per-view override 范式 见 AT-2)
- DEBT-078 (lib/ws.ts 综合拐点 watchdog · 5 字段时考虑拆 PoolOptions
  vs ClientOptions)
- AU-3 commit (useAuditStream + Dashboard + AuditPage 全部 wire
  30 min · 是 first-caller-wins 跨 caller 协调的样板)
- L-056 (lib 行为契约修改即使 caller 0 改也需 ADR · 本 ADR 是案例)


## ADR-048 · WS 消费者边界：何时走 `lib/ws.ts`、何时允许 raw WebSocket（round11 AR9-4）

**背景**: 全仓 7 个 WebSocket 消费者，只有 2 个走 `lib/ws.ts` 的 `connect()`
（`useAuditStream` 池化 · `useWsChatStream` solo），其余 5 个 inspect hook
（`useUartStream` / `useLogcatStream` / `useMetricsStream` / `useTerminalSession` /
`useFileTransferStream`）各自 `new WebSocket(wsUrl(...))` 手写生命周期（~100 行/个）。
ADR-045 的 Enforcement 只约束"新 `lib/hooks/*.ts` 用 `connect()` 必须决定是否池化"，
对 `features/` 下手写 raw WebSocket **零规则** —— 新 contributor 加第 6 个流式 hook
时无边界可循（round11 AR9-4：decisions/lessons/debts grep 0 命中）。

**决定（边界规则）**:

| 流的特征 | 走哪 |
|---|---|
| 共享（多 view 同 URL+参数）/ 可重连 / JSON 帧为主 | **`lib/ws.ts`**（池化 `shareKey` 或 solo `noReconnect`） |
| 一次性 · 用户显式 connect · binary/双向会话为主（UART 双向 / terminal PTY+HITL / file transfer progress+cancel） | **允许 raw `WebSocket`**，但 hook JSDoc **必须写明为何不走 lib/ws** |

**为什么不现在统一重写这 5 个**: 它们协议各异（UART 双向 binary / terminal HITL /
file transfer progress+cancel），强行抽成一个 client 是 premature —— 注意这是"5 份
**重复**未抽象"而非"1 场景提前抽象"，方向与 L-052 相反，所以保留 raw 是对的。但
"保留 raw" 必须是**有意决定 + JSDoc 记录**，不是默认放任。

**代价 / 已知**: lib 层的容错改进（AR-3 listener try/catch 不截断 fan-out、
Blob→ArrayBuffer demux、统一 error 事件）不覆盖这 5 个 hook；CR-1 / UI-3 的
stale-socket 守护就是因此要在 5 个 hook 各修一遍。**触发迁移**见 DEBT-081：任一
inspect hook 将来需要 reconnect/backoff，或踩 listener-throw 截断 fan-out，就迁到
`lib/ws.ts` solo 模式（`noReconnect` 可表达"UART 板掉了不自动重连"语义），而不是再
手写第 N 份。

**Enforcement**: architecture-reviewer grep checklist 加一条 —— 见到
`features/**/use*Stream*.ts` / `use*Session.ts` 里 `new WebSocket(` 时，确认 JSDoc
有"为何 raw 不走 lib/ws"说明；新增第 6 个流式 hook 必须先对照本表。

**关联**: ADR-045（池化 opt-in · 本 ADR 补 features/ 边界）· ADR-046/047（pool 语义）·
DEBT-081（迁移触发账）· CR-1 / UI-3（5 hook 各修一遍 stale-socket 的代价实例）·
L-052（thin wrapper 是"提前抽象"反面 · 本 ADR 是"重复未抽象"的有意保留 · 两者区分）

## ADR-049 · metrics 物化读模型：event bus 双消费者 + MetricStore 投影 + per-backend 吞吐

**背景**: Dashboard be-card 要 per-backend token 吞吐 sparkline（round10 MBC-3 ·
web UI 多后端并发）。现状 `tps_sample` 事件无 backend 维度，`/metrics/summary`
**每次轮询全量扫 `events.jsonl`**（O(整个日志)，DEBT-008）。两个被否的方案：
(a) 新建 `infra/throughput.py` 内存分桶环 + 新端点 —— 与已有 `tps_sample` /
`metrics_summary` 管线平行重复（架构评审 v1 否）；(b) 最小改动只给 tps_sample 加
backend + `/metrics/summary` 加 `group_by` —— 但保留扫文件读路径，per-backend
`group_by` 把 O(文件) 放大到分组量级（用户定调"按性能/扩展性最优、该重构就重构"，
v2 不够）。

**决定（物化读模型 / materialized read model）**:

1. **event bus 第二类消费者** —— `EventBroadcaster.add_listener(fn)` 同步进程内
   listener，`publish()` 里**先于** queue 扇出 + 落盘调用，每个包 try/except 隔离。
   契约：cheap · 同步 · 无 IO · 不抛。与原有异步 queue 订阅者（WS streamer，容忍背压、
   满则 drop）并存。
2. **`infra/metric_store.py` MetricStore** —— bus 的投影：注册成 listener，维护按
   `(kind, backend/session)` 的滚动样本窗口。`summary(window, session_id)` +
   `throughput_series(window, buckets, group_by=backend)`，O(1) ingest / O(窗口) 读。
   幂等 `attach/detach`，`reset_metric_store()` 供测试。
3. **重构 `/metrics/summary` 双路径** —— 窗口被读模型覆盖（`is_warm`）走内存；冷启动
   或窗口 > 容量（900s）走原 `_read_tps_samples` **文件扫兜底**（免 tail 回放，复用
   现成代码）。新增**非破坏** `source:"memory"|"file"` 字段。`group_by=backend` 始终
   走内存（live near-window view）。
4. **`tps_sample` 加 `backend` + `source` 维度** —— chat_route 传 `backend=llm.name`；
   playground WS 接 sampler（`source="playground"`，补吞吐盲区）。口径 = `on_raw_token`
   （含工具轮 = "backend 实际吐了多少 token"）。
5. **前端** —— `fetchBackendThroughput()` → `useBackends` 折进 `BackendRuntimeState`
   up 变体（非 60s static 的 BackendCardData，消除撕裂态）→ `LlmBackendCards` 用抽出的
   `scaleSparkPoints`（per-card 归一化）喂 `<Sparkline>`，无数据走 `empty` 平虚线。

**为什么不建平行管线 / 不丢文件持久化**: 读模型是 bus 事件的**投影**（单一源 =
bus），不是第二个记录点 —— 与 ADR-027"扩字段不扩系统"、ADR-021"加 metric kind 类"
同源。`tps_sample` **仍落 events.jsonl**（留离线历史 + 文件兜底），写量不变，故
DEBT-006（rotation）不被本 ADR 加重。

**边界（write model vs read model 解耦）**: `events.jsonl` = durable 写模型（audit
历史、长窗口兜底）；MetricStore = ephemeral 读模型（近窗口快读）。读模型**永不读文件**；
重启后空、一个窗口内自填，期间 scalar summary 落文件兜底（`source` 如实标）。

**代价 / 已知**:
- 单进程假设：同步 listener + 内存投影只在单 worker 成立（uvicorn 当前单 worker）。
  多 worker 会分裂读模型 —— 上多 worker 前需把读模型移到共享存储或每 worker 各算。
- 重启后约 1 窗口 scalar summary 走文件兜底、sparkline 部分/空（可接受，自愈）。
- 长窗口（> 容量 900s）summary 仍走 O(文件) 扫 —— DEBT-008 的扫文件成本只剩这条罕见路径。

**Enforcement**:
- listener 契约由 `publish()` 的 try/except 隔离兜底（一个坏 listener 不炸 chat 持久化）。
- **新 metric 需求先查 `tps_sample` / `metric_store` 现有管线能否加字段/投影满足**，
  再考虑新存储；见到提案新建 `infra/*throughput*` / `*rate*` 内存聚合先 grep
  `tps_sample` + `metric_store`（L-060）。
- `conftest.py` autouse fixture 成对有序 `reset_metric_store()+reset_bus()`，防 per-test
  `create_app()` lifespan 把 listener 重复注册到共享 bus（重复计数）。

**关联**: ADR-021（metric kind 类 · 本 ADR 是其首个 per-backend 消费者）· ADR-018（bus）·
ADR-027（扩字段不扩系统先例）· DEBT-008（**本 ADR 关闭**：扫文件读路径退到冷/长窗口兜底）·
DEBT-006（不受影响 · tps 仍持久化）· L-060（先查现有管线 · "复用"要审被复用物性能）·
round10 MBC-3（关闭）· feedback_optimize_for_quality_not_simplicity（用户定调）

## ADR-050 · 远程设备接入：agent 出站拨回家 + 信令/数据面分离 + per-channel 独立连接

**背景**: 北极星 = Linux 上的 alb 中枢让 LLM（经 MCP，含外部 Claude Code / Cursor /
Codex 等客户端）和人（经 Web UI）调试**物理接在 Windows 主机上**的安卓设备：ADB over
USB + UART 控制台，Linux 侧驱动、Windows 零 per-session 脚本、COM/baud on-demand、
不依赖第三方终端工具。现状靠 Windows 发起的 SSH 反向隧道手动建（每会话手跑桥脚本 +
手配 `-R` 端口）。

候选传输（架构评审压测）：
- overlay 网（Tailscale/WireGuard）/ tunnel daemon（frp/cloudflared）只解决"网络可达"，
  **都不能 on-demand 开 COM 口 / 列设备** → agent 控制面无论如何都得写，它们省不掉。
- agent 管 SSH（`asyncssh` 已是依赖、可 in-process reverse-forward）→ 拿成熟数据面但
  引入第二信任域（key 分发），且开 COM 仍需自研 RPC。
- 自造**单 WS 全多路复用**（初稿）→ `adb pull`（MB/s）head-of-line block 掉 UART 交互。

**决定**:
1. **agent 出站拨回家** —— Windows agent 主动开 `wss://<hub>/agent/connect`（出站，
   NAT/防火墙友好，无入站端口，无 SSH key，无第三方终端）。复用 alb-api TLS + token
   鉴权（单信任域）。
2. **信令面 / 数据面分离** —— 拨回家那条 WS **只跑控制**：`hello`（agent_id/version/
   caps + token）、`heartbeat`、`list_com`/`list_adb`、`open_channel{type,params}`、
   `close_channel`。
3. **per-channel 独立连接** —— `open_channel` 后 agent 为该 channel **回拨一条独立连接**，
   hub 绑到对应 forwarder。`adb pull` 与 UART 键入物理隔离，消除 HoL。
4. **channel 类型**：`tcp`（给 adb，target 白名单仅 `127.0.0.1:5037`，防 agent 变开放
   代理）、`serial`（`{com,baud}`，on-demand，无预分配端口池）。
5. **agent 形态**：无头服务 + 系统托盘（状态/重连/日志/设置）+ 可选 `127.0.0.1` 只读
   状态页。**操作面只在 Linux 侧（Web/CLI/MCP）**，Windows 不出现选 COM/连设备的操作
   UI。agent 静态配置（hub URL/token/name）装一次，COM/baud 是 hub 下发的 per-session 参数。

**为什么不选 SSH / overlay / 单 WS 多路复用**: SSH 引第二信任域且仍需自研控制面；
overlay/daemon 不解决"开 COM"；单 WS 多路复用对交互流有 HoL。出站 WS 信令 +
per-channel 连接同时拿到：去第三方终端/SSH、NAT 友好、on-demand COM、交互不被大流量
饿死、单信任域复用 alb-api 鉴权。`asyncssh` 数据面留作 fallback（若 raw-WS 字节搬运/
背压实现超预期难）。

**代价 / 已知**:
- 自己拥有一套 wire 协议（组帧/背压/重连），要配 parity test。
- 信令面断 → 所有数据 channel 视为失效（agent 重连后重 `hello`，hub 标 offline 拆 forwarder）。
- 多 host fleet 的设备寻址不在本 ADR（见 ADR-051 / DEBT-083）。

**Enforcement**:
- channel 必须带显式 role（见 ADR-052），adb 与 serial 不能抹平成统一 `tcp` channel。
- `tcp` channel target 强制白名单，禁止任意 host:port。
- agent 代码进公开仓·品牌中立（同 `scripts/windows_serial_bridge.py`）；真实 COM/baud/
  hub-IP/token 留本地 config 不提交。

**关联**: ADR-051（alb-api 单中枢 + forwarder）· ADR-052（channel retry 角色）·
ADR-048（WS 消费者边界）· ADR-D 草案（多实例 backend · 独立排）· 设计稿
`.claude/reports/alb-remote-agent-design-draft.md`

## ADR-051 · alb-api 为设备流量唯一中枢进程：OS 级 loopback forwarder + MCP 新依赖方向

**背景**: dial-home（ADR-050）下 agent 的 WS 只连到一个 hub 端点。但 `alb-api`
（uvicorn asyncio）和 `alb-mcp`（FastMCP stdio，被 Claude Code/Cursor/Codex 当
subprocess 拉起）是**两个独立 OS 进程**。若 forwarder 是"alb-api asyncio loop 里的
对象"，独立的 alb-mcp 进程根本触不到它 → "LLM 经 MCP 调设备"主路断（评审 critical）。

**决定**:
1. **forwarder = alb-api 进程开的真·OS 级 loopback listener** —— `127.0.0.1:5037`
   （adb）+ serial 端点。它**就是操作系统资源**，任何本地进程（独立的 alb-mcp / CLI /
   Web）都能 `connect()`。
2. **alb-api 成为所有设备流量的唯一中枢进程** —— 持有 agent registry（`{agent_id→WS}`）
   + forwarder。MCP/CLI/Web 全是它的本地 socket 客户端，**transport / capabilities /
   MCP 工具零改动**（仍 `ADB_SERVER_SOCKET=tcp:localhost:5037`）。
3. **依赖方向变更（铁律）** —— dial-home 模式下 **alb-api 必须常驻且先于 MCP 运行**。
   今天 alb-mcp 可脱离 alb-api 独立连真设备；之后 alb-mcp **强依赖 alb-api 在跑**。这是
   实质依赖方向变化，不是"plumbing swap"。
4. **forwarder 生命周期** —— **进程级单例**（`get_adb_forwarder()`，registry 后端，
   复用 ADR-049 幂等 attach 模式），**不是 per-connection 对象**。`attach()` 幂等绑 OS
   listener，第一个 agent 连上时懒绑（避免无 agent / 测试场景空绑 5037），重连 / 第二
   agent 调 `attach()` 是 no-op → **无 EADDRINUSE 竞态**。agent 断开**不** detach（listener
   常驻，无 active agent 时新本地连接 fail-fast），仅 alb-api lifespan shutdown
   `shutdown_adb_forwarder()` 真拆。forwarder 经 `registry.current_agent()` 路由到当前
   agent（P0 单 agent；多 agent 寻址 DEBT-083）。
5. **单设备单 host 用固定端口** —— `127.0.0.1:5037`（adb）+ `127.0.0.1:19001`（serial），
   与今隧道端口一致 → MCP/CLI/serial transport **真零配置改**。动态端口只在多 host /
   多 COM 并发（P4）引入。

**为什么**: OS 级 socket 是唯一能让"独立 MCP 进程"复用"alb-api 持有的 agent 连接"的
机制（进程间无共享内存/loop）。固定端口让 keystone invariant（呈现成现有本地端点 → 核
零改）对单设备 adb+serial 双路都成立。

**代价 / 已知**:
- alb-mcp 多了"alb-api 必须在跑"的运行期前提（文档要写清：先起 alb-api + agent 连上，
  Claude Code 的 alb-mcp 才能经 forwarder 操作设备）。
- 一个设备的 agent 连接只能被一个 hub 端点持有 → MCP **不能**各开自己的 forwarder
  （会与 alb-api 抢同一 agent/COM）。
- 多 host 时固定 5037 listener 与多 agent 冲突 → 寻址要进 `build_transport`（DEBT-083）。

**Enforcement**:
- forwarder 必须是真 OS listener，**禁止**做成只在 alb-api loop 内可见的对象（否则 MCP
  路径不通，评审 critical）。
- 部署文档明确"alb-api 常驻先行"前提。
- 多 host 落地前先消化 `factory` 无 host 维度的债（DEBT-083）。

**关联**: ADR-050（dial-home 拓扑）· ADR-052（channel role）· ADR-049（lifespan 幂等
attach 先例）· `transport/factory.build_transport`（CLI/MCP/API 共用入口）· DEBT-083

## ADR-052 · 反向代理 channel 的 retry 角色：adb=daemon 失败即报 / serial=独占网关 bounded retry（L-034 落地）

**背景**: ADR-050 两类 data channel 在 transport 角色上是**异类**，但初稿抹平成"两种
tcp channel"并暗示统一 retry。L-034（2026-05-09 part 131）：被代理端是 listen-socket
daemon 还是 per-connection 独占网关，决定能不能 retry。

**决定**:
1. **adb channel = listen-socket daemon 语义 → 失败即报，零 retry**。Windows adb server
   是常驻 daemon；连 5037 的 `ECONNRESET`/`BrokenPipe` 几乎一定是真问题（USB 重授权 /
   设备掉线 / server crash），retry 会把真错误吸成静默重试，追溯极慢（违反 L-034）。每条
   adb-client→hub 入站 TCP = 一条独立端到端 channel，连接关即 channel 关，不跨连接复用、
   不应用层重连。
2. **serial channel = per-connection 独占网关 → 允许 bounded retry**。ser2net 风格桥有
   fd-release race（连续两命令间），沿用 `SerialTransport._open_tcp_with_retry` 的
   bounded backoff（3 次 0.1/0.3/0.6s 自愈）。
3. **channel spec 带显式 role 字段**（`daemon` | `gateway`），代码按 role 决定 retry
   策略，不靠 channel type 名隐式推断。

**为什么**: 抹平两类性质相反的端 → 要么给 adb 误加 retry（掩盖真错），要么给 serial 去
retry（踩 fd race）。显式 role 是唯一不诱导误判的抽象。

**代价 / 已知**: code review 多一条 checklist（新增 channel 类型必须声明 role + 给出
"被代理端是 daemon 还是独占网关"的判断依据）。

**Enforcement**:
- code-reviewer checklist 加规则：反向代理新增 channel 类型时，必须按 L-034 分清 daemon
  （失败即报）vs 独占网关（bounded retry），禁止抹平成统一 `tcp` channel 后误加 retry。
- adb channel 实现禁止出现重连/retry 循环。

**关联**: ADR-050（channel 模型）· ADR-051（forwarder）· L-034（listen-socket daemon
反模式 · 本 ADR 扩到反向代理新场景）· `SerialTransport._open_tcp_with_retry`（gateway
正例来源）
