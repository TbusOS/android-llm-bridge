---
title: 架构设计
type: design-doc
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [architecture, design, transport, capability]
---

# 架构设计

> 描述 android-llm-bridge 的分层、模块、接口、数据流、状态管理。读完这篇你应该能写出任意新能力或新传输的实现。

---

## 一、分层总览

```
┌──────────────────────────────────────────────────────────┐
│  L1 接入层（Interface layer）                             │
│  ┌──────────┬──────────┬──────────┬──────────┐          │
│  │   CLI    │   MCP    │ Web API  │  Web UI  │          │
│  │ (typer)  │  Server  │(FastAPI) │ (future) │          │
│  └────┬─────┴─────┬────┴────┬─────┴────┬─────┘          │
│       └───────────┴─────────┴──────────┘                 │
│                        ↓                                  │
├──────────────────────────────────────────────────────────┤
│  L2 业务能力层（Capabilities layer）                      │
│  纯 Python 函数，三层接入共享调用                         │
│  shell │ logging │ filesync │ diagnose │ power │ app ... │
├──────────────────────────────────────────────────────────┤
│  L3 传输抽象层（Transport layer）  ⭐ 架构核心            │
│  统一接口                                                 │
│    shell(cmd) → Result                                   │
│    stream_read(source) → AsyncIterator                   │
│    push / pull / forward / reboot / check_permissions    │
│  ┌──────────┬──────────┬──────────┬──────────┐          │
│  │ AdbT.    │ SshT.    │ SerialT. │ HybridT. │          │
│  │ (A/B)    │ (C/D/F)  │  (G)     │  router  │          │
│  └──────────┴──────────┴──────────┴──────────┘          │
├──────────────────────────────────────────────────────────┤
│  L4 驱动层（Drivers layer）                               │
│  adb · ssh · scp · rsync · pyserial · socat · 隧道管理    │
├──────────────────────────────────────────────────────────┤
│  L5 基础设施（Infrastructure layer）                      │
│  config │ workspace │ profile │ permissions │ errors     │
│  event-bus │ prompt-builder │ memory │ registry          │
└──────────────────────────────────────────────────────────┘
```

### 为啥这么分层

1. **L1 壳化 → L2 业务集中** ：三层接入不重复实现业务，改一次三层同步生效
2. **L2 和 L3 解耦** ：能力不关心底层是 adb/ssh/serial
3. **L3 抽象层是价值核心** ：新传输只实现 base.py，所有能力自动可用
4. **L4 只做驱动** ：wrap 二进制命令 + 连接池 + 重试，不含业务
5. **L5 横切基础** ：跨层使用，权限 / 错误 / 事件 / 配置

---

## 二、L3 传输抽象层（核心）

### 接口定义（`src/alb/transport/base.py`）

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from alb.infra.errors import ErrorCode
from alb.infra.permissions import PermissionResult


@dataclass(frozen=True)
class ShellResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    artifacts: list[Path]       # 有时会落地到 workspace
    error: "ErrorInfo | None"   # ok=False 时必填


class Transport(ABC):
    """所有传输实现的统一契约。

    关键约束：
    - 所有方法异步（asyncio），避免长任务阻塞
    - 每个方法返回结构化 Result，不抛未包装异常
    - shell 与 stream_read 分别实现 —— 读写路径隔离，避免 logcat 流阻塞命令响应
    - 失败时必须填 error.code，参考 errors.md 错误码表
    """

    name: str                         # "adb" / "ssh" / "serial"
    supports_boot_log: bool = False   # 是否能看 boot log（只有 serial=True）
    supports_recovery: bool = False   # 是否能在 recovery 模式下工作（只有 adb=True）

    # ── 基本操作 ──────────────────────────────────
    @abstractmethod
    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult: ...

    @abstractmethod
    async def stream_read(self, source: str, **kwargs) -> AsyncIterator[bytes]:
        """流式读取 —— logcat/dmesg/uart 都走这个。
        source: "logcat" / "dmesg" / "kmsg" / "uart"
        """

    # ── 文件传输 ──────────────────────────────────
    @abstractmethod
    async def push(self, local: Path, remote: str) -> ShellResult: ...

    @abstractmethod
    async def pull(self, remote: str, local: Path) -> ShellResult: ...

    # ── 端口转发（可选，serial 不支持） ─────────────
    async def forward(self, local_port: int, remote_port: int) -> ShellResult:
        raise NotImplementedError

    # ── 设备控制 ──────────────────────────────────
    @abstractmethod
    async def reboot(self, mode: str = "normal") -> ShellResult:
        """mode: normal / recovery / bootloader / fastboot / sideload"""

    # ── 权限 hook（tool 自检） ─────────────────────
    async def check_permissions(self, action: str, input: dict) -> PermissionResult:
        """能力层调用前会走这里。默认 allow，子类可覆盖危险操作规则。"""
        from alb.infra.permissions import default_check
        return await default_check(self.name, action, input)

    # ── 健康检查 ──────────────────────────────────
    @abstractmethod
    async def health(self) -> dict: ...
