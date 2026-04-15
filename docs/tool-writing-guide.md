---
title: Tool 编写指南（LLM 友好）
type: contribution-guide
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [contributing, tool, llm-friendly]
---

# Tool 编写指南

> 给贡献者看：如何在 android-llm-bridge 里加一个**对 LLM 友好**的新能力 / 新 tool。遵循本指南能让你的 tool 被大模型用得好。

---

## 一、LLM 友好的 tool 长啥样

**反面例子**（人用 OK，LLM 难用）：

```python
@app.command()
def logcat(d: int = 60, f: str = None):
    """Get logs."""
    r = subprocess.run(["adb", "logcat", "-d"], ...)
    click.echo(r.stdout)
```

问题：
- 名字不自描述（`logcat` 没说是采集还是查看）
- description 是 "Get logs" ——LLM 不知道什么时候该用
- 参数名 `d`/`f` 不自明
- 输出是原始 stdout，LLM 无法解析
- 没错误处理，设备没连就崩

**正面例子**：

```python
async def collect_logcat(
    duration: int = 60,
    filter: str | None = None,
    tag: list[str] = [],
    clear_before: bool = False,
) -> Result[LogcatSummary]:
    """
    Collect Android logcat output to workspace file.

    When to use:
      - Investigating app crashes, ANRs, system errors
      - Long monitoring (use duration + filter to avoid context overflow)
      - Reproducing bugs with targeted tags

    Examples:
      collect_logcat(duration=30, filter="*:E")                # Only errors, 30s
      collect_logcat(duration=60, tag=["ActivityManager"])     # Only AM logs
      collect_logcat(duration=300, clear_before=True)          # Clean slate 5min

    LLM notes:
      - Returns summary (count of lines/errors/warnings), not full log
      - Full log is in result.artifacts[0], use alb_log_search or alb_log_tail
      - For duration > 300s, prefer alb_logcat_watch (background)

    Args:
        duration: Capture duration in seconds. Range: 1-3600.
        filter: logcat filter expression, e.g. "*:E" or "ActivityManager:I *:S"
        tag: Tag names to keep (combined into filter)
        clear_before: Run 'logcat -c' before collecting to start fresh

    Returns:
        Result with LogcatSummary and artifact path to full log file.
    """
    ...
```

---

## 二、命名规范

### 函数 / tool 名

| 规则 | 例子 |
|-----|------|
| **动词开头，语义完整** | `collect_logcat` ✅ / `logcat` ❌ |
| **capability + action** | `app_install` / `filesync_push` / `power_reboot` |
| **async 优先** | 所有 capability 函数 `async def` |
| **tool 名加 `alb_` 前缀** | MCP tool: `alb_collect_logcat` 或简化 `alb_logcat` |

### 参数名

| 规则 | 例子 |
|-----|------|
| **全称胜于缩写** | `duration` ✅ / `d` ❌ |
| **布尔名表达问题** | `clear_before=False` ✅ / `clear=False` ❌（不明确何时清） |
| **path 类参数显式类型** | `local: Path` / `remote_path: str` |
| **list 类参数复数** | `tags: list[str]` ✅ / `tag: list[str]` ❌ |

### tool description

写 tool docstring 时按顺序：

```
1. 一句话说这个 tool 做什么（简洁 but 完整）

When to use:
   - 场景 1
   - 场景 2

When NOT to use / alternatives:
   - 场景（用其他 tool）

Examples:
   code-ish examples

LLM notes:     （关键：告诉 LLM 陷阱）
   - 返回值规模
   - 超时风险
   - 推荐换其他 tool 的条件

Args:
   param1: 描述，包含范围
   param2: ...

Returns:
   Result 里包了什么
```

---

## 三、参数设计

### 给明确范围 / 默认值

```python
def collect_logcat(
    duration: int = 60,           # 默认 1 分钟，避免意外长跑
    filter: str | None = None,    # None 意味着无过滤
):
```

