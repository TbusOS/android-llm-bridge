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

## 2026-04-28 · F.6 useLiveSession reducer 加 tps_sample 分支

**评审对象**：F.6 staged diff（useLiveSession.ts +50/-7）让
LiveSessionCard.tpsSpark 从空 → 滚动；落实 ADR-021 / ADR-022 前端消费侧；
目标关闭 DEBT-001。

**调动**：code-reviewer + architecture-reviewer 并行（项目 agents 在当前
session runtime 未自动加载，主对话用 general-purpose 代理 + 注入 agent 定义）

### 采纳清单（已实施）

- code [mid] **MAX_EVENTS=200 vs metric 1Hz 流冲突，长 session > 200s 后
  user 事件被挤出** → **登记 DEBT-011**，F.7 一并修
- code [mid] **totalTokens 单调守卫跨 sampler 重启会卡住** → 删 `>=`
  守卫，加 invariant 注释（per-session sampler 不重启）
- code [mid] **done fallback 注释 "legacy sessions" 不准确** → 改
  "legacy or chats shorter than sample interval"
- code [low] **rate 守卫接受 NaN/Infinity** → 加 `Number.isFinite`
- code [low] **chat-only filter 加注释说明意图**
- code [low] **reducer 纯函数无单测** → **登记 DEBT-012**
- arch [low] **DEBT-001 关闭前必须跑行为验证** → ship 后跑一次 chat
  看 spark 真滚动 + 截图，再标 closed
- arch [mid] **LiveCard tps（瞬时） vs F.7 KPI throughput（窗口）数据源
  不一致** → **F.7 落地时强制给两个数字加 label**：LiveCard `tok/s now`
  / KPI `tok/s · 5m avg`（写入本文件作为 F.7 硬性要求，下次评审引用）
- arch [low] **升级 lessons L-016：view-aware 协议 + scaling 同层** →
  写入 lessons.md

### 部分采纳（变通）

- code [mid] SPARK_WINDOW=60 注释假设 1Hz，env 可调失真
  - **变通**：改注释（明示"≈60s when interval=1s"），写入 L-016；
    不做动态 cap（增加复杂度 vs 默认 1Hz 现实价值低）

### 维持现状（reviewer 主动结论"现状对"）

- arch [low] scaleSparkPoints 在 hook 层是对的（types.ts 协议已 view-aware）
- arch [low] 200 cap 不卡 UI（rawEvents 已是 200 cap，reducer reverse + for-of 不构成卡顿）
- arch [low] L-015 不触发（F.6 不引入新决策，只实施 ADR-021 / ADR-022）
- arch [low] 不抽 TpsSampleEvent type（避免给"前端单边类型 = 安全"假象）
- arch [low] 跨 session 切换 spark 从 0 重建（这是用户期望，无 carry-over 才对）

### 对应 agent prompt 调整建议

- 暂无。两个 reviewer 反馈质量高，无重复驳回。
- **runtime 问题**：项目 `.claude/agents/*.md` 在本次 session 未被
  Claude Code 自动加载（只显示内置 5 个 subagent_type）。临时方案：
  用 general-purpose 注入 agent 定义代理。需 follow-up 排查（可能是
  Claude Code session 启动时机或 frontmatter format 问题）。

### 学到的新规则（升级到 lessons.md）

- **L-016 · view-aware 协议，scaling 也属 hook 层**

### F.7 硬性要求（来自 arch reviewer · 写在这里给下次评审引用）

> **F.7 落地 useMetricsSummary 时必须给 LiveCard tps 和 KPI throughput
> 加 label 区分语义**：LiveCard 显示瞬时 rate（label 标 `tok/s now` /
> 中文"现"），KPI throughput 显示 5min 窗口平均（label 标
> `tok/s · 5m avg` / 中文"5 分均"）。否则用户看到"LiveCard 24, KPI 18"
> 会困惑。F.7 评审时强制校验。

### 新登记 DEBT

