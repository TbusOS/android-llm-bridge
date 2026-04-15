---
title: 错误码全集
type: reference
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [errors, reference, llm-friendly]
---

# 错误码参考

> 所有 `Result.error.code` 的完整列表。LLM 可按类别查到对应的 suggestion 做条件分支。

---

## 一、错误结构

```json
{
  "code": "TRANSPORT_NOT_CONFIGURED",
  "message": "No active transport configured",
  "suggestion": "Run: alb setup adb",
  "category": "transport",
  "details": { ... }
}
```

| 字段 | 含义 |
|-----|-----|
| `code` | 固定常量，LLM 可用于 switch/case |
| `message` | 人类可读的描述 |
| `suggestion` | LLM 下一步可执行的建议 |
| `category` | 粗分类，便于过滤 |
| `details` | 可选，结构化附加信息 |

---

## 二、分类总览

| 分类 | 错误码前缀 | 场景 |
|-----|-----------|------|
| `transport` | `TRANSPORT_*`, `ADB_*`, `SSH_*`, `SERIAL_*` | 底层通道问题 |
| `device` | `DEVICE_*` | 设备状态问题 |
| `permission` | `PERMISSION_*` | 权限系统拒绝 |
| `timeout` | `TIMEOUT_*` | 各类超时 |
| `io` | `IO_*`, `FILE_*` | 文件 / 磁盘 / 网络 IO |
| `input` | `INVALID_*` | 参数错 |
| `system` | `SYSTEM_*` | 进程 / 依赖 / 环境 |
| `capability` | `CAPABILITY_*` | 业务层错误 |

---

## 三、错误码完整表

### transport · 底层通道

| Code | 场景 | suggestion |
|------|------|-----------|
| `TRANSPORT_NOT_CONFIGURED` | 无活跃 transport | `Run: alb setup adb (or ssh / serial)` |
| `TRANSPORT_NOT_SUPPORTED` | 请求的 transport 未实现 | `Use one of: adb, ssh, serial` |
| `TRANSPORT_HEALTH_CHECK_FAILED` | health check 失败 | `Run: alb status --verbose` |
| `ADB_BINARY_NOT_FOUND` | 找不到 adb 可执行文件 | `Install platform-tools or set ALB_ADB_PATH` |
| `ADB_SERVER_UNREACHABLE` | adb server 连不上 | `Check Xshell tunnel; run: ss -tlnp \| grep 5037` |
| `ADB_VERSION_MISMATCH` | client/server 版本不匹配 | `Sync adb versions on both sides` |
| `SSH_KEY_NOT_FOUND` | SSH key 文件不存在 | `Generate key: ssh-keygen -t ed25519` |
| `SSH_AUTH_FAILED` | ssh 鉴权失败 | `Check key or run: ssh-copy-id <host>` |
| `SSH_HOST_UNREACHABLE` | ssh 主机不通 | `ping <host>; check network` |
| `SERIAL_PORT_NOT_FOUND` | 串口不存在 | `Check ser2net config and Xshell tunnel` |
| `SERIAL_PERMISSION_DENIED` | 串口访问权限 | `Add user to dialout group or sudo chmod` |
| `SERIAL_BAUD_MISMATCH` | 波特率不匹配 | `Try 115200 / 9600 / 921600` |

### device · 设备状态

| Code | 场景 | suggestion |
|------|------|-----------|
| `DEVICE_NOT_FOUND` | 指定 serial 不存在 | `Run: alb devices` |
| `DEVICE_UNAUTHORIZED` | USB 未授权 | `Accept "Allow USB debugging" on device screen` |
| `DEVICE_OFFLINE` | 设备离线 | `Reconnect USB or re-run alb_devices` |
| `DEVICE_BOOTING` | 设备启动中 | `Wait 30s and retry; or alb_power_wait_boot` |
| `DEVICE_NO_ROOT` | 需要 root 但设备不允许 | `adb root or accept root prompt on device` |
| `DEVICE_BUSY` | 其他 session 占用中 | `adb kill-server or wait for other session` |

### permission · 权限系统

| Code | 场景 | suggestion |
|------|------|-----------|
| `PERMISSION_DENIED` | 命令被黑名单拦截 | 见 `error.details.matched_rule`；调整 scope 或配置 allow |
| `PERMISSION_ASK_TIMEOUT` | 需要询问但无人响应 | `Set ask_on_ambiguous=false or use --confirm` |
| `PERMISSION_SCOPE_TOO_BROAD` | 命令范围过大 | `Scope to specific path/process/partition` |

### timeout

| Code | 场景 | suggestion |
|------|------|-----------|
| `TIMEOUT_SHELL` | shell 命令超时 | `Increase timeout param or use stream_read` |
| `TIMEOUT_PUSH` / `TIMEOUT_PULL` | 文件传输超时 | `Check device / network; retry with --resume` |
| `TIMEOUT_CONNECT` | 连接超时 | `Check tunnel / firewall` |
| `TIMEOUT_BOOT` | 等设备启动超时 | `Device may have kernel panic; check UART` |

