---
title: 能力 · filesync
type: capability-spec
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capability, filesync, push, pull, rsync]
---

# Capability · filesync

文件 / 目录在 Linux 主机 ↔ Android 设备之间传输。自动选最快通道（adb push / scp / rsync），支持增量同步（C 方案独占）。

---

## CLI

```bash
alb push <local> <remote> [options]
alb pull <remote> <local> [options]
alb rsync <local> <remote> [options]           # 方案 C 独占
```

选项：
- `--device <serial>`
- `--transport {adb|ssh}` 强制通道
- `--resume` 断点续传（M2）
- `--delete` pull 时删除源（危险，走 ask）
- `--verify` 传完后算 md5 校验
- `--progress` 显示进度条
- `--chmod MODE` 目标文件权限

---

## MCP tools

```python
@mcp.tool()
async def alb_push(local: str, remote: str, verify: bool = False) -> dict:
    """
    Push local file(s) to Android device.

    When to use:
      - Deploying APK / binaries / config files to device
      - Pushing test resources

    When NOT to use:
      - Large directory with many small files → alb_rsync (much faster, ssh only)
      - Streaming a long log back → alb_pull at end

    LLM notes:
      - For /system/, /vendor/, /product/ the device must be mount rw (ask-level perm)
      - Pushing to /data/local/tmp/ is safe default
      - verify=True adds md5 check but slows down
    """

@mcp.tool()
async def alb_pull(remote: str, local: str) -> dict:
    """Pull file(s) from Android device."""

@mcp.tool()
async def alb_rsync(local_dir: str, remote_dir: str) -> dict:
    """Incremental sync using rsync. REQUIRES ssh transport (method C).
       Much faster than alb_push for large dirs with few changes."""
```

---

## 业务函数

```python
async def push(transport, local: Path, remote: str,
               verify: bool = False) -> Result[PushResult]:
    perm = await transport.check_permissions(
        "filesync.push", {"local": str(local), "remote": remote}
    )
    if perm.behavior == "deny":
        return fail(code="PERMISSION_DENIED", ...)

    if not local.exists():
        return fail(code="FILE_NOT_FOUND",
                    suggestion=f"Check path: {local}")

    r = await transport.push(local, remote)
    if verify and r.ok:
        # 算本地和远端 md5 对比
        ...
    return r

async def pull(transport, remote: str, local: Path) -> Result[PullResult]: ...

async def rsync_sync(transport, local_dir: Path, remote_dir: str,
                     delete: bool = False) -> Result[RsyncResult]:
    if not isinstance(transport, SshTransport):
        return fail(code="TRANSPORT_NOT_SUPPORTED",
                    suggestion="rsync requires ssh transport. Run: alb setup ssh")
    ...
```

---

## 各传输下的实现

### A / B (adb)

- push: `adb push <local> <remote>`
- pull: `adb pull <remote> <local>`
- 全量传输，无增量
- 支持目录

### C (ssh)

- push (默认): `scp <local> user@host:<remote>`
- rsync: `rsync -avz --progress <local>/ user@host:<remote>/`
  - 增量：只传改变的文件
  - 压缩：`-z`
  - 权限：`-a` 保留
  - 速度：改动小时比 scp 快几十倍

### G (serial)

**不支持**。串口带宽太小（115200 bps ≈ 11 KB/s），传文件不实用。
返回 `TRANSPORT_NOT_SUPPORTED`，建议切 A/B/C。

### 特殊：巨型文件走不同通道

M2 智能路由：
```
< 10MB  → adb push
10M-500M → scp
> 500MB → rsync + compression
```

---

## 路径权限

### 远端常见路径及可写性

| 路径 | 默认可写 | 说明 |
|-----|:-------:|------|
| `/data/local/tmp/` | ✅ | shell user 默认可读写，调试首选 |
| `/sdcard/Download/` | ✅ | 外部存储，大文件存这 |
| `/data/data/<app>/` | ❌ | 仅对应 app uid 可写 |
| `/system/` | ❌ | ro 挂载，需 `mount -o remount,rw /system`（ask） |
| `/vendor/` | ❌ | 同上 |
| `/boot/`, `/recovery/` | ❌ | 分区级，需刷机 |

权限系统对 `/system/*` `/vendor/*` `/boot/*` 自动打 ask。

---

## 典型用例

### 推一个 apk

```bash
alb push my-test.apk /data/local/tmp/
alb shell "pm install /data/local/tmp/my-test.apk"
# 或更简洁:
alb app install my-test.apk       # 内部自动 push + pm install
```

### 拉一个日志文件

```bash
alb pull /data/tombstones/tombstone_00 ./tombstone_00
# 建议用 alb tombstone pull（更全）
```

### 增量同步 SDK 编译产物（C 独占）

```bash
alb rsync ~/aosp/out/target/product/foo/system/ /system-dev/
# 只推改过的文件，几秒搞定
```

### 批量拉日志

```bash
alb pull /data/anr/ ~/my-anrs/
```

---

## 产物路径（pull 的缺省位置）

如果 pull 没指定本地路径，alb 自动落到：

```
workspace/devices/<serial>/pulls/<remote-basename>-<ts>
```

例如 `alb pull /data/tombstones/tombstone_00` → 
`workspace/devices/abc/pulls/tombstone_00-2026-04-15T10-30-00`

---

## 权限切点

```python
# transport/adb.py
class AdbTransport:
    async def check_permissions(self, action, input):
        if action == "push":
            remote = input["remote"]
            if remote.startswith(("/system/", "/vendor/", "/product/")):
                return PermissionResult(
                    behavior="ask",
                    reason=f"Pushing to read-only system path: {remote}",
                    suggestion="mount -o remount,rw first, "
                               "or use --allow-dangerous"
                )
            if remote.startswith("/dev/block/"):
                return PermissionResult(behavior="deny", ...)
        ...
```

---

## 错误场景

| 错误码 | 场景 | suggestion |
|------|------|-----------|
| `FILE_NOT_FOUND` | 本地文件不存在 | 检查路径 |
| `REMOTE_PATH_INVALID` | 远端路径含空格/特殊字符 | 加引号 |
| `DEVICE_STORAGE_FULL` | 板子盘满 | 清 `/data/local/tmp` |
| `WORKSPACE_FULL` | 本地盘满 | `alb workspace clean` |
| `PERMISSION_DENIED` | 拦住 /system/ 等 | 见 matched_rule |
| `TIMEOUT_PUSH` | 传输超时 | 增大 timeout |

---

## 关联文件

- `src/alb/capabilities/filesync.py`
- `src/alb/cli/filesync_cli.py`
- `src/alb/mcp/tools/filesync.py`
- `tests/capabilities/test_filesync.py`
