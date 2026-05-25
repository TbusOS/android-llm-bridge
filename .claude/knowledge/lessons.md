# 反面教材与经验

每条：曾经怎样、踩什么坑、现在为什么这样。让 agents 评审时不光看
"是否符合规则"，还知道"规则为什么这么定"。

---

## 索引（按编号 · 24 lessons + 1 meta · 2026-05-09）

**前端 / UI 层**
- L-001 · React UI 必须以 mockup HTML 为视觉基线
- L-002 · Vite base 路径不能在 index.html link 里手写
- L-003 · sky-skills design-review 三道闸不能完全替代视觉审
- L-016 · view-aware 协议，scaling 也属 hook 层
- L-018 · 静态托管 SPA fallback URL 闪现 + recovery 必须 inline 同步
- L-022 · 设计良好的错误态是双刃剑 · 视觉 review 看不出 vite proxy 之类的配置 stale
- L-025 · 新 useQuery hook 必须 sweep `refetchIntervalInBackground` / `refetchOnWindowFocus`
- L-028 · React.lazy + Suspense fallback 高度必须显式匹配 lazy 子组件最小高度
- L-029 · 共享 modal 组件 N=2 起必有 a11y 三件套基线（focus / Enter+ESC / 危险按钮顺序）
- L-032 · 新 sidebar / list pattern 抽出时 a11y 三件套基线（aria-current + aria-live + destructive 防呆）

**后端 / 协议层**
- L-013 · bus event 加新 kind 时分类（business / metric）
- L-014 · `@mcp.tool()` 函数首行 docstring 等同于公开 API description
- L-021 · `status: planned → beta` 是用户可见状态变更，不是无副作用 flag
- L-023 · 路径前缀 HITL 写在 endpoint 层是合理 v1（不是技术债）
- L-026 · 多 task 并发 send 同一 WS 时，close-frame 必须只发一次
- L-027 · HITL `approve_session` 用 line 字面 key 抗不住 shell 变量展开 / 别名
- L-031 · `contextlib.suppress(Exception)` 不抓 CancelledError · 必显式列举
- L-033 · async FastAPI endpoint 内 sync FS 调用必走 `asyncio.to_thread` · "io_to_thread sweep" 模式
- L-034 · per-connection 独占网关 vs listen-socket daemon 的 ECONNRESET 语义不同 · TCP 重试范围必须按 transport 角色设计
- L-035 · 用户输入拼路径必须根因层 reject `..` / 绝对路径 / 分隔符 · `Path / user_input` 不规范化必逃逸

**抽象 / 设计 / 决策**
- L-008 · 评估方案先看设计合理性，不先看难度
- L-009 · 代码事实禁止 hedge
- L-010 · 4 维度分析 - 编译能过 ≠ 设计合理
- L-015 · ADR 备选段会随后续 ADR 反转 —— 反转时必须新立 ADR
- L-019 · ABC 默认方法用 sentinel flag 表达 capability 否定 = 反模式
- L-020 · ABC 第 1 个非首例消费者 = 抽象设计的免费检验（N=2 不抽象）
- L-024 · 单元测试用 GNU coreutils mental model 写 fake，会漏掉 Android toybox 实际行为差异
- L-030 · NaN 钳位行为按"语言 + 顺序"分级 · explicit NaN check 是唯一跨语言可移植安全写法

**流程 / 协作 / 安全**
- L-004 · 公开仓 commit message 中文（vendor 规则不适用）
- L-005 · 公开仓 vs 内网仓物理分离（不能 sync）
- L-006 · 95 服务器禁止 adb kill-server
- L-007 · 外发内容必须脱敏 + 双 grep 自检
- L-011 · 上游原码保留 + 下游宏 gate 原则
- L-012 · 配置体系必须遵循框架原生流程
- L-017 · 端到端验证才能发现 wiring 静默 bug —— code review 看不出

**meta**
- L-meta-001 · "新增类规则" lesson 必带 grep pattern + 反面教材 + 正例 + agent checklist 同步

---

## L-001 · React UI 必须以 mockup HTML 为视觉基线

**坑**：2026-04-24 第一版 ChatPage 直接用 anthropic.css 的 `--anth-*`
token + 内联 style 拼出来，被用户当场打回"太丑了吧"。

**根因**：token 是色板/字号/间距，不是布局。缺了 mockup 的容器结构 /
组件比例 / 视觉重心，token 只是把页面变成米白底而已。

**规则**：
1. 先用 sky-skills `anthropic-design` skill 写一份完整 mockup HTML，放
   `docs/webui-preview-v*.html`
2. 走 design-review 三道闸（verify.py / visual-audit.mjs / screenshot.mjs）
3. 用户审 mockup 通过后，**React 照搬 class 名 + 容器结构 + 组件模式**
4. 共享样式抽到 `web/src/styles/components.css`（class-based，不是 inline）
5. 跨 mockup 改版（v1 → v2）保持流程不变

**应用到 agents**：mockup-baseline-checker 是 `/ui-check` 第一道关；偏离
mockup → 不让进后两道。

---

## L-002 · Vite base 路径不能在 index.html link 里手写

**坑**：早期 `web/index.html` 写 `<link href="/app/foo.css">`，部署后 CSS
全 404。

**根因**：Vite 配置了 `base: "/app/"`，构建时会自动给所有 link/script
拼上 base。如果 index.html 自己写 `/app/foo.css`，最终成了 `/app/app/foo.css`。

**规则**：index.html 里写**绝对路径但不加 base 前缀**：
```html
<link href="/foo.css">    <!-- 对：Vite 自己拼成 /app/foo.css -->
<link href="/app/foo.css"> <!-- 错：Vite 拼成 /app/app/foo.css -->
```

**应用到 agents**：performance-auditor / code-reviewer 看到 link 路径里
有 `/app/` 前缀（除了已知的 prod build 输出）→ 立刻提"嫌疑"。

---

## L-003 · sky-skills design-review 三道闸不能完全替代视觉审

**坑**：2026-04-23 设计 mockup 时三道闸全过，但实际渲染：
- grid-template-columns 太多列 第 N 列被截断
- inline SVG text 字号没用 token，硬编码导致和 H 标题不一致
- visual-audit 的"孤儿卡"判定阈值漏

**规则**：三道闸过是必要不充分条件。**必须人眼审 + Playwright 截图**。
visual-audit-runner agent 的 prompt 里专门列了 6 个盲区类型，每次
跑完三闸要补 grep + 截图肉眼审。

**应用到 agents**：visual-audit-runner 是 `/ui-check` 最后一道；它的
"盲区检查"段落不能跳过。

---

## L-004 · 公开仓 commit message 中文（vendor 规则不适用）

**坑**：2026-04-24 用户明确要求公开仓（`TbusOS/android-llm-bridge`）的
commit message 用中文叙述，技术关键词保留英文。

**规则**：
- commit 标题：中文 + 技术关键词英文（如 `feat(api): GET /audit ——
  Dashboard 真实数据后端 step 3`）
- commit body：中文为主，必要时穿插英文术语
- 不带 `Co-Authored-By: Claude` 署名（全局禁用）
- 不带 `Co-authored-by: dev@vendor`（vendor 内网仓规则，不适用本
  公开仓）

**应用到 agents**：code-reviewer 看 commit message 时不要建议改成英文。

---

## L-005 · 公开仓 vs 内网仓物理分离（不能 sync）

**坑**：曾经把公开仓 commit 直接 sync 到内网仓，导致内网仓污染了
开源中立内容（设备名 / IP），需要 filter-repo 清理。

**规则**：
- 代码改动**只在 `~/android-llm-bridge/`**（公开仓）
- 真机验证**只在 `~/android-llm-bridge-internal/`**（内网仓，暂停
  自动 sync）
- 两边手动 cherry-pick / patch 转移，不脚本同步

**应用到 agents**：security-and-neutrality-auditor 看公开仓 diff 时
要 grep 任何 vendor / RK / 内网 IP 痕迹。

---

## L-006 · 95 服务器禁止 adb kill-server

**坑**：在 95 服务器跑 `adb kill-server` 会通过 SSH 反向隧道杀掉
Windows 那一头的 adb daemon，整个调试链断掉。

**规则**：任何 adb daemon 重启**只能在 Windows 那头操作**。95 上禁止
直接调 `adb kill-server`。

**应用到 agents**：code-reviewer 看到代码里有 `adb kill-server` 调用
要立刻 high。

---

## L-007 · 外发内容必须脱敏 + 双 grep 自检

**坑**：2026-04-23 给 `TbusOS/android-llm-bridge` 提 issue 时，自以为
脱敏过但实际放过 7 处敏感词（Rockchip / arm-soc / RKDevTool /
upgrade_tool / `~/...` / COM27）。issue 已发公网，删了重发
干净版（#3）。但旧版可能已被 GitHub 邮件订阅 / 爬虫抓走。

**规则**：任何外发到公网的内容（GitHub issue / PR / discussion / wiki /
gist / Stack Overflow / 公开邮件列表 / 在线 paste），发布前**必须**
跑双 grep 自检（词边界 + 字面 pattern），全 0 命中才能发。详见
项目根 `CLAUDE.md` "外发内容必须脱敏" 段。

**应用到 agents**：security-and-neutrality-auditor 是 ABSOLUTE 守关。

---

## L-008 · 评估方案先看设计合理性，不先看难度

**坑**：2026-04-28 D 档完工后评估 C / A / E 三档候选，我用"用户价值/
工作量/风险"三维打分表把 C 标"高风险"建议暂缓、推荐 E "工作量小"。
被用户反驳："你不要总是考虑难度问题，应该去考虑怎么设计更合理，如果
之前设计不合理那就要重构"。

**规则**：评估"下一步走哪条路"时**先按设计合理性排序**，不先按难度/
工作量/风险打分。原设计不合理就直接列重构方案，不要绕开难项把活做小。

**应用到 agents**：architecture-reviewer 给重构建议时不能因为"成本高"
就退缩；要给完整 sketch + 成本估算 + 不重构的代价，让用户拍板。

---

## L-009 · 代码事实禁止 hedge

**坑**：2026-04-22 Task 15 defconfig 分析，第一轮回复"嫌疑这 5 个 vendor
符号多年静默失效"，被用户打回："啥叫幽灵，有就有没有就没有，不确定
就去看代码"。事实是 5 个符号全部有定义，完全正常。

**规则**：代码事实类判断（"符号有没有定义" / "函数是不是被调用" /
"条件什么时候为真"）**禁止**用"应该" / "可能" / "好像" / "嫌疑" /
"估计" 等含糊措辞。**有就说"有在 file:line"**，**没有就说"在 path
下 grep 0 命中，确认没有"**。

**应用到 agents**：所有 reviewer agent 输出代码事实时必须带 file:line
引用，不允许 hedge。

---

## L-010 · 4 维度分析 - 编译能过 ≠ 设计合理

**坑**：2026-04-21 Task 15 FIT_SIGNATURE 分析，只看 Kconfig select 链 +
cmd #ifdef 保护就下结论"不是必须"。后来发现没查运行时路径 / vendor 三级
信任架构 / PCI 7.0 合规要求。

**规则**：任何"删除/禁用某段代码"判断必须从 4 维度完整分析：
1. 编译/链接（最低要求）
2. 运行时代码路径（实际执行 / 调用链）
3. 业务/功能语义
4. 认证/合规（PCI / FCC / 客户契约）

**应用到 agents**：architecture-reviewer 给"建议删除 X" 类建议前必须
说清 4 维度怎么过的关。

---

## L-011 · 上游原码保留 + 下游宏 gate 原则

**坑**：2026-04-22 Task 15 第一版修复想直接删 `#elif defined(CONFIG_FIT_SIGNATURE)`
整支 3 行，被用户打回，改为加宏 gate 保留。

**规则**：对上游代码（RK / kernel / U-Boot 等）做定制改动时，**默认**
保留上游原码 + 加下游宏 gate（如 `CONFIG_vendor_<purpose>`）卡一下，而
不是删除上游代码。注释里标 `RK_ORIGINAL` / `UPSTREAM_ORIGINAL`。

> 备注：这条规则在 alb 项目（纯应用层 Python/TS）几乎不触发。但留在
> lessons 里给跨项目复用此 agents 团队时参考。

---

## L-012 · 配置体系必须遵循框架原生流程

**坑**：跨项目教训（rkr8.1 u-boot defconfig 规范化时误判 5 个符号
"无定义 + 历史污染"）。

**规则**：Kconfig / menuconfig 驱动的配置文件不允许直接 Edit 手改，
必须走框架原生流程（make defconfig / merge_config.sh / savedefconfig）。

> 备注：alb 项目无 Kconfig，本条留作跨项目记忆。

---

## L-015 · ADR 备选段会随后续 ADR 反转 —— 反转时必须新立 ADR

**坑**：F.5 双 WS 实例方案在 sketch 阶段已经定（ADR-021 时就讨论过），
但 decisions.md 文本里只记了"include_metrics opt-in"，没提"几条 WS"。
F.5 实施时 architecture-reviewer agent 翻 ADR-018 备选段才发现："两个
WS 各连"在 ADR-018 被显式否决（理由"浪费连接"），但 ADR-021 引入新
事实让该备选的 trade-off 反转。如果不立 ADR-022 显式记录这次反转，
下任 reviewer 看到 DashboardPage 双调会怀疑"是不是误改"，重走推断
路径浪费认知开销。

**根因**：ADR 的"备选"段在原 ADR 写下时被否决，但项目后续 ADR 可能
引入新事实让该备选的 trade-off 改变。如果没文档化反转，知识库里就
有两条互相矛盾的"决策": 老 ADR 说否决，新代码却走否决方案。

**规则**：当实施代码走的是某 ADR 已否决的备选时：
1. **必须**新立一条 ADR 显式说明 "reverses ADR-X 备选 Y, because
   ADR-Z 引入了新事实 W"
2. 新 ADR 标 status: "accepted; reverses ADR-X under ADR-Z conditions"
3. 老 ADR 不改（保留历史决策上下文）；只在新 ADR 里说明反转
4. 不准只在 sketch / commit message / code 注释里写"我们决定这么做"
   —— 这些都不进 knowledge 库，半年后没人记得为什么

**反例（不要这样做）**：
```
// in DashboardPage.tsx:
// 备选 c 反转了，所以这里开两条 WS
```
这条注释 6 个月后看不懂"备选 c 是什么"。

**正例**：
```
// see ADR-022 · Dashboard 同页双 WS 实例
```
+ ADR-022 完整记录上下文。

**应用到 agents**：architecture-reviewer 评审任何"看起来反直觉的设计"
时，先翻 decisions.md 看是不是某 ADR 备选的反转，如果是 → 立刻要求
立新 ADR。

---

## L-014 · `@mcp.tool()` 函数首行 docstring 等同于公开 API description

**坑**：F.4 加 `GET /tools` 后，`fn.__doc__` 第一行被作为 description
公开到 Web UI Dashboard。任何后续 PR 在 `src/alb/mcp/tools/*.py` 给
`@mcp.tool()` 函数加首行 docstring 写了 `arm-soc` / `vendor` / 内部 IP /
内部安全策略细节（如"DENY: rm -rf, reboot bootloader"），都会**直接
通过 GET /tools 流到外部**。`scripts/check_sensitive_words.sh` 是全文
grep 能拦中立性，但**安全策略细节不在禁用词清单里**，会被静默放行。

**根因**：`@mcp.tool()` 装饰的函数 docstring 不是"内部代码注释"，是
公开 API description。在 GET /tools 端点引入前没人意识到这一点。

**规则**：
1. `@mcp.tool()` 函数的**首行** docstring 按"公网外发标准"写：
   - ✓ 描述功能：`"Execute a shell command on the connected Android device."`
   - ✗ 列举攻击向量 / 默认 deny 列表 / 绕过表
2. 安全策略细节放函数体下半部分 docstring（不在 `_first_doc_line`
   的范围内）
3. 评审 `src/alb/mcp/tools/` 改动时 reviewer 自动检查首行
4. 跨项目复用此规则：任何"按反射暴露代码元数据"的端点（`/tools` /
   `/capabilities` / `/metrics-schema`）都按此规则审 docstring 首行

**应用到 agents**：security-and-neutrality-auditor 评审 mcp/tools/ 改动时
强制检查首行 docstring 是否包含 policy 细节。

---

## L-013 · bus event 加新 kind 时分类（business / metric）

