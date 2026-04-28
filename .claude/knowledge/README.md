# android-llm-bridge · 团队知识库

agents 团队的"集体记忆"。每次 agents spawn 时**先读这里再开始评审**，
让评审带着项目历史视角，而不只是"对当前规则打分"。

## 文件结构

| 文件 | 含义 | 谁更新 | 何时更新 |
|---|---|---|---|
| `architecture.md` | 当前架构快照（模块边界 / 数据流 / 依赖图） | 主对话 | 每次重构 / 新模块 ship 后 |
| `decisions.md` | ADR：每个重大决策的 why + trade-off | 主对话 | 每次重要选型 / 推翻原决定 |
| `debts.md` | 已知技术债清单（不是 bug，是妥协） | 主对话 | 每次发现新债 / 还债 |
| `lessons.md` | 反面教材（曾经怎样、踩什么坑、现在为什么这样） | 主对话 | 每次踩坑后立即写 |
| `review-feedback.md` | 历次评审：哪些建议被采纳 / 驳回 / 为什么 | 主对话 | 每次 /review /arch /preflight 完成后 |

## 写权限

- **只主对话能写**（用户监督下，避免 agent hallucination 污染）
- **agents 只读**（在它们 prompt 里被强制约束）
- 写入期间不能新 spawn agents（避免读到半写状态）

## 写入流程

每次 agents 评审结束后：

1. 主对话整理建议清单：`[采纳 / 驳回 / 待定]`
2. 用户确认每条
3. 主对话写到对应 knowledge 文件：
   - 采纳的设计建议 + 实施 → 更新 `architecture.md` / `decisions.md`
   - 发现的新债（妥协）→ 加到 `debts.md`
   - 驳回的建议（误报或意图）→ 写到 `review-feedback.md`，agent 下次会
     看到不再重复提
4. 写完后才能开始下一轮评审

## 文件长度控制

- 每个文件保持在 ~500 行以内
- 超过的话：拆子文件（`debts/<area>.md` 等）
- review-feedback.md 单独有归档：超 200 行的老条目移到 `feedback-archive/<year>.md`

## "越用越聪明"如何具体表征

- 同一类建议被驳回 ≥ 3 次 → 调整对应 agent 的 prompt 把这个盲区移除
- 同一类建议被采纳 ≥ 3 次 → 升级到 `lessons.md` 或 `debts.md`（成为
  规则，agents 下次自动尊重）
- agents 提出"应该重构"被采纳 → 写新 ADR 到 `decisions.md`
- 重大里程碑（C 档完工等）→ 主对话主动 update `architecture.md`，让
  agents 下次拿到的总是最新视图

## 用户级 memory 的关系

`~/.claude/projects/<project>/memory/` 是**用户私人的**（个人偏好 /
风格 / 反面教材），不入仓。本目录是**团队共享的**（项目历史 / 架构 /
债），入仓。两者互补不冲突：

- 同一条信息只放一处（不重复）
- 涉及"项目本身"的（architecture / decisions / debts / lessons）→ 本目录
- 涉及"用户偏好"的（commit 风格 / 命令习惯 / 个人外部资源指针）→ memory
