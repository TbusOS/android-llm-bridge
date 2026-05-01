# 反面教材与经验

每条：曾经怎样、踩什么坑、现在为什么这样。让 agents 评审时不光看
"是否符合规则"，还知道"规则为什么这么定"。

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

## L-004 · 公开仓 commit message 中文（PAX 规则不适用）

**坑**：2026-04-24 用户明确要求公开仓（`TbusOS/android-llm-bridge`）的
commit message 用中文叙述，技术关键词保留英文。

**规则**：
- commit 标题：中文 + 技术关键词英文（如 `feat(api): GET /audit ——
  Dashboard 真实数据后端 step 3`）
- commit body：中文为主，必要时穿插英文术语
- 不带 `Co-Authored-By: Claude` 署名（全局禁用）
- 不带 `Co-authored-by: zhangbh@paxsz.com`（PAX 内网仓规则，不适用本
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
要 grep 任何 PAX / RK / 内网 IP 痕迹。

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
脱敏过但实际放过 7 处敏感词（Rockchip / RK3576 / RKDevTool /
upgrade_tool / `/home/zhangbh/...` / COM27）。issue 已发公网，删了重发
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

**坑**：2026-04-22 Task 15 defconfig 分析，第一轮回复"嫌疑这 5 个 PAX
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
cmd #ifdef 保护就下结论"不是必须"。后来发现没查运行时路径 / PAX 三级
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
保留上游原码 + 加下游宏 gate（如 `CONFIG_PAX_<purpose>`）卡一下，而
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
`@mcp.tool()` 函数加首行 docstring 写了 `RK3576` / `paxsz` / 内部 IP /
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

（新教训按此格式追加）
