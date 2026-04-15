---
title: 权限系统设计
type: design-doc
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [permissions, security, llm-safety]
---

# 权限系统设计

> 防止 LLM（或人）误触危险操作把设备搞坏。借鉴 Claude Code 的双层权限设计。

---

## 一、为什么需要

**不是所有命令都该自由执行**。典型高危：

| 命令模式 | 后果 |
|---------|------|
| `rm -rf /` / `rm -rf /sdcard` | 删用户数据 |
| `reboot bootloader` / `fastboot erase <partition>` | 进 fastboot + 擦分区 |
| `setprop persist.sys.xxx` | 改持久化系统属性 |
| `dd of=/dev/block/bootdevice/by-name/boot` | 直接写底层块设备 |
| `mkfs.ext4 /dev/block/xxx` | 格式化分区 |
| `echo 1 > /sys/class/power_supply/battery/...` | 改电池控制 |
| `killall system_server` | kill 系统核心 |

LLM 有可能在推理失误时触发这些。**没有权限兜底 ≈ 不敢让 LLM 自主操作**。

---

## 二、双层架构

```
┌─────────────────────────────────────────────┐
│  第一层 · 全局权限引擎 (infra/permissions)   │
│  ├─ 黑名单正则 (DANGEROUS_PATTERNS)          │
│  ├─ 多层策略合并                             │
│  └─ 规则可运行时加载 / 覆盖                   │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  第二层 · Tool 内自检 (transport/capability) │
│  每个 tool 可定义自己的 check_permissions     │
│  例：transport.push 禁止推到 /system/        │
└─────────────────────────────────────────────┘
```

两层任一 deny → 操作拒绝。

---

## 三、策略来源（多层覆盖）

借鉴 Claude Code 的来源分层，后者覆盖前者：

```
1. defaults           （包内置）
      ↓
2. global config      (~/.config/alb/config.toml)
      ↓
3. profile config     (workspace/profiles/<name>.toml)
      ↓
4. CLI flags          (--allow "rm -rf /sdcard/tmp" / --deny "setprop")
      ↓
5. session override   (运行时临时授权，仅本 session)
```

任一层可以给命令打 `allow` / `ask` / `deny` 标签。

---

## 四、默认黑名单（DANGEROUS_PATTERNS）

```python
# src/alb/infra/permissions.py

DANGEROUS_PATTERNS = [
    # ── 文件系统毁灭 ────────────────────────
    (r"^\s*rm\s+-rf?\s+/($|\s|\*)",        "rm root fs"),
    (r"^\s*rm\s+-rf?\s+/sdcard($|\s|/?\*)", "rm entire sdcard"),
    (r"^\s*rm\s+-rf?\s+/data($|\s|/?\*)",   "rm /data"),
    (r"^\s*rm\s+-rf?\s+/system",            "rm /system"),
    (r">\s*/dev/block/",                    "write to raw block device"),
    (r"^\s*dd\s+.*of=/dev/block",           "dd to block device"),
    (r"^\s*mkfs\.",                         "format partition"),

    # ── 启动 / 刷机 ──────────────────────────
    (r"^\s*reboot\s+(bootloader|fastboot)", "reboot to bootloader/fastboot"),
    (r"^\s*fastboot\s+(erase|flash|format|oem)", "fastboot destructive"),

    # ── 持久属性 ────────────────────────────
    (r"^\s*setprop\s+persist\.",            "modify persistent property"),
    (r"^\s*setprop\s+ro\.",                 "modify read-only property"),

    # ── 系统进程 ────────────────────────────
    (r"^\s*(killall|pkill)\s+(system_server|zygote|init|surfaceflinger)",
                                             "kill critical system process"),
    (r"^\s*kill\s+-9\s+1(\s|$)",            "kill init"),

    # ── 电源 / 硬件控制 ───────────────────────
    (r">\s*/sys/class/power_supply/",       "write battery control"),
    (r">\s*/sys/class/thermal/",            "write thermal control"),
    (r">\s*/proc/sys/kernel/",              "write kernel runtime tunable"),

    # ── SELinux / 权限降级 ──────────────────
    (r"^\s*setenforce\s+0",                 "disable SELinux"),
    (r"^\s*chmod\s+777\s+/system",          "world-writable on /system"),
]
```

匹配到任一条 → 返回 `behavior: "deny"` + `reason`。

---

## 五、Tool 级自检（第二层）

每个 Transport / Capability 可以实现 `check_permissions`：

```python
# src/alb/transport/adb.py

class AdbTransport(Transport):
    async def check_permissions(self, action: str, input: dict) -> PermissionResult:
        # 先走全局层
        base = await super().check_permissions(action, input)
        if base.behavior == "deny":
            return base

        # 自定义规则
        if action == "push":
            remote = input.get("remote", "")
            if remote.startswith(("/system/", "/vendor/", "/product/")):
                return PermissionResult(
                    behavior="deny",
                    reason=f"push to read-only system path: {remote}",
                    suggestion="mount system rw first, or push to /data/local/tmp"
                )
        return base
```

---

## 六、策略模式

三种预设 `mode` 组合，config 里选一种：

