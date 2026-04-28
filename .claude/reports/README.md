# agents 评审报告归档

agents 评审产生的报告落到这里。文件名严格遵守
`<agent-name>-<timestamp>.md` 格式，避免冲突。

## 命名规则

```
.claude/reports/perf-<ts>.md            # performance-auditor
.claude/reports/ui-fluency-<ts>.md      # ui-fluency-auditor
.claude/reports/visual-<ts>.md          # visual-audit-runner
```

`<ts>` 用 `date +%Y%m%dT%H%M%S` 格式（如 `20260428T143000`），保证两个
agent 同时启动也不会撞文件名。

## 截图

`.claude/reports/screenshots/<ts>/<filename>.png` —— **gitignore，不入仓**
（PNG 太大，且只对运行时本地审查有意义）。

## 归档策略

每类报告保留**最近 10 份**。超过部分由主对话定期 mv 到：

```
.claude/reports/archive/<year>/<agent-name>-<ts>.md
```

archive 跟仓库走（让"团队成长记录"可追溯），但不在主目录下混淆当前活
报告。

每次 `/preflight` 后主对话**自动**检查并归档老报告：
```bash
ls -t .claude/reports/perf-*.md | tail -n +11 | xargs -I {} mv {} .claude/reports/archive/<year>/
```

## 谁可以写

- `performance-auditor` 写 `perf-<ts>.md`
- `ui-fluency-auditor` 写 `ui-fluency-<ts>.md` + screenshots
- `visual-audit-runner` 写 `visual-<ts>.md` + screenshots
- 其他 agents **没有 Write 工具**，写不进来
- 主对话能在归档时 mv 文件

## dev-team 展示页用

`docs/dev-team.html`（计划，未实施）会派生本目录的统计：
- 总报告数
- 各 agent 报告频次
- 最近一次报告日期 → 给外部参观者看"团队活跃度"