- **DEBT-011** · useAuditStream MAX_EVENTS 不分类型（mid，F.7 一并修）
- **DEBT-012** · web/ reducer 纯函数无单测（low，等 vitest 引入）

---

## 2026-04-29 · F.7 KpiStrip 4/4 + dual buffer + label 语义区分

**评审对象**：F.7 staged diff（5 web 文件 + 1 验证报告）。关闭
DEBT-003 / DEBT-004 / DEBT-011，落实 F.6 review #4 label 强制要求。

**调动**：code-reviewer + architecture-reviewer 并行（项目 agents
runtime 仍未自动加载，主对话用 general-purpose 代理 + 注入 agent 定义）

### 采纳清单（已实施）

- code [mid] **MCP tools / throughput 在 isError 态显示假"0"** →
  改 `isLoading || isError ? "—"`，error 单独走"数据获取失败 / fetch
  failed" 提示
- code [mid] **snapshot 同帧双 setState React 18 batches 注释**
  → 加注释说明合并到一次 render，useMemo 跑一次
- arch [low] **METRIC_KINDS server-side authoritative 注释 + 双改
  提醒** → 加注释 + 登记 DEBT-013 候选
- arch [mid] **DEBT-008 升级 low → mid + 触发条件细化**
  → debts.md 标 升级（events.jsonl ≥ 10k 行 或 多 tab ≥ 1h 触发）

### 维持现状（reviewer 主动结论"现状对"）

- code [low] sort tie-break：ts 微秒精度业务不会撞，加 source
  localeCompare 是 over-engineered，注释已覆盖意图
- code [low] `tps ?` 在 mean=0 时走 0 不塌缩 null：是对的（5min 内
  零生成 = 0 是真值），登记到 DEBT-012 vitest 引入时一起测，不新增独立债
- code [low] useTools docstring：自查清晰，撤回
- arch [low] LiveCard tps vs KPI throughput 重连边界短时不一致：
  label 已解释，F.8 截图脚本不要断言两数字一致性（会误报）
- arch [low] metric cap 60 在小时级长 session 仍合理：spark 是 live
  view 不是历史轨迹，evict 老 sample 是设计意图

### 拆 commit 决议

code-reviewer 建议拆三 commit (a)hooks + (b)消费侧 + (c)dual buffer。
**主对话决议：保持单 commit ship**。理由：项目历史所有 F.x 都单
commit；拆 (a) 单独"加 dead code 等后续消费"反而显得不完整；DEBT-011
独立 revert 价值边际。这条 review 反馈记入此处，未来更复杂改动可参考。

### 关闭 DEBT

- ✅ DEBT-003（MCP tools 写死 21）→ KpiStrip 显示真实 33 / 11 categories
- ✅ DEBT-004（throughput "—"）→ KpiStrip 显示 11.4 tok/s + 5m avg label
- ✅ DEBT-011（dual cap）→ business 200 + metric 60 dual buffer 落地

### 升级 DEBT

- ⚠ DEBT-008 · severity low → mid（F.7 是第一个稳定消费者）

### 新登记 DEBT

- **DEBT-013**（候选 low） · 前端 METRIC_KINDS 与后端
  _DEFAULT_METRIC_KINDS 双写不同步。N=1 时不触发，N≥3 时触发

### 学到的新规则（升级到 lessons.md）

- 暂无。F.7 落实既定 ADR-021/022 + L-013/L-016/L-017 路径，无新规律。

### 端到端验证状态（L-017 规则）

✅ done · `.claude/reports/visual-2026-04-29-f7.md`：

- 真实 ollama gemma4:e4b chat session 跑通
- KpiStrip 4/4 真数据（33 tools / 11.4 tok/s 5m avg / 12 samples /
  1 device / 4 sessions）
- DEBT-011 模拟验证：50 biz + 500 metric 合成事件，旧单 cap 18/50
  vs 新 dual cap 50/50
