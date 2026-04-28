---
description: ship 前总检查 —— 所有 7 agents 并行 + 三闸（pytest / sensitive-words / offline-purity）+ 主对话最后汇总
argument-hint: [范围：默认 HEAD~1，或 commit range / "current" 当前未提交]
---

ship 前最终质量检查：7 个 agents 并行启动 + 三道脚本闸 + 主对话最后写
一份"ship 决策报告"。

## 何时用

- 准备 push 一个里程碑（一组 commits）
- merge 一个 feature branch 到 main
- 发布一个 release（GitHub Pages / npm / pyproject 版本号 bump）
- 外部审视前的"最后一次自检"

## 步骤

### 1. 确认范围

- `$ARGUMENTS` 为 diff range，默认 `HEAD~1`
- 跑 `git diff <range> --stat` 告诉用户"将评审 N 文件 / N 行"
- 如果范围太大（> 50 文件）→ 警告用户"范围大，每个 agent 评审会聚焦
  关键改动而非细节"

### 2. 跑三道脚本闸（先于 agents，因为这些是机器闸）

并行跑（**用 Bash 在同一条消息发 3 个 tool call**）：

```bash
./scripts/check_sensitive_words.sh --all
./scripts/check_offline_purity.sh
uv run pytest -q --no-cov
```

记录每个的 exit code + 关键输出。任一失败 → 报告"机器闸未过"，但
**不停下** —— 继续跑 agents（agent 报告也是有用信息，且 agents 不依赖
机器闸过）。

### 3. 并行 spawn 全部 7 agents（一条消息内全部发出去）

每个 agent 的 prompt 模板见 `.claude/commands/<对应 command>.md`。
本 command 把 7 个 agent 的"独立调度"整合成一次性发起：

```
agents 同时跑（一条消息含 7 个 Agent tool call）：

1. code-reviewer       — diff 范围 + 5 维
2. architecture-reviewer — diff 范围 + 6 维 + 重构建议
3. performance-auditor   — bundle 基线 + 6 维（不主动 build）
4. ui-fluency-auditor    — 改动涉及 React 时跑（如未改 React 则跳过）
5. mockup-baseline-checker — 同上（仅 React 改动时）
6. visual-audit-runner   — 改动涉及 mockup / React build 时跑
7. security-and-neutrality-auditor — 深扫
```

如果改动**完全不涉及前端**（只改 src/、tests/、docs/methods/）→ 跳过
4 / 5 / 6（节省 spawn 成本），明确报告"前端无改动，跳过 UI 三档 agent"。

### 4. 等所有 agents 返回（不汇总边到边）

7 个独立 context，最长那个返回前不输出。

### 5. 主对话汇总（preflight 报告）

```
# /preflight 报告 · diff <range>

## 机器闸（3 道）
- check_sensitive_words.sh: <pass/fail + 命中>
- check_offline_purity.sh:  <pass/fail + 命中>
- pytest -q --no-cov:       <pass/fail + N tests>

## agents 评审（7 个）

### code-reviewer
<摘要 + high/mid 数量 + 关键 1-2 条>

### architecture-reviewer
<摘要 + 是否建议重构 + 关键 1-2 条>

### performance-auditor
<摘要 + bundle 现状 + high 1-2 条>

### ui-fluency-auditor (前端有改动时)
<摘要 + 三态完整性 + 截图路径>

### mockup-baseline-checker (前端有改动时)
<是否通过基线 + 偏离 1-2 条>

### visual-audit-runner (mockup/React 有改动时)
<三道闸结果 + 截图路径>

### security-and-neutrality-auditor
<是否阻塞 ship + 必修条目>

## 综合 ship 决策

- **必修 high 合集**：<...>
- **建议修 mid 合集**：<...>
- **机器闸状态**：<...>
- **是否 ship-ready**：是 / 否
- **如果不 ship-ready**：列出具体阻塞条目 + 修法

## knowledge 更新建议
- `.claude/knowledge/architecture.md` 是否需要更新：<是 / 否，理由>
- `.claude/knowledge/decisions.md`：<新 ADR 草案>
- `.claude/knowledge/debts.md`：<新债条目>
- `.claude/knowledge/lessons.md`：<新教训>
- `.claude/knowledge/review-feedback.md`：<驳回条目自动写入>

## 报告归档
- 本次产生的 agent 报告：
  - .claude/reports/perf-<ts>.md
  - .claude/reports/ui-fluency-<ts>.md
  - .claude/reports/visual-<ts>.md
- 老报告归档（如某类已超 10 份）→ 自动 mv 到 `.claude/reports/archive/<year>/`
```

### 6. 询问用户决策

呈现完报告后明确问：
- 是否 ship？
- 如果不 ship，先修哪些？
- knowledge 更新哪些落地？

### 7. 报告归档

- 本次产生的 `.claude/reports/<agent>-<ts>.md` 留在 reports/
- 检查每类是否已超 10 份，超的话主对话 mv 到 `archive/<year>/`

## 性能预期

- 7 agents 并行跑 ~ 60-180 秒（最长那个决定）
- 3 道机器闸 ~ 15 秒
- 主对话汇总 ~ 30 秒
- **总耗时 ~ 2-4 分钟**

## 不要做

- 不串行跑 agents（必须并行省时）
- 不跳过机器闸即便很快（机器闸是地基）
- 不让任何 agent 改产品代码 / knowledge
- 不替用户拍板"是否 ship"（高决策权限不归 AI）
- 不裁剪关键发现（high 必须全部呈现）
