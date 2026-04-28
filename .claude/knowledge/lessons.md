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

（新教训按此格式追加）