- F.6 review #4 完整落实：LiveCard "tok/s now / 现" + KPI "5m avg /
  5 分均"，semantic 区分清晰

### 对应 agent prompt 调整建议

- 暂无。两个 reviewer 反馈质量高，无重复驳回。

### 累计统计调整

- 总评审次数：6（F.1 / F.3 / F.4 / F.5 / F.6 / **F.7**）
- 总建议数：72（F.1: 18 / F.3: 10 / F.4: 8 / F.5: 12 / F.6: 15 / **F.7: 9**）
- F.7 采纳：4 全采纳 + 5 维持（reviewer 主动结论现状对）
- F.7 形成新规则：0 / 关 3 债 / 升 1 债 / 新候选债 1 / 落实 1 跨档要求

---

## 2026-04-29 · F.8 视觉端到端 + /preflight F 档收官

**触发**：F 档（接通 metric 流 + Dashboard 真数据）共 6 commits 累积，
所有验证都是数据层（reducer 模拟、JSON 拉取），视觉层从未在端到端
ollama 真跑下被验证过。F.8 收官档跑 Playwright 多分辨率截图 + sky-skills
三闸 + /preflight 总检查。

**执行内容**：
- 起 alb-api + ollama gemma4:e4b，跑一段 chat 让 KPI / spark / timeline
  累积真数据
- 自定义 `scripts/f8_screenshots.mjs`：6 路由 × 2 viewport（1440/768）
  = 12 截图，归档至 `.claude/reports/screenshots/2026-04-29-f8/`
- sky-skills 三闸：verify.py / visual-audit.mjs 跑 mockup 基线 PASS
- /preflight 总报告归档：`.claude/reports/preflight-2026-04-29-f-dock.md`

**视觉重点验证（dashboard@1440）**：
- KPI 4/4 全真数据：`Devices 1/1 / Sessions 1 / MCP Tools 33 (11
  categories) / LLM Throughput 10.7 tok/s · 5m avg · 10 samples` ✓
- Devices 真设备：`7bcb17848a177476` ✓
- Recent activity 真事件：`agent done · 115881ms` + 用户 prompt ✓
- LiveSession idle 态：chat 完成显示 "no live session"（设计正确）

### F.8 自身暴露的问题

1. **alb-api `mount_ui` 不支持 SPA fallback**：F.8 初版 Playwright
   `page.goto(/app/dashboard)` 直接拍到 FastAPI 404 JSON 页面 →
   登记 **DEBT-014**（severity mid）
2. **cmd-palette 在 768 屏 placeholder 文字裁切换行不优雅**：UI fluency
   候选，未登记债（M3 期 layout polish 一并）
3. **LiveSession spark 滚动状态截图**需 chat 进行中触发，本档静态
   截图拍不到（候选下一档 polish 用作 dev_team.html 素材）

### 累计统计

- 总评审次数：6（F.1 / F.3 / F.4 / F.5 / F.6 / F.7；F.8 不调 reviewer，
  是收官视觉档不是代码档）
- 总建议数：72，累计采纳率 82%
- 形成规则数：5（L-013/014/015/016/017）+ 3 ADR + 6 DEBT 操作（4 关
  / 1 升 / 1 候选）
- F 档共 7 commits + 1 收官，**F 档完整 ship**

### F 档 ship 决策

✅ ship：见 preflight 报告。F.8 暴露的 DEBT-014 不阻塞收官（dev/local
绕开有效，prod 影响待跟进）。

---

## 2026-04-29 · DEBT-014 alb-api SPA fallback 修复

**评审对象**：staged diff（`src/alb/api/ui_static.py` + `tests/api/test_ui_static.py`）

**调动**：code-reviewer + architecture-reviewer 并行

**改动**：新增 `SPAStaticFiles(StaticFiles)` 子类 override get_response，
404 + 无扩展名 → fallback 到 index.html；含点 path（asset）让真 404
propagate。+2 unit test。L-017 端到端验证规则的正面 case。