```

### 四种实现各自的侧重

| 实现 | 关键点 |
|------|------|
| `AdbTransport` | wrap `adb` 二进制；透明使用 `ADB_SERVER_SOCKET`（兼容 Xshell 反向隧道场景 A）；支持 `adb connect <host:port>`（场景 B） |
| `SshTransport` | 基于 `asyncssh`；带连接池、重用 session；push/pull 优先用 rsync，降级 scp |
| `SerialTransport` | 基于 `pyserial-asyncio`；支持 TCP 串口（`socat`/`ser2net` 转换后）；stream_read 是主要方法；shell 用 "发字符串 + readline 期望提示符" 实现 |
| `HybridTransport` | 智能路由，按命令类型选最佳通道（见下） |

### HybridTransport 智能路由（借鉴 Claude Code）

```python
class HybridTransport(Transport):
    """组合多个 Transport，按能力/命令类型路由到最合适的一个。

    例子：
    - logcat 流式读 → 优先 AdbTransport（有原生 logcat 协议）
    - rsync 同步目录 → 优先 SshTransport
    - 进 u-boot → 必须 SerialTransport
    - reboot bootloader → 必须 AdbTransport
    """
    def __init__(self, primary: Transport, alternates: list[Transport]):
        self.primary = primary
        self.alternates = alternates

    async def shell(self, cmd, *, timeout=30):
        t = self._pick_for("shell", cmd)
        return await t.shell(cmd, timeout=timeout)

    def _pick_for(self, op: str, hint: str) -> Transport:
        # 路由规则见 transport/routing.py
        ...
```

### 数据流：logcat 流式读

```
LLM 调用 alb_logcat(duration=60, filter="*:E")
    ↓
capabilities/logging.py:collect_logcat()
    ↓
transport.stream_read("logcat", filter="*:E")  ← 返回 AsyncIterator
    ↓ 逐 chunk
event-bus 广播 "logcat.line"     ─→ Web UI 实时显示
                                 ─→ CLI 实时打印
                                 ─→ 文件 sink 写 workspace
    ↓ 60s 后
LogcatResult(ok=True, artifacts=[Path("workspace/.../logcat.txt")])
```

---

## 三、L2 业务能力层

### 能力模块结构

```
src/alb/capabilities/
├── __init__.py          # 能力注册表（元数据驱动）
├── base.py              # Capability ABC（可选，M2 补）
├── shell.py
├── logging.py
├── filesync.py
├── diagnose.py
├── power.py
└── app.py
```

### 典型能力函数（示例）

```python
# src/alb/capabilities/logging.py

from alb.infra.result import Result, ok, fail
from alb.infra.workspace import workspace_path
from alb.transport import Transport

async def collect_logcat(
    transport: Transport,
    duration: int = 60,
    filter: str | None = None,
    tag: list[str] = [],
    clear_before: bool = False,
) -> Result[LogcatResult]:
    """
    收集 logcat 日志到 workspace。

    LLM 提示：
      - 长时间收集（>300s）请用 stream=True，否则会阻塞。
      - 返回 result.artifacts[0] 是产物文件路径，可 `alb log show` 读取。
      - filter 例子："*:E"（仅 Error 以上）/ "ActivityManager:I *:S"（仅 AM 的 Info）

    参数:
      duration: 秒数，默认 60
      filter:   logcat -s 风格的过滤器
      tag:      要保留的 tag 列表（会合成到 filter）
      clear_before: 开始前先 logcat -c 清缓冲

    Returns:
      Result(ok=True, data=LogcatResult(...), artifacts=[logfile])
    """
    # 权限检查
    perm = await transport.check_permissions("logging.logcat",
                                              {"duration": duration})
    if perm.behavior == "deny":
        return fail(code="PERMISSION_DENIED", reason=perm.reason)

    # 业务逻辑
    artifact = workspace_path("logs", f"logcat-{now()}.txt")
    async with open(artifact, "wb") as f:
        async for chunk in transport.stream_read("logcat", filter=filter,
                                                   clear=clear_before):
            f.write(chunk)
            if elapsed() > duration:
                break

    return ok(data=LogcatResult(lines=..., errors=..., warnings=...),
              artifacts=[artifact])
```

### 三层壳如何装饰

```python
# src/alb/cli/logging_cli.py
import typer
from alb.capabilities.logging import collect_logcat

app = typer.Typer()

