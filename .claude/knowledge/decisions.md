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

## ADR-027 (seed) · BackendSpec.runs_on_cpu 语义改名 / 拆分

**Status**: seed（M3 step 3 Anthropic 落地时拍板）
**Date**: 2026-04-30
**Context**: 当前 `BackendSpec.runs_on_cpu` 语义模糊：
- OllamaBackend `runs_on_cpu=True` —— 字面对（本机 CPU 推理）
- OpenAICompatBackend `runs_on_cpu=True` —— 字面**错**（上游可能 8×H100
  GPU server，alb-host 只发 HTTP）
- 未来 AnthropicBackend `runs_on_cpu=False` —— 字面**也错**（alb-host
  根本不跑模型，是 SaaS API）

字段当前实际语义 = "alb-host 端的 CPU/GPU 需求 = CPU 即可"。三个
backend 都满足这个，但字面读起来"runs on cpu" 让用户误解为"模型在
CPU 跑"。

**Decision**：暂不改（破坏性 + 当前 N=2 还能忍），M3 step 3 Anthropic
落地时一并改名。

**改名候选**：

- (a) **`host_gpu_required: bool`**（语义反向）—— Ollama: False, OpenAI-
  compat: False, Anthropic: False, LlamaCpp(GPU 编译): True。**优势**：
  名字直接说"alb-host 这边需不需要 GPU"，不混淆"模型是否在 CPU 跑"
- (b) **拆成 `host_compute_type: 'cpu' | 'gpu' | 'remote'`**（三态）——
  Ollama/LlamaCpp: 'cpu'，OpenAI-compat: 'remote'，Anthropic: 'remote'，
  未来本地 GPU 推理 backend: 'gpu'。**优势**：表达更准；**劣势**：
  序列化 / 前端 enum 处理要改
- (c) **拆成两个字段** `host_cpu_only: bool` + `inference_remote: bool`
  —— **劣势**：两个布尔表达三态，组合能出 4 个但只用 3 个。

**推荐方向**：(b) `host_compute_type` 三态 enum。前端 `LlmBackendCards`
显示 "CPU"/"GPU"/"远程"，比"runs on cpu: true"清晰。

**何时拍板**：M3 step 3 AnthropicBackend PR 必含本 ADR 决策 + 一并把
`runs_on_cpu` 改名（registry + frontend manifest type + dashboard 渲
染同步）。

**关联**：M3 step 1 arch-reviewer #6 / architecture.md 字段语义条目

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

（后续 ADR 在主对话决策时按此格式追加）
