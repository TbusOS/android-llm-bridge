---
title: Slash 命令模板
type: reference
owner: sky
---

# Slash 命令模板

> 给使用 Claude Code / Cursor 等客户端的用户使用的 slash 命令预设。在你的项目 `.claude/commands/` 或 `.cursor/commands/` 下放对应的 `.md` 文件即可激活。

## 预设列表（M1）

| 命令 | 用途 |
|------|-----|
| `/alb-status` | 查当前设备 / transport 状态 |
| `/alb-log` | 抓 60 秒 logcat（Error 级） |
| `/alb-anr` | 拉最新 ANR 并分析 |
| `/alb-crash` | 全量拉 crash（ANR + tombstone + dropbox） |
| `/alb-boot-check` | 用 UART 抓启动日志 + 找关键字 |
| `/alb-reboot-test` | 重启后等 boot_completed + 记录启动时长 |
| `/alb-perf-snap` | （M2）一次性 perf snapshot |

## 写你自己的命令

Claude Code `.claude/commands/xxx.md` 格式：

```markdown
# alb-xxx

## 用途
一句话描述

## 步骤
1. alb_status
2. alb_logcat(duration=30, filter="*:E")
3. 分析结果...
```

把它放到你的项目 `.claude/commands/` 下，重启 Claude Code 后可用 `/alb-xxx` 激活。

## 模板内容

M1 发布时会在本目录下提供实际 `.md` 文件。现在是占位。