### 采纳清单（已实施）

- code [mid] **trailing slash / query string / multi-dot 边界没测** →
  加 3 case 测试（trailing `/`、`?tab=charts`、`/app/foo.bar.baz` 真 404）
- code [low] **get_response 缺返回类型注解** → 加 `-> Response`
- code [low] **docstring 没说清 html=True 与 SPA fallback 职责分工**
  → 加段说明两者覆盖正交 path 模式不冗余
- code [low] **错误传播谱系完整性需说明** → docstring 加"401/405/OSError
  propagate unchanged"
- arch [low] **隐性合约：SPA route 不能含点** → SPAStaticFiles docstring
  加硬合约 + `web/src/router.tsx` 顶部加注释指回
- arch [low] **architecture.md 关键不变量加 SPA route 不变量** →
  加一行
- arch [mid] **范围拆分合理但 follow-up 需要载体** → 拆 **DEBT-015**
  GH Pages prod 同问题，避免 DEBT-014"半 closed"
- arch [low] **L-017 加正面 case 引用** → lessons.md L-017 段加 DEBT-014
  详细案例，强调"部署层兜底也是 path，加 mount 后必须真浏览器 hit"
- arch (建议) **f8_screenshots.mjs 改回直访不再绕开** → 已落地，12/12
  直 page.goto 通过

### 维持现状（reviewer 主动结论"现状对"）

- code [mid] FileResponse 不传 stat_result，每次多一次 stat：observation
  only，未 measure 性能问题不动
- code [low] error 传播谱系完整：维持
- arch [low] 不升级白名单方案：启发式失败模式清晰自限，over-engineering

### 关闭 DEBT

- ✅ DEBT-014 标 **CLOSED 2026-04-29 (alb-api side)** —— 含 +2 unit
  test + 真浏览器 Playwright deep-link/refresh/nested 3/3 pass +
  curl 5/5 SPA route 200 HTML / 3/3 missing asset 真 404

### 新登记 DEBT

- **DEBT-015**（low）· GH Pages prod 同 SPA fallback 缺失（DEBT-014
  follow-up）。修法用 spa-github-pages 套路（404.html + query-encoded
  redirect script），候选下一档

### 累计统计调整

- 总评审次数：7（F.1 / F.3 / F.4 / F.5 / F.6 / F.7 / **DEBT-014**）
- 总建议数：82（前 72 + DEBT-014 10 条）
- DEBT-014 采纳率：90%（9 全采纳 + 1 撤回 + 3 维持 reviewer 主动结论）
- 形成 L-017 第一个正面 case 引用（DEBT-014 = L-017 实战范例）
- DEBT 操作累计：5 关 + 1 升 + 2 新 + 1 候选

---

## 2026-04-29 · DEBT-015 GH Pages SPA fallback（DEBT-014 follow-up）

**评审对象**：staged diff（`docs/404.html` 新 / `web/index.html` 改 /
`docs/app/index.html` rebuild 产物 / `tests/web/spa_fallback_test.mjs` 新）

**调动**：code-reviewer + arch-reviewer 并行

**改动**：spa-github-pages 套路修 GH Pages prod surface SPA fallback。
`docs/404.html` redirect → `/app/?spa=1&p=<route>` → `web/index.html`
inline recovery `history.replaceState` 还原。

### 采纳清单（已实施 11/12）

#### code-reviewer
- [mid] **recovery script 不消费 `?spa=1` 残留** → 加 cleanup 路径
  （`?spa=1` 缺 `p` 时清掉 query 让 URL bar 干净）
- [mid] **404.html 死循环防御不足** → 加 `if (search has spa=1) return;`
  早 return 防止递归 wrap
- [mid] **recovery `pathname.replace(/\/$/, "")` 在 `pathname === "/"`
  边界** → `pathname === "/" ? "" : pathname.replace(/\/$/, "")`，
  避免 `//foo` 双斜杠
