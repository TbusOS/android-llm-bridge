---
description: architecture-reviewer 单跑 —— 评审设计 sketch / 重构提案 / 新模块的架构合理性
argument-hint: [评审对象描述：sketch 路径 / commit / 模块名]
---

调 architecture-reviewer agent 做架构层评审。

## 步骤

1. **确认评审对象**：
   - 如果有 `$ARGUMENTS`，用作评审目标
   - 否则问用户："要评审什么？(a) 当前未提交的 diff (b) 最近一次 commit (c) 一段贴上来的 sketch (d) 某个文件路径"

2. **spawn architecture-reviewer**：

   ```
   subagent_type: architecture-reviewer
   prompt: |
     评审对象：<...>
     上下文：<把 sketch 文本 / commit message / 文件路径告诉它>

     按你 prompt 里的流程：
     - 先读 .claude/knowledge/ 全部 5 份
     - 评审 5 维（边界 / 依赖 / 债 / 扩展性 / API 一致性）
     - 必须质疑现有架构是否仍合理
     - 给出"是否建议重构"明确结论
     - 列出建议加入 knowledge 的草案条目
   ```

3. **呈现 agent 报告全文给用户**

4. **询问用户**：
   - 哪些建议采纳？哪些驳回？
   - 如果建议重构：是否走重构路线？
   - 是否把"建议加入 knowledge" 草案落地到 `.claude/knowledge/` 文件？

5. **落地知识更新**（用户同意后）：
   - 更新 `.claude/knowledge/decisions.md` / `debts.md` / `lessons.md`
   - 把驳回的建议写到 `.claude/knowledge/review-feedback.md`

## 不要做

- 不直接改产品代码
- 不替用户拍板"是否重构"（重构是高成本决策）
- 不跳过 knowledge 更新（每次 /arch 都要看是否产生新 ADR / 新债）
