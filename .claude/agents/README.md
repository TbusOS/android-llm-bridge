# android-llm-bridge · agents 团队说明书

7 个项目专属 agents 团队，保证开发质量、界面流畅性、性能、架构合理性。
任何 clone 这个仓库的环境都能直接启动这套团队（agents / commands /
knowledge 都在仓库里）。

## 团队成员

| Agent | 主线 | 触发时机 | 工具 | 写权限 |
|---|---|---|---|---|
| **code-reviewer** | 代码层（bug / 资源 / 并发 / 错误处理 / 测试覆盖） | 每个非 trivial commit 前 | Read, Grep, Bash | 无 |
| **architecture-reviewer** | 架构层（边界 / 依赖 / 债 / 扩展性 / API 一致性） | 设计 sketch / 重构提案 / 新模块前 | Read, Grep, Bash, WebFetch | 无 |
| **performance-auditor** | 性能层（bundle / render / 内存 / 网络 / 长任务） | bundle 涨明显 / WS 频次改 / 大循环改 / 端点新增 | Read, Grep, Bash, Write | `.claude/reports/perf-<ts>.md` |
| **ui-fluency-auditor** | 体验层（交互 / 动画 / CLS / 三态 / a11y / 键盘） | UI 改动 commit 前 | Read, Grep, Bash, Write | `.claude/reports/ui-fluency-<ts>.md` + `screenshots/<ts>/` |
| **mockup-baseline-checker** | UI 基线（React vs `docs/webui-preview-v*.html` 一致性） | UI 改动 commit 前（先于流畅性 / 视觉） | Read, Grep | 无 |
| **visual-audit-runner** | 视觉验证（sky-skills 三道闸 + Playwright 截图） | mockup / React UI 改完 | Read, Bash, Write | `.claude/reports/visual-<ts>.md` + `screenshots/<ts>/` |
| **security-and-neutrality-auditor** | 合规（敏感词 / OWASP / XSS / dangerouslySetInnerHTML / 凭证） | 涉及外发 / 凭证 / inject HTML 的改动 | Read, Grep, Bash | 无 |

## 团队铁律（所有 agents prompt 都引用）

**永不修改的路径**：
- `src/**`、`web/**`、`docs/**`（产品代码 / 文档）
- `.claude/agents/**`、`.claude/commands/**`（团队定义）
- `.claude/knowledge/**`（团队记忆 — 只主对话能更新）
- 任何 `.py / .ts / .tsx / .css / .md`（除 `.claude/reports/` 子树）

**唯一可写的路径**（仅 perf / ui-fluency / visual 三个 agent 有 Write 工具）：
- `.claude/reports/<agent-name>-<timestamp>.md`
- `.claude/reports/screenshots/<timestamp>/<filename>.png`

**写文件命名硬规则**：路径里**必须**包含 `<agent-name>` + `<timestamp>`，
保证两个 agent 同时跑也绝不撞文件名。

**需要改产品代码 / knowledge / 团队定义的情况**：
**不要写**。在评审报告里明确写 `建议改 file:line: <一句话>`，主对话审定后落地。

## 并行 / 串行规则

### 默认全部可并行

5/7 agents 完全只读、2/7 写各自独立子树（文件名带 timestamp + agent name），
所以**任意 agents 同时跑都不会写冲突**。

### 但 commands 按业务依赖决定串/并行

| Command | 内部组织 | 理由 |
|---|---|---|
| `/review` | code-reviewer + security-and-neutrality-auditor **并行** | 只读独立视角，并行省时 |
| `/arch` | architecture-reviewer 单跑 | 单 agent |
| `/perf` | performance-auditor 单跑 | 单 agent |
| `/security` | security-and-neutrality-auditor 深扫单跑 | 比 /review 更彻底 |
| `/ui-check` | mockup-baseline-checker → ui-fluency-auditor → visual-audit-runner **串行** | 业务依赖：基线偏离了不必跑流畅性；流畅性挂了不必跑视觉三闸 |
| `/preflight` | 7 agents **并行启动** + 主对话最后汇总 | ship 前总检查，最后写一份 summary |

## 主对话与 agents 的协议

1. spawn agents 前**确保 `.claude/knowledge/` 不在写入中**
2. agents 一次性读完 knowledge → 评审过程中不再重读
3. 所有 agents 返回后，主对话评估反馈：
   - 采纳 → 改代码
   - 驳回（设计意图）→ 写到 `knowledge/review-feedback.md`（agents 下次会看到）
   - 驳回（误报）→ 同上 + 调整 agent prompt 的盲区
4. 写入 `knowledge/` 期间不能新 spawn agents（避免读到半写状态）
5. **agents 不写 knowledge**，只主对话能写（用户监督下，避免 hallucination 污染）

## 输出格式（所有 agents 共享）

每个 reviewer agent 输出统一格式：

```
# <agent-name> 评审 · <subject>

## 摘要
（1-3 句：评审对象 / 主要发现）

## 发现（≤ N 条，N = agent 各自 prompt 规定）
1. **[severity]** `file:line` — <一句话问题>
   原因：<具体根因>
   建议：<具体修复，引用 file:line>

2. ...

## 是否建议重构（仅 architecture-reviewer 必填，其他可选）
- 是/否
- 理由：<...>
- 重构 sketch：<...>
```

severity 取值：`high`（必须修）/ `mid`（建议修）/ `low`（可选）。

## 知识库（agents 必读，主对话写）

`.claude/knowledge/`：
- `architecture.md` — 当前架构快照
- `decisions.md` — 重大决策（ADR 风格）+ trade-off
- `debts.md` — 已知技术债（severity / 引入时间 / 是否计划修）
- `lessons.md` — 反面教材：曾经那样、踩什么坑、现在为什么这样
- `review-feedback.md` — 历次评审：哪些建议被采纳 / 驳回 / 为什么

每个 agent prompt 内嵌"先读 knowledge"指令 + "质疑能力"指令（不只是
"是否符合现有规则"，还要质疑"规则本身合理吗"）。

## 报告归档策略

`.claude/reports/`：
- 每类报告保留最近 10 份
- 老报告归档到 `archive/<year>/<agent>-<ts>.md`
- `screenshots/` 子树**不入仓**（gitignore），仅本地参考

## 触发矩阵速查

| 我刚改了什么 | 该跑哪个 command |
|---|---|
| 后端 Python 代码 | `/review` |
| 前端 React UI（class 改了） | `/ui-check` |
| 前端纯 wiring（hook / fetch） | `/review` |
| 设计 sketch / 重构提案 | `/arch` |
| 性能敏感改动（WS / loop / bundle） | `/perf` + `/review` |
| 涉及凭证 / 外发 / dangerouslySetInnerHTML | `/security` |
| 准备 ship 一个里程碑 | `/preflight` |