Pydantic 验证（FastAPI）：
```python
class LogcatRequest(BaseModel):
    duration: int = Field(60, ge=1, le=3600)
    filter: str | None = Field(None, max_length=1024)
```

### 禁止模糊语义

```python
# ❌ 不好
def rm(path: str, force: bool = False): ...  # force 什么意思不明

# ✅ 好
def rm(path: str,
       recursive: bool = False,    # -r
       force_missing: bool = False,# -f（不存在不报错）
       ): ...
```

### 布尔参数避免双重否定

```python
# ❌
def logcat(no_clear: bool = True): ...

# ✅
def logcat(clear_before: bool = False): ...
```

---

## 四、返回值设计

### 永远返回 Result

```python
from alb.infra.result import Result, ok, fail

async def collect_logcat(...) -> Result[LogcatSummary]:
    try:
        # ... 业务
        return ok(
            data=LogcatSummary(lines=n, errors=e, warnings=w),
            artifacts=[logfile]
        )
    except DeviceUnauthorized:
        return fail(
            code="DEVICE_UNAUTHORIZED",
            message="Device rejected USB debugging",
            suggestion="Accept 'Allow USB debugging' on device screen"
        )
```

### data 里放"摘要"，不是"原始数据"

```python
# ❌ 会把 context 塞爆
@dataclass
class LogcatResult:
    full_log: str   # 几 MB 文本

# ✅
@dataclass
class LogcatSummary:
    lines: int
    errors: int
    warnings: int
    top_tags: list[tuple[str, int]]   # ("ActivityManager", 132)
    first_line_ts: str
    last_line_ts: str
    # 全文在 artifacts[0] 路径里
```

### artifacts 是"值得保留且较大"的产物

- 日志文件、bugreport.zip、性能 CSV、截图 PNG
- 不是：临时中间文件、`/tmp` 里的东西

---

## 五、错误处理

### 永不裸抛异常

```python
# ❌
async def push(local, remote):
    await transport.push(local, remote)   # 失败时抛 RuntimeError
    return True

# ✅
async def push(local, remote) -> Result[None]:
    if not local.exists():
        return fail(code="FILE_NOT_FOUND", ...)
    try:
        await transport.push(local, remote)
    except AdbError as e:
        return fail(code="ADB_PUSH_FAILED", message=str(e), ...)
    return ok()
```

### error.suggestion 必填且可行动

| ❌ | ✅ |
|----|----|
| "Network error" | "Check Xshell tunnel status; run: ss -tlnp \| grep 5037" |
| "Failed" | "Device offline; run: alb_devices to refresh" |
| "Permission denied" | "Scope to specific path, e.g. /sdcard/Download/" |

### 使用错误码表

所有 `code` 都必须在 [`errors.md`](./errors.md) 里登记。加新 code 时：
1. `src/alb/infra/errors.py` 添加到 `ERROR_CODES`
2. `docs/errors.md` 同步
3. 给出 suggestion 范例

---

## 六、权限切点

### 每个"改变设备状态"的操作都过 check_permissions

```python
async def push(local: Path, remote: str) -> Result[None]:
    # 权限检查
    perm = await transport.check_permissions(
        "filesync.push",
        {"local": str(local), "remote": remote}
    )
    if perm.behavior == "deny":
        return fail(code="PERMISSION_DENIED",
                    message=perm.reason,
                    suggestion=perm.suggestion,
                    matched_rule=perm.matched_rule)
    if perm.behavior == "ask":
        # CLI: 提示; MCP: 返回 ask 响应让 client 处理
        ...
    # ... 实际操作
```

### 只读操作不需要（视情况）

```python
# 读取只读的操作：logcat / dmesg / devices / battery 查询
# 可以跳过权限检查，或只做基础速率限制
```

---

## 七、产物路径规范

使用 `workspace_path()` 辅助函数：