**坑**：F.1 第一版 sketch 想直接把 `tps_sample` 当成第 6 类业务事件加，
没注意到它是 1Hz 周期数据 —— 一旦 ship，会让所有现有 audit 订阅者
（前端 Timeline、未来 audit log 看板）被刷屏。architecture-reviewer
agent 在首次实战中 catch 到这个问题，要求"F.1 不应单独 ship，至少
audit 默认过滤要一起做"。

**根因**：bus event 加新 kind 时只想着"它能塞进 schema 吗"，没分类
"它的语义是什么"。tps_sample 是 metric 流（周期 / 高频 / 不在故事
线上），和 user/assistant/tool_call_*（business 流，每条都是故事
节点）属于不同 audit 类别。

**规则**：bus event 加新 kind 时**必须先回答**：
1. 这是 business 还是 metric？
   - business：人类阅读时序故事的事件（用户问 / 模型答 / 工具调）
   - metric：周期 / 高频 / 数值采样（tps / cmd_rate / push_rate）
2. 默认订阅方应该看到吗？
   - business → 看到
   - metric → 默认过滤，opt-in
3. 是否进 ADR？
   - 引入新 kind 类（business → metric 第一个 / metric → business 第一个）必须 ADR
   - 同类的第 N 个不必（如果已有 metric 流再加一个 cmd_rate）

**应用到 agents**：architecture-reviewer 评审涉及 bus event 加 kind
的改动时，强制问"是 business 还是 metric"。

---

## L-016 · view-aware 协议，scaling 也属 hook 层

**坑**：F.6 实施 LiveSession spark 时会有"该不该把 SVG 坐标换算放进
component"的犹豫——hook 层算意味着 hook 里有 SVG 高度常量
（`SPARK_HEIGHT=36`），看起来"hook 知道太多 view"。

**根因**：types.ts 里 `tpsSpark: y-coords 0..36` 这个协议本身就是
view-aware（不是 raw rate 数组）。换算放在哪就是"协议在哪一层"的
问题，不是"代码风格选择"。

**规则**：
1. types.ts 协议字段如果已经是 view-aware（明确写"y-coords 0..N"
   / "0..32 for inline sparkline" / "px"），那 normalize / scale
   函数也应该在生产这个值的 hook 层（`useLiveSession.ts` /
   `useDeviceTrend.ts` 等），不是 component
2. 例外：当**有 ≥ 2 个 view**复用同一份 raw 数据，把协议改成 raw
   + 把 scaling 推到 component（避免不同 view 用不同 scale 但 hook
   只能选一个）
3. 副推论：双写硬编码（hook 里 `SPARK_HEIGHT=36` + component 里
   `height={36}`）是这种 view-aware 协议的代价；下次视觉调整两边
   要同步改

**应用到 agents**：architecture-reviewer 评审 hook 层出现"看起来像
view 常量"时，先查 types.ts 是不是 view-aware 协议，是的话不要建议
把常量挪到 component。

---

## L-017 · 端到端验证才能发现 wiring 静默 bug —— code review 看不出

**坑**：F.6 ship 时 code-reviewer + architecture-reviewer 两个 agent
评审 reducer 改动，给了 15 条建议（87% 采纳率），无人发现 reducer
依赖的 `data` 字段从 C.1（commit 36537d5，4 个月前）就被 `audit_route
._project()` silently dropped。bug 一路活到 F.6 端到端验证（2026-04-29）
才暴露 —— LiveSession 滚动 spark 数据全是 0。

**为什么 code review 没发现**：
1. reviewer 只看 staged diff（F.6 改的文件），不会回头审已 ship 的
   `audit_route.py`（C.1 时 ship 的代码）
2. reducer 代码本身正确：`(e as { data?: ... }).data ?? {}` 是 defensive
   写法，data 缺失时不抛错只 fallback 到 0
3. 历史 commit C.5 ship LiveSession 框架时没人真跑 chat 触发 tool
   验证 → tool 一直显示 "?"，但 dashboard 没 tool 跑就看不出
4. F.5 ship 双 WS 时也没端到端跑 → tps_sample 流配通但前端拿不到 data

**根因**：纯 code review 假设"上下游 wiring 不变"。如果 wiring 本身
就有静默 bug（输出方少给字段 / 接收方默认值兜底），代码层 review
看不出，**只有真跑数据流才会炸**。

**规则**：
1. 涉及"新接通一条数据 path"的改动（新事件 kind / 新端点 / 新 hook
   连旧 backend），ship 前**必须**端到端跑一次真数据，不能只过
   typecheck + unit test
2. 端到端验证用 reducer-level 模拟（Node 跑等同纯函数）就能发现 90%
   wiring bug，成本远低于 Playwright；reducer 是纯函数时**优先**用
   这个手段
3. ship 时如果还没端到端跑，债登记里要写"行为验证待做"作为 unfinished
   condition，绝不"代码看起来对就当对了"
4. 老代码（特别是 projection / serialization 层）改动要保守
   —— 这种"加字段"看起来安全，但"少字段"是 silent 灾难

**应用到 agents**：
- code-reviewer / architecture-reviewer 在评审"新数据 path 接通"
  类改动时，强制问"reducer 依赖的 data 字段，从 producer 一路到
  consumer 是否被中间所有层（projection / WS handler / fetch wrapper）
  原样保留？"
- 评审报告里加一节"端到端验证状态"：✓ done / ⚠ pending（说明何时跑）
  / ✗ skipped（说明为什么不需）

**应用到工作流**：F 档之后所有"接通新 path"档（如 F.7 useMetricsSummary
/ F.8 Playwright）默认带端到端验证步骤，不能光跑 unit test 就 ship。

### 正面 case · 2026-04-29 DEBT-014（alb-api SPA fallback 缺失）

F.8 收官跑 Playwright 端到端截图，`page.goto(/app/dashboard)` 直接
拍到 FastAPI `{"detail":"Not Found"}` JSON 页面 —— 暴露 `mount_ui`
用 `StaticFiles(html=True)` 对 SPA 深链直接 404 的 wiring bug。这条
bug 从 M2 Web Tier 1（commit b07b930，2026-04-23）就存在 6 天，
期间所有验证都跑过：
- ✓ 645 pytest（旧 test 只验 `/app/` 根能加载，没验深链）
- ✓ typecheck strict pass
- ✓ 敏感词 + offline-purity 三闸
- ✓ 本机 dev 进 `/app/` 让 SPA client-side router push 跳转能绕开

只有 F.8 真浏览器 hit `/app/dashboard` 才暴露。

**衍生应用规则**：部署层兜底（SPA fallback / 反代 / CDN cache rule）
也是 path，加 mount 后必须用真浏览器 hit 深链验证，不能光 `curl /app/`。

---

## L-018 · 静态托管 SPA fallback 用 client-side roundtrip 时的 URL 闪现 + recovery 必须 inline 同步

**坑**：DEBT-015 用 spa-github-pages 套路修 GH Pages SPA fallback。
用户从 `/app/dashboard` 进站，体感流程：

```
浏览器 GET /app/dashboard
  → GH Pages 找不到 → 服务 docs/404.html (HTTP 404 但 body 是 HTML)
  → docs/404.html redirect script 跑 → window.location.replace(
      "/app/?spa=1&p=dashboard")
  → 浏览器 GET /app/?spa=1&p=dashboard
  → GH Pages 服务 docs/app/index.html (HTTP 200)
  → docs/app/index.html recovery script 跑 → history.replaceState(
      {}, "", "/app/dashboard")
  → React 加载，TanStack Router 看到 /app/dashboard
```

URL 在 ~50ms 内闪现一次 `?spa=1`。如果 recovery script 没在 React
加载前同步执行（比如被 vite plugin 改成 `defer` / async / 异步
import），TanStack Router 第一次解析路径会拿到 `?spa=1&p=dashboard`
而不是 `/app/dashboard`，路由匹配失败显示 404 页面。

**根因**：
- spa-github-pages 是 client-side 协议，URL 闪现是协议固有行为
- recovery 必须 inline + 同步：放在 `<head>` 里 inline `<script>`，
  不能 `<script type="module">` / `<script defer>` / `<script async>`
- 必须在加载主 React bundle 之前执行，否则 router 拿到错误 URL

**规则**：
1. 静态托管 SPA fallback（GH Pages / S3 / Netlify w/o redirects）
   不可避免 client-side roundtrip + URL 短暂闪现协议参数
2. recovery script 必须 inline `<script>` 在 `<head>` 同步执行，
   **不能** defer / async / module
3. 必须在 main bundle `<script type="module" src=".../index-XYZ.js">`
   之前出现
4. vite 默认把 main bundle 注入 `<head>` 末尾，inline recovery 在
   main bundle import 之前 → 顺序对；如果未来 vite plugin 改 inject
   顺序，本档失效但**没有自动检测**——`tests/web/spa_fallback_test.mjs`
   只测纯逻辑，不测 inject 顺序

**应用到工作流**：
- 任何静态托管的 SPA fallback 改动都必须真浏览器 hit deep link 验证
  （不能只 node 模拟纯逻辑）
- 改 vite plugin / 升级 vite 版本时，必须 grep 确认 `web/index.html`
  里的 inline recovery 还在 `<script src=...>` 之前
- L-017 + L-018 联合应用：reducer-level / vm node 模拟可验逻辑，
  真浏览器 prod 验视觉 / 时序 / DOM 副作用

**应用到 agents**：
- architecture-reviewer 评审涉及 SPA inline script 的改动时，强制
  问 "recovery script 是否在 main bundle 之前？是否同步执行（不
  defer/async/module）？"
- code-reviewer 评审 vite plugin 升级时，加入"inline script inject
  顺序回归"check

---

## L-019 · ABC 默认方法用 sentinel flag 表达 capability 否定 = 反模式

**坑**：DEBT-017 主 commit `67c0820` 在 `LLMBackend.health()` 默认实
现里返回 `{reachable: False, implemented: False}`。端点 `if not
result.get("implemented")` 反查这个 dict key 来判定"未接探测"。
OllamaBackend.health() 不显式设 `implemented: True`（依赖 key 缺失
fallthrough 走 truthy 路径），arch-reviewer + code-reviewer 同时指
出：下个 backend 复制 ABC 模板做基础时，留 `implemented: False` +
返回 reachable=True，端点会**静默判错**为 unprobed，明明在跑显示成
"未探测"。

**根因**：

1. **dict-as-interface 没 schema**：endpoint 读 `result.get("model")`
   / `result.get("model_present")` / `result.get("implemented")`，
   concrete backend 加字段 / 改字段 / 漏字段都 type-check 不出。
   ChatResponse / ToolCall / Message 早 dataclass 化，health() 是孤儿。
2. **capability 隐式表达**：用"返回字典里的 sentinel key"声明"我有
   probe 能力"，与项目里其他 capability（`supports_tool_calls` /
   `supports_streaming` 都是 class attribute）格调不一。
3. **sentinel 反向语义**：False 表示"我没接"，必须**两边**（基类 +
   子类）都正确写才工作。基类写 True 子类没传 → 误报有；基类写
   False 子类传 True → 当时对，复制时忘改 → 误报无。

**规则**：

1. ABC 表达 capability 否定 / 缺失能力，**用 class attribute**（默
   认 False，子类显式设 True 才生效）。例：`has_health_probe: bool
   = False`，对齐 `supports_tool_calls` 模式。
2. ABC 默认方法**不留占位 dict**。改 `raise NotImplementedError`，
   让"调用未声明能力的方法"变成 loud failure。
3. ABC 方法返回值**用 dataclass**，每个字段 type 化。增加字段时全
   局扩，删除 / 改名时调用方都报错。
4. 调用方先 `getattr(type(b), "<capability>", False)` 查 capability，
   再 call。

**反例 / 正例对比**：

```python
# ❌ 反例（DEBT-017 主 commit 67c0820 一度采用）
class LLMBackend(ABC):
    async def health(self) -> dict[str, Any]:
        return {"reachable": False, "implemented": False, ...}

# 调用方
result = await b.health()
if not result.get("implemented"):  # 隐式契约
    return _no_probe()

# ✅ 正例（DEBT-017 follow-up commit 63a10c2 修正后）
class LLMBackend(ABC):
    has_health_probe: bool = False

    async def health(self) -> HealthResult:
        raise NotImplementedError(
            f"{type(self).__name__} has no health probe wired; "
            "set has_health_probe=True and override health()."
        )

# 调用方
if not getattr(type(b), "has_health_probe", False):
    return _no_probe()
result = await b.health()  # type-checked HealthResult
```

**应用到工作流**：

- 写 ABC / interface 时，capability advertise 用 class attribute；
  默认方法要么 abstractmethod 要么 raise NotImplementedError；返回值
  用 dataclass 或 TypedDict（dataclass 强于 TypedDict 因 runtime 校验
  字段）。
- 调用方查能力先 gate（`if not has_capability` 短路），不调"可能没接"
  的方法。

**应用到 agents**：

- code-reviewer / architecture-reviewer 看到 ABC 默认方法返回 dict
  + 子类靠 dict key 反向 fallthrough 表达 capability 时，立即提
  L-019 + 引 ADR-024。
- 看到 `result.get("...")` chain 在 endpoint / hot path，建议升级
  为 dataclass。

---

## L-020 · ABC 第 1 个非首例消费者 = 抽象设计的免费检验（N=2 不抽象）

**坑**：M3 step 1 (commit `344fb47`) 落 OpenAICompatBackend 时，arch-
reviewer 提议抽 `HttpLLMBackend` base class，理由是 OllamaBackend 与
OpenAICompatBackend 共享 `_post` / 错误映射 / `list_models` / `_PROBE_CACHE`
互动等约 80-100 行。仔细看反而抽不出干净 base：(1) `_build_body` 形状
差异大（Ollama `options` 嵌套 vs OpenAI 平铺 + `stream_options`）；
(2) `_parse_response` 流式 framing 完全不同（NDJSON `done:true` vs SSE
`[DONE]` + per-index tool_call accumulator）；(3) `_message_to_*` /
`_tool_to_*` wire format 必然分叉。能干净抽的只有 `_map_httpx_error`
和 `_PROBE_CACHE` 装饰器，**收益小于 N=3 才抽的开销**。

**根因**：

1. **N=1 不知道哪部分会重复**：写 OllamaBackend 时不知道哪些是 ollama
   特殊 vs 哪些是"任何 HTTP backend 都该这样"。N=1 时定的 helper 边
   界主导抽象方向，必然有 wire-format 偏见。
2. **N=2 看到形状但不知道哪部分是真共享**：N=2 容易看到"两 backend
   共有 `_post` + 错误映射"，但实际 N=3 加上 LlamaCpp（无 HTTP 层 ·
   in-process）就废了。N=2 抽 base 经常被 N=3 推翻。
3. **抽象的成本**：抽 base class 后调试一个 backend 要看 2 个文件 +
   理解 base 提供的钩子点 + 后续每加新 backend 评估"我能放进 base
   还是要写 override"。这个隐性 cognitive load 在 N=2 时不值。

**规则**：

1. **N=1 → 写实现，不抽 base**：第一个 concrete impl 全文留在
   subclass，don't try 提前预测共享点。
2. **N=2 → 仍不抽 base，但记录差异点**：在 lesson / commit message
   里写下"两 backend 共享了哪些代码段，差异在哪"。这个观察是 N=3 抽
   象决策的输入。
3. **N=3 → 真抽 base**：这时 3 个 concrete impl 给的"共有 vs 差异"
   信号足够强，base class 设计能避开 N=2 时的局部偏见。
4. **N=2 阶段允许的抽象**：纯函数 helper（`_map_httpx_error` /
   `_normalize_finish_reason`）抽到模块级，不抽 class。class-level
   抽象等 N=3。

**ABC 第 1 个非首例消费者的额外价值**：

**ADR-024 case study**：OllamaBackend 落地后 ABC 看似自洽，arch-
reviewer 评 ADR-024 时也通过。但只有等 OpenAICompatBackend 这个
**第 1 个非首例消费者**真接进来，才暴露 `HealthResult.model` 三态
化漂移（OllamaBackend 默认 `qwen2.5:3b` 永远填，OpenAICompatBackend
默认 `""` → 隐式三态）。N=2 是 ABC 设计的**免费 stress test**。

**应用到工作流**：

- 写 ABC + N=1 实现时：commit message 注明"contract 由 N=1 验证，N=2
  落地时复审"
- 写 N=2 实现时：在 review-feedback 里专门加一段"ABC 契约压力测试"
  评论，重点看是否有"OllamaBackend 隐式假设但 OpenAICompatBackend 暴
  露"的字段语义
