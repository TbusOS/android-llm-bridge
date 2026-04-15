---
title: 能力 · logging
type: capability-spec
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capability, logging, logcat, dmesg, uart]
---

# Capability · logging

日志采集、检索、换页读取。这是 **LLM 最常用的能力之一**，设计上专门避免"context 爆炸"。

---

## 覆盖的日志源

| 源 | 传输 | 说明 |
|----|------|------|
| `logcat` | A / B / C(间接) | Android 应用 / framework 日志 |
| `dmesg` | A / B / C / G | kernel ring buffer |
| `kmsg` | C / G | `/proc/kmsg` 实时 kernel log |
| `uart` | G | 串口原始字节流（独占） |
| `last_kmsg` | A / B / C | `/proc/last_kmsg`（上次重启前的内核日志） |

---

## CLI

```bash
# logcat 采集
alb logcat [--duration N] [--filter FILTER] [--tag TAG]... [--clear]

# dmesg
alb dmesg [--duration N]

# uart（方案 G）
alb uart-capture [--duration N] [--baud 115200]

# 检索已采集的日志
alb log search <pattern> [--device D] [--from TIME] [--to TIME]

# 分页读日志文件
alb log tail <path> [--lines N]
alb log head <path> [--lines N]

# 列已收集的日志
alb log list [--device D]
```

例子：
```bash
alb logcat --duration 30 --filter "*:E"                     # 仅 Error 级别
alb logcat --duration 60 --tag ActivityManager --tag WindowManager
alb logcat --clear --duration 120                           # 先清缓冲再采集
alb dmesg --duration 10
alb uart-capture --duration 30 --baud 1500000
alb log search "FATAL" --from "2026-04-15T10:00" --to "2026-04-15T11:00"
alb log tail workspace/devices/abc/logs/xxx.txt --lines 50
```

---

## MCP tools

```python
@mcp.tool()
async def alb_logcat(
    duration: int = 60,
    filter: str | None = None,
    tags: list[str] = [],
    clear_before: bool = False,
    device: str | None = None,
) -> dict:
    """
    Collect Android logcat to workspace.

    When to use:
      - Investigating app crashes, ANRs, system errors
      - Reproducing a bug and capturing logs during the repro window

    When NOT to use:
      - Continuous background monitoring → use alb_logcat_watch
      - Need specific log file (e.g., /data/tombstones/) → use alb_tombstone_pull

    LLM notes:
      - Returns SUMMARY only (lines/errors/warnings counts).
      - Full log is at result.artifacts[0]. Use alb_log_search or alb_log_tail to read.
      - For duration > 300s prefer alb_logcat_watch (runs in background).
      - filter syntax: "*:E" (only Error), "Tag:I *:S" (only Tag at Info, silence rest)

    Args:
      duration: 1-3600 seconds, default 60
      filter:   logcat filter expression
      tags:     tag names to keep (convenience; combined into filter)
      clear_before: logcat -c before collecting
    """

@mcp.tool()
async def alb_dmesg(duration: int = 10, ...) -> dict:
    """Collect kernel dmesg to workspace. Use for kernel-level issues,
       driver messages, OOM kills, low-level errors."""

@mcp.tool()
async def alb_uart_capture(duration: int = 30, baud: int = 115200, ...) -> dict:
    """Capture UART serial output. REQUIRES serial transport (method G).
    Use for: boot log, u-boot stage, kernel panic rescue."""

@mcp.tool()
async def alb_log_search(pattern: str, device: str | None = None,
                         from_time: str | None = None,
                         to_time: str | None = None) -> dict:
    """Search across all collected logs for a pattern.
       LLM 换页模式: 当长日志 context 爆时, 先 collect 只返路径,
       然后用 search 按需读取相关片段."""

@mcp.tool()
async def alb_log_tail(path: str, lines: int = 50) -> dict:
    """Read last N lines of a log file (from workspace)."""
```

---

## 业务函数（精选）

```python
# src/alb/capabilities/logging.py

async def collect_logcat(transport, duration=60, filter=None, tags=[],
                         clear_before=False) -> Result[LogcatSummary]: ...

async def collect_dmesg(transport, duration=10) -> Result[DmesgSummary]: ...

async def capture_uart(transport, duration=30) -> Result[UartCapture]:
    """只支持 SerialTransport. 其他 transport 返回 TRANSPORT_NOT_SUPPORTED."""
    if not isinstance(transport, SerialTransport):
        return fail(code="TRANSPORT_NOT_SUPPORTED",
                    suggestion="Switch to serial transport: alb setup serial")
    ...

async def search_logs(pattern: str, device=None, from_time=None,
                      to_time=None) -> Result[SearchResults]:
    """在 workspace 里搜。M1 用 grep；M2 用 sqlite FTS5。"""

async def tail_log(path: Path, lines: int = 50) -> Result[str]:
    """只允许读 workspace/ 内的路径（防路径穿越）。"""

async def watch_logcat(transport, pattern: str,
                       on_match: Callable) -> Result[WatchHandle]:
    """长跑监控. 匹配 pattern 时触发 on_match. M2 实现."""
```