### io · 文件 / 磁盘

| Code | 场景 | suggestion |
|------|------|-----------|
| `FILE_NOT_FOUND` | 本地文件不存在 | 检查路径 |
| `FILE_NOT_READABLE` | 本地文件不可读 | 检查权限 |
| `WORKSPACE_FULL` | 工作目录磁盘满 | `Run: alb workspace clean --older-than 7d` |
| `WORKSPACE_WRITE_DENIED` | 工作目录不可写 | 检查 ALB_WORKSPACE 配置 |
| `REMOTE_PATH_INVALID` | 远端路径不合法 | 检查是否包含空格 / 特殊字符 |
| `DEVICE_STORAGE_FULL` | 设备存储满 | 清理设备 `/data/local/tmp` |

### input · 参数

| Code | 场景 | suggestion |
|------|------|-----------|
| `INVALID_DEVICE_SERIAL` | 序列号格式错 | 使用 `alb_devices` 返回的值 |
| `INVALID_FILTER` | logcat filter 语法错 | 参考 `docs/capabilities/logging.md#filter` |
| `INVALID_DURATION` | duration 超范围 | 1 ≤ duration ≤ 86400 |
| `INVALID_PORT` | 端口号不合法 | 1-65535 |
| `INVALID_PROFILE` | profile 不存在 | `Run: alb profile list` |

### system · 环境

| Code | 场景 | suggestion |
|------|------|-----------|
| `SYSTEM_DEPENDENCY_MISSING` | 缺依赖（rsync/socat/picocom） | 根据 message 安装对应工具 |
| `SYSTEM_PYTHON_VERSION` | Python 版本过低 | `Upgrade to Python 3.11+` |
| `SYSTEM_UV_NOT_FOUND` | uv 未安装 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `CONFIG_INVALID` | config.toml 格式错 | 查看 `error.details.line` 修改 |
| `CONFIG_NOT_FOUND` | 找不到配置文件 | `Run: alb init` |

### capability · 业务层

| Code | 场景 | suggestion |
|------|------|-----------|
| `NO_ANR_FOUND` | 触发了 anr_pull 但设备无 ANR | 正常，等下次 crash |
| `NO_TOMBSTONE_FOUND` | 无 tombstone | 同上 |
| `LOGCAT_BUFFER_OVERFLOW` | logcat 缓冲溢出（采集太慢） | 降 filter 粒度或提高 buffer |
| `APP_NOT_INSTALLED` | 目标 apk 未安装 | `alb_app_install` 先 |
| `APP_ALREADY_RUNNING` | 启动已在运行的 app | 先 `alb_app_stop` 或 `--force` |
| `PACKAGE_NAME_INVALID` | 包名格式错 | 应类似 `com.example.app` |
| `BENCHMARK_UNAVAILABLE` | 跑分 app 未装（M2） | `alb_app_install <benchmark.apk>` |

---

## 四、LLM 处理模式

### 模式 1 · 按 code 直接分支

```python
result = await alb_logcat(...)
if not result["ok"]:
    code = result["error"]["code"]
    if code == "TRANSPORT_NOT_CONFIGURED":
        await alb_setup("adb")        # 自动修复
        result = await alb_logcat(...) # 重试
    elif code == "DEVICE_UNAUTHORIZED":
        return "请在设备上点击'允许 USB 调试'"
    elif code.startswith("TIMEOUT_"):
        # 重试一次，翻倍超时
        ...
```

### 模式 2 · 按 category 批量处理

```python
if result["error"]["category"] == "permission":
    # 用户决策 / 向用户请求
    ...
elif result["error"]["category"] == "transport":
    # 运行 diagnostic
    await alb_status(verbose=True)
```

### 模式 3 · 无脑执行 suggestion

```python
# 某些 suggestion 是具体的命令，直接跑
sugg = result["error"]["suggestion"]
if sugg.startswith("Run: "):
    # 提示用户（不要自动跑，suggestion 仍需要人类判断）
    print(f"建议: {sugg}")
```

---

## 五、错误码编程访问

```bash
alb describe-errors                    # 输出全表 JSON
alb describe-errors --code ADB_SERVER_UNREACHABLE  # 单个查询
```

MCP tool：

```python
@tool()
async def alb_describe_errors(code: str | None = None) -> dict:
    """Query the error code catalog. LLM can call this when uncertain about a received error."""
```

---

## 六、贡献新错误码

1. 在 `src/alb/infra/errors.py` 的 `ERROR_CODES` dict 加条目
2. 本文档同步更新
3. 在抛出的位置用 `fail(code="YOUR_NEW_CODE", ...)`
4. PR 里说明场景 + suggestion 写法
