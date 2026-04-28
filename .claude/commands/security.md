---
description: security-and-neutrality-auditor 单跑（深扫，比 /review 内的同名 agent 更彻底）
argument-hint: [范围：commit / 模块 / 全局]
---

调 security-and-neutrality-auditor agent 做合规深扫。

**何时用 /security 而不是 /review**：
- 涉及外发内容（公开仓 commit / GitHub issue / Pages 站 / 客户文档）
- 涉及凭证 / token / 私钥处理
- 引入 dangerouslySetInnerHTML / shell=True / subprocess + 用户输入
- 重大 release 前（外部可见的版本）

## 步骤

1. **确认范围**：
   - 如果有 `$ARGUMENTS`，用作目标
   - 否则问用户："深扫范围 (a) 当前 diff (b) 最近 N commit (c) 全仓
     (d) 某文件"

2. **spawn security-and-neutrality-auditor**（深扫模式）：

   ```
   subagent_type: security-and-neutrality-auditor
   prompt: |
     深扫模式，范围：<...>

     按你 prompt 里的 5 维（中立 / OWASP / 凭证 / 输入校验 / 文件 IO）
     跑，比 /review 内同名 agent 更彻底：

     - 自动跑 ./scripts/check_sensitive_words.sh --all
     - 自动跑 ./scripts/check_offline_purity.sh
     - 全仓 grep dangerouslySetInnerHTML / shell=True / api[_-]key
     - 历史豁免视角：检查每个发现是否在 lessons.md / decisions.md 有
       已记录的豁免

     输出 ≤ 8 条发现 + 是否阻塞 ship 结论 + 必修条目列表。
   ```

3. **呈现报告**：完整给用户看，不裁剪

4. **询问用户**：
   - high 必修条目是否立刻修
   - 是否阻塞当前 commit / push

5. **knowledge 更新**：
   - 新风险类型 → `.claude/knowledge/lessons.md`
   - 新攻击向量 → `.claude/knowledge/lessons.md`
   - 建议加白名单到 `scripts/check_sensitive_words.sh` → 让用户决定

## 不要做

- 不直接改产品代码
- 不替用户决定"是否阻塞"（合规决策必须用户拍板）
- 不裁剪 agent 输出