---

## LogcatSummary 结构

```python
@dataclass
class LogcatSummary:
    lines: int              # 总行数
    errors: int             # E 级别行数
    warnings: int           # W 级别行数
    top_tags: list[tuple[str, int]]  # [("ActivityManager", 132), ...]
    first_line_ts: str      # ISO 8601
    last_line_ts: str
    duration_captured_ms: int
    full_log_path: Path     # 相对 workspace 的产物路径
```

**关键设计**：不把全文塞 data，避免 context 爆炸。LLM 通过 `alb_log_search` 或 `alb_log_tail` 按需读 `full_log_path`。

---

## 产物路径

```
workspace/devices/<serial>/logs/
├── 2026-04-15T10-30-00-logcat.txt
├── 2026-04-15T10-35-00-dmesg.txt
└── 2026-04-15T10-40-00-uart.log
```

时间戳是采集**开始时间**，ISO 8601 （去掉冒号以方便文件名）。

---

## 典型 LLM 工作流

### 工作流 1 · 调查 crash

```
1. alb_logcat(duration=30, filter="*:E")
   → returns { errors: 5, artifacts: ["/workspace/.../logcat.txt"] }

2. alb_log_search(pattern="FATAL", path=<artifact>)
   → returns { matches: [
       { line: 234, content: "FATAL EXCEPTION: main ..." }
     ]}

3. alb_log_tail(path=<artifact>, from_line=230, to_line=280)
   → 精确读那段堆栈
```

### 工作流 2 · 看 kernel panic

```
1. alb_uart_capture(duration=60)
   → 触发重启 panic
2. alb_log_search(pattern="panic|oops|BUG:", path=<artifact>)
3. 分析 panic 栈
```

### 工作流 3 · 长时间监控（M2）

```
1. alb_logcat_watch(pattern="ANR", on_match="store-to-workspace")
   → returns WatchHandle{id: "watch-123"}
2. [等待很久]
3. alb_watch_status(id="watch-123")
   → returns { matched: 2, last_match_ts: "..." }
```

---

## 各传输下实现差异

### A / B (adb)

```python
# 直接走 adb logcat 协议
proc = await asyncio.create_subprocess_exec(
    "adb", "-s", serial, "logcat", "-v", "threadtime",
    *filter_args,
    stdout=asyncio.subprocess.PIPE,
)
async for line in proc.stdout:
    yield line
```

### C (ssh)

```python
# ssh 进去跑 logcat 命令（不是 adb 协议）
async with ssh.create_process("logcat -v threadtime") as proc:
    async for line in proc.stdout:
        yield line
```

实时性略差，但对 LLM 来说差异不大。

### G (serial)

串口没有 logcat 协议，只有原始 UART 输出流：

```python
# pyserial-asyncio 读串口
async for chunk in serial_reader:
    yield chunk
```

记录原始字节 + 简单换行分行。

---

## 日志归档（M3）

workspace 日志会越积越多：

```
workspace/devices/<serial>/logs/        # 热区：最近 7 天
workspace/archive/2026-04/              # 冷区：gzip 压缩
```

工具：
```bash
alb log archive --older-than 7d         # 手动归档
alb log clean --older-than 30d          # 彻底清
```

M3 自动按 TTL 执行。

---

## 错误场景

| 错误码 | 场景 |
|------|------|
| `LOGCAT_BUFFER_OVERFLOW` | adb logcat 缓冲满 |
| `INVALID_FILTER` | filter 语法错 |
| `TRANSPORT_NOT_SUPPORTED` | 用 serial 调 logcat，或用 adb 调 uart |
| `WORKSPACE_FULL` | 磁盘满 |
| `TIMEOUT_*` | 各种超时 |

---

## filter 语法提示（logcat）

```
*:E                    # 所有 tag，只保留 Error 以上
Foo:V *:S              # 只看 Foo tag 的 Verbose 及以上，其他全 Silent
Foo:I Bar:D *:S        # 多个白名单

级别（从低到高）: V(erbose) D(ebug) I(nfo) W(arn) E(rror) F(atal) S(ilent)
```

---

## 关联文件

- `src/alb/capabilities/logging.py`
- `src/alb/cli/logging_cli.py`
- `src/alb/mcp/tools/logging.py`
- `src/alb/api/routers/logging.py`
- `tests/capabilities/test_logging.py`
