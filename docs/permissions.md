---
title: 权限系统设计
type: design-doc
created: 2026-04-15
updated: 2026-10-23
owner: sky
tags: [permissions, security]
---

# 权限系统设计

> 结论:板子上的命令一律放行;权限检查只用来保护接板子的 Windows 主机,不让它上面的文件被删。

---

## 一、保护的是什么

```
  Linux 侧(alb CLI / MCP / Web)
        │
        ├── 板子上的命令 ──────────► 板子          不拦截
        │   adb / 串口 / ssh / 网页终端
        │
        └── Windows 侧操作 ────────► Windows agent  只执行操作表里登记过的操作
            fastboot 烧录 / USB 列表 / 日志 / agent 更新
```

| 对象 | 规则 | 原因 |
|---|---|---|
| 板子 | 不拦截、不先问 | 板子是调试对象。`reboot bootloader`、`fastboot flash`、`setprop persist.`、`setenforce 0` 都是调试的日常操作,拦掉以后调试做不下去 |
| Windows 主机 | 只执行操作表里的操作 | 主机上是用户自己的文件。agent 不接受任意命令,没登记的操作回 `BENCH_OP_UNKNOWN`(`clients/windows-agent/alb_agent.py` `_build_ops`) |

2026-10-23 user 定:「我们这个权限控制只是为了防止删除 windows 下的文件,板子上的命令全要放开的」。

在这之前,板子侧有一份拦截名单(`rm -rf /`、`reboot bootloader`、`setprop persist.`、`dd of=/dev/block` 等,命中就拒绝),
adb / ssh 推送到 `/system/` 要确认、推到 `/dev/block/` 直接拒绝,adb 重启到 recovery / bootloader 要确认,
网页终端另有一份名单(任意 `reboot`、`mount rw`、`fastboot flash` 等)要弹框确认。这些都已去掉。

---

## 二、代码里还留着什么

| 位置 | 现状 | 为什么留 |
|---|---|---|
| `src/alb/infra/permissions.py` `default_check` | 永远返回 `allow` | 各传输层的 `check_permissions` 和各能力调用它;以后真要加主机侧规则,从这里加 |
| 各能力里的 `PERMISSION_DENIED` 分支 | 板子侧走不到 | 同上,规则加回来时不用再改调用方 |
| `allow_dangerous` 参数 / `--allow-dangerous` 开关 | 板子侧没有命令需要它 | 保留兼容,已有脚本和 AI 调用带着它也不会报错 |
| 网页终端 `TerminalGuard` | 默认名单为空 | 确认弹框和审计日志还在;部署方可以 `TerminalGuard(rules=...)` 自己传规则;网页上的「只读模式」开关照常可用 |

---

## 三、对调用方的影响

- AI 调 `alb_shell` / `alb_uart_shell` 跑什么,板子就执行什么,没有人替它确认。
  撤不回的命令(删数据、写块设备、擦分区)要由调用方自己判断,用户没要求就不跑。
  面向 AI 的说明见 `llm/CLAUDE.md`、`llm/AGENTS.md`、`src/alb/skills/generator.py`。
- `alb power reboot bootloader` 现在串口也能用,做法见 [`capabilities/power.md`](capabilities/power.md)「权限规则」。
