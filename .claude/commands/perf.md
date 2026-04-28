---
description: performance-auditor 单跑 —— 找性能瓶颈 + 优化建议（含成本估算）
argument-hint: [范围：commit / 模块 / 全局]
---

调 performance-auditor agent 做性能层评审。

## 步骤

1. **确认评审范围**：
   - 如果有 `$ARGUMENTS`，用作目标
   - 否则问用户："评审 (a) 当前 diff (b) 最近 commit (c) 全局现状 (d) 某文件"

2. **预检 build 产物**（不要主动 build）：
   - 用 `Bash` 跑 `ls -la docs/app/assets/`
   - 如果没 `index-*.js` → 告诉用户"docs/app/ 没 build 产物，建议先
     `cd web && npm run build` 再调 /perf"
   - 如果有 → 记录当前 bundle 大小作为基线

3. **spawn performance-auditor**：

   ```
   subagent_type: performance-auditor
   prompt: |
     评审范围：<...>
     当前 bundle 基线：<基线数据>
     上下文：<commit / 改动描述>

     按你 prompt 里的 6 维：
     - bundle / render / 后端运行时 / 内存 / 网络 / 长任务

     **不主动跑 build / pytest --benchmark**（避免污染产品）。
     报告写到 `.claude/reports/perf-<timestamp>.md`，并把摘要 +
     关键发现输出到对话。
   ```

4. **agent 返回后**：
   - 报告路径在 `.claude/reports/perf-*.md`
   - 把摘要 + ≤ 6 条主要发现呈现给用户
   - 如果 high 条建议成本可控 → 询问是否采纳

5. **更新 knowledge**（如果产生新发现）：
   - 新性能债 → `.claude/knowledge/debts.md`
   - "为什么不优化 X" 的决策 → `.claude/knowledge/decisions.md`

## 不要做

- 不主动跑 `npm run build` / `vite build` / `pytest --benchmark`
  （会改 `docs/app/`，污染产品代码）
- 不直接改代码
- 不裁剪 agent 输出
