---
description: UI 改动评审 —— mockup-baseline-checker → ui-fluency-auditor → visual-audit-runner 三段串行（任一失败提前停）
argument-hint: [React 文件 / 组件 / 全局]
---

UI 改动 commit 前的串行评审。**严格串行**，因为后两道依赖前一道结果。

## 串行依据

- 如果 React **基线偏离** mockup → ui-fluency / visual 验证没意义
- 如果 React **流畅性挂了**（loading / empty / error 漏） → visual
  纯视觉验证没意义
- 三道都过才说"UI 改动 ship-ready"

## 步骤

### 1. spawn mockup-baseline-checker（第一道）

```
subagent_type: mockup-baseline-checker
prompt: |
  评审对象：<React 文件列表>
  最新 mockup：`docs/webui-preview-v2.html`（或最新 vN）

  按你 prompt 里的 6 维（class / 容器 / token / inline style / 覆盖
  完整性 / mockup 自身合理性）跑。
  输出 ≤ 8 条偏离 + 是否通过。
```

**等返回。看"是否通过 /ui-check 串行下一道"结论。**

- **不通过**（有 high 偏离）→ 把报告呈现给用户 → 停在这一步，不跑后两道
  。问用户"先修偏离再 retry，还是接受偏离继续跑后两道？"
- **通过** → 继续第 2 步

### 2. spawn ui-fluency-auditor（第二道）

```
subagent_type: ui-fluency-auditor
prompt: |
  评审对象：<同上>

  按你 prompt 里的 6 维（延迟 / 动画 / CLS / 三态 / a11y / 响应式）
  跑。
  报告写到 `.claude/reports/ui-fluency-<ts>.md`。
  截图（如有）写到 `.claude/reports/screenshots/<ts>/`。
  输出摘要 + ≤ 6 条到对话。
```

**等返回。**

- 有 high 体验问题 → 呈现给用户，问"先修体验再 retry visual，还是
  继续视觉验证？"
- 没 high → 继续第 3 步

### 3. spawn visual-audit-runner（第三道）

需要明确"评审哪个 HTML"：
- 如果改了 mockup（`docs/webui-preview-v*.html`）→ 评审 mockup HTML
- 如果只改了 React → 评审 React build 出的 `docs/app/index.html`
  （前提：用户已 `cd web && npm run build` 过）

```
subagent_type: visual-audit-runner
prompt: |
  评审对象：<HTML 路径>

  按你 prompt 里的流程：
  1. 跑 sky-skills 三道闸（verify.py / visual-audit.mjs / screenshot.mjs）
  2. 任一失败提前停
  3. 三道都过 → 跑盲区检查（grid 溢出 / SVG 字号 / hollow 阈值 / 响应式
     / 字体回退 / dark token 误用）
  报告写到 `.claude/reports/visual-<ts>.md`。
  截图写到 `.claude/reports/screenshots/<ts>/`。
  输出摘要 + 三道闸结果 + 截图路径到对话。
```

### 4. 综合呈现

```
# /ui-check 报告 · <对象>

## 第一道 · mockup-baseline-checker
<结论 + 偏离条数>

## 第二道 · ui-fluency-auditor
<结论 + 报告路径>

## 第三道 · visual-audit-runner
<三闸结果 + 盲区发现 + 截图路径>

## 综合结论
- UI ship-ready：是 / 否
- 必修：<合集>
- 截图（请用户肉眼审）：<路径列表>
```

### 5. knowledge 更新（如有）

- 新 UX 债 / 体验教训 → `.claude/knowledge/lessons.md`
- 新视觉盲区（建议 sky-skills 提 issue）→ `.claude/knowledge/lessons.md`
- 驳回的建议 → `.claude/knowledge/review-feedback.md`

## 不要做

- 不并行跑（有业务依赖）
- 不跳过任何一道（mockup baseline 是 ABSOLUTE rule）
- 不让 agent 自己改 mockup / React（agent 只输出建议）
- 不裁剪输出
