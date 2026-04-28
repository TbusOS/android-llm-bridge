---
description: 并行跑 code-reviewer + security-and-neutrality-auditor 评审当前 diff（或指定范围）
argument-hint: [diff-range，默认 HEAD~1]
---

并行跑两个独立 agents 评审当前 diff，把两份独立报告整合后呈现给用户决策。

## 步骤

1. **准备 diff 范围**：
   - 如果有 `$ARGUMENTS`，用作 diff range
   - 否则默认 `HEAD~1`（最近一次 commit）
   - 跑 `git diff <range> --stat` 先看影响面，告诉用户"将评审 N 个文件"

2. **并行 spawn 两个 agents**（**一条消息里同时发两个 Agent tool call**）：

   - `subagent_type: code-reviewer`
     prompt: |
     评审 git diff <range>。范围：<file 列表>。
     完整 sketch / commit message：<贴上下文，让 agent 知道改动意图>
     按你 prompt 里的 5 维评审 + 必读 knowledge 流程跑。
     输出 ≤ 8 条具体发现 + 历史视角 + 不在范围说明。

   - `subagent_type: security-and-neutrality-auditor`
     prompt: |
     评审 git diff <range>。范围：<file 列表>。
     按你 prompt 里的 5 维（中立 / OWASP / 凭证 / 输入校验 / 文件 IO）跑。
     先跑 `./scripts/check_sensitive_words.sh --all` 和
     `./scripts/check_offline_purity.sh`。
     输出 ≤ 8 条 + 是否阻塞 ship 结论。

3. **等两个 agent 都返回**（不要边跑边汇总）

4. **整合呈现**：
   ```
   # /review 报告 · diff <range>

   ## code-reviewer
   <agent 输出摘要 + high/mid 数量 + 关键 1-2 条>

   ## security-and-neutrality-auditor
   <agent 输出摘要 + 是否阻塞 + high 必修条目>

   ## 综合建议
   - 必修（high 合集）：<...>
   - 建议修（mid 合集）：<...>
   - 是否阻塞 commit：<是 / 否>
   ```

5. **询问用户**：是否采纳建议、哪些采纳哪些驳回。

6. **如果用户驳回某条**：在主对话写到 `.claude/knowledge/review-feedback.md`
   一条新条目（格式见 knowledge/review-feedback.md README）。

## 不要做

- 不直接改产品代码（agent 只输出建议；改代码由用户授权后主对话来）
- 不串行跑（这两个 agent 完全独立，必须并行省时）
- 不裁剪 agent 输出（让用户看完整报告）