- [mid] **404.html `rest === ""` 边界** → 加 `if (rest === "") return;`
  让 /app/ 直访不走 SPA 流
- [low] **404.html dev 时 CSS 加载失败** → 加 prod-only 注释
- [low] **测试持久化** → 新建 `tests/web/spa_fallback_test.mjs` 12 case
  入仓（node + vm.runInContext 跑两个脚本逻辑）

#### arch-reviewer
- [mid] **新立 ADR-023** → SPA fallback 跨部署 surface 异构实现，
  alb-api server-side intercept vs GH Pages client-side roundtrip，
  不统一原因 + 共享不变量
- [mid] **architecture.md 关键不变量加 3 条** → SPA route 不能含
  `? # &` / 不能以 `assets/` 开头 / GH Pages 协议保留 query 名
  `spa / p / qs`
- [mid] **新立 L-018** → 静态托管 SPA URL 闪现 + recovery 必须 inline
  同步（不 defer/async/module）+ 必须在 main bundle 之前
- [low] push 后 ScheduleWakeup(180s) curl prod 复检（强制 L-017，
  alb-api 关闭走过同流程）

### 维持现状（reviewer 主动结论"现状对"）

- code [low] 404.html dev CSS 失败：可接受（prod 兜底页 dev 不需好看）
- arch [low] vite build 入仓策略合理：维持
- arch [low] 404.html 非 /app 降级 UX OK：维持

### 关闭 DEBT

- ✅ DEBT-015 标 **CLOSED (mechanism) 2026-04-29** —— SPA fallback 协议
  层面已 prod verify，详见下方 prod 验证

### Prod 验证发现的独立问题

DEBT-015 commit `64ad2e1` push 后 240s ScheduleWakeup curl + Playwright
真浏览器跑完整 redirect chain：

**机制层 5/6 pass**：
- ✅ redirect chain：`/app/dashboard` → `/app/?spa=1&p=dashboard` → 还原
- ✅ URL 最终态干净（无 `?spa=1` 残留）
- ✅ nested route：`/app/sessions/abc-123` 链路正确
- ✅ refresh on `/app/inspect` 还原
- ✅ `/app/` 直访无回归
- ❌ Dashboard h1 missing（root div 空白）

**根因**（不是 DEBT-015 引入）：vite `base: "/app/"` 但 GH Pages 部署在
`/android-llm-bridge/app/`，bundle 出 `<link href="/app/anthropic.css">`
绝对路径在 `doc.tbusos.com` 下解析 → 缺 `/android-llm-bridge/` 前缀 →
4 个资源全 404 → SPA shell 启动失败。这是 commit `b07b930` (M2 Web
Tier 1，6 天前) 起一直存在的 bug，没人主动访问 GH Pages /app/ 才一直
没暴露（landing 没指向 /app/，只有 webui-preview.html mockup）。

**决策**：拆 **DEBT-016**（vite base 部署 base 错配，独立 issue）。
DEBT-015 评审定义的关闭条件是"SPA fallback 工作"，狭义看（fallback
协议机制本身）已 ✓；广义看（SPA 在 GH Pages 真能用）⚠（DEBT-016
阻塞）。机制层 PASS 已足够标 CLOSED，DEBT-016 单独跟进。

### 学到的新规则

- **ADR-023** SPA fallback 跨部署 surface 异构实现
- **L-018** 静态托管 SPA URL 闪现 + recovery 必须 inline 同步
- architecture.md 关键不变量段 +3 条 SPA route 合约
- **L-017 强化案例**：DEBT-015 prod verify 发现 DEBT-016（一直存在
  的独立 bug）—— "端到端 prod 验证才能暴露 wiring 静默 bug" 又一次
  应验，Playwright 真浏览器 hit 比 curl 多看到 console errors

### 累计统计调整

