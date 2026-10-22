---
title: 能力 · power
type: capability-spec
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capability, power, reboot, sleep, battery]
---

# Capability · power

电源状态管理：重启、进特殊模式、休眠唤醒测试、电池状态查询。**所有重启类命令都过权限检查**。

---

## CLI

```bash
alb reboot [mode] [options]
# mode: normal (默认) / recovery / bootloader / fastboot / sideload

alb sleep-wake [options]       # 触发休眠 + 唤醒（测试场景）
alb battery                    # 电池状态
alb wait-boot [--timeout N]    # 等设备 boot_completed
alb screen on / off            # 亮屏/灭屏（不是休眠，屏幕级）
```

选项：
- `--wait-boot` reboot 后等启动完成
- `--timeout N` 等待超时

---

## MCP tools

```python
@mcp.tool()
async def alb_reboot(
    mode: str = "normal",
    wait_boot: bool = True,
    timeout: int = 180,
) -> dict:
    """
    Reboot the device. mode: normal | recovery | bootloader | fastboot | sideload.

    Permission: every mode runs without confirmation (2026-10-23).
    Transports: recovery / bootloader / fastboot over adb, or over serial
    when the console is at a Linux shell; sideload needs adb.

    LLM notes:
      - mode=normal reboots to normal OS.
      - mode=recovery / bootloader / fastboot does NOT auto-return; you'll need
        additional steps (adb reboot or fastboot reboot) to come back.
      - wait_boot=True waits until sys.boot_completed=1 before returning.
      - Reboot to recovery/bootloader requires adb transport (A). ssh/serial won't work.
    """

@mcp.tool()
async def alb_sleep_wake_test(cycles: int = 1, hold_sec: int = 5) -> dict:
    """
    Trigger N sleep/wake cycles. Useful for power regression testing.

    Method:
      - sleep: `input keyevent KEYCODE_POWER` (or `svc power`)
      - wake:  after hold_sec, `input keyevent KEYCODE_WAKEUP`
      - records timing + screen state transitions
    """

@mcp.tool()
async def alb_battery() -> dict:
    """Return battery status: level, health, temperature, plugged, status."""

@mcp.tool()
async def alb_wait_boot(timeout: int = 180) -> dict:
    """Poll sys.boot_completed until True or timeout.
       Returns boot duration for regression analysis."""
```

---

## 业务函数

```python
# src/alb/capabilities/power.py

async def reboot(transport, mode: str = "normal",
                 wait_boot: bool = True,
                 timeout: int = 180) -> Result[RebootResult]:
    # 权限检查
    perm = await transport.check_permissions("power.reboot", {"mode": mode})
    if perm.behavior == "deny":
        return fail(code="PERMISSION_DENIED", ...)
    if perm.behavior == "ask":
        # CLI 交互提示 / MCP 返回 ask 响应
        ...

    # 某些模式需要特定 transport
    if mode in ("recovery", "bootloader", "fastboot", "sideload"):
        if not isinstance(transport, AdbTransport):
            return fail(code="TRANSPORT_NOT_SUPPORTED",
                        suggestion=f"Mode '{mode}' needs adb transport")

    await transport.reboot(mode)
    if mode == "normal" and wait_boot:
        return await wait_boot(transport, timeout)
    return ok(data=RebootResult(mode=mode))


async def sleep_wake_test(transport, cycles: int = 1,
                          hold_sec: int = 5) -> Result[SleepWakeReport]:
    results = []
    for i in range(cycles):
        # 睡
        t0 = now()
        await transport.shell("input keyevent KEYCODE_POWER")
        # 等 hold_sec
        await asyncio.sleep(hold_sec)
        # 醒
        await transport.shell("input keyevent KEYCODE_WAKEUP")
        await transport.shell("input keyevent KEYCODE_MENU")  # 解锁
        t1 = now()
        results.append(CycleResult(i, t0, t1))
    return ok(data=SleepWakeReport(cycles=results))


async def battery(transport) -> Result[BatteryInfo]:
    r = await transport.shell("dumpsys battery")
    # parse: level / scale / health / temperature / voltage / plugged
    ...


async def wait_boot(transport, timeout: int = 180) -> Result[BootResult]:
    """Poll sys.boot_completed=1."""
    start = now()
    while elapsed(start) < timeout:
        r = await transport.shell("getprop sys.boot_completed", timeout=5)
        if r.stdout.strip() == "1":
            return ok(data=BootResult(duration_ms=elapsed_ms(start)))
        await asyncio.sleep(3)
    return fail(code="TIMEOUT_BOOT",
                suggestion="Device may have kernel panic; check UART (method G)")
```