@app.command("logcat")
def cli_logcat(duration: int = 60, filter: str | None = None):
    """Collect logcat to workspace."""
    result = run_async(collect_logcat(get_transport(), duration, filter))
    print_result(result)          # --json 或 人类可读


# src/alb/mcp/tools/logging.py
from mcp.server import tool
from alb.capabilities.logging import collect_logcat

@tool()
async def alb_logcat(duration: int = 60, filter: str | None = None) -> dict:
    """Collect logcat to workspace. Use for investigating app crashes, ANRs, system errors."""
    result = await collect_logcat(get_transport(), duration, filter)
    return result.to_dict()


# src/alb/api/routers/logging.py
from fastapi import APIRouter
from alb.capabilities.logging import collect_logcat

router = APIRouter()

@router.post("/logging/logcat")
async def api_logcat(req: LogcatRequest):
    return await collect_logcat(get_transport(), **req.dict())
```

**三层只是壳，业务逻辑只写一次。**

---

## 四、L5 基础设施

### workspace 目录规范

```
workspace/
├── devices/
│   └── <serial>/              # 按设备序列号隔离
│       ├── meta.json          # 设备信息（型号 / 版本 / 首次连接时间）
│       ├── logs/
│       │   ├── 2026-04-15T10-30-00-logcat.txt
│       │   ├── 2026-04-15T10-35-00-dmesg.txt
│       │   └── 2026-04-15T10-40-00-uart.log
│       ├── anr/
│       │   └── 2026-04-15T10-45-00/
│       │       ├── traces.txt
│       │       └── context.json
│       ├── tombstones/
│       ├── bugreports/
│       ├── perf/              # M2
│       │   └── 2026-04-15T11-00-00/{cpu.csv,mem.csv,...}
│       └── snapshots/         # undo/redo 用（M2）
│
├── sessions/
│   └── <session-id>/          # 一次调试会话
│       ├── history.jsonl      # tool 调用历史（Evo-Memory 用）
│       └── summary.md         # M3：会话自动摘要
│
├── archive/                   # 冷归档（gzip）
│   └── <year-month>/
│
├── cache/                     # 临时 / 可重建
│   └── tunnels/               # SSH 隧道状态
│
└── .alb-state                 # 运行时状态（当前 transport / 当前 device）
```

**规则**：
1. LLM 产生的所有"值得保留"的数据 → `workspace/`
2. 路径可预测（序列号 → 类别 → ISO 时间戳）
3. 热路径在 `devices/<serial>/{logs,anr,...}`，冷数据归档到 `archive/<year-month>/`
4. **决不使用 `/tmp`**（全局 CLAUDE.md 规则）

### config / profile

两级：

- **config**（`~/.config/alb/config.toml` 或 `$ALB_CONFIG`）：全局默认
- **profile**（`<workspace>/profiles/<name>.toml` 或 CLI `--profile`）：多设备 / 多场景切换

```toml
# ~/.config/alb/config.toml
default_profile = "work"

[workspace]
root = "~/alb-workspace"

[transport.adb]
server_socket = "tcp:localhost:5037"       # 场景 A 专用
bin_path = "adb"

[transport.ssh]
default_user = "root"
key_path = "~/.ssh/id_ed25519"

[permissions]
mode = "strict"  # strict / standard / permissive
ask_on_ambiguous = true
```

```toml
# workspace/profiles/lab-devices.toml
[profile]
name = "lab-devices"
primary_transport = "adb"

[[devices]]
serial = "abc123"
alias = "lab-a"
transport = "adb"

[[devices]]
serial = "def456"
alias = "lab-b"
transport = "ssh"
ssh_host = "192.168.1.42"
```

### 权限系统

详见 [`permissions.md`](./permissions.md)。核心要点：

```
  用户输入 / LLM 调用
         ↓
   infra/permissions.py
    ├─ 读取黑名单 pattern
    ├─ 查多层配置（defaults < config < profile < CLI flags < session）
    ├─ 调用 transport.check_permissions(tool 级自检)
    └─ 返回 PermissionResult{behavior: allow | ask | deny, reason, suggestion}
         ↓
   capability 根据结果决定执行 / 询问 / 拒绝
```

### 错误码 / Result

详见 [`errors.md`](./errors.md)。

```python
# src/alb/infra/result.py
@dataclass(frozen=True)
class ErrorInfo:
    code: str            # 错误码，如 "TRANSPORT_NOT_CONFIGURED"
    message: str         # 人类可读
    suggestion: str      # LLM 可行动的建议，如 "run: alb setup adb"
    category: str        # "transport" | "permission" | "timeout" | ...

@dataclass(frozen=True)
class Result[T]:
    ok: bool
    data: T | None
    error: ErrorInfo | None
    artifacts: list[Path]
    timing_ms: int

    def to_dict(self) -> dict: ...