- 总评审次数：8（前 7 + DEBT-015）
- 总建议数：94（前 82 + DEBT-015 12 条）
- DEBT-015 采纳率：92%（11 全采纳 + 1 维持 + 0 驳回）
- 累计形成规则：6 lessons (L-013/014/015/016/017/**018**) + **4 ADR**
  (020/021/022/**023**)
- DEBT 操作累计：6 关（含 014/015 mechanism）+ 1 升 + **3 新（含 016）**
  + 1 候选

---

## 2026-04-29 · G 档 DEBT-002 LlmBackendCards 接真数据

**评审对象**：staged diff（5 web 文件）。useBackends hook 调
GET /playground/backends，DashboardPage 替换 MOCK_BACKENDS。

**调动**：code + architecture reviewer 合体（一份合并报告）。

### 采纳清单（已实施 4/5）

- **arch [mid]** "registry beta → UI up 是语义抹平，用户看到 — 会误
  解为'刚启动'而非'未知'" → 落地 alt 建议：backendMeta caption
  "implemented" → "registered"（zh "已注册"），reviewer 提的更深改
  动（LlmBackendCards 三槽改文案）拆 **DEBT-017** 一并做（health
  endpoint 来时改一次更经济）
- **arch [low]** BackendCardData 死字段 lastUsed/budget → DEBT-017
  type 清理 sketch
- **code [low]** mapApiBackendToCard 5 case → DEBT-012 sketch 段补
  一行
- **code [low]** MOCK_BACKENDS 加 dev fixture 注释 → 已落地

### 部分采纳（DEBT-017 follow-up）

- **arch [mid]** runtime health 缺口 → 登记新 DEBT-017（mid，含
  health endpoint sketch + UI 文案 + type 清理 + isError empty
  placeholder）
- **code [low]** isError 空 grid → DEBT-017 sketch 第 5 项 follow-up

### 关闭 DEBT

- ✅ DEBT-002 标 **CLOSED 2026-04-29** —— 范围 "mock → registry 真
  数据"，runtime health 拆 DEBT-017

### 新登记 DEBT

- **DEBT-017** (mid) · LLM backend runtime health 缺口（DEBT-002
  follow-up）—— 含完整 sketch（后端 health endpoint / 前端 ping /
  UI 文案 / type 清理 / empty placeholder）

### 学到的新规则

- 暂无新 lesson（registry 语义 ≠ runtime 语义已隐含在 architecture.md，
  不升新 lesson）

### 端到端验证状态（L-017 规则）

✅ done · `.claude/reports/screenshots/2026-04-29-g/dashboard-1440.png`：

- 起 alb-api → /playground/backends 返回 4 backends（1 beta + 3 planned）
- Playwright 真浏览器渲染 4 cards / 0 console errors / 1 implemented →
  "1 registered · 3 planned" caption / ollama 卡 latency/tps/errors
  显示 "—"（不是 0 假数据）/ 3 planned 卡显示 "unconfigured"
- L-017 强化点：本次端到端 hit 暴露 reviewer 发现 #1（"用户看到
  — 是 unknown 还是 0？"）—— 视觉验证才能注意到的语义模糊

### 累计统计调整

- 总评审次数：9（前 8 + **G 档 DEBT-002**）
- 总建议数：99（前 94 + G 档 5 条）
- G 档采纳率：80%（4 完全采纳 + 1 部分采纳 follow-up DEBT-017 + 0
  维持 + 0 驳回）
- 累计采纳率：83%（前 84% 微调）
- 累计形成规则：6 lessons + **4 ADR**（不变）
- DEBT 操作累计：**7 关 + 1 升 + 4 新（含 017）+ 1 候选**

---

## 2026-04-29 · F.6 端到端验证 + DEBT-001 关闭 + audit_route _project bug 修复

**触发**：arch reviewer 在 F.6 评审里要求"DEBT-001 ship 前必须跑行为
验证才能标 closed"，主对话执行验证。

**验证手段**：起新版 alb-api（commit 606b88d 含 F.6）→ ollama gemma4:e4b
真实 chat → GET /audit?include_metrics=true → Node 跑等同 useLiveSession
reducer 纯函数逻辑（reducer 是纯函数，Node 跑结果 = React useMemo 跑结果）。

### 端到端发现的 P0 bug

`src/alb/api/audit_route.py:62 _project()` 把事件的 `data` 字段 silently
dropped。从 C.1（commit 36537d5，4 个月前）就存在，C.5 ship 时没真跑
tool 触发就没人发现，F.5 ship 双 WS 时也没端到端跑就没暴露，到 F.6
端到端验证才炸。

修复：`_project()` 加 `data: raw.get("data")` 字段 + TS `AuditEvent.data?`
类型同步。一行修。645 pytest pass · web bundle 不变（TS-only 类型字段）。

### 验证结果（修后）

```
prompt:     真实显示 ✓
modelName:  gemma4:e4b ✓
totalTokens: 118 (sampler 累计，非 done 平均) ✓
tps:        9 tok/s (瞬时) ✓
tpsSamples: [3, 12, 11, 12, 12, 11, 12, 12, 12, 12, 9]  ← 真实曲线
tpsSpark:   [27, 0, 3, 0, 0, 3, 0, 0, 0, 0, 9]  ← peak normalize 正确
```

DEBT-001 关闭（debts.md 标 CLOSED 2026-04-29）。F.8 阶段补 Playwright
视觉截图（不阻塞本次关闭）。

### 学到的新规则（升级到 lessons.md）

- **L-017 · 端到端验证才能发现 wiring 静默 bug —— code review 看不出**

### 对应 agent prompt 调整建议

- code-reviewer / architecture-reviewer 在评审"新数据 path 接通"类
  改动时，强制问 "reducer 依赖的 data 字段，从 producer 一路到
  consumer 是否被中间所有层原样保留？"
- 评审报告加一节 "端到端验证状态"：✓ done / ⚠ pending / ✗ skipped

### 累计统计调整

- F.6 验证额外发现 1 P0 bug + 1 新 lesson L-017，agents 间接立功
  （是 arch reviewer 坚持"行为验证"才让 bug 暴露 —— 否则 F.7 开工
  会撞同样问题但定位更难）

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

**当前（2026-04-28 update · F.6 完成后）**：
- 总评审次数：5（F.1 / F.3 / F.4 / F.5 / F.6）
- 总建议数：63（F.1: 18 / F.3: 10 / F.4: 8 / F.5: 12 / **F.6: 15**）
- 采纳率：81%（27 + 11 + 13 = 51 / 63）
- 驳回类型分布：模块归属偏好 1 / 已登记 DEBT 复提 2 / 未来 milestone 范围 2 / 测试便利权衡 1 / "不强求"风格调整 1 / 无场景的防御 1 / premature abstraction 1 / 影响轻不修 1
- 形成新规则数：4（L-013 / L-014 / **L-015 元规则** / **L-016 view-aware**）
  + 3 ADR（ADR-020/021/022）+ 4 DEBT（DEBT-008/010/**011**/**012**）
- F.1 发现 2 条 high 级架构问题 → 推迟 ship，重做调整版
- **F.5 architecture-reviewer 主动翻历史 ADR 备选段，识别"反转未立新
  ADR"的文档债 → 升级为元规则 L-015**（agents 团队"会演进"的具体证据）
- **F.6 code-reviewer 识别"双 WS 实例下 metric 流挤掉 business 事件"
  跨档隐患 → 登记 DEBT-011 让 F.7 一并修**（agents "看一档发现下一档
  问题" 的具体证据）
- F.3 / F.4 / F.5 / F.6 reviewer 反馈持续聚焦代码质量 + 架构债识别，
  **采纳率稳定在 78-87%**，F.6 创单档新高 87%

> 每次评审后由主对话更新（commit message 里附带"review-feedback +N
> 条"作为信号）。