---

## 各模式与各传输的兼容性

| 模式 | A (adb) | B (WiFi) | C (ssh) | G (serial) |
|------|:-----:|:------:|:-----:|:---------:|
| `normal` | ✅ | ✅* | ✅ | ✅（板子里跑 reboot） |
| `recovery` | ✅ | ❌ | ❌ | 部分** |
| `bootloader` | ✅ | ❌ | ❌ | 部分** |
| `fastboot` | ✅ | ❌ | ❌ | ❌ |
| `sideload` | ✅ | ❌ | ❌ | ❌ |

\* B 重启后 TCP 模式会丢失，需要重 `adb tcpip`
\*\* G 能发 u-boot 命令让板子重启进特定模式，但 alb 封装 M3

---

## 权限规则

所有模式都直接执行，不要确认（2026-10-23 起，见 [`permissions.md`](../permissions.md)）。

| 模式 | adb | 串口 |
|---|---|---|
| normal | `adb reboot` | 板子在 Linux shell 时可用 |
| recovery / bootloader / fastboot | `adb reboot <mode>` | 板子在 Linux shell 时可用 |
| sideload | `adb reboot sideload` | 不支持（sideload 要 adbd 先设置 recovery） |

**adb 找不到设备时自动改走串口**（2026-10-23）：`alb power reboot` 和 MCP `alb_reboot` 在
没指定 `--transport`、没设 `ALB_TRANSPORT`、没给 `--device` 时，如果 adb 回 `DEVICE_NOT_FOUND`，
就改从串口重启。adb 那次的错误不会被吞掉：

- 成功时，结果里的 `data.fallback` 记着 adb 的错误码和原文；CLI 另外往 stderr 打一行
  `warning: adb found no device (DEVICE_NOT_FOUND: …); rebooted over the serial console instead`。
- 串口也失败时，报错信息里两边的错误都有，`details.fallback` 是 adb 那次的错误。
- 只认 `DEVICE_NOT_FOUND`。设备离线、未授权这类错误说明设备还在，只是状态不对；这时改去重启串口上的板子，会把问题盖住。
- 给了 `--device` 就不回退：点名的 adb 设备不一定就是串口上接的那块板。

串口重启的做法：发 `(sleep 1; reboot <mode>) </dev/null >/dev/null 2>&1 & echo __ALB_REBOOT_QUEUED__`，
看到这个标记才算成功（`src/alb/transport/serial.py` `reboot()`）。原因：

- 安卓的 `reboot` 命令发出后不会返回。放在前台执行，串口 shell 等不到结束标记，
  超时后还会补发一个 Ctrl-C。如果板子这时已经进了 U-Boot 的 fastboot，Ctrl-C 会把它打断。
- 板子停在 U-Boot / recovery 时，这一行不会被当成 shell 命令执行，也就不会打出这个标记。
  这时返回 `TRANSPORT_NOT_SUPPORTED`，不会误报重启成功。

---

## 典型用例

### 正常重启 + 等待
```bash
alb reboot --wait-boot
# 返回启动时长，可用于开机速度回归
```

### 重启到 recovery 刷包
```bash
alb reboot recovery       # 会 ask 确认
# 之后 alb sideload xx.zip（M2）
```

### 休眠唤醒测试
```bash
alb sleep-wake --cycles 10 --hold 30
# 触发 10 次休眠/唤醒，每次睡 30s
# 报告每次耗时 + 唤醒失败数
```

### 电池监控
```bash
while true; do
    alb battery --json >> battery.jsonl
    sleep 60
done
# 后续用 alb_log_search 或 pandas 分析
```

---

## LLM 提示

- **reboot 默认 wait_boot=True** —— 避免 LLM 下一步调命令时板子还没起
- **reboot 的 ask 流程** —— LLM 看到 behavior=ask 要返回给用户确认，不能自己 allow
- **wait_boot 超时** → 很可能 kernel panic，suggest 切 UART 看

---

## 错误场景

| 错误码 | 场景 | suggestion |
|-------|------|-----------|
| `TIMEOUT_BOOT` | 启动超时 | 切 UART 看 panic |
| `PERMISSION_DENIED` | ask 被拒 / 策略 deny | - |
| `TRANSPORT_NOT_SUPPORTED` | ssh 调 `reboot recovery` | 切 adb |

---

## 关联文件

- `src/alb/capabilities/power.py`
- `src/alb/cli/power_cli.py`
- `src/alb/mcp/tools/power.py`
- `tests/capabilities/test_power.py`
