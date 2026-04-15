---
title: 能力 · shell
type: capability-spec
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capability, shell]
---

# Capability · shell

执行任意命令，结构化返回 stdout / stderr / exit_code / duration。所有传输（A/B/C/G）都支持，但语义略有差异。

---

## CLI

```bash
alb shell "<cmd>" [options]
```

选项：
- `--timeout N` (秒，默认 30)
- `--device <serial>` 多设备时指定
- `--transport {adb|ssh|serial}` 指定传输（默认按 profile）
- `--allow-dangerous` 绕过 ask 级权限（deny 级仍拦截）
- `--json` 结构化输出

例子：
```bash
alb shell "getprop ro.build.version.sdk"
alb shell "ls /data/local/tmp" --device abc123
alb shell "reboot" --allow-dangerous
alb --json shell "dumpsys battery | grep level"
```

---

## MCP tool

```python
@mcp.tool()
async def alb_shell(
    cmd: str,
    timeout: int = 30,
    device: str | None = None,
    allow_dangerous: bool = False,
) -> dict:
    """
    Execute a shell command on the connected Android device.

    When to use:
      - Querying device state (getprop, dumpsys, ps, etc.)
      - Running one-off commands
      - Invoking Android tools (pm, am, service, input, etc.)

    When NOT to use:
      - Long-running commands (> timeout) → use alb_shell_stream
      - Need stdout streamed line-by-line → use alb_shell_stream
      - File transfer → use alb_push / alb_pull

    LLM notes:
      - Default timeout is 30s. Increase for slow commands.
      - Dangerous patterns (rm -rf /, reboot bootloader, etc.) are BLOCKED by default.
      - Use allow_dangerous=True for ask-level commands; deny-level stays blocked.
      - The output IS included in data.stdout, BUT for long output use alb_logcat etc.

    Returns:
      { ok, data: { stdout, stderr, exit_code, duration_ms }, error, timing_ms }
    """
```

---

## 业务函数

```python
# src/alb/capabilities/shell.py

async def execute(
    transport: Transport,
    cmd: str,
    *,
    timeout: int = 30,
    allow_dangerous: bool = False,
) -> Result[ShellOutput]:
    perm = await transport.check_permissions(
        "shell.execute",
        {"cmd": cmd, "allow_dangerous": allow_dangerous}
    )
    if perm.behavior == "deny":
        return fail(code="PERMISSION_DENIED", ...)

    r = await transport.shell(cmd, timeout=timeout)
    return ok(data=ShellOutput(...))
```

---

## 典型用例

### 查询设备信息
```bash
alb shell "getprop ro.product.model"
alb shell "getprop ro.build.fingerprint"
alb shell "uname -a"
alb shell "df -h"
alb shell "cat /proc/meminfo | head -5"
```

### 使用 Android 工具
```bash
alb shell "pm list packages | grep example"
alb shell "am start -n com.example/.MainActivity"
alb shell "service call SurfaceFlinger 1008 i32 0"
alb shell "input tap 500 500"
alb shell "dumpsys activity activities | head -50"
```

### 组合命令
```bash
alb shell "cat /proc/cpuinfo | grep Processor | wc -l"
alb shell "ls /sdcard/Download/ | tail -10"
```

---

## 各传输下的差异

| 特性 | A (adb) | B (adb) | C (ssh) | G (serial) |
|-----|:------:|:------:|:------:|:---------:|
| 单次 shell | ✅ | ✅ | ✅ | ✅ (慢) |
| 大输出稳定性 | 中 | 中 | 高 | 低（超过 buffer 丢） |
| 交互（shell prompt） | ✅ | ✅ | ✅ | ✅ |
| root shell | `adb root` + adb shell su | 同 A | ssh root user 直接 | 直接 |

**对 G (串口) 特别注意**：
- 每个命令间有明显延迟（等 prompt）
- 大输出可能截断 —— 优先用 `tail N` / `head N` 限制
- 某些 shell 没 bash，只有 busybox/toybox（不支持复杂 pipe）

---

## 权限切点

高危模式 → deny（见 [`permissions.md`](../permissions.md) 黑名单）：

```bash
alb shell "rm -rf /"                    # deny
alb shell "rm -rf /sdcard"              # deny
alb shell "reboot bootloader"           # deny (建议用 alb reboot bootloader)
alb shell "setprop persist.sys.x 1"     # deny
alb shell "dd if=/dev/zero of=/dev/..."  # deny
```

中危 → ask（可 `--allow-dangerous` 放行）：

```bash
alb shell "mount -o remount,rw /system"  # ask
alb shell "mv /data/important /tmp"      # ask
```

---

## 错误场景

| 错误码 | 场景 | suggestion |
|------|------|-----------|
| `TIMEOUT_SHELL` | 命令超过 timeout | 增加 timeout 或用 stream 模式 |
| `DEVICE_OFFLINE` | 设备离线 | 重新连接 |
| `DEVICE_UNAUTHORIZED` | USB 未授权 | 设备屏幕点允许 |
| `PERMISSION_DENIED` | 命令被拦 | 读 suggestion / matched_rule |

---

## LLM 使用模式

### 模式 1 · 做简单查询

```python
r = await alb_shell("getprop ro.build.version.sdk")
if r["ok"]:
    sdk = r["data"]["stdout"].strip()
```

### 模式 2 · 出错就读 suggestion

```python
r = await alb_shell("unknown-binary")
if not r["ok"]:
    # error.suggestion 会说明下一步
    print(r["error"]["suggestion"])
```

### 模式 3 · 避免长输出

```python
# ❌ 不好
r = await alb_shell("dumpsys")   # 输出 MB 级

# ✅ 好
r = await alb_shell("dumpsys activity | head -100")
# 或用 alb_bugreport（走 workspace）
```

---

## 关联文件

- `src/alb/capabilities/shell.py`（实现）
- `src/alb/cli/shell_cli.py`（CLI 壳）
- `src/alb/mcp/tools/shell.py`（MCP 壳）
- `src/alb/api/routers/shell.py`（API 壳）
- `tests/capabilities/test_shell.py`（测试）