- N=2 落地的 PR 必含"ABC contract 是否需要 amendment"的 ADR seed 或
  amendment（M3 step 1 → HealthResult.model docstring 加约定）
- N=3 落地的 PR 必评估 base class 抽取（M3 LlamaCpp 时）

**应用到 agents**：

- code-reviewer / architecture-reviewer 看到 N=2 PR 提议抽 base class
  时，立即引 L-020：列出 (a) 共享代码段 (b) 差异点，**不抽**，等 N=3
- ABC 设计 review 时主动问"N=2 时第 1 个非首例消费者是什么？还没有的
  话契约弹性如何验证？"

---

## L-021 · `status: planned → beta` 是用户可见状态变更，不是无副作用 flag

**坑**：M3 step 1 (commit `344fb47`) 落 OpenAICompatBackend 时，最初
我把 registry status 从 `planned` 改 `beta`（"实现 ship 了，自然就是
beta"）。arch-reviewer 当场打回：默认 `base_url=http://localhost:8080/v1`
在没装 vLLM/llamafile/LM Studio 的 dev 机上永远不可达 → dashboard
ADR-025 polling 命中 → 永远显示**红卡**。

**根因**：

1. **默认 base_url 是 byo-server 兜底**：OpenAI-compat 是 BYO（"bring
   your own"）协议，没有官方默认 server。`http://localhost:8080/v1`
   是 vLLM/llamafile 自托管的最常见端口，但不会在 dev 机上自动启动。
2. **dashboard `polling` × `down`**：ADR-025 定的 health probe 每 15s
   跑一次 → 每 15s 一次"unreachable" → 卡片永远红。
3. **红色常态化 = dashboard 报警价值废掉**：dashboard 的 red signal
   原本表示"这事该处理"。如果 1/4 卡固定红色，用户会训练成"忽略红
   卡"——下次 ollama 真挂了反而不警觉。这是**新增的固定噪音**，比
   "卡是 planned 灰底"更糟。

**规则**：

1. **status 翻 beta 前必须验**：dev 默认配置（无 env / 无 flag）下，
   dashboard 这张卡是绿、灰、还是红？红是回归。
2. **如果默认配置必然 unreachable**（BYO 协议 / 需 API key / 需远程
   server），有 3 个选项：
   - (a) **status 留 planned** + 实现已 ship 但 UI 不主动暴露（M3 step
     1 选这条 — "实现可用，dashboard 等接 cloud 时再亮"）
   - (b) **加新 status `beta-byo`** + dashboard 加新 reason
     `not_configured`（蓝灰，不是红）
   - (c) **加默认 cloud target**（OpenAI proper / DeepSeek free tier）
     让 dev 默认就能 reach
3. **永远不该的做法**：让 status=beta + 默认 base_url 不可达 + dashboard
   永远红卡。

**反例 / 正例对比**：

```python
# ❌ 反例（M3 step 1 主对话第一版）
BackendSpec(
    name="openai-compat",
    status="beta",  # 实现 ship 了
    ...
)
# 后果：dev 机 dashboard 永远红卡，红色常态化

# ✅ 正例（arch-reviewer 拍回后改）
BackendSpec(
    name="openai-compat",
    # 实现已 ship，但默认 base_url 在 dev 机不可达；改 beta 会让
    # dashboard 永远显红卡，废掉报警价值。M3 step 2 接 cloud 时再翻
    # beta（或加 status="beta-byo" + reason="not_configured"）。
    status="planned",
    ...
)
```

**应用到工作流**：

- registry status 改动是 commit 必单独说明的项 + ship 前必须真起
  alb-api 看 dashboard 卡片
- 任何"状态改 flag" PR 必经 mockup-baseline-checker（看新视觉）+
  arch-reviewer（看 UX 影响）

**应用到 agents**：

- architecture-reviewer 看到 BackendSpec / CapabilitySpec status 字段
  改动时，强制问"dev 默认配置下 dashboard 卡是什么颜色"
- mockup-baseline-checker 看到 status 改动时，主动跑一遍 dashboard
  视觉验证

---

## L-022 · 设计良好的错误态是双刃剑 · 视觉 review 看不出 vite proxy 之类的配置 stale

**Date**: 2026-05-01（commit `0ef2d87` web_check.mjs 落地 + 当场暴露
vite proxy 漏 /devices /sessions /tools /audit 4 endpoint）

**规则**：错误态显示得"自然"（"Couldn't load devices" 文案 + KPI 显 0
+ Recent activity "connecting..." 都是设计过的合理空态）时，**人眼 review
看不出是 bug 还是预期**。必须有自动化脚本断言"应该有 N 个 article 卡 /
应该 0 console error / 应该有特定 fetch 命中"，否则 dev 模式可以好几天
没人发现 stale 配置。

**Why**:
- 2026-04-26 F.4/F.6/G 档加了 `/devices /sessions /tools /audit` 4 个
  endpoints，但 `web/vite.config.ts` proxy 没同步加。
- dev 模式下 dashboard fetch 这些路径直接打到 vite (5173) → 404。
- 但前端有错误态 fallback：device 段显"Couldn't load devices"、sessions
  显"No sessions yet"、KPI 显 0、activity 显"connecting..."。
- 这些错误态显示得**很像合理空态**——视觉上看不出是 bug。
- 4 天里跑了 mockup-baseline-checker / ui-fluency-auditor / visual-audit
  -runner 多轮人/agent review，全没发现。
- 直到 2026-04-30 跑 `web/scripts/web_check.mjs` 第一次自动化跑，
  console.json 里 6 console errors + 5 network failures 立刻暴露。

**How to apply**:
- 任何加新 alb-api endpoint 的 PR：必须 grep `web/vite.config.ts` 确认
  proxy 段已包（prefix 命中即可）。
- preflight 流程加一道"无 web_check 验证不放行"闸（dashboard 关键 route
  必须 0 console error / articles ≥ 期望数）。
- 视觉 review（mockup-baseline-checker / visual-audit-runner）不能替代
  console error 验证 —— 设计良好的错误态本来就该看起来"自然"，是优点
  也是盲区。
- 写 web_check 测试时，对每个有意义的 route 都列出"应该有的关键元素 /
  应该有的 fetch / 应该 0 console errors"，让脚本断言。

**触发条件**:
- 加新 alb-api HTTP endpoint
- 改 vite.config 的 proxy 段
- 加新 dashboard 段（新 useQuery）

**反面教材记录**:
- 2026-04-26 加 GET /devices /sessions /tools /audit 后，4 天内 mockup
  -baseline / 人眼 review 都没发现 vite proxy 没跟上 → console 全红但
  视觉无异常
- 2026-04-30 第一次 web_check.mjs 跑就暴露 6 console errors → 当场修
  vite.config.ts

**应用到 agents**:
- 任何加 alb-api endpoint 的 PR，code-reviewer 必须 grep vite.config.ts
  proxy 段确认覆盖
- ui-fluency-auditor / visual-audit-runner 报告里要附 web_check.mjs 的
  console.json 摘要（不能只看视觉）

---

## L-023 · 路径前缀 HITL 写在 endpoint 层是合理 v1（不是技术债）

**Date**: 2026-05-01（PR-H ship · `00cc532`）

**规则**：当跨层抽象（如 PermissionEngine）的接口面**还不够通用**时，把
domain-specific 规则（如 "filesync.push 命中 /system 要 HITL"）**先写在
最近的调用方**（endpoint / capability），同时在 ADR seed 里登记下沉时机
（依赖哪一层先扩 spec）。**不是技术债，是分层等待**。

**Why**：
- PR-H 写 push HITL 时考虑了两条路：
  - (a) `files_route._is_sensitive_remote(remote)` + `force` flag（v1 选）
  - (b) 扩 `infra.permissions.default_check`，让 endpoint 走
    `transport.check_permissions("filesync.push", ...)`，跟 shell HITL
    完全同形态
- (b) 看似"更架构正"，但 M1 engine 的 `default_check` 现在只接 `cmd`
  字符串，要扩 spec（加 action 维度 + 多类型 input_data + multi-layer
  config）才能放进去
- 如果 PR-H 为了走 (b) 顺手扩 engine，spec 就被 1 个调用方"拍歪"了，
  下次 PR-X 加 SSH 写入 HITL 又得改一次接口
- 等 M2 engine 扩展 spec 到位（独立设计 + 多 sample），再让 PR-H 下沉，
  接口不被局部需求绑架

**How to apply**：
- 决策时显式判断：跨层接口面是否已支持你的需求？没有 → 写本地 + 登 ADR
  seed 标"待 X 层扩展后下沉"
- ADR seed 里写清楚：何时下沉（依赖哪个 spec）、下沉后端点 / capability
  改成什么样
- 不要把 v1 的 "endpoint 层 inline" 当债务记，记成 ADR + "等待时机"
  （DEBT 是"必须修"，ADR seed 是"看时机"）

**触发条件**：
- 新 PR 引入 domain-specific 规则
- 现有抽象层接口面不够通用（要扩 spec）
- 下沉的 follow-up 依赖另一个 milestone

**反面 vs 正面**：
- 反面：PR-H 顺手扩 PermissionEngine spec → engine 接口被 1 个调用方
  拍歪，后续 SSH/audio/sensor HITL 又得改 spec
- 正面（PR-H 实际选）：endpoint 层写 HITL + ADR-031 seed 标"M2 engine
  扩 spec 后下沉" → engine spec 由 M2 独立设计阶段统一拍