| mode | 行为 |
|------|------|
| **strict** | 所有未明确 allow 的危险命令 → deny；常规命令默认 allow |
| **standard**（默认） | 黑名单 deny；部分中危（`mount` / `mv /data`）→ ask |
| **permissive** | 仅最高危 deny；其他 allow |

```toml
# ~/.config/alb/config.toml
[permissions]
mode = "standard"
ask_on_ambiguous = true
log_denied = true          # 写到 workspace/sessions/<id>/permissions.log
```

### 运行时查权限

```bash
alb permissions check "rm -rf /sdcard/tmp"
# {
#   "behavior": "deny",
#   "matched_rule": "rm /sdcard/*",
#   "reason": "Would delete user data",
#   "suggestion": "Use alb_pull --delete with specific path"
# }

alb permissions rules list        # 当前生效的所有规则
alb permissions log              # 查看 denial 历史
```

---

## 七、临时授权（session override）

LLM 需要做看起来危险但确实必要的操作时，可以请求临时授权：

```python
# MCP tool 签名
async def alb_shell(cmd: str, allow_dangerous: bool = False) -> dict:
    """
    Execute shell command. Dangerous commands (rm -rf, reboot bootloader, etc.)
    require allow_dangerous=True AND must be scoped specifically.
    LLM 注意: allow_dangerous 不是万能钥匙, 黑名单 "deny" 类规则仍然拦截.
    """
```

```bash
# CLI
alb shell --allow-dangerous "reboot recovery"
```

授权的效果：
- `ask` 级别 → 直接放行
- `deny` 级别 → **仍然拒绝**（除非规则在 config 里被显式改为 ask）

---

## 八、Permission 结果结构

```python
@dataclass(frozen=True)
class PermissionResult:
    behavior: Literal["allow", "ask", "deny"]
    reason: str | None = None
    matched_rule: str | None = None
    suggestion: str | None = None
    ask_prompt: str | None = None    # behavior=ask 时给用户的问题
```

LLM 收到 deny 时的返回（沿用 Result 结构）：

```json
{
  "ok": false,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Command blocked by dangerous pattern",
    "suggestion": "Scope to a specific path, or configure allow rule in profile",
    "category": "permission",
    "matched_rule": "rm -rf /sdcard",
    "details": {
      "attempted_command": "rm -rf /sdcard",
      "policy_layer": "defaults"
    }
  },
  "artifacts": []
}
```

---

## 九、审计 / 日志

所有 deny + ask 事件写入 `workspace/sessions/<id>/permissions.log`：

```jsonl
{"ts":"2026-04-15T10:30:00Z","cmd":"rm -rf /sdcard","decision":"deny","rule":"rm /sdcard/*","source":"mcp"}
{"ts":"2026-04-15T10:31:00Z","cmd":"reboot recovery","decision":"ask","answer":"yes","source":"cli"}
```

作用：
- LLM 可以学习上次被 deny 过什么（M3 Evo-Memory）
- 人工审查 agent 行为
- 合规 / 安全追溯

---

## 十、配置示例

### 示例 1 · 研发人员放松模式

```toml
# ~/.config/alb/config.toml
[permissions]
mode = "permissive"

# 额外允许 fastboot 用于刷机开发
[[permissions.allow]]
pattern = "^\\s*fastboot\\s+(flash|erase)\\s+recovery"
reason = "dev needs to flash recovery repeatedly"
```

### 示例 2 · CI 严格模式

```toml
# workspace/profiles/ci.toml
[permissions]
mode = "strict"

[[permissions.deny]]
pattern = "^\\s*(reboot|shutdown)"
reason = "CI should not reboot devices"

[[permissions.allow]]
pattern = "^\\s*pm\\s+(install|uninstall)"
reason = "needed for test flow"
```

### 示例 3 · 默认基础上加一条白名单

```toml
[permissions]
mode = "standard"

[[permissions.allow]]
pattern = "^\\s*setprop\\s+debug\\."
reason = "debug.* props are safe to modify"
```

---

## 十一、设计原则

| # | 原则 | 理由 |
|---|------|------|
| 1 | **默认 deny 优于默认 allow** | 一个误操作损失远大于一次不便 |
| 2 | **deny 要给 suggestion** | LLM 被挡住要知道怎么绕 |
| 3 | **允许多层策略** | 团队协作 / CI / 个人开发需求不同 |
| 4 | **tool 级自检不可省** | 全局层不可能知道"push 到 /system 不合理"这种语义 |
| 5 | **所有 deny 审计** | 调试 agent 行为需要 |
| 6 | **临时授权不破坏黑名单** | `allow_dangerous=True` ≠ 管理员 |

---

## 十二、未来扩展（M2+）

- **机器学习分类器** —— Claude Code 的 `classifierDecision.ts` 思路，用 LLM 判断"这命令在这上下文下是不是危险的"，补充正则的不足
- **设备级策略** —— profile 不同设备不同规则（生产机 strict / 测试机 permissive）
- **用户交互式 ask** —— Web UI / CLI 终端对话框

---

## 十三、参考

- Claude Code `utils/permissions/` 的 27 个文件设计
- [OWASP Agentic AI Top 10](https://genai.owasp.org/) —— Agent 安全最佳实践
- [Anthropic: Responsible Scaling Policy](https://www.anthropic.com/news/anthropics-responsible-scaling-policy)