```python
from alb.infra.workspace import workspace_path, iso_timestamp

logfile = workspace_path(
    "logs",                          # 分类子目录
    f"logcat-{iso_timestamp()}.txt", # ISO 时间戳命名
    device=transport.serial,         # 自动前缀 devices/<serial>/
)
# → workspace/devices/abc123/logs/2026-04-15T10-30-00-logcat.txt
```

**不允许**：
- `/tmp/xxx` —— 违反全局规则
- `./xxx` —— 污染 cwd
- 硬编码绝对路径

---

## 八、三层壳怎么写

### CLI

```python
# src/alb/cli/logging_cli.py
import typer
from alb.capabilities.logging import collect_logcat
from alb.cli.common import run_async, print_result, json_mode

app = typer.Typer(help="Logging commands")

@app.command("logcat")
def cli_logcat(
    duration: int = typer.Option(60, "-d", "--duration"),
    filter: str = typer.Option(None, "-f", "--filter"),
):
    """Collect logcat to workspace."""
    result = run_async(collect_logcat(
        get_transport(),
        duration=duration,
        filter=filter,
    ))
    print_result(result)     # 自动处理 --json
```

### MCP

```python
# src/alb/mcp/tools/logging.py
from mcp.server.fastmcp import FastMCP
from alb.capabilities.logging import collect_logcat

def register(mcp: FastMCP):
    @mcp.tool()
    async def alb_logcat(
        duration: int = 60,
        filter: str | None = None,
    ) -> dict:
        """Collect Android logcat.

        When to use: investigating crashes, ANRs.
        LLM notes: For > 300s use alb_logcat_watch instead.

        Args:
          duration: 1-3600 seconds
          filter:   logcat filter like "*:E"
        """
        r = await collect_logcat(get_transport(), duration, filter)
        return r.to_dict()
```

### API

```python
# src/alb/api/routers/logging.py
from fastapi import APIRouter
from alb.capabilities.logging import collect_logcat

router = APIRouter(prefix="/logging")

@router.post("/logcat")
async def api_logcat(req: LogcatRequest):
    r = await collect_logcat(get_transport(), **req.dict())
    return r.to_dict()
```

---

## 九、测试要求

每个新 capability 至少：

```python
# tests/capabilities/test_logging.py

@pytest.mark.asyncio
async def test_collect_logcat_happy_path(mock_transport):
    mock_transport.stream_read.return_value = async_iter([b"line1\n", b"line2\n"])
    result = await collect_logcat(mock_transport, duration=1)
    assert result.ok
    assert result.data.lines == 2
    assert len(result.artifacts) == 1

@pytest.mark.asyncio
async def test_collect_logcat_permission_denied(mock_transport):
    mock_transport.check_permissions.return_value = PermissionResult(behavior="deny", reason="...")
    result = await collect_logcat(mock_transport, duration=60)
    assert not result.ok
    assert result.error.code == "PERMISSION_DENIED"

@pytest.mark.asyncio
async def test_collect_logcat_timeout(mock_transport):
    ...
```

---

## 十、检查清单（PR 合并前）

- [ ] 函数名 verb-first，参数名全称
- [ ] Docstring 含 "When to use" / Examples / LLM notes / Args / Returns
- [ ] 返回 `Result[T]`，不裸抛
- [ ] `ok` / `fail` 辅助函数
- [ ] 错误码在 `errors.md` 可查
- [ ] 权限切点（改状态的操作）
- [ ] 产物用 `workspace_path()`
- [ ] 三层壳（CLI / MCP / API）完整
- [ ] registry 注册
- [ ] 单元测试（happy / permission / timeout / error）
- [ ] `docs/capabilities/<name>.md` 更新

---

## 十一、好样本参考

看这几个已实现的 capability 做参考：

- `src/alb/capabilities/shell.py` —— 最基础
- `src/alb/capabilities/logging.py` —— 流式采集 + 产物管理
- `src/alb/capabilities/filesync.py` —— 传输 + 权限切点
- `src/alb/capabilities/diagnose.py` —— 多步骤组合操作

（实现在 M1 陆续补齐。）