**关联**：ADR-031 seed (filesync HITL 下沉路径) · ADR-013
(PermissionEngine 设计) · L-019 (sentinel 反模式 · 也是"接口被局部需求
绑架"的反面)

---

## L-024 · 单元测试用 GNU coreutils mental model 写 fake，会漏掉 Android toybox 实际行为差异

**Date**: 2026-05-02（PR-H 真机验证暴露 · fix `bd49156`）

**规则**：写 capability / endpoint 调 `transport.shell()` 的代码时，
**单元测试 fake response 必须以目标设备的实际工具实现为准**，不能用
开发机（GNU coreutils / macOS BSD utils / etc）的输出格式当 mental model。
Android 设备 99% 是 toybox（少数早期是 busybox），命令行 flag / 输出
格式和 GNU 不一致。**真机 smoke 必须跑**，不能只信单元测试。

**Why**：
- 2026-05-02 PR-H ship 时 `src/alb/api/files_route.py` 用 `ls -la
  --time-style=long-iso /sdcard/`，22 单测全 pass（fake response 是手写
  的 GNU long-iso 格式）。真机一跑直接 100% 失败：toybox 报 "Unknown
  option 'time-style=long-iso'"。
- `--time-style` 是 GNU coreutils ls 的扩展 flag，toybox / BusyBox / BSD ls
  都不支持。我写代码时凭"GNU ls 都支持"的 mental model 加了 flag，
  fake response 也就理所当然按 long-iso 格式写。两层错误叠在一起，
  单测看不出来。
- 修复用 `ls -la`（无 flag），Android toybox 默认输出就是
  `YYYY-MM-DD HH:MM`，刚好和 GNU long-iso 同形态，parser 不动。

**How to apply**：
- 写 `transport.shell(...)` 的代码时：
  - 优先选 POSIX-only flags（不依赖 GNU 扩展）：no `--time-style`,
    no `--color`, no `-Z`, no `--block-size`, no long-form
    `--human-readable`（用 `-h` 短形式更兼容）
  - 命令选 toybox / busybox 都有的：`ls -la`, `cat`, `grep -E`, `wc -l`,
    `head -N`, `tail -N`, `cut -d`, `awk`, `sort`, `find` 都 OK；
    `xargs --no-run-if-empty` GNU 限定 → 用 `[ ... ] && xargs`
  - 验证 flag 兼容性：先在真机 `adb shell <cmd> --help 2>&1 | head` 看
    哪些 flag 真的接（toybox 报 "Unknown option" 就 fail-fast）
- 写单测 fake response 时：
  - **从真机 `adb shell <cmd>` 抓真实输出**贴进 fake，不要凭"应该长这样"
    手写
  - 测试集里加一条 `tests/fixtures/<cmd>-toybox.txt` 用真机原始输出做样本
- PR ship 流程加一道闸：**单测全过 ≠ 可 ship · 必须真机 smoke 1 个
  典型场景**才算完
- 不只 ls：`ps`, `top`, `df`, `free`, `dumpsys`, `getprop` 都有同形态坑
  （GNU vs toybox 输出 column / flag 差异）

**触发条件**：
- 新增 `transport.shell(...)` 调用
- 解析任何 Android shell 工具输出
- 改 capability 行为依赖某个 flag

**反面教材**：
- 2026-05-02 PR-H ls --time-style 真机 100% 失败
- （提醒自查）后续如新增 `ps -ef`、`df -h`、`top -n 1 -m N`、
  `dumpsys battery`、`getprop -T` 等都先真机验证

**应用到 agents**：
- code-reviewer 看 `transport.shell(...)` 的 PR：必须查命令的 flag 在
  toybox 是否支持（grep flag 名 in `external/toybox/` 是金标准）
- 单元测试 fake response 必须有"来源"注释（真机抓的 → ✅，凭 mental
  model 写的 → ❌ 标 TODO）

**关联**：L-022 (设计良好的错误态是双刃剑 · 视觉 review 看不出 vite proxy
stale，本条是"单测 mental model 看不出 toybox 差异" · 同形态盲区)

---

## L-025 · 新 useQuery hook 必须 sweep `refetchIntervalInBackground` / `refetchOnWindowFocus` 两 flag

**Date**: 2026-05-02（perf-audit `0c74b2c` · 6 dashboard hook 漏 background gate）

**规则**：写 `useQuery({refetchInterval: ...})` 时**必须**同步检查 + 显式
设：
- `refetchIntervalInBackground: false` —— 浏览器 tab 切走时停止 polling
- `refetchOnWindowFocus`: 默认 true（回到 tab 立刻刷新一次），如不需要
  显式关掉

不能"先写 refetchInterval 后续再加 gate"，会在隐藏窗口持续浪费请求 +
被审计才发现。

**Why**：
- 2026-05-02 perf-audit 发现 6 个 dashboard hook（`useSessions`/`useTools`/
  `useMetricsSummary`/`useAudit`/`useDeviceDetails`/`useDevices`）全部漏
  `refetchIntervalInBackground:false`。只有 `useBackends` 当年（M2 ship）
  显式加了。新 hook（如 PR-A 加的 `useDeviceDetails`）按"复制 useSessions
  pattern"思路写，pattern 本身就缺 gate，bug 等比例传染
- 用户在 dashboard 点开 chrome、切到别的 tab 看视频/写代码 → 6 hook 仍
  按 30s 间隔 polling。每分钟 ≈ 12 HTTP request 全打到 alb-api。
  `useMetricsSummary` 还触发 events.jsonl 全量扫
- DEBT-008 events.jsonl 扫全量已知，但被 background polling 放大
- 审 8 PR 才发现，肉眼 review 不会注意到（API 是工作的）

**How to apply**：
- 写新 `useQuery` 时按 checklist：
  1. 这条 query 是否 `refetchInterval` 周期性？是 → step 2
  2. 周期 polling 在隐藏窗口要不要继续？99% 答 no → 必须加
     `refetchIntervalInBackground: false`
  3. 用户回到 tab 要不要立刻刷新？看 query 数据"陈旧多久不能接受"。
     30s 内 OK 通常 `refetchOnWindowFocus: false`（不闪屏）
- `staleTime` 也一并显式：缺省 0 = 任何 invalidate 都重 fetch，多数情况
  应该 = `refetchInterval` 或更高
- code-reviewer 工作清单：grep `useQuery.*refetchInterval` + `useQuery.*staleTime`，
  缺 `refetchIntervalInBackground` 自动标 finding
- 模板/架构层：可建 `useDashboardQuery(key, fn, opts)` wrapper，默认带
  3 个 flag 全填，新 hook 调 wrapper 不能漏（**N=7 时再抽，现 N=7
  正好**）

**触发条件**：
- 新增任何 `useQuery({refetchInterval: ...})`
- 加新 dashboard / 后台轮询数据源
- copy-paste 已有 hook pattern

**反面教材**：
- 2026-05-02 perf-audit `.claude/reports/perf-audit-debt022-2026-05-02.md`
  HIGH #2：6 hook 漏 gate，隐藏窗口 zero-value polling 累计 ~720 req/h
  浪费

**应用到 agents**：
- code-reviewer 加规则：所有 `useQuery({refetchInterval: ...})` 必须
  附带 `refetchIntervalInBackground: false`，否则标 medium finding
- ui-fluency-auditor 加视觉 / network-tab 验证：浏览器隐藏 30s 后
  network 应 0 新 request

**关联**：L-022 (vite proxy stale · 也是"代码看着对，行为静默失效"
的同形态)，performance-auditor finding HIGH #2 / 2026-05-02

---

## L-026 · 多 task 并发 send 同一 WS 时，close-frame 必须只发一次（race + state machine 双重隐患）

**Date**: 2026-05-02（PR-C.c review HIGH #1 暴露 + 修 commit `8a98dfd`）

**规则**：当 WebSocket endpoint 启 ≥ 2 并发 task（pump_task + recv_task
是典型 pattern）时，**任何 task 都不要直接 `ws.send_json({type:"closed",
...})`**。改成 task 各自更新一个共享 `_CloseState` dataclass + return，
**outer finally 在 wait/cancel 完成后唯一发一条 close 帧**。

**Why**：
- PR-C.c bidirectional UART WS 启 pump + recv 两 task 共享 link：
  pump 在 link.reader OSError 时本来发 `{closed reason=stream_error}`
  然后 return；recv 在 link.writer OSError 时本来发 `{closed
  reason=write_error}` 然后 return；outer finally 不管谁先结束都补一条
  `{closed reason=ended}`
- 双 task 错误几乎同时发生时（最常见 link 半断）→ 客户端可能收 2 条
  close 帧，前端状态机依赖第一条，顺序乱
- 即使只一个 task 错，cancel 不是瞬时的：被 cancel 的另一个 task 如果
  正卡在 `await ws.send_json` 中段，cancellation 注入后 outer finally
  已经写入第二条，依旧双发
- starlette WebSocket `send_json` 在 close 后调会抛 `RuntimeError`，
  虽然外层 `contextlib.suppress(Exception)` 兜底但日志还是污染
- 前端 useUartStream / useTerminalSession state machine 都用第一条
  close.reason 决定 error/ended，乱序 = 误报 error 状态或者错过真因

**How to apply**：
- WebSocket 多 task pattern 必须 3 件套：
  1. `_CloseState` dataclass(`reason: str = "ended"`, `error: str | None`)
  2. inner task 错误 path 仅写 close_state + return，**不发 close 帧**
  3. outer finally 跑 wait + cancel 后唯一发一条 close 帧（payload 来自
     close_state）
- 现有 reference implementation：
  - `src/alb/api/uart_stream_route.py::_run_bidirectional` (PR-C.c
    follow-up 修后)
  - `src/alb/api/terminal_route.py:139` (M2 ship 时就用对的 pattern，
    PR-C.c 第一版没参考是 review 暴露的)
- code-reviewer 加规则：grep 任何 WS endpoint 内的 `ws.send_json.*closed`
  调用点超过 1 个，标 HIGH finding
- 必须有 OSError 路径回归测试：fake reader/writer 抛 OSError →
  期望仅 1 条 close 帧 + 期望 reason 正确

**触发条件**：
- 写 WebSocket endpoint 启 ≥ 2 task
- inner task 有错误 path 想发 close/error 帧
- 多 task 共享同一 link 资源

**反面教材**：
- 2026-05-02 PR-C.c v1 (`cef3d1f`) `_pump_link_to_ws` 与 `_recv_loop`
  各自发 close 帧 → review HIGH 1 → 修 commit `8a98dfd` 加 _CloseState
- 反观 terminal_route.py (M2 ship `bef8b2a`) 一开始就用 outer-finally
  唯一 close pattern，没踩这个坑

**应用到 agents**：
- code-reviewer：WS endpoint review 必须 grep 内部 task 函数体里的
  `send_json.*closed`，多于 1 处 = HIGH
- 写新 WS endpoint 时主对话查 reference: terminal_route.py 是金标准

**关联**：terminal_route.py:139 (close-frame outer-finally pattern 金标准) ·
PR-C.c review HIGH 1 · L-019 (同形态：local error path 各自决策结果不一致
= 反模式)

---

## L-027 · HITL `approve_session` 用 line 字面 key 抗不住 shell 变量展开 / 别名

**Date**: 2026-05-02（PR-E.v2 引入 + security audit 当天发现 + 修
commit `75a07d7`）

**规则**：HITL "approve for session" 类机制不能用**用户输入字面值**当
session-allowed key — 命令含 shell metachar (`$`/`` ` ``/`;`/`|`/`&`/
`>`/`<`/`(`/`)`/`{`/`}`/`*`/`?`/`[`/`]`/`\\`) 时，下一次同字面值可以
解析到完全不同的命令。要么 (a) 拒绝把含 metachar 的命令晋升 session-
allowed (本项目选)，要么 (b) 用 rule.name 当 session key（"凡 rm-rf-root
规则后续命中都直通"），要么 (c) 解析 + 规整化命令再 hash。

**Why**：
- 2026-05-02 PR-E.v2 给 ShellTab 加 HITL approve/deny modal · 用户能选
  approve once / approve session
- backend `terminal_guard.respond_hitl` 在 allow_session 路径
  `self._session_allowed.add(line.strip())` —— **session key = 用户
  输入字面值**
- 攻击向量：approve `eval $X` 一次 → 用户/agent 后续设 `X='rm -rf /'`
  → 再敲 `eval $X` → `line.strip() == "eval $X"` 命中 set 直通 →
  shell 端展开 `eval rm -rf /` → 绕过整个 deny-list
- 等价路径：`alias rm=cp` 后 approve `rm /system/build.prop` →
  `_session_allowed` 含 `rm /system/build.prop`，用户改 alias 回去 →
  下次同字面 → 跑真 `rm`（虽然 alias 通常 shell 重启失效，但 PTY 持
  续会话内有效）
- v1 silent auto-deny 不存在这个攻击面 —— PR-E.v2 引入 modal 后
  approve_session 才有"被字面 key 误信"的问题
- security-and-neutrality-auditor 当天发现，real-world exploitable

**How to apply**：
- HITL session 缓存 key **必须**对原始命令做以下之一：
  - 拒含 metachar 命令晋升 session（保留 approve once 路径）
  - rule-name 级 session（"approve 这条规则后续直通"，更宽松但语义
    一致）
  - 命令规整化后 hash（去空格 / 解 alias / 拒展开变量）
- code-reviewer 加规则：grep `_session_allowed.add\|allow_session`，必
  查 line key 是否对 metachar 安全
- 文档化：approve_session 提示"仅用于无 metachar 的精确字面命令"
- 反向：单次 approve 路径不受影响（每次都过 deny-list）

**触发条件**：
- 实现 HITL approve/deny + "for session" 长效授权机制
- 用 line 字面值 / regex 匹配做缓存 key
- 缓存条目本身可被用户/agent 后续输入命中

**反面教材**：
- 2026-05-02 PR-E.v2 (commit `14fa208`) `terminal_guard.respond_hitl:284`
  `self._session_allowed.add(line.strip())` 字面 key bypass，security
  audit 立即发现，commit `75a07d7` 加 `_has_shell_metachars` 检查 +
  audit `hitl_approve_session_refused` 事件 + regression test

**应用到 agents**：
- security-and-neutrality-auditor 加规则：HITL session-cache key 设计
  必须查 metachar / alias / glob 抗性
- code-reviewer：approve/deny 类 modal + session option 出现时必须 grep
  对应 backend cache 实现是否对原始输入安全

**关联**：L-019 (sentinel 反模式，本条是"用未经清洗的字面值当 trust
key" · 同形态) · L-022 (设计良好的合理态掩盖配置 stale，本条是"设计
良好的 session approve 掩盖语义漂移") · ADR-031 seed (filesync HITL
endpoint vs PermissionEngine · 同 modal pattern 下沉路径)

---

## L-028 · React.lazy + Suspense fallback 高度必须显式匹配 lazy 子组件最小高度

**Date**: 2026-05-02（PR-E.v2 + DEBT-022 perf-fix `0c74b2c` lazy load 落地后被 ui-fluency-auditor 发现）

**规则**：用 `<Suspense fallback={...}>` 包裹 `React.lazy()` 子组件时，
fallback 元素**必须**显式设置 `min-height` 至少匹配子组件实际渲染最小
高度（≤ 100px 差异内）。否则首次 chunk 加载时 fallback 60-80px →
真组件 480-540px 跳变，**首屏必 CLS**。

**Why**：
- 2026-05-02 DEBT-022 perf-fix 把 inspect 8 tab 全 React.lazy 化，
  Suspense fallback `<div className="mock-card">loading…</div>` 高度
  = padding(48px) + 1 line ≈ 60-80px
- 真 tab 内容（FilesTab/UartTab/UiDumpTab）`min-height: 480-540px`
- 用户首次切 tab → fallback 60px → 真 tab 540px → ~480px 跳变
- subsequent 切 tab（chunk 已 cache）≈ 10ms 闪过，肉眼不一定看到，但
  4-9 KB chunk 在弱网 / 真夸国节点 100-300ms fallback 可见 = 肉眼能
  看到页面跳

**How to apply**：
- `<Suspense fallback={X}>` 的 X 必须满足以下之一：
  - inline style `{minHeight: <匹配子组件高度>}`
  - 专用 skeleton class 自带 `min-height`
  - 或直接复用子组件的容器 className 做 placeholder（推荐 — 高度自动
    一致）
- 文件落地：`web/src/features/inspect/InspectPage.tsx` 这种 lazy 集中
  注册的地方，fallback 应该集中在 1 个位置 + 用最大 lazy 子组件的
  min-height
- code-reviewer / ui-fluency-auditor 加 grep 规则：
  `grep -rn 'Suspense fallback={' web/src/` → 看每处 fallback 元素是否
  有 `minHeight` 设置 / 自带 `min-height` className

**触发条件**：
- 加新 React.lazy + Suspense
- 改 lazy 子组件的高度 / padding
- 加新 tab / route 走 lazy load

**反面教材**：
- 2026-05-02 DEBT-022 perf-fix `0c74b2c` lazy 化 8 tab 时 fallback 写
  成 `<div className="mock-card">loading…</div>` 没 minHeight，首次切
  tab CLS 480px。当天 ui-fluency 复审才发现

**应用到 agents**：
- ui-fluency-auditor checklist 加："Suspense fallback 必查 minHeight"
- code-reviewer 加同样 grep 规则（防回归）

**关联**：L-022 (设计良好的错误态是双刃剑 · 同源——视觉上"loading 字看
着没问题"掩盖布局跳变) · DEBT-022 perf-fix (PR-F lazy load 落地时引入
本问题)

---

## L-029 · 共享 modal 组件 N=2 起必有 a11y 三件套基线（focus / Enter+ESC / 危险按钮顺序）

**Date**: 2026-05-02（PR-E.v2 HitlConfirmModal 抽提 N=2 后被
ui-fluency-auditor 当天发现）

**规则**：`web/src/components/` 下含 `role="dialog"` 的共享 modal 组件
被 ≥ 2 处 import 时，**必须**满足：
1. **初始 focus**：组件 `useEffect` 在 open=true 时 `cardRef.current?.focus()`
   （容器加 `tabIndex={-1}`），不能停在背景按钮
2. **Enter + ESC 键**：ESC 关闭（非危险默认）+ Enter 绑定**非破坏性**
   按钮（避免误触发 destructive action）
3. **危险按钮顺序**：approveDanger=true 时，destructive 按钮在最右
  （离手指最远），Cancel 在最左 + autoFocus（"危险动作离手指最远"
   惯例）

不能等 N=3 引 react-aria-modal 才补 a11y 基线 —— N=2 抽组件时就要立。

**Why**：
- 2026-05-02 PR-E.v2 commit `14fa208` 抽 HitlConfirmModal（ShellTab +
  FilesTab N=2 共享）
- 当天 ui-fluency-auditor 三 HIGH 全在这组件：
  - 无 focus trap + 无初始 focus 设置（用户从背景 Tab 出 modal）
  - DOM 顺序 Cancel → Approve session → Approve once，红色 danger 在
    最右但 Tab 第二下落"Approve session"（更危险但视觉权重低）
  - 无 Enter 绑定，键盘按 Enter 体感"modal 没反应"
- L-020 "N=3 才抽 base class" 是讲**抽象时机**，但 modal 组件 N=2
  抽出时**a11y 基线已经欠下**，等 N=3 引专用库已经晚 —— 抽组件那一刻
  就该一次性把 a11y 起手三件套写对

**How to apply**：
- ui-fluency-auditor / code-reviewer 加 grep：
  `grep -rn 'role="dialog"' web/src/components/` 命中 + import 处 ≥ 2
  → 必查三件套是否齐全
- 模板：modal 组件 props 必含 `autoFocusOn?: 'cancel' | 'approve'`
  （approveDanger=true 默认 cancel） + 内部 `useEffect` 在 open 时
  focus 对应 ref + 加全局 onKeyDown for Enter

**触发条件**：
- `web/src/components/` 加新 `role="dialog"` 组件
- 已有 modal 组件被第 2 个调用方 import
- modal 加 destructive action（rm/push 系统区/reboot 等）

**反面教材**：
- 2026-05-02 commit `14fa208` HitlConfirmModal N=2 抽提，当天 a11y 三
  HIGH 暴露，紧 follow-up 修

**应用到 agents**：
- ui-fluency-auditor checklist 含"shared modal a11y 三件套"
- code-reviewer 看 `web/src/components/` 加新含 dialog 的组件 → 触发
  ui-fluency-auditor 深审

**关联**：L-020 (N=3 抽 base 时机 · 本条是"抽组件 N=2 时 a11y 基线
也要同步落"补充) · L-027 (HITL allow_session metachar bypass · 同源
"危险 action UI 必须有防误触安全余量")

---

## L-030 · NaN 钳位行为按"语言 + 顺序"分级 · explicit NaN check 是唯一跨语言可移植安全写法

**Date**: 2026-05-02（functional audit MID-7 metrics 触发 + 同日 grep
全仓扫触发 self-correction 修订）

**修订史**：
- v1（同日早写）：错误地宣称"max/min 永远不安全 NaN 会传染"。把 Python
  `max(LO, min(HI, x))` 也归入 HIGH，是技术事实错误
- v2（同日 retroactive grep audit 后修订）：实测发现 Python 这个特定
  顺序实际安全。规则按语言 + 顺序重新分级（本条）

**实测真值表**（IEEE 754 NaN，Python 3.11 / Node.js 22 / numpy 1.26）：

| 写法 | 入参 NaN 时返回 | 安全 |
|---|---|---|
| Python `max(LO, min(HI, nan))` | `HI`（clamps） | ✅ |
| Python `min(nan, HI)` | `nan`（传染） | ❌ |
| Python `min(HI, nan)` | `HI` | ✅ |
| Python `max(nan, LO)` | `nan` | ❌ |
| Python `max(LO, nan)` | `LO` | ✅ |
| JS `Math.max(LO, Math.min(HI, NaN))` | `NaN`（传染） | ❌ |
| JS `Math.min/max` 任意顺序 | `NaN` | ❌ |
| `numpy.clip(nan, lo, hi)` | `nan`（传染 · 设计如此） | ❌ |
| `pandas.clip` / `Tensor.clamp` | `nan` | ❌ |
| `int(float("nan"))` | `ValueError` 抛 | ✅（隐式守护） |

**根因**：Python 内置 `min(a, b)` 实现是 `a if a < b else b`。NaN 比较
全 False，所以 `a < b` 是 False 时返回 `b`。JS `Math.min/max` 设计上
显式传染 NaN（C 标准 fmin/fmax 反而 quiet-NaN，但 Math.min 不是）。
顺序敏感性是 Python 实现细节，**不应依赖**。

**规则**：

- **强 (HIGH)**：JS `Math.max/min` / `numpy.clip` / pandas/torch clamp
  路径上接 user-supplied float 且无 `Number.isFinite` / `np.isnan`
  守护 → 必报 NaN 传染风险
- **中 (MID)**：Python `min(x, HI)` 或 `max(x, LO)` 把变量放第一位 →
  顺序敏感，建议加守护或改成 `max(LO, min(HI, x))` 标准顺序
- **弱 (LOW / 可放过)**：Python `max(LO, min(HI, x))` 标准顺序 + 上游
  有 `int()` / try-except / `|| DEFAULT` 兜底 → 实际安全，但**仍建议
  加 explicit NaN check 提高跨语言可读性**（避免读者误以为不安全 ·
  避免后续重构改顺序时静默引入 bug）

**How to apply**（统一推荐写法 · 最强可移植）：

```python
def setter(self, value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return  # garbage 不动旧值
    if v != v:  # NaN check（IEEE 754 唯一可移植判法）
        return
    self._field = max(LO, min(HI, v))
```

```typescript
function clamp(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;  // NaN / Inf / -Inf 全挡
  return Math.max(lo, Math.min(hi, v));
}
```

**触发条件**：

- 输入来源是 user-supplied float（HTTP body / WS frame / param /
  React input + 无 `|| DEFAULT` 兜底）
- 写 `np.clip` / pandas clamp / torch clamp（任何顺序都传染）
- 写 JS `Math.max/min` 钳位（任何顺序都传染）
- setter 写入后续被 `asyncio.sleep` / `time.sleep` / `Decimal(x)` /
  绘图坐标 / SVG d-path 等"NaN 不友好"消费者用

**反面教材**：

- 2026-05-02 早班自己写 L-030 v1 时**没实测**就泛化"NaN 穿过
  max/min"。同日下午 grep 全仓扫拿"playground.py HIGH retroactive"，
  实测后才发现 Python `max(LO, min(HI, x))` 在这个顺序下**实际安全**。
  虚警一次。教训：**写 lesson 前必须实测**，不能用"应该如此"的推演
  当事实
- 真存在风险的是 useLiveSession.ts:180 `Math.max(0, Math.min(SPARK_HEIGHT,
  SPARK_HEIGHT * (1 - norm)))` —— JS Math 真传染 + samples 来自 WS 帧
  无 isFinite filter（DEBT-030 跟踪）

**应用到 agents**（按语言 + 顺序分级 · 见 code-reviewer.md L-030 grep）：

- 命中 `Math\.max\(.*Math\.min\(|Math\.min\(.*Math\.max\(` 在 .ts/.tsx
  → user input 链路无 Number.isFinite 守护 = **HIGH**
- 命中 `np\.clip\(|\.clamp\(` 在 .py → 无 np.isnan 守护 = **HIGH**
- 命中 Python `max\([^)]*min\(` → 标准顺序 + 上游隐式守护 = LOW
  （建议加显式 NaN check，但不阻塞 ship）
- 命中 Python `min\([a-z_][^)]*,` 变量在第一位 → MID（顺序敏感）

**关联**：L-024 (单测用 GNU coreutils mental model · 本条是"用'数学常识'
mental model 写代码漏 IEEE 754 corner case + 漏跨语言行为差异" · 同
形态盲区) · 全局 CLAUDE.md "代码事实禁止 hedge / 写前必须实测"

**v3 addendum（2026-05-05 · audit 虚警同形态收窄）**：

retroactive grep audit 找到的 finding 不应直接评 HIGH/MID。**static-only
audit 触发 finding 必须先 TestClient / 真跑一遍验证**再决定严重度。

**累计虚警率（2026-05-05 update · 4/13 = 31%）**：functional audit
2026-05-02 共 13 个 finding（5 HIGH + 8 MID），retroactive 验证后 4
个虚警：
- HIGH 3 "metrics queue 无界" — 实测已 cap maxsize=20 + drop-oldest
- MID-4 "Range header 不支持" — Starlette FileResponse 1.0 原生支持
- MID-5 "PTY exit 无 rationale" — stdout 实时 send_bytes 给 xterm，
  close-frame 带 exit_code，rationale 走输出不走 JSON
- MID-8 "WS 无 heartbeat" — uvicorn 默认 ws_ping_interval=20s 已开

共同失败模式：**"我 grep 文件没看到 X 关键字，所以判 GAP"**。framework /
library / runtime 提供的隐式能力（Starlette Range / uvicorn ws_ping /
async generator drain semantics 等）grep 不到，但运行时行为正确。

**处理方式 vs 真 GAP**：
- 真 GAP（如 MID-1/2/3/7、MID-6 / 早班 9 HIGH 中真的 8 个）→ 修代码
- 虚警（4 个）→ 不修，加 regression 测试**锁定隐式行为**（防止未来
  framework 降级 / refactor 静默退化掉这个能力），audit 报告标注
  retroactive corrections + reason

**应用到 agents**（functional-auditor / security-auditor）：

- 找到看似 GAP / 缺失 / 未验证的 endpoint behavior → 触发"先用 TestClient
  跑一下"步骤再下严重度。framework / library 提供的隐式能力（Starlette
  Range / FastAPI auto-OpenAPI / Pydantic v2 strict / uvicorn ws_ping /
  asyncio EOF semantics 等）必须实测验证本 endpoint 是否启用，不能
  仅凭 grep 不到关键字就判 GAP
- audit 报告 finding 必含 "verification method" 标注：`[static]` /
  `[runtime]` / `[real device]` 三档，让读者知道 finding 的可信度。
  static-only finding 默认严重度上限 LOW（升级到 MID/HIGH 前必须
  re-verify with runtime）
- 虚警率超 20% 时，触发 audit-method 复盘 — 当前 31% 已超阈值

---

## L-031 · `contextlib.suppress(Exception)` 不抓 CancelledError · 必显式列举

**Date**: 2026-05-06（MID-6 commit 90 _pump_transfer 调试触发 · fix `7b9afc0`）

**规则**：Python 3.11+ `asyncio.CancelledError` 是 **`BaseException` 子类**，
不是 `Exception` 子类。任何 `with contextlib.suppress(Exception):` 包住
**会被 cancel 的 await**（典型 `await task` / `await gen.aclose()` /
`await asyncio.wait_for(...)`），CancelledError **不会被吞**，会从 `with`
块漏出，破坏 finally 的"安静清理"语义。

**正解**：显式列举 `(asyncio.CancelledError, Exception)`，或用
`BaseException`（更宽泛但要小心 KeyboardInterrupt / SystemExit 也吞掉）。

```python
# ❌ 3.11+ 会漏 CancelledError
with contextlib.suppress(Exception):
    await producer_task

# ✅ 三种正解
with contextlib.suppress(asyncio.CancelledError, Exception):
    await producer_task
# 或
try:
    await producer_task
except (asyncio.CancelledError, Exception):
    pass
# 或参考 terminal_route.py 已有写法（仅 CancelledError）：
with contextlib.suppress(asyncio.CancelledError):
    await pending_task
```

**Why**：

- Python 3.7 以前 CancelledError 继承 `concurrent.futures.CancelledError`（=
  Exception 系）。**3.8 改继承 BaseException**，避免被 broad except 误吞。
  这是有意为之的语言变更（PEP 567 鄄关），但对老代码 / mental-model 不友好
- 2026-05-06 MID-6 commit 90 调试：4 个 WS 测试失败，traceback 终端是
  `concurrent.futures._base.CancelledError`，testclient 的 `with __exit__`
  收到 CancelledError 抛出。耗时排查 1+ 小时定位到 _pump_transfer finally
  里 `suppress(Exception): await producer_task`（producer 已被 cancel）。
  用户视角看到的是"测试失败但日志含混"，实际是 cancel 路径漏到上层
- 同形态在 alb 已有正例：`terminal_route.py:166-168` `with
  contextlib.suppress(asyncio.CancelledError): await t`。说明 reviewer
  规则若早立，本次 commit 90 写下时就能被自动抓出，省 1 小时

**How to apply**：

写"finally 清理 cancel 过的 task" 模式时，suppressor 必须包含
`asyncio.CancelledError`：

```python
finally:
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
```

**触发条件**：

- finally / except 里 await 之前 `.cancel()` 过的 task
- finally 里 `await gen.aclose()` 一个 async generator（aclose 内部可能 cancel）
- `await asyncio.wait_for(..., timeout)` 超时分支后 cleanup
- 任何 `try: await ...; except (asyncio.TimeoutError,): pass` 之外的 exception 处理

**反面教材**：

- 2026-05-06 commit 90 (`7b9afc0`) `_pump_transfer` finally：
  ```python
  if not producer_task.done():
      producer_task.cancel()
      with contextlib.suppress(Exception):  # ❌ 漏 CancelledError
          await producer_task
  ```
  4 个 WS 测试失败，traceback 链路 6+ 层。修法：
  `(asyncio.CancelledError, Exception)`。
- 现 alb 仓 grep `suppress\(Exception` 在 finally / except 块中的所有
  位置（commit 91 升级时一并扫一遍 retroactive）

**应用到 agents**：

- code-reviewer agent grep checklist 加规则：
  - 命中 `with contextlib\.suppress\(Exception\)` →
    上下文 5 行内有 `await .*\.cancel\(\)|await .*task|await .*\.aclose\(\)|
    await asyncio\.wait_for` → **MID** finding
  - 提示用户改成 `(asyncio.CancelledError, Exception)` 或专用
    `(asyncio.CancelledError,)`
- 该规则同时帮检 `except Exception:` 后跟同类 await 但漏 CancelledError 的情况

**关联**：L-026 (WS 多 task close-frame race · 同 finally cleanup 区域 ·
本条补"清理代码本身的 cancel 安全"维度) · L-030 (NaN 钳位 · 同形态
"语言版本细节让代码看起来对实际错"的 mental-model 盲区) · 全局 CLAUDE.md
"代码事实禁止 hedge / 写前必须实测"

---

## L-032 · 新 sidebar / list pattern 抽出时 a11y 三件套基线 · `aria-current` + `aria-live` + destructive 防呆

**Date**: 2026-05-07（ui-fluency audit 当周 6 web feat 一次发现 3 HIGH ·
fix `<待 commit>`）

**规则**：当一个 React feature 引入 **sidebar 列表 pattern**（左侧 list
+ 右侧 viewer，用 `selected` state 标记当前激活行），三件套必须
**首次落地时一起加**，否则后续审计周必"系统性命中"：

1. **`aria-current="true"`** 加在选中行的 `<button>` / `<a>`：
   纯 CSS `is-active` border 是视觉信号，screen reader 完全感知不到
   "我在哪一行"。`aria-current` / `aria-selected` 二选一（list 用
   current，菜单/tab 用 selected）。

2. **`aria-live="polite"` + `role="status"`** 包动态状态文本：
   filter counter "M of N matches" / debounce hint "applying…" /
   capture trigger "上次：N 行 / N 错误" 等会随交互变化的文本，盲用户
   原本完全沉默；polite 不打断当前阅读，刚好。

3. **destructive button 双重防呆**：
   - 视觉：opacity 30-40% 默认（不要 0/全隐藏 + hover-only 显形 →
     键盘用户不安全）
   - 行为：第一次点击 *arm* (3s timeout)、第二次点击才执行；或
     `<HitlConfirmModal>`（N=3 时按 L-020 抽 base）

**反面教材** 2026-05-07：

- `UartCaptureView.tsx` 删 capture：默认 opacity 0 + hover 显形 +
  onClick 直接 `remove.mutate(name)`。键盘 Tab focus 后 Enter 立即
  删，无任何防呆 → ui-fluency HIGH-1
- `ScreenshotTab.tsx` / `UartCaptureView.tsx` / `FilesTab.tsx` 三个
  sidebar 全用 `is-active` 纯视觉标选中，0 命中 `aria-current` →
  ui-fluency HIGH-2
- `LogcatTab.tsx` "applying…" hint + `UiDumpTab.tsx` "M / N 匹配"
  counter 都是普通 `<span>`，0 命中 `aria-live` → ui-fluency MID-1

**触发条件**：任何新 feature 出现 list/sidebar/动态 hint/destructive
button 任一即触发，不等 N=2/N=3（这是 a11y 基线，不是抽组件抉择）。

**应用到 agents**：ui-fluency-auditor grep checklist 加规则（已落档）：

- list `is-active` 命中 → 同行/同 component 必须有 `aria-current` /
  `aria-selected` / `aria-pressed` 之一 · 缺 → **HIGH**
- destructive `mutate(...)` 在 onClick 直挂（无 confirm / modal /
  arm-step）· 缺 → **HIGH**
- 动态变化的状态文本（counter / hint / pill）周围找 `aria-live` ·
  缺 → **MID**

**关联**：L-029 (共享 modal a11y 三件套 · 本条 "list" 同形 "modal"
基线，但针对不同 pattern) · L-028 (Suspense fallback minHeight · 都是
"基线漏一次后续每个 feature 复 N 次"模式) · L-020 (N=2 不抽 base ·
本条相反，a11y 不等 N=3 — 是基线不是抽象)

---

## L-033 · async FastAPI endpoint 内 sync FS 调用必走 `asyncio.to_thread` · "io_to_thread sweep" 模式

**Date**: 2026-05-08（perf-audit-2026-05-08 sweep · fix `<待 commit>`）

**规则**：FastAPI `async def` endpoint 里 **任何同步 FS / IO 调用**
（`Path.read_text` / `Path.read_bytes` / `Path.open().read()` /
`Path.stat` / `Path.glob` / `os.listdir` / `subprocess.run` /
`time.sleep` 等）必须包到 `await asyncio.to_thread(...)`。否则**单
event loop stall**：高 QPS 下任意一个慢 IO 就让所有连接卡住。

```python
# ❌ 错（async 路径 sync IO，loop 卡）
@router.get("/preview/{path}")
async def preview(path: str):
    target = resolve(path)
    data = target.read_bytes()  # 64 KB cache miss → ms 级 stall
    return {"text": data.decode("utf-8")}

# ✅ 对（sync IO 入 worker thread，loop 自由）
@router.get("/preview/{path}")
async def preview(path: str):
    target = resolve(path)
    data = await asyncio.to_thread(target.read_bytes)
    return {"text": data.decode("utf-8")}
```

**触发条件**：

- `async def` endpoint / WS handler 中 path/file/glob/stat/subprocess
- 单文件 ≤4 KB cache hit 影响小，但 cold cache / >64 KB / 慢盘
  立刻显形为 P99 spike
- 高 QPS（多用户同时点 preview / list）下，event loop 一旦卡，所有
  其它请求排队等

**反面教材** 2026-05-08 perf-audit `5/06~5/08 累积 15 commits`：

- `workspace_preview`：`target.stat()` + `target.open("rb").read(64K)`
  + `_looks_binary()` 三步全 sync → MID
- `read_capture`：`f.read_text()` 同步加载 5-50 MB UART log + UTF-8
  decode → LOW（边界场景但触发即 200-500 ms stall）
- `list_screenshots`：`base.glob` + N×`stat` + N×`f.read(24)` 全 sync →
  LOW（小 N 无感，未来 unbounded 后变 hot path）

修法（同源批量）：

- 抽 `_xxx_in_thread(args)` helper（pure sync 函数），endpoint 内
  `await asyncio.to_thread(_xxx_in_thread, args)` 调用一次
- 多个相关 IO（如 `stat + read`）打包进同一 helper，避免多次 thread
  hop overhead

**触发时机**：每次新写 async endpoint 时；对老 endpoint 周期性
sweep（5/02 perf-audit 漏检的 `read_capture` 就是没 sweep 全才漏）。

**应用到 agents**：performance-auditor + code-reviewer agent grep
checklist 加规则：

- async endpoint / async def 内命中 `\.\(read_text\|read_bytes\|stat\|glob\|listdir\|open\)\(` →
  上下文 5 行内无 `asyncio\.to_thread` → **MID** finding
- 同 commit 多处命中 → 建议批量 sweep（一次 commit 修完同源债）

**关联**：L-014 (alb_describe / hot path 同形 · "新功能必 sweep
checklist" 模式) · L-025 (useQuery refetchInterval · 同形 "新 hook
必 sweep config flag" 模式) · L-032 (sidebar a11y · 同形 "基线漏一次
全 feature 复 N 次") · ADR-026 (httpx.AsyncClient · 同源 IO 协程化
原则)

---

## L-034 · per-connection 独占网关 vs listen-socket daemon 的 ECONNRESET 语义不同 · TCP 重试范围必须按 transport 角色设计

**Date**: 2026-05-09（Bug-1 fix · part 131 commit `fb236ac`）

**规则**：写 transport `_open()` / `_connect()` 的 retry policy 前，
**先分清网关角色**：

- **per-connection 独占网关**（ser2net、socat single-port、qemu serial
  bridge、某些 SLIP 桥）：一次只允许一个客户端持有底层资源（serial
  fd / pty）。前一个客户端断开后有 release window，期间新连接 accept()
  但被立即 RST。这种 ECONNRESET 是**预期的临时态**，必须 bounded
  retry 吸收。
- **listen-socket daemon**（adb server、sshd、redis、postgres 等）：
  服务端始终接受新连接、每个连接独立处理。ECONNRESET 几乎一定是真
  问题（client crash / firewall RST inject / kernel resource exhaust），
  retry 是误判 → 掩盖真 bug。

不分清两者就一股脑 retry 全部 `ConnectionResetError` →daemon 类
transport 上把真错误吸成静默重试，等 issue 跑出来追溯极慢。

**触发条件**：

- 写新 transport 的 connect 路径
- 看到 in-flight 已经处理 `ConnectionResetError` / `BrokenPipeError`，
  但 connect 阶段没处理 → 判断要不要补
- review 别人加 retry loop 时

**反面教材**（假想 · 但模式真实存在 · 给 reviewer 一眼能识别的 diff）：

```python
# ❌ 错（adb daemon 是 listen socket，retry 会掩盖真 bug）
class AdbTransport:
    async def _open(self) -> Link:
        for attempt in range(3):
            try:
                return await connect_adb_server()
            except ConnectionResetError:
                # daemon 端 RST 几乎不可能是 race，是 daemon crash /
                # firewall 干预 / 资源耗尽 — retry 掩盖真错误
                await asyncio.sleep(0.1 * 2**attempt)
                continue
        raise
```

```python
# ❌ 错（同样在 ssh transport / redis client 加宽泛 retry）
async def _open_ssh(self):
    for _ in range(5):
        try:
            return await asyncssh.connect(...)
        except (OSError, ConnectionError):  # 太宽：refused / unreachable / RST 都吃
            await asyncio.sleep(0.5)
            continue
```

**正例**（part 131 fb236ac · `src/alb/transport/serial.py:155-220`）：

```python
class SerialTransport:
    # 窄白名单 + bounded budget + 错误信息标注语义
    _TRANSIENT_CONNECT_ERRORS = (ConnectionResetError, BrokenPipeError)
    _CONNECT_BACKOFF_S = (0.1, 0.3, 0.6)  # cumulative ~1s 内自愈

    async def _open_tcp_with_retry(self) -> _SerialLink:
        attempts = len(self._CONNECT_BACKOFF_S)
        for attempt in range(1, attempts + 1):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.tcp_host, self.tcp_port),
                    timeout=10,
                )
                return _SerialLink(reader=reader, writer=writer, ...)
            except self._TRANSIENT_CONNECT_ERRORS as e:
                # 已知 ser2net 有 fd-release race；其他 transport 不该来这条
                if attempt < attempts:
                    await asyncio.sleep(self._CONNECT_BACKOFF_S[attempt - 1])
                    continue
                raise ConnectionError(
                    f"... kept resetting after {attempts} attempts: {e}"
                ) from e
            except (OSError, asyncio.TimeoutError) as e:
                # refused / timeout / ENETUNREACH 立刻失败 — 真 misconfig
                raise ConnectionError(f"Cannot reach ...: {e}") from e
```

设计四要点（reviewer checklist）：

1. **retry 范围窄**：仅在已确认是 per-connection 独占网关的 transport 加
2. **错误白名单**：只 retry `ConnectionResetError` + `BrokenPipeError`；
   refused / timeout / ENETUNREACH 立即失败
3. **budget bounded**：3 次 backoff 累计 ~1s；超出抛 `ConnectionError`
   with "kept resetting after N attempts"
4. **错误信息语义化**：retry 耗尽后的错误信息和 first-attempt 错误信息
   必须不同（前者带 attempts 计数）

**应用到 agents**：code-reviewer + architecture-reviewer 看到 transport
`_open` / `_connect` 加 retry 时：

- grep `except.*ConnectionResetError|BrokenPipeError.*\n.*sleep\|continue`
  on transport 类
- 命中后**不直接报 finding**，要看上下文 transport 角色：
  - 是 per-connection 独占网关（ser2net 类）→ ✅ ok
  - 是 daemon-style listen socket（adb / ssh / redis / pg 类）→ **HIGH**
    finding（提示 retry 掩盖真错）
- review 评论里要明确标注 transport 角色判断依据

**关联**：

- L-019（ABC sentinel · 同形 "广覆盖看似聪明，实际掩盖语义差异"）
- L-020（N=2 不抽 base · 本条 N=1 的 retry pattern 故意不抽 base
  helper，因为不同 transport 的 transient-error 语义不同，抽出来反而
  失去类型差异）
- L-009（代码事实禁止 hedge · "ser2net 是 per-connection 是事实，
  不靠猜测"）
- next_dev_priorities.md Bug-1 历史 · 修复 commit `fb236ac` part 131

**L-meta-001 四件套自检**：

- ✅ grep pattern：`except.*ConnectionResetError|BrokenPipeError.*\n.*sleep\|continue`
- ✅ 反面教材：AdbTransport / ssh client 假想 retry diff（具体可识别）
- ✅ 正例：`src/alb/transport/serial.py:155-220` part 131 实现
- ⚠️ agent checklist 同步：本条更偏架构判断（transport 角色）而非
  mechanical grep；reviewer 上下文阅读后判断，不能纯 regex 自动报

---

## L-035 · 用户输入拼路径必须根因层 reject `..` / 绝对路径 / 分隔符 · `Path / user_input` 不规范化必逃逸

**Date**: 2026-05-09（self-audit security-reviewer 找到 path traversal MID ·
PoC 验证 · 修复 commit `a1612aa` part 134）

**规则**：所有"用户输入字段拼到文件路径"的位点，**必须在根因层（构造 Path
的源头函数）** reject `..` / 绝对路径 / 路径分隔符 / 非 ASCII 等异常输入。
**不能依赖 CLI 层 / API 层各自重复 sanitize**，否则下一个新 surface（Web /
MCP / 自动化脚本）忘了 sanitize 就漏。

`Path("/base") / "../etc"` 不会规范化为 `/etc`，但 `is_dir()` 跟随符号链
+ 文件存在就放行。下游 `read_text()` / `open()` 直接读穿。

```python
# ❌ 错（CLI 层只查存在不查越界）
def _ensure_session_exists(session_id: str) -> Path:
    sdir = _sessions_root() / session_id
    if not sdir.is_dir():  # 跟随符号链；不阻止 ".."
        raise typer.Exit(1)
    return sdir

# ❌ 错（API 层重复一份 sanitize 容易漏）
@router.get("/sessions/{session_id}")
def show(session_id: str):
    sdir = workspace_root() / "sessions" / session_id
    if ".." in session_id:  # 漏掉 absolute path / unicode / NUL
        raise HTTPException(400)
    ...

# ✅ 对（根因层 enforce + 异常类型 + 字符白名单 + .resolve() 防 symlink）
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

class InvalidSessionId(ValueError):
    pass

def session_path(session_id: str, ...) -> Path:
    if not _SAFE_SESSION_ID_RE.match(session_id):
        raise InvalidSessionId(...)
    base = workspace_root() / "sessions" / session_id
    sessions_root = (workspace_root() / "sessions").resolve()
    if not base.resolve().is_relative_to(sessions_root):
        raise InvalidSessionId(...)  # defence-in-depth: symlink 绕过
    return base
```

**触发条件**：

- 任何函数签名 `(user_input: str) -> Path` 或 `Path / user_input` 拼接
- 任何 stat / open / read / iterdir 之前的路径来自外部输入
- 重点目录：sessions/ / workspace/ / config/ / artifacts/ / profiles/
- **隐蔽变种**：`_foo_dir(user_input) -> Path` 这种"helper 内部拼接"，下游
  即使用 `resolve_under` 加防穿越也接不住

**`base.resolve()` flatten gotcha**（part 138 找到的非显义陷阱）：

`resolve_under(base, name, ...)` 防 `name` 穿越（regex + symlink + `relative_to`），
但 `base.resolve()` 会把 base 内部的 `..` 也 flatten。如果 base 本身已被
`<...>/devices/../etc/screenshots` 污染，`base.resolve()` 变成
`<root>/etc/screenshots`，然后 `resolved.relative_to(base.resolve())` 对
该 escape 目标里的任意文件反而 succeed —— escape 完成。

```python
# ❌ 错（resolve_under 看似严格，但 base 被上游污染就漏）
def _screenshots_dir(serial: str) -> Path:
    return workspace_root() / "devices" / serial / "screenshots"

def read_screenshot(serial: str, name: str):
    return resolve_under(_screenshots_dir(serial), name, ...)
    # serial="../etc" + name="leaked.png" → 读 <root>/etc/screenshots/leaked.png

# ✅ 对（构造 Path 的源头 helper 自己校验 user_input）
def _screenshots_dir(serial: str) -> Path:
    if not _SAFE_DEVICE_RE.match(serial):
        raise HTTPException(400, detail=f"invalid serial: {serial!r}")
    return workspace_root() / "devices" / serial / "screenshots"
```

教训：**`_foo_dir(user_input) -> Path` 也是根因层**，不只 `Path / user_input`
直接拼接才是。reviewer 看到这种 helper 必须问"user_input 校验在哪"。

**反面教材** 2026-05-09 Bug-X PoC（part 134 `a1612aa` 修复前）：

```bash
# 攻击者只需可读的任意文件路径（受 workspace 父目录权限限制）：
mkdir -p /tmp/ws/etc
echo '{"backend":"evil","model":"pwned"}' > /tmp/ws/etc/meta.json
echo '{"role":"user","content":"INJECTED"}' > /tmp/ws/etc/messages.jsonl
ALB_WORKSPACE=/tmp/ws alb session show ../etc
# → backend=evil model=pwned 都加载进来
```

CLI 自调用是自伤，**但** trust boundary 在 part 130 已经从 1 个命令（chat）
扩到 4 个（chat / show / replay / list）。Web/MCP 接入后是真实任意文件读。

**正例**（多 commit 累计 · 6 个根因层 path-construction 防御点 + 4 个 caller-side 友好错误展示）：

根因层（构造 Path 的源头函数 / helper 都加校验）：

- session_id（part 134 `a1612aa`）：`infra/workspace.session_path()` 用
  `_SAFE_SESSION_ID_RE` + `InvalidSessionId`，双道防御（regex +
  resolve+is_relative_to）
- workspace_path device（part 137 `2dbb7b2`）：`infra/workspace.workspace_path()`
  用 `_SAFE_DEVICE_RE` + `InvalidDeviceSerial`
- API 路由 dir helper（part 138 `5e78c34`）：`_screenshots_dir(serial)` /
  `_logs_dir(device)` 在路径构造前 reject（防 `resolve_under` 的
  `base.resolve()` flatten 陷阱，见下方专门段）
- profile_path（part 139 `571802c`）：`infra/config.profile_path()` 用
  `_SAFE_PROFILE_NAME_RE` + `InvalidProfileName`
- _resolve_search_targets device（part 140 `551bef6`）：MCP `alb_log_search`
  入口，校验 `device`；`search_logs` 捕获并返 `INVALID_DEVICE` fail Result
- 公开 is_safe_X helper（part 142 `88606a6`）：`is_safe_session_id` /
  `is_safe_device` / `is_safe_profile_name` 抽出 N=5+ caller 共享单一
  来源；`_SAFE_*_RE` 改回真正 module-private

**故意不收紧的接口**（design decision，不算 L-035 漏检）：

- `search_logs(path=...)` / CLI `alb log search --path` / MCP `alb_log_search`
  的 `path` 参数：刻意接受任意 FS 位置（用户可以 grep 任意 *.txt），由
  caller 的 trust boundary 控制。这是 grep-style 工具的 feature 而非
  bug — 如果某天接到非 trusted caller（如未来 web exposes 任意 path
  search），需要在那一层重新加沙箱，不在根因层 reject
- `--output` / `-o` 参数（`alb logcat` / `alb dmesg` / `alb diag bugreport`
  等）：用户明确选写入位置，可以是绝对路径 / `~` / 跨 workspace。也是
  feature 不是 bug

Caller-side 友好错误展示（捕获 `Invalid*` 异常转结构化错误）：

- `cli/session_cli._ensure_session_exists` + `cli/chat_cli`（part 134
  `a1612aa`）：catch `InvalidSessionId` → `typer.Exit(1)` + hint
- `api/chat_route` POST `/chat`（part 140 `551bef6`）：catch → error envelope
  `INVALID_SESSION_ID`
- `api/terminal_route` WS（part 140 `551bef6`）：catch → 结构化 closed frame
  `INVALID_SESSION_ID`，不发 1011 abrupt
- `cli/main._main_options` callback（part 140 `551bef6`）：early-validate
  `--profile` flag + `ALB_PROFILE` env，raise `typer.BadParameter`

**测试覆盖**：see `tests/infra/test_workspace_session_id.py` /
`tests/infra/test_config.py` / `tests/api/test_*_route.py` —— 每维度 PoC
+ 参数化恶意输入 + 合法输入边界，逐 commit 增量。

**caller-side wrap 必须在任何 side-effect 之前**（part 143 找到的回归
教训）：

`Invalid*` 异常的 catch 包装在 caller 层时，必须放在**所有触发副作用
的 await 调用之前**。否则触发→失败→return 路径会泄漏已分配的资源。
part 140 把 `terminal_route.py` 的 `session_path(session_id)` 校验放
在 `await transport.interactive_shell(...)` 之后，恶意 `session_id`
触发的 except 分支跳过 `await shell.close()`，泄漏 PTY 子进程。

```python
# ❌ 错（PTY 已 spawn 才校验，except return 漏 close）
shell = await transport.interactive_shell(...)
try:
    audit_path = session_path(session_id, "terminal.jsonl")
except InvalidSessionId:
    await ws.send_json({"type": "closed", ...})
    return  # ← shell 子进程 leak

# ✅ 对（校验前置到任何 side-effect 之前）
try:
    audit_path = session_path(session_id, "terminal.jsonl")
except InvalidSessionId:
    await ws.send_json({"type": "closed", ...})
    return  # ← 此时还没 spawn shell，无 leak
shell = await transport.interactive_shell(...)
```

修复 commit `<part 143>`。

**应用到 agents**：security-and-neutrality-auditor + code-reviewer agent
grep checklist 加规则：

- 命中 `Path\([^)]*\) / [a-z_]+` 或 `_root\(\)\s*/\s*[a-z_]+` 上下文 5 行
  内无 `_SAFE_.*_RE\.match` 或 `is_relative_to` → **MID/HIGH** finding
- 命中 user-input 拼路径 + 仅 `if ".." in name`（字符串 in 检查）→
  **HIGH** finding（漏 absolute path / unicode / NUL）
- 推荐修法：根因层（构造 Path 的源头）加 helper + 自定义 ValueError

**关联**：

- L-009（代码事实禁止 hedge · "PoC 已验证"是事实，不是猜测）
- L-019（ABC sentinel 反模式 · 同形 "宽松默认看似无害，实际是设计陷阱"）
- L-meta-001（四件套：本条全部满足）
- 修复 commit `a1612aa` part 134

**L-meta-001 四件套自检**：

- ✅ grep pattern：见上方 "应用到 agents" 段
- ✅ 反面教材：part 134 修复前 PoC（具体 bash 命令可复现）
- ✅ 正例：infra/workspace.py + 测试文件
- ✅ agent checklist 同步：security-and-neutrality-auditor + code-reviewer
  下一段 commit 同步规则到 agent definition

---

## L-meta-001 · "新增类规则" lesson 必带可执行 grep pattern + 反面教材 + 正例 + agent checklist 同步 · 防止 lesson 写完就稀释

**Date**: 2026-05-08（architecture-reviewer 跨 16 commits audit 提的
meta-观察 · 关 DEBT-035）

**规则**：lessons.md 里所有"新增 X 时漏一组规则"形态的 lesson —— 即
**新增类规则**（new-instance-of-X 时必须按 checklist 加上某些东西）
—— 写入时**必须四件套同时落地**，否则规则随项目 N 增长自然稀释成
"依赖人记忆"，下次跑 reviewer 不会自动捕捉，等于没写。

**四件套**：

1. **可执行 grep pattern**（不只描述，要能 `grep -rE` 跑）：
   - 给出具体正则，标注上下文窗口（"5 行内"/"同一组件内"等）
   - 标注命中后的判定（HIGH / MID / LOW）
2. **至少 1 个反面教材**（具体 commit / 文件 / 行号）：
   - 来源真实 commit · 让 reviewer 知道"长这样的 diff 该报"
   - 跨语言时给等价反例（Python + JS / 前端 + 后端）
3. **至少 1 个已知正例**（修法参考）：
   - 当前仓里能跑得对的 reference 实现位置
   - reviewer 报 finding 时可直接指 "参考 X 文件 Y 行"
4. **agent definition checklist 同步**：
   - 新 lesson 写完，**同 commit** 或紧跟 commit 把规则加到对应
     `.claude/agents/<reviewer>.md` 的"自动 grep checklist"段
   - 这是 lesson **从写到生效**的关键步骤；漏了就是知识衰减

**Why**：

- L-014 (mcp tool docstring sweep) / L-025 (useQuery refetchInterval
  background gate) / L-032 (sidebar a11y 三件套) / L-033 (async sync
  FS to_thread) 都是同骨架"新增 X 时漏一组规则" — 写 lesson 时若漏
  了 grep pattern + agent sync，下次审 PR 不会自动报 → finding 靠
  人脑记
- 5/02 4-agent audit 找到 9 HIGH 之所以有效，正是因为 agents 已加
  L-019/022/024/025/026/027/028/029 的 grep checklist 自动跑 · 5/08
  part 113 把 L-031/032/033 同步进去后，下次跑 reviewer 自动报这 3
  类 finding · 不再依赖主对话上下文记忆
- 反面案例：5/02 perf-audit 漏检 `read_capture` 是 sync IO，因为
  当时 L-014/L-025 模式没扩展到"async sync FS"维度。L-033 入档
  后 part 113 sync 到 performance-auditor + code-reviewer
  checklist，5/08 perf-audit 才在新 commit 里捕捉到

**反面教材** 2026-05-08：

- L-031 / L-032 / L-033 三 lesson 入 lessons.md（5/06~5/08 part 91/
  105/109）时**仅 L-031 同步到 code-reviewer checklist**；L-032/033
  靠主对话临时口头提醒。直到 5/08 part 113 才系统补完。中间 ~3 天
  跑 audit 没自动捕捉 a11y/sync-IO 类 finding 是潜在风险

**已知正例**：

- L-031 同 commit 写 lesson + 同步到 `.claude/agents/code-reviewer.md`
  ：grep pattern (`with contextlib\.suppress\(Exception\)` 5 行内
  await task/aclose) + 反面教材 (commit `7b9afc0` _pump_transfer
  finally) + 正例 (`terminal_route.py:166-168`) + agent
  checklist 段 "来自 L-031" 完整四件套
- 5/08 part 113 commit `e185b4a` 把 L-032/033 补完同款四件套

**触发条件**：写新 lesson 时检查是否属"新增类规则" — 标志是规则
描述含 "新 X 时必加 Y/Z" 形态。是 → 必须四件套；否（如 L-019
sentinel 反模式 / L-031 语言细节） → 仍鼓励但不强制 grep pattern
（因为 grep 形式可能不通用）。

**应用到 agents**：

- 写 lesson PR 同时改 `.claude/agents/<相关 reviewer>.md` 加 grep
  规则段 → 一次 commit ship + 一次 commit sync 是接受的，但**不
  能跨周**
- 主对话 review lesson 入档时 **必须问一句**："这条 grep pattern
  落到哪个 agent？" 漏问就是责任在主对话

**关联**：L-014 / L-025 / L-032 / L-033（这 4 条都是 meta-pattern
的实例 · 本条是它们的共性提取） · `agents/code-reviewer.md` 等 6 个
agent definition（grep checklist 段） · 5/08 part 113 commit
`e185b4a`（meta-pattern 落地实操）

---

## L-036 · REST 三态 envelope `_resolve_transport` 必走 tuple-return · 抛 HTTPException 违反约定

**Date**: 2026-05-21（5/18 batch architecture-reviewer 找到 HIGH#1 ·
5 路由 `_resolve_transport` 抛 503 违 REST 三态 ADR · 修复 commit
`4c718fc` · part 145）

**规则**：所有"按 device 解析 transport"的 endpoint helper，**必须**
返 `tuple[Transport, None] | tuple[None, envelope-b dict]`，**不能**
抛 `HTTPException(503)`。否则 device-side 失败（adb server down /
serial unreachable）会变成"全局 onError handler 接住"，前端不能
inline 渲染在原 panel 里。

```python
# ❌ 错（违反 REST envelope 三态 ADR）
def _resolve_transport(device: str | None) -> Transport:
    try:
        return build_transport(device=device)
    except Exception as e:
        raise HTTPException(503, str(e))  # 全局 fallback 接管

# ✅ 对（三态 b：200 + ok=false device-side error）
def _resolve_transport(
    device: str | None,
) -> tuple[Transport, None] | tuple[None, dict[str, Any]]:
    try:
        return build_transport(device=device), None
    except Exception as e:
        return None, envelope_transport_init_error(e)

# endpoint 改成
@router.post("/foo")
async def post_foo(...):
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    ...
```

**触发条件**：任何 `_resolve_*` / `build_*` helper, 接 device / 接
profile / 接 backend, 失败可能源自 device-side / config 端 / network。
全部走 tuple-return 模式。

**关联**：[[ADR · REST envelope 三态]] · `infra/result.py`
`envelope_transport_init_error()` helper · 5 路由 power/diag/app/log_search
统一过 · commit `4c718fc` (Phase 1 A)

---

## L-037 · 长操作（30s+）必加 elapsed timer + cancel · 静默 spinner ≠ 反馈

**Date**: 2026-05-21（5/18 batch ui-fluency-reviewer 找到 MID ·
bugreport / log search / install 等 30-180s op 只显示静态
"collecting…" · 修复 commit `aacf691` part H · 抽 useElapsedSeconds hook）

**规则**：任何**运行时 > 30 秒**的 op，UI 必须显示**实时 elapsed
计数**。仅显示"loading…" / "collecting…" 用户分不清"卡死"还是"还在
跑"，看 2 分钟没动反应等于事故。

```ts
// 通用 hook（web/src/lib/useElapsedSeconds.ts）
export function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!active) return;
    setElapsed(0);
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return elapsed;
}

// 消费方
const elapsed = useElapsedSeconds(mut.isPending);
<button>{mut.isPending ? `collecting… ${elapsed}s` : "collect"}</button>
```

**触发条件**：

- bugreport / fullsnap / large-pull / install / model-download
- 任何 transport.shell 带 timeout > 30s 的 mutation
- 任何 streaming op 但首字节到达延迟 > 5s

**关联**：[[L-029]]（HITL 三件套 — 涉及 destructive op）·
[[useArmedAction]] · [[useElapsedSeconds]] · commit `aacf691`

---

## L-038 · ship 后必跑多 agent audit · mockup 三道闸 ≠ React 实现审

**Date**: 2026-05-21（5/18 batch 8 commit 只跑 mockup-baseline-checker
三道闸 · 用户 5/21 用浏览器看出问题 · 跑 5 agent audit 出 8 HIGH /
15 MID / 6 LOW · 修复 Phase 1-4 11 commit）

**规则**：**任何"实现新 React 模块 / 新路由 / 新 endpoint"** 的 ship
**必须**触发 5 reviewer 并行 audit (code-reviewer / ui-fluency /
security / perf / architecture)。**mockup-baseline-checker** 只查
"class 名 / 容器结构是否照搬 mockup"，**不查 React 行为**（mutation
反馈正确性 / a11y 键盘 / 路由设计 / regex DoS / .find(truthy) bug 等
全部漏审）。

**反面教材**：5/18 ship 8 batch（doctor panel / QuickAction wire /
session detail / power / log search / diag / app / PR-C.c）只跑
mockup 三道闸全过 → 用户 5/21 浏览器一开发现 8 HIGH 全是 mockup
能过但实现错的类型（.find(truthy) 永远拿第一个 truthy / Inspect 12
tab 单文件 conditional / SubNav 不滚 / PackageList 不虚拟化 / ReDoS
无 timeout / a11y li onClick / armed 操作无 8s timeout / envelope
HTTPException 违三态约定）。

**触发条件**：

- ship 任何新 React 模块（route / page / tab / panel）
- ship 任何 REST endpoint
- ship 任何 mutation / 长操作 / 危险操作
- ship 后 batch ≥ 3 commit 就必须触发（单 commit 可省）

**审查者团队** 6 个 agent 全要跑（mockup-baseline-checker 仍是 visual
gate，但**不能替代**实现层审）:
- code-reviewer
- ui-fluency-auditor
- security-and-neutrality-auditor
- performance-auditor
- architecture-reviewer
- mockup-baseline-checker（第六个 · visual gate · 不替代上面 5 个）

**关联**：[[5-18-batch-audit-phase-1-4]] memory · agents/* 6 个
agent definition · L-meta-001（reviewer 新增类规则四件套）·
sky-skills design-review 三道闸（visual 盲区）

---

## L-039 · 用户输入 regex 必走 thread + asyncio.wait_for · 单 C-call 不能 mid-cancel

**Date**: 2026-05-21（5/18 batch security-reviewer 找到 HIGH#8 ·
LogSearchTab 接 `/api/log/search?pattern=...` 直传给 `re.compile` /
`re.search` · 恶意 ReDoS payload 例 `(a+)+$` × 长字符串 = CPU 飙满
事件循环 freeze · 修复 commit `139e117` part B）

**规则**：任何"接用户输入的正则 → server 端编译 / 搜索"必须满足两
条之一：

1. **(首选) 走 `asyncio.wait_for(asyncio.to_thread(...), timeout=N)`
   双保险**（外层 wait_for 截事件循环 / 内层 to_thread 防 sync re
   block）
2. 拒 `re` 切 `re2` / 引擎层支持时间预算

```python
# ❌ 错（同步 re 在 async endpoint · ReDoS 时无救）
async def search(pattern: str, lines: list[str]) -> list[Match]:
    rx = re.compile(pattern)  # 编译也可能 ReDoS
    return [m for line in lines if (m := rx.search(line))]

# ✅ 对（外层 wait_for + 内层 to_thread + per-line deadline）
_SEARCH_TIMEOUT_S = 2.0

async def search(pattern: str, ...) -> Result[...]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_scan_files_for_pattern, pattern, ...),
            timeout=_SEARCH_TIMEOUT_S + 0.5,  # 0.5s slack 给 thread teardown
        )
    except asyncio.TimeoutError:
        return fail(code="PATTERN_TIMEOUT", ...)
```

**已知 limitation**：Python `re` 单 C-call 不能 mid-cancel · 真 ReDoS
payload 会 leak 一个 thread 跑到自然完成（最坏几秒到几十秒）。
事件循环已经被 wait_for 解放回来 · 不影响其他请求 · 仅 thread pool
泄一格容量。可接受。

**关联**：[[L-033]]（async FastAPI sync FS → to_thread sweep · 同根）
· commit `139e117` · `capabilities/logging.py` `_scan_files_for_pattern`

---

## L-040 · bash tool cwd 在内部仓 · 文件操作必 cd 公开仓 / Edit 走绝对路径

**Date**: 2026-05-21（用户 4/29 给内部仓做真机测 · bash tool 启动
cwd 锁在内部仓 · Edit/Write/Read 用绝对路径仍走对的 · 但 bash 里
跑 `grep` / `cat` / `sed` / `npm` 走相对路径就读内部仓的 stale 副本 ·
画面与 Read tool 视图分裂 · 5/21 实战踩两次）

**规则**：内部仓 + 公开仓双副本场景下:

1. **Edit / Write / Read 一律走绝对路径**指向公开仓（`~/
   android-llm-bridge/...`），不依赖 cwd
2. **bash 跑命令时必须显式 `cd ~/android-llm-bridge && ...`**
   或用绝对路径 · 否则 `grep -rn ...` / `npm` / `pytest` 全在内部仓跑
3. **验证命令** （`./scripts/check_sensitive_words.sh` / `npm run
   typecheck` 等）必须 cd 进公开仓再跑

**反面教材**（5/21 实战 commit E）:
- Edit 改公开仓 router.tsx 成功（绝对路径）
- 紧跟 `grep -n InspectPage web/src/...` 在内部仓跑，read 到旧
  InspectPage 还在 → 误判 Edit 失败
- 一段对话内多次确认 cwd 后才理清

**触发条件**：

- 任何 vendor/PXX 内部仓 + 开源公开仓双副本工作流
- 任何 worktree / submodule / vendor copy 场景
- 任何 `~/...` 别名指向不固定时

**关联**：commit E `14279e4` ·
[[feedback_repo_collaboration_rules]] memory · `CLAUDE.md` 公开仓
+ 内部仓协作规则段

---

## L-041 · TanStack Router code-based `addChildren` 必须 inline 链式 · 拆 statement 类型丢

**Date**: 2026-05-21（commit E `14279e4` Inspect 12 tab 嵌套路由
refactor · 拆 `inspectRoute.addChildren([...])` 到单独 statement 后
TS 报 `"/inspect/$tabKey"` not in `to` union · inline chain 才修好）

**规则**：TanStack Router code-based routing 用 `addChildren` 注册
子路由时，**必须 inline 链式调用**在 `rootRoute.addChildren([...])`
里。**拆成单独的 statement runtime 仍正确，但类型推导丢 children**
（routeTree 的 `to` union 不包含子路径，所有 `<Link to="/parent/$child">`
TS 报错）。

```tsx
// ❌ 错（runtime OK · 类型丢）
inspectRoute.addChildren([
  inspectIndexRoute,
  ...inspectTabRoutes,
]);
const routeTree = rootRoute.addChildren([dashboardRoute, inspectRoute, ...]);
//                                                       ^^^^^^^^^^^^
//                                                       类型里 inspectRoute 无 children

// ✅ 对（inline · 类型链贯通）
const routeTree = rootRoute.addChildren([
  dashboardRoute,
  inspectRoute.addChildren([
    inspectIndexRoute,
    ...inspectTabRoutes,
    inspectTabFallbackRoute,
  ]),
  ...
]);
```

**触发条件**：

- 任何 TanStack Router code-based routing 的多级嵌套
- 任何 `<Route>.addChildren()` 用法
- 跨 file 抽 helper 时尤其要小心（helper 返回 `Route<...>` 没 children 类型）

**关联**：commit E `14279e4` · [[ADR-035 / ADR-036]] 路由层决策 ·
TanStack Router doc "Code-Based Routing"

---

## L-042 · stub page 不写 stub 占位 · 改写 redirect 到已有实现

**Date**: 2026-05-22（commit S `c829895` 收尾 Terminal/Files 两个 stub
入口 · activity bar 8 个全可用化）

**规则**：当 activity bar / 主导航顶层入口对应的内容**已有实现**只是
"在别处"（嵌套 tab / inspect sub-route / 历史路径）时，**禁止**用
stub page 写占位介绍, **改写成 redirect** 直接送用户到真实页。stub
page 只在"功能完全没实现且短期不会做"的边界场景留。

**Why**：

- 用户从 activity bar 点 "Terminal" 期待落到 terminal 功能, 不期待
  看 stub 解释 "Terminal 是什么", **打开 stub 是反 UX**
- redirect 一行代码 (`beforeLoad: () => throw redirect(...)`),
  比 stub 文案 + consumes 列表的成本低十倍
- stub 文案有衰减风险, 实现挪了一处 stub 文案没改 → 谎言 / 误导用户
  (commit S 实测: `<StubPage consumes=["GET /devices/.../fs"]>` 但
  server 是 `/workspace/files`, 误)
- 跨 N 个 stub 时, stub page 自己也有维护成本 (component import +
  consumes 列表 + i18n)

**How to apply**：

1. 任何 createRoute 想用 StubPage 时先问: "这功能现在在哪里能用?"
2. 若已实现在嵌套 / 别处: `beforeLoad: () => throw redirect({...})`
3. 若完全无实现: 先检查 server 是否已有 endpoint 等接 (audit 翻出来
   的 orphan endpoint 模式) · 有的话立刻接, 没的话才写 stub
4. 真要写 stub 也加 TODO + 关联 issue / DEBT 编号, 不写漂亮废话

```tsx
// ❌ 错（写占位 stub, 用户点了不能用功能）
const terminalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/terminal",
  component: () => (
    <StubPage
      title="Terminal"
      summary="Interactive adb / serial shell..."
      consumes={["WS /terminal/ws"]}
    />
  ),
});

// ✅ 对（redirect 到已实现的嵌套 tab）
const terminalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/terminal",
  beforeLoad: () => {
    throw redirect({
      to: "/inspect/$tabKey",
      params: { tabKey: "shell" },
      replace: true,
    });
  },
});
```

**触发条件**：

- 任何 stub page 上线超过 1 周
- 任何 activity bar / 顶层 nav 入口对应的内容已在子路由可用
- audit 发现 stub 文案与实际 endpoint / 实现脱节

**关联**：

- commit S `c829895` 实战
- [[L-038]]（ship 后多 agent audit · audit 模式发现 5 stub 都该收尾）
- audit_findings_5_18_batch memory · 5 stub MAJOR finding 全 close

---

## L-043 · 任何 @keyframes 必须同建 `prefers-reduced-motion` 兜底

**Date**: 2026-05-22（commit Z `822560e` 补齐 live-pulse + pg-cursor-blink
两个 keyframe 漏的 prefers-reduced-motion 段 · ui-fluency audit HIGH#1）

**规则**：新增任何 CSS `@keyframes` 时, **同 PR / 同文件内必加一段**
`@media (prefers-reduced-motion: reduce) { .target { animation: none }
}`。前庭功能障碍 / 癫痫敏感用户系统级开 "减少动效", 没兜底就被强制
看 1Hz strobe / 持续 pulse。

```css
/* ❌ 错（无兜底） */
.live-pulse {
  animation: live-pulse 1.6s ease-out infinite;
}
@keyframes live-pulse { ... }

/* ✅ 对（同段加兜底） */
.live-pulse {
  animation: live-pulse 1.6s ease-out infinite;
}
@keyframes live-pulse { ... }
@media (prefers-reduced-motion: reduce) {
  .live-pulse { animation: none; }
}
```

**触发条件**：任何新 `@keyframes` 名出现; 任何 `animation: name ...
infinite` 出现。

**grep checklist** (reviewer 自动加进 ui-fluency-auditor agent):
- 每个 PR `git diff | grep "^+.*@keyframes"` 拿到所有新 keyframe 名
- 每个名字 `grep -A6 "$NAME" file.css | grep -q "prefers-reduced-motion"`
- 0 命中 → finding。

**反面教材记录**（同款规则已 3 次复发）:
- 5/06 live-pulse 漏 (Dashboard hero card 初版)
- 5/07 alb-armed-pulse 漏 → commit D 补
- 5/22 pg-cursor-blink + live-pulse 又漏 (Playground 上线时 cursor /
  live-pulse 两个一起跌进同坑) → commit Z 补

**关联**: L-029 destructive op a11y 三件套 · ADR-039 危险/长操作通用
hook 模式 · commit Z `822560e`

---

## L-044 · 长操作 / 长任务 UI 用 readOnly 不用 disabled · disabled 吞 focus 键盘锁死

**Date**: 2026-05-22（commit Z `822560e` Playground textarea 修 · ui-fluency
audit HIGH#5）

**规则**：长操作 (streaming chat / 长上传 / 长查询) 期间需要"防止用户
改输入" 时**禁止**用 `disabled={true}`, 改 `readOnly={true}` (+
`aria-readonly`)。原因: `disabled` 立即把 focus 移到 body, 用户按 ESC
不进 textarea 的 onKeyDown, 键盘用户无法 cancel 长操作。

```tsx
// ❌ 错（disabled 吞焦点 · 键盘用户无法 ESC cancel）
<textarea
  disabled={streaming}
  onKeyDown={(e) => {
    if (e.key === "Escape") cancelLongOp();  // 永不触发
  }}
/>

// ✅ 对（readOnly 保焦点 · ESC 进 onKeyDown · 同步 aria-readonly 给 AT）
<textarea
  readOnly={streaming}
  aria-readonly={streaming}
  onKeyDown={(e) => {
    if (e.key === "Escape" && streaming) {
      e.preventDefault();
      cancelLongOp();
    }
  }}
  placeholder={streaming ? "生成中 · Esc 取消" : "Message…"}
/>
```

**触发条件**：

- streaming chat / long-running mutation 期间需要"防改输入"
- 任何 `disabled={isPending}` 在表单输入元素上
- 配合 ESC 键 cancel 操作的场景

**关联**: L-029 destructive op a11y 三件套 · commit Z `822560e` ·
L-037 长操作 elapsed timer + cancel

---

## L-045 · 注释声称"X 共享 / dedup"必须用 grep 验证, 否则就是事实错误

**Date**: 2026-05-22（commit Y `bf9d1dc` 订正 AuditPage 撒谎注释 · 跨
agent 共识 code-r MID#3 + perf HIGH#1）

**规则**：任何代码注释或 docstring 声称"X 共享 / dedup / cache / 复用
N 个消费者"必须先 grep 验证。N 个消费者的 import 路径里都能找到同一
个符号才算共享; 同一个**调用名**不代表共享 — `connect(path)` 在 lib/
ws.ts 里每次都 `new WebSocket(url)`, 这是事实, 注释说"by path dedup"
是事实错误。

```tsx
// ❌ 错（注释撒谎）
/**
 * Why reuse useAuditStream rather than fetching /audit on a timer:
 *   - Dashboard already pays the WS cost, second consumer on the same
 *     hook is essentially free (lib/ws.ts dedupes connect by path)
 *                                  ^^^^^^^^ 实际并没 dedup
 */

// ✅ 对（注释准确)
/**
 * Caveat — NOT free yet: lib/ws.ts does NOT dedup `connect()` by path
 * today, so opening AuditPage while Dashboard is mounted creates a
 * second socket. Tracking in DEBT-047.
 */
```

**触发条件**：

- 写 / 改任何 "shared" / "deduped" / "cached" / "reused" 性质的注释
- review 时 grep 验证：claim 涉及 `connect(`, 实际有几次 `new WebSocket(`?
- reviewer agent 需建 grep checklist: 注释里包含 dedup/cache/share
  关键词 → 自动跑反向调用计数

**关联**: L-035 path-traversal 实测验证文化 · commit Y `bf9d1dc` ·
DEBT-047 (该 dedup 真做的工作)

---

（新教训按此格式追加）

## L-046 · WS 流式 hook 必须在 close-before-done 路径同时构造 synthetic done payload

**症状**: 用户在 PlaygroundPage 发 prompt · token 正在流 · 后端 503
/ WS 被中间代理断 · 流式光标 ▍ 消失 · 用户看到截断的 assistant 气泡 +
idle 输入框 · 0 反馈说"连接断了"。

**根因**: hook 在 close-before-done 路径只 `setStatus("error")` · 不
塞 `done` payload · 而 PlaygroundPage 的错误 UI 渲染条件是
`status === "error" && done`（line 267）· `done` 为 null 时整个 error
block 不渲染。

**Fix pattern** (commit AH-3 `8f5d44a` · useWsChatStream):
```ts
onCloseBeforeDone: (info) => {
  setDone({
    ok: false,
    content: "",
    finish_reason: "disconnected",
    model: "", backend: "",
    error: { code: "WS_DISCONNECTED", message: info.reason || ... },
  });
}
```
hook 内置 fallback 模板的 **必填项**：consumer 渲染逻辑常用
`status && payload` 双条件 · 单 setStatus 一定 silent-freeze。

**触发条件**：任何 WS 流式协议 hook · 任何 consumer 的错误 UI 渲染
读 hook 返回的 payload 字段 → 必须 audit 这条 race。

**关联**: AH-3 commit · DEBT-047 (将来 ws dedup 时 pool 复用注意保留
synthetic done 语义) · ui-f HIGH-1 audit finding

---

## L-047 · 流式 chat / log UI 必须实现 stick-to-bottom 双模式 (跟随尾部 + 用户上滚脱离)

**症状**: 长回复（500+ token） · log 容器 `overflow-y:auto` · 但无
scroll-follow → 流的内容把视口推到顶 · 用户看不到新 token。

**Fix pattern** (commit AI-8 `02d6076` · PlaygroundPage):
- `useRef<HTMLDivElement>(logRef)` + `useState<stickToBottom>(true)`
- `useEffect`: stick 为真时 `el.scrollTop = el.scrollHeight` (每 render)
- `onScroll`: 检测 `scrollHeight - clientHeight - scrollTop <= 40px`
  → 设回 stick (用户滚到底自动恢复) · `> 40px` → 关 stick
- 发送 / clear 强制 setStick(true) 重新锚到底
- 显示 floating "回到最新 ↓" 按钮 when !stick && (log 非空 || streaming)

**触发条件**: 下一个 chat-like / streaming log / agent trace / repl
组件出现时 (Anthropic SSE bridge / Gemma 4 on-device chat / etc.) ·
**单一容器 overflow:auto + 流式 setState 不会自动滚** · 必须显式管。

**关联**: AI-8 commit · ui-f MID-2 audit · DEBT-046 (Playground
metrics rail) 不影响本不变量

---

## L-048 · 提层 hook 必须同步剥离 view-model 反向依赖 · 不留 "future split" TODO

**症状**: 把 hook 从 `features/dashboard/` 提到 `lib/hooks/` 关 layering
audit · 但 hook 内部仍 `import features/dashboard/types` +
`mapAuditToTimeline` · 注释里写 "future split: keep raw-only" 表示
"明天的我会做" · 实际下次 audit 再次抓 · "提层只提了一半"。

**根因**: 把"被多处 import"问题转成"低层 import 高层"问题 · 分层规则
破得更深。Future TODO 是 self-defeating · 当前 reviewer 看见有
comment 以为问题在管 · 真问题更隐蔽。

**Fix pattern** (commit AH-2 `60c031f`):
- raw hook 只返服务端原 shape (ApiDevice[] / AuditEvent[])
- 每个 feature 自己 wrapper hook 做 view projection
- 共享 utility (Transport union / status mapper) 提到 `lib/<name>.ts`
  · types.ts re-export 给 dashboard 继续用

**触发条件**: 任何 layering audit finding "X 被多处用 · 提到 lib"
· 检 X 内部有无 import features/ → 同步剥离 · 不留 future split

**关联**: AH-2 commit · arch HIGH-4 + code MID-1 audit · 之前 AA
commit (5/22) 是"提一半"的反例

---

## L-049 · 配置文件类型 trade-off 实测优先 · 不能"听上去 narrows"就误诊

**症状**: 5/25 arch audit 怀疑 "vitest 双 config 是过度防御 · 应可
单 config" · AH-5 实测后两条都是真错 (vite 的 defineConfig 无 test
字段 / vitest 的 defineConfig narrows manualChunks record form) ·
arch agent 的"误诊"说法本身是误诊。

**根因**: 类型限制 / 兼容性问题不能纯靠"看注释" / "凭印象"判 · 工具
版本演进快 (vite 5 + vitest 4 + rollup 4 各自的 narrow 策略不同) ·
唯一可信的方式是**实测合并 · 看错出在哪**。

**Fix pattern** (commit AH-5 `7c96fae`):
- 试合并 vite + vitest config · 跑 `tsc -b && vite build`
- 实测两条错都是真 · 注释改写真因 (而非"听上去")
- 写 ADR-040 永久留档 + 维护契约段 (plugins / alias 同步两边)

**触发条件**: 任何 "X 配置应该可以合并" / "Y 类型限制听上去可以
绕开" 的判断 · 必须先合并跑 build 实测 · 0 错才能下"误诊"结论 ·
有错就把真因写注释 + ADR

**关联**: AH-5 commit · ADR-040 · L-009 (代码事实禁止 hedge) ·
arch audit MID-5

---

## L-050 · diag/file-scan TOCTOU 防御必须 full coverage · not pointwise

**症状**: AC commit 给 `p.stat()` 包了 try/except OSError · 但
`p.iterdir()` / `p.is_symlink()` / `p.is_file()` / `p.is_dir()` 都
仍裸抛 · TOCTOU race 还在 · 修了 1/5 的 case。

**根因**: 文件系统 race window 在**每一个 stat call** 之间都打开 ·
单独包某一个无意义 · 调用方法签名一变 ATTACK SURFACE 不变。

**Fix pattern** (commit AI-6 `5d4877e` · diag_route):
- 抽 `_safe_iterdir(p) -> list[Path]` · OSError → []
- 抽 `_safe_entry(p, want="file"|"dir") -> bool` · symlink reject +
  is_* + OSError → False
- **所有 file-system 探测都走 helper** · 不允许裸 iterdir / is_* 出现

**触发条件**: 任何 endpoint 跑 file-system scan / dir walk · 任何
sync function 在 async event loop 里 · 任何 OSError 没 catch 直接
冒到 ASGI handler · 必须 full coverage 防 race

**关联**: AI-6 commit · code HIGH-2 audit · ADR-027 (workspace path
boundary) · L-035 (path-traversal 实测验证文化)

---

（新教训按此格式追加）

## L-051 · 安全修复必须扫 sibling endpoints 一并修

**症状**: AI-6 commit 修了 `diag_route.py` 两处 `detail=f"invalid device
serial: {device!r}"` 改成无回显 · 加了 test_diag_route.py regression
spec · 但 5/25 第二轮 audit 发现 4 个 sibling endpoints (power /
uart / app / log_search) 同款 detail 回显**全没改** · screenshots /
sessions / files 也漏。修了 1/7 · 给后续 reviewer 假阳性"安全防护
已就位"信号。

**根因**: 安全修复是"按 finding 改" · 没"按 helper 扫一遍 sibling
callsite"。`is_safe_device` / `is_safe_session_id` / `_resolve_workspace
_path` 这种安全 helper 的所有 caller 应该 hold 同款 detail-不-回显
契约 · grep 全仓 + 统一修才完整。

**Fix pattern** (commit AK-2):
1. grep 所有 `detail=f".*{<user-input>!r}"` 或 `detail=f".*{<user-
   input>}"` pattern 找全 callsite
2. 全部改 `detail="<reason>"` 不回显原值 · 加注释引用 L-051
3. 加 `tests/api/test_sec_no_echo.py` cross-route parametrize spec ·
   一处覆盖所有 reject 路径 · 同款 sneaky `<script>` payload 验
   detail 不含原值
4. 例外：404 / not-found 路径回显 client 提供的 ID 是 OK 的（client
   原本知道这个值 · 不是新信息）· 在代码注释里说清楚

**禁止的写法**:
- ❌ "我修的是 finding 里点名的那 1 处" → 漏 sibling
- ❌ "test 只覆盖修过的路由" → reviewer 看到 test 以为全防护
- ❌ "detail 字符串回显客户端原 query 没风险" → 任何 future consumer
  用 dangerouslySetInnerHTML 就成 reflected XSS

**触发条件**:
- 任何安全 finding 涉及 detail / error message 回显
- 任何 reject helper (is_safe_*) 调用方扫一遍
- 任何 audit finding 标 "类问题" 时（不是单点 bug）

**关联**:
- AK-2 commit · sec MID-1 一致性修
- L-035 (path-traversal 根因层 reject)
- L-050 (TOCTOU full coverage not pointwise) · 同款"全覆盖 not 局部"思想
- agent grep checklist 建议加：security-and-neutrality-auditor 在 diff
  命中 `detail=f"...{<param>[!r]?}"` 时 · 全仓 grep 同 helper · 任一
  漏修即 MID

---

（新教训按此格式追加）

## L-052 · thin wrapper hook 是规则压力的副产物 · N=1 时该撤

**症状**: AH-2 commit 抽 `useDeviceCards` / `useAuditTimeline` 两个
thin wrapper hook (每个 10-12 行有效代码) 来消"lib/hooks/ 不准
import features/" 的反向依赖规则。N=1 consumer 实际只服务
DashboardPage 1 处 · 反过来引入：render boundary 多一层 · API
surface 多一层 · 测试 mock 多一层 · 0 复用收益。下一轮 arch
review (5/25 第二轮) 抓 "wrapper 是为规则的规则"。

**根因**：ADR / lint 把"raw hook 必须配 wrapper"当硬规则·而没区分
N=1 / N≥2 consumer。规则在低 N 时变成 over-design 强制。

**Fix pattern** (commit AL-2 / ADR-043):
- N=1 consumer：raw hook + 纯 mapping 函数（放 `features/<x>/
  mappers.ts`）+ consumer 内 `useMemo`，**不抽 wrapper hook**
- N=2 consumer：抽 wrapper 到首消费 feature 的 use<Y>.ts，第二
  消费者 sibling import
- N ≥ 3 consumer：升 wrapper 到 lib/hooks/<X>.ts

**禁止的写法**:
- ❌ "提层规则要求 wrapper → 我建一个 10 行 wrapper"
- ❌ thin wrapper 只是 `useMemo(map)` 一行包装
- ❌ wrapper hook 0 spec 因为"它太 thin 不需要测"——证明该撤

**触发条件**:
- 任何"raw hook 提层 / 反向依赖修复"工作 · 评估每个 wrapper 的
  consumer count
- arch / code reviewer 看到 thin wrapper hook (useMemo + map 一行)
  无独立 spec · 该 flag MID
- 新加 `features/<x>/use<Y>.ts` 文件只是 useMemo + map → 撤回 inline

**关联**:
- AL-2 commit · arch HIGH-2 fix
- ADR-043 (wrapper hook 抽取临界)
- L-048 (提层提一半 = 反向依赖 trap · 正确方向)
- L-020 (N=2 不抽象 / N=3 抽 · 这是 hook 维度延伸)

---

（新教训按此格式追加）
