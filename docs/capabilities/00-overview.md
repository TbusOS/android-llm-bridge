---
title: 业务能力总览
type: reference
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capabilities, overview]
---

# 业务能力总览

> 六大能力是 M1 交付范围。每个能力都是一个 Python 模块（`src/alb/capabilities/`），通过 CLI / MCP / Web API 三层暴露。

---

## 一、能力清单

| 能力 | 模块 | CLI | MCP tool | 典型用途 |
|-----|------|-----|---------|---------|
| [shell](./shell.md) | `capabilities/shell.py` | `alb shell` | `alb_shell` | 执行任意命令，结构化返回 |
| [logging](./logging.md) | `capabilities/logging.py` | `alb logcat / dmesg / uart-capture / log-search / log-tail` | `alb_logcat / alb_dmesg / alb_uart_capture / ...` | 日志采集、检索、换页 |
| [filesync](./filesync.md) | `capabilities/filesync.py` | `alb push / pull / rsync` | `alb_push / alb_pull / alb_rsync` | 文件传输（自动选最快通道） |
| [diagnose](./diagnose.md) | `capabilities/diagnose.py` | `alb bugreport / anr pull / tombstone` | `alb_bugreport / alb_anr_pull / alb_tombstone` | 一键拉诊断信息 |
| [power](./power.md) | `capabilities/power.py` | `alb reboot / sleep-wake / battery` | `alb_reboot / alb_sleep_wake / alb_battery` | 重启、休眠测试、电池 |
| [app](./app.md) | `capabilities/app.py` | `alb app install/uninstall/start/stop/list` | `alb_app_*` | apk 管理 |

---

## 二、能力 × 传输 支持矩阵

| 能力 | A (adb USB) | B (adb WiFi) | C (sshd) | G (UART) |
|-----|:----------:|:-----------:|:--------:|:---------:|
| shell | ✅ | ✅ | ✅ | ✅（慢） |
| logging.logcat | ✅ | ✅ | ⚠️ ssh 里跑 | ❌ |
| logging.dmesg | ✅ | ✅ | ✅ | ✅ |
| logging.uart_capture | ❌ | ❌ | ❌ | ✅ |
| filesync.push/pull | ✅ | ✅ | ✅ scp | ❌ |
| filesync.rsync | ❌ | ❌ | ✅ | ❌ |
| diagnose.bugreport | ✅ | ✅ | ⚠️ | ❌ |
| diagnose.anr_pull | ✅ | ✅ | ✅ 用 scp | ❌ |
| power.reboot | ✅ | ✅ | ✅ | ✅ `reboot` |
| power.reboot recovery | ✅ | ❌ | ❌ | 部分 |
| app.install | ✅ | ✅ | ⚠️ 先 scp | ❌ |
| app.start/stop | ✅ | ✅ | ✅ | ✅ |

`⚠️` 表示间接可行（要走辅助步骤）。`❌` 表示完全不支持（会返回 `TRANSPORT_NOT_SUPPORTED` 错误）。

---

## 三、能力共享的基础

所有能力都依赖：

- [`infra/result.py`](../architecture.md) —— 统一返回类型
- [`infra/errors.py`](../errors.md) —— 错误码
- [`infra/permissions.py`](../permissions.md) —— 权限检查
- [`infra/workspace.py`](../architecture.md) —— 产物路径
- `transport/base.py` —— Transport ABC

---

## 四、M2+ 规划能力

| 能力 | 说明 | 目标里程碑 |
|-----|------|----------|
| `perf` | CPU/MEM/FPS/温度/电流持续采集 + CSV 产物 | M2 |
| `benchmark` | 跑分集成（AnTuTu / GeekBench / 自定义） | M2 |
| `network` | 端口转发 / tcpdump 抓包 / 弱网模拟 | M2 |
| `screen` | 截图 / 录屏 / 坐标点击（方案 E 集成） | M3 |
| `ui` | uiautomator2 控件级操作 | M3 |
| `memory` | workspace 历史检索 / LLM 日志分析（MemGPT 风格） | M3 |
| `watcher` | 事件监听 + 触发器（panic watch / logcat keyword） | M2 |

---

## 五、能力设计一致性要求

所有能力必须：

1. **async** —— 所有函数 `async def`
2. **Result 返回** —— 见 [`errors.md`](../errors.md)
3. **权限检查** —— 改变设备状态的操作过 `transport.check_permissions`
4. **产物路径规范** —— 用 `workspace_path()` 辅助函数
5. **LLM 友好 docstring** —— 含 `When to use` / Examples / LLM notes
6. **单元测试** —— happy / permission / timeout / error 四类最小

详见 [`tool-writing-guide.md`](../tool-writing-guide.md)。

---

## 六、典型调用链（以 `alb_logcat` 为例）

```
LLM 调 alb_logcat(duration=60, filter="*:E")
  ↓
alb.mcp.tools.logging:alb_logcat (薄壳)
  ↓
alb.capabilities.logging:collect_logcat (业务)
  ↓
├─ infra.permissions.check_permissions("logging.logcat", ...)
├─ infra.workspace.workspace_path("logs", ...)
├─ transport.stream_read("logcat", ...) ─► driver.adb wrapper
│    逐 chunk 写文件
└─ 返回 Result(ok=True, data=LogcatSummary, artifacts=[logfile])
```

---

## 七、下一步

读各能力细节文档：

- [shell.md](./shell.md)
- [logging.md](./logging.md)
- [filesync.md](./filesync.md)
- [diagnose.md](./diagnose.md)
- [power.md](./power.md)
- [app.md](./app.md)