```

### 事件总线（M2）

```python
# src/alb/infra/events.py
class EventBus:
    """进程内 pub/sub，用于流式数据同时推给多个订阅者。

    订阅者：
    - CLI 实时打印
    - Web UI WebSocket 推送
    - 文件 sink 写 workspace
    - history.jsonl 审计

    事件类型：
    - logcat.line / dmesg.line / uart.line
    - device.connected / device.disconnected
    - tool.invoked / tool.failed
    - permission.denied
    """
```

### Registry（元数据驱动）

借鉴 claude-code2：

```python
# src/alb/infra/registry.py
@dataclass(frozen=True)
class TransportSpec:
    name: str
    impl_path: str              # "alb.transport.adb.AdbTransport"
    methods_supported: list[str]  # ["A", "B"]
    status: str                 # "stable" | "beta" | "planned"
    requires: list[str]         # ["adb binary"]

TRANSPORTS = [
    TransportSpec("adb", "alb.transport.adb.AdbTransport",
                  methods_supported=["A", "B"], status="stable",
                  requires=["adb binary"]),
    TransportSpec("ssh", "alb.transport.ssh.SshTransport",
                  methods_supported=["C", "D", "F"], status="stable",
                  requires=["ssh client"]),
    TransportSpec("serial", "alb.transport.serial.SerialTransport",
                  methods_supported=["G"], status="stable",
                  requires=["pyserial"]),
]

# 同样的模式：
CAPABILITIES = [...]
```

效果：
- `alb describe` 输出全部 schema 供 LLM 预读
- `alb workspace inspect` 自动生成实现进度矩阵
- 文档里的方案矩阵 / 能力矩阵从这生成，避免手工维护偏差

---

## 五、启动流程

```
uv run alb shell "ls /sdcard"
    ↓
1. entry point (pyproject.toml → alb = "alb.cli:main")
2. cli/main.py:
   - 加载 config（~/.config/alb/config.toml）
   - 加载 profile（默认 or --profile）
   - 解析 subcommand: shell
3. 根据 profile 选择 transport:
   - adb → AdbTransport
   - ssh → SshTransport
   - serial → SerialTransport
4. capabilities/shell.py:execute() 被调用
5. transport.check_permissions("shell.execute", {"cmd": "ls /sdcard"})
   - 若 deny → 返回 PermissionDenied Result
6. transport.shell("ls /sdcard")
7. 包装成 Result → 打印
```

MCP 启动：

```
uv run alb-mcp
    ↓
1. entry point (alb-mcp = "alb.mcp.server:main")
2. mcp/server.py 注册所有 @tool() 装饰的函数
3. stdio 模式监听请求
4. 客户端（Claude Code）调用 → 执行同样的 capabilities/*.py
```

---

## 六、可扩展性

### 加一个新传输（例如 F · frp 云中转）

1. 新建 `src/alb/transport/frp.py`，实现 `Transport` ABC
2. 在 `registry.py` 的 `TRANSPORTS` 注册
3. 写 `docs/methods/06-frp-cloud.md`（方案说明）
4. 写 `scripts/setup-method-frp.sh`（引导式配置）
5. 所有 capabilities 立即可用，无需改任何业务代码

### 加一个新能力（例如 perf · 性能采集）

1. 新建 `src/alb/capabilities/perf.py`，写业务函数
2. 在 `cli/perf_cli.py` / `mcp/tools/perf.py` / `api/routers/perf.py` 各加一个薄壳
3. 在 `CAPABILITIES` registry 注册
4. 写 `docs/capabilities/perf.md`
5. 单元测试 `tests/capabilities/test_perf.py`

### 加一个新接入层（例如 Slack Bot）

1. 新建 `src/alb/bots/slack.py`
2. 复用同一套 `capabilities/*.py`
3. 业务代码零改动

---

## 七、设计检查清单

每加一个新 transport / capability / interface，对照：

- [ ] 是否实现了统一 Result 返回？
- [ ] 错误是否带 code 且在 `errors.md` 可查？
- [ ] 是否走了 permission 检查？
- [ ] 产物是否落到 `workspace/` 规范路径？
- [ ] `docs/` 下是否有对应手册？
- [ ] `registry.py` 是否注册？
- [ ] LLM 友好性：description 是否说了"When to use" + 给了 example？
- [ ] 单元测试覆盖关键分支？

---

## 八、下一步

- 想了解**为什么这样分层** → [`design-decisions.md`](./design-decisions.md)
- 想了解**LLM 如何使用这架构** → [`llm-integration.md`](./llm-integration.md)
- 想**开始开发** → [`contributing.md`](./contributing.md)
