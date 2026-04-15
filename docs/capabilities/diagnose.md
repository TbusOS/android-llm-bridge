---
title: 能力 · diagnose
type: capability-spec
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capability, diagnose, bugreport, anr, tombstone]
---

# Capability · diagnose

一键拉取 Android 标准诊断信息：bugreport / ANR / tombstone / dropbox。**高价值、低使用门槛**，LLM 排故障首选。

---

## 支持的诊断类型

| 诊断 | 来源 | 用途 |
|-----|------|------|
| `bugreport` | `adb bugreport` | 全量系统快照 zip，包含 logcat / dmesg / 内存 / 进程 / 网络 / 所有 dumpsys |
| `anr` | `/data/anr/*.txt` | ANR (Application Not Responding) traces |
| `tombstone` | `/data/tombstones/*` | native crash 堆栈 |
| `dropbox` | `/data/system/dropbox/` | Android DropBox (crash / strict mode / wtf 等) |
| `last_kmsg` | `/proc/last_kmsg` | 上次重启前 kernel log |
| `devinfo` | 组合 | 设备基本信息快照（ro.* / uname / 电池 / 存储） |

---

## CLI

```bash
alb bugreport [--output DIR]
alb anr pull [--output DIR] [--clear-after]
alb tombstone pull [--output DIR]
alb dropbox pull [--tag TAG] [--output DIR]
alb last-kmsg
alb devinfo
alb diagnose all                       # 一次全拉（适合大型排故障）
```

---

## MCP tools

```python
@mcp.tool()
async def alb_bugreport(output_dir: str | None = None) -> dict:
    """
    Trigger adb bugreport and save the zip to workspace.

    When to use:
      - Comprehensive system snapshot (used for escalating to vendor)
      - Reproducing complex issues (gets everything in one shot)
      - Not sure what logs are relevant

    LLM notes:
      - Takes 60-180s; returns only the zip path, NOT the content.
      - Use alb_log_search on the extracted logcat for analysis.
    """

@mcp.tool()
async def alb_anr_pull(clear_after: bool = False) -> dict:
    """
    Pull /data/anr/*.txt to workspace.

    When to use:
      - After observed ANR
      - Periodically polling a test device

    LLM notes:
      - Returns count + list of files pulled.
      - Each ANR file is ~KB to MB. Use alb_log_tail to read content.
      - clear_after=True deletes device-side files after pull (fresh slate for next).
    """

@mcp.tool()
async def alb_tombstone_pull(limit: int = 10) -> dict:
    """Pull native crash tombstones. Similar to anr_pull."""

@mcp.tool()
async def alb_devinfo() -> dict:
    """Collect basic device info (brand/model/build/kernel/battery/storage).
       Fast - returns structured data directly (no artifact)."""
```

---

## 业务函数

```python
# src/alb/capabilities/diagnose.py

async def bugreport(transport, output_dir: Path | None = None) -> Result[BugreportResult]:
    """调 adb bugreport -> zip. 只支持 adb 传输（A/B）."""
    if not isinstance(transport, AdbTransport):
        return fail(code="TRANSPORT_NOT_SUPPORTED",
                    suggestion="bugreport needs adb. Run: alb setup adb")
    # 权限检查 - bugreport 是只读操作，默认 allow
    r = await transport.shell("bugreportz", timeout=300)
    # parse path from stdout, then adb pull
    ...

async def anr_pull(transport, clear_after: bool = False) -> Result[AnrResult]:
    ls = await transport.shell("ls /data/anr/*.txt 2>/dev/null")
    files = ls.stdout.strip().split("\n")
    if not files or not files[0]:
        return ok(data=AnrResult(count=0, files=[]))
    # pull each
    ...

async def devinfo(transport) -> Result[DeviceInfo]:
    """Composite getprop + /proc queries."""
    props = await transport.shell("getprop", timeout=10)
    # parse ro.product.model / ro.build.fingerprint / ro.hardware ...
    battery = await transport.shell("dumpsys battery")
    storage = await transport.shell("df /data /sdcard")
    ...
    return ok(data=DeviceInfo(...))
```

---

## 产物路径

```
workspace/devices/<serial>/bugreports/
└── 2026-04-15T10-30-00/
    ├── bugreport.zip
    ├── bugreport.txt         # 解压的主日志
    └── metadata.json

workspace/devices/<serial>/anr/
└── 2026-04-15T10-45-00/
    ├── traces.txt
    ├── anr_2026-04-15-10-30-12-*.txt
    └── context.json          # {collection_ts, device_state, ...}

workspace/devices/<serial>/tombstones/
└── 2026-04-15T10-50-00/
    ├── tombstone_00
    └── tombstone_01
```

`metadata.json` / `context.json` 含：
- 采集时间
- 设备状态（uptime, free memory, battery）
- 最近 `alb_shell` / `alb_logcat` 调用记录
- 当前 ALB session ID

—— LLM 分析 ANR 时能看到"ANR 发生时设备处于什么状态"。

---

## 典型 LLM 工作流

### 工作流 1 · 标准排 crash

```
1. alb_status                        # 确认设备在线
2. alb_devinfo                       # 设备基础信息（一次）
3. 做某操作使其 crash
4. alb_anr_pull(clear_after=True)
   → { count: 1, files: ["/workspace/.../anr_xxx.txt"] }
5. alb_log_tail(path=files[0], lines=100)
   → 读前 100 行看 stack
6. 分析 + 给结论
```

### 工作流 2 · 偶发 bug，拉完整 bugreport

```
1. alb_bugreport
   → zip 落 workspace，180 秒完成
2. alb_log_search(pattern="FATAL", path=<zip>/bugreport.txt)
3. alb_log_search(pattern="W ActivityManager", ...)
4. 综合分析
```

### 工作流 3 · native 崩溃

```
1. alb_tombstone_pull(limit=3)       # 最近 3 个
2. 逐个 alb_log_tail 读 stack
3. 结合 alb_devinfo 的 CPU arch 定位 symbol
```

---

## 权限系统

诊断类 tool 几乎都是只读 + 标准路径，默认全部 allow。唯一例外：
- `alb_anr_pull --clear-after` → 删除 `/data/anr/*` 是写操作，过 `ask`

---

## 错误场景

| 错误码 | 场景 | suggestion |
|-------|------|-----------|
| `NO_ANR_FOUND` | /data/anr 为空 | 正常，等下次 crash |
| `NO_TOMBSTONE_FOUND` | 无 tombstone | 正常 |
| `BUGREPORT_FAILED` | 板子 bugreportz 返回错 | 老 Android 可能不支持，用 `adb bugreport` 旧模式 |
| `TRANSPORT_NOT_SUPPORTED` | 用 serial 调 bugreport | 切 adb |

---

## 关联文件

- `src/alb/capabilities/diagnose.py`
- `src/alb/cli/diagnose_cli.py`
- `src/alb/mcp/tools/diagnose.py`
- `tests/capabilities/test_diagnose.py`
