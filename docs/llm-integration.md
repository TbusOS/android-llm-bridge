---
title: LLM / Agent 集成指南
type: design-doc
created: 2026-04-15
updated: 2026-11-10
owner: sky
tags: [llm, mcp, cli, agent, integration]
---

# LLM / Agent 集成指南

> 本文档是 android-llm-bridge 的**核心定位说明** —— 如何让大模型用好这套工具。
> 面向：Claude Code / Cursor / Codex / OpenAI Agent SDK / 自研 Agent 的开发者。

---

## 一、三种接入方式

```
                        ┌──── MCP Server ────┐        (推荐：LLM 客户端首选)
                        │                     │
  你的 LLM Agent  ──────┤──── CLI (alb)   ────┤  ────→  android-llm-bridge
                        │                     │
                        └──── Web API ────────┘        (Web UI / 外部 HTTP 集成)
```

| 方式 | 推荐场景 | 优点 | 缺点 |
|-----|---------|------|------|
| **MCP Server** | Claude Code / Cursor / Codex / 任意 MCP 兼容客户端 | 原生 tool schema、权限协商、流式 | 需要 MCP 客户端支持 |
| **CLI (`alb`)** | 脚本化 / 自研 Agent subprocess / shell 习惯 | 零接入成本、无状态、`--json` | 每次 fork 进程有开销 |
| **Web API** | Web UI / 远程 HTTP 集成 / 其他语言客户端 | 跨语言、跨机器 | 需要启动 server |

**三者共享同一套业务逻辑**（见 [`architecture.md`](./architecture.md)），选哪种都一样好用。

---

## 二、MCP Server（推荐接入方式）

### 架构

```
claude mcp add alb -- … alb-mcp(登记一次)
         ↓
Claude Code 启动 alb-mcp 子进程（stdio）
         ↓
alb-mcp 加载所有 @tool() 装饰的函数
         ↓
Claude 请求 tools/list → 返回全部 tool schema
Claude 请求 tools/call alb_logcat → 执行 capabilities/logging.py
         ↓
结果返回 → Claude 上下文
```

### 配置（各家客户端）

**Claude Code**(命令行登记;默认只对当前目录生效,`-s user` 对所有目录生效,`-s project` 写进项目根的 `.mcp.json` 给团队共用)
```bash
claude mcp add alb -e ALB_WORKSPACE=~/alb-workspace -e ALB_PROFILE=work \
  -- uv run --project /path/to/android-llm-bridge alb-mcp
```

**Cursor** （`~/.cursor/mcp.json`）
```json
{
  "mcpServers": {
    "alb": {
      "command": "uvx",
      "args": ["--from", "/path/to/android-llm-bridge", "alb-mcp"]
    }
  }
}
```

**Codex CLI**(`codex mcp add` 写进 `~/.codex/config.toml`;codex-cli 0.159.2 实测)
```bash
codex mcp add alb -- uv run --project /path/to/android-llm-bridge alb-mcp
```
写出来的配置:
```toml
[mcp_servers.alb]
command = "uv"
args = ["run", "--project", "/path/to/android-llm-bridge", "alb-mcp"]
```

更多示例见 [`/llm/mcp-config-examples/`](../llm/mcp-config-examples/)。

### 暴露的 tool 列表

下表是 `alb-mcp` 注册的全部 tool（`src/alb/mcp/server.py` `create_server`）。
参数和返回值以客户端 `tools/list` 拿到的 schema 为准。
`tests/test_docs_llm_integration.py` 检查这张表和代码注册的 tool 一致。

| Tool 名 | 作用 | 定义在 `src/alb/mcp/tools/` |
|--------|-----|-----------------|
| `alb_status` | 当前 transport、设备列表、adb server 通不通。新会话先调 | `devices.py` |
| `alb_describe` | 版本、传输方案、能力清单（来自 `src/alb/infra/registry.py`） | `devices.py` |
| `alb_describe_errors` | 查错误码目录：按错误码精确查，或按关键词筛 | `devices.py` |
| `alb_devices` | 列出连接的设备 | `devices.py` |
| `alb_shell` | 在设备上跑一条 shell 命令（走当前 transport：adb / ssh / 串口） | `shell.py` |
| `alb_logcat` | 收集 N 秒 logcat 到 workspace，返回行数 / 错误数摘要 | `logging.py` |
| `alb_dmesg` | 收集 N 秒 kernel dmesg 到 workspace | `logging.py` |
| `alb_uart_capture` | 抓 N 秒串口原始输出到 workspace（方案 G） | `logging.py` |
| `alb_uart_send` | 往串口发一段文本，不读回（如发 `\x03` 打断 u-boot 自动启动） | `logging.py` |
| `alb_uart_shell` | 在串口控制台跑一条命令，等提示符，返回输出 | `logging.py` |
| `alb_uart_watch_panic` | 抓 N 秒串口，报告有没有 kernel panic / Oops，附崩溃前后几行 | `logging.py` |
| `alb_log_search` | 在 workspace 已收集的日志里按正则搜 | `logging.py` |
| `alb_log_tail` | 读 workspace 里某个日志文件的一段（结尾或行号范围） | `logging.py` |
| `alb_push` | 把本地文件或目录推到设备 | `filesync.py` |
| `alb_pull` | 从设备拉文件或目录到本地 | `filesync.py` |
| `alb_rsync` | 目录增量同步，只支持 ssh（方案 C） | `filesync.py` |
| `alb_bugreport` | 跑 adb bugreport，zip 存进 workspace | `diagnose.py` |
| `alb_anr_pull` | 拉 /data/anr 下的 ANR 文件 | `diagnose.py` |
| `alb_tombstone_pull` | 拉 native crash tombstone | `diagnose.py` |
| `alb_devinfo` | 汇总设备信息：品牌 / 型号 / 版本 / 内核 / 电池 / 存储 | `diagnose.py` |
| `alb_reboot` | 重启：normal / recovery / bootloader / fastboot / sideload | `power.py` |
| `alb_wait_boot` | 等 `sys.boot_completed=1`，直到超时 | `power.py` |
| `alb_battery` | 电池状态：电量 / 健康 / 温度 / 电压 / 充电方式 | `power.py` |
| `alb_sleep_wake_test` | 用电源键 / 唤醒键做 N 次休眠唤醒 | `power.py` |
| `alb_app_install` | 安装 APK | `app.py` |
| `alb_app_uninstall` | 卸载应用 | `app.py` |
| `alb_app_start` | 启动应用或指定 activity | `app.py` |
| `alb_app_stop` | 强制停止应用 | `app.py` |
| `alb_app_list` | 列出已安装的包 | `app.py` |
| `alb_app_info` | 应用版本号、安装 / 更新时间、申请的权限 | `app.py` |
| `alb_app_clear_data` | 清空应用数据 | `app.py` |
| `alb_ui_screenshot` | 截一张 PNG 屏幕截图 | `ui.py` |
| `alb_ui_dump` | 当前界面的 view 层级，JSON 格式 | `ui.py` |
| `alb_info` | 结构化软硬件信息，按面板取（system / cpu / memory …，默认全部） | `info.py` |
| `alb_metrics_snapshot` | 采一次 CPU / 内存 / 温度 / IO / GPU / 电池 | `metrics.py` |
| `alb_playground_chat` | 给本地模型发一条提示词，采样参数全部可调 | `playground.py` |

---

## 三、LLM-first 设计铁律

本项目所有 tool / CLI 严格遵守以下约定，LLM 可以**信任并依赖**这些契约。

### 铁律 1 · 结构化返回

所有 tool 返回相同结构（MCP 自动转 JSON）：

```json
{
  "ok": true,
  "data": {
    "lines": 12345,
    "errors": 42,
    "warnings": 118
  },
  "error": null,
  "artifacts": [
    "/home/user/alb-workspace/devices/abc123/logs/2026-04-15T10-30-00-logcat.txt"
  ],
  "timing_ms": 60123
}
```

失败时：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "TRANSPORT_NOT_CONFIGURED",
    "message": "No active transport. Current profile has no transport set up.",
    "suggestion": "Run: alb setup adb  (or: alb setup ssh / serial)",
    "category": "transport"
  },
  "artifacts": [],
  "timing_ms": 5
}
```

**LLM 应该**：
- 先检查 `ok`
- 失败时读 `error.code` 做条件分支
- 读 `error.suggestion` 采取下一步行动
- 读 `artifacts` 获得产物路径继续处理

### 铁律 2 · 错误码可查

所有错误码集中在 [`errors.md`](./errors.md)，并通过 `alb_describe_errors` tool 可编程查询。

### 铁律 3 · 产物路径可预测

```
workspace/devices/<serial>/logs/2026-04-15T10-30-00-logcat.txt
workspace/devices/<serial>/anr/2026-04-15T10-30-00/traces.txt
workspace/devices/<serial>/bugreports/2026-04-15T10-30-00.zip
workspace/sessions/<session-id>/history.jsonl
```

规则详见 [`architecture.md#workspace-目录规范`](./architecture.md)。

### 铁律 4 · 自描述 + 可发现

```bash
alb describe            # 传输方案和能力清单（JSON）
alb status              # 当前环境状态快照
alb doctor              # 环境体检：配置、外部工具、各传输通不通
alb setup deps          # 缺哪些外部工具，各自怎么装
alb log search <正则>    # 在 workspace 已收集的日志里搜
```

`alb_describe` MCP tool 返回（`src/alb/mcp/tools/devices.py`）：
```json
{
  "version": "...",
  "transports": [
    {"name": "adb", "impl_path": "alb.transport.adb.AdbTransport",
     "methods_supported": ["A", "B"], "status": "beta", "requires": ["adb binary"],
     "description": "..."},
    ...
  ],
  "capabilities": [
    {"name": "shell", "impl_path": "alb.capabilities.shell", "cli_command": "alb shell",
     "mcp_tools": ["alb_shell"], "supported_transports": ["adb", "ssh", "serial"],
     "status": "beta", "description": "..."},
    ...
  ]
}
```

它不返回 tool 的参数 schema。tool 的完整清单和参数由 MCP 协议的 `tools/list` 给出，就是上一节那张表。

### 铁律 5 · 板子上的命令不拦截，LLM 自己把关

详见 [`permissions.md`](./permissions.md)。从 2026-10-23 起，板子上的命令（adb / 串口 / ssh）一律放行，
也不先问：`reboot bootloader`、`setprop persist.*`、`rm`、`dd` 都照常执行
（`src/alb/infra/permissions.py` `default_check` 永远返回 `allow`）。
权限检查只保护接板子的 Windows 主机：那边的 agent 只执行它操作表里登记过的操作。

所以 LLM 发破坏性命令前要自己确认目标和后果。`allow_dangerous` 参数还在，只为兼容旧调用，板子侧没有命令需要它。
`PERMISSION_DENIED` 错误码保留着，给以后的主机侧规则用，板子侧现在走不到。

### 铁律 6 · 长任务流式 + 产物落地

日志类 tool 不把日志正文塞进返回值：全文写进 workspace，返回值只有摘要和文件路径。

```python
result = await alb_logcat(duration=600)    # duration 范围 1–3600 秒，调用会阻塞到时长结束
# result["artifacts"] = [logfile_path]       ← 全文在这里
# result["data"] = {"lines": N, "errors": M, "warnings": K, ...}   ← 摘要
# 要看细节，再按需调：
await alb_log_search(pattern="FATAL|ANR")
await alb_log_tail(path=logfile_path, lines=100)
```

### 铁律 7 · 反面规则先列

`llm/CLAUDE.md` 和每个 tool 的 description 都明确列出"不应做什么"：

- ❌ 不要在未 `alb_devices` 确认前假设设备在线
- ❌ 不要随手用 `alb_shell` 跑 `rm -rf` —— alb 不拦板子上的命令，删了就没了
- ❌ 不要一次抓很久的 `alb_logcat` —— `duration` 上限 3600 秒，而且调用会一直阻塞；分几段抓，再用 `alb_log_search` 搜
- ❌ 不要假设 `/data/local/tmp` 永远可写 —— 先 `alb_shell("test -w /data/local/tmp")`

---

## 四、System Prompt 静态/动态边界（缓存优化）

`alb-mcp` 不拼系统提示词：它只把 tool 列表和一段简短的 `instructions` 交给 MCP 客户端
（`src/alb/mcp/server.py` `create_server`），系统提示词由客户端自己组。

alb 自己的 agent（`alb chat` 和 Web 界面的助手）用 `src/alb/infra/prompt_builder.py` 的
`default_agent_prompt` 组提示词，分两层：

```
┌─ 静态层（可缓存） ──────────────────────┐
│  • 角色说明                             │
│  • 安全规则                             │
│  • tool 用法约定                        │
│  • 回答语言                             │
└─────────────────────────────────────────┘
        ↑ 缓存边界：最后一个静态块
┌─ 动态层（每次请求可能不同） ────────────┐
│  • 当前设备和 transport                 │
│  • 模型名和上下文长度                   │
│  • workspace 路径                       │
│  • tool 数量                            │
└─────────────────────────────────────────┘
```

- 静态块必须全部排在动态块前面，顺序错了 `PromptBuilder` 直接抛 `PromptOrderError`。
- 发给 Anthropic 时，`Prompt.as_anthropic()` 在最后一个静态块上加 `cache_control`，静态层走 prompt caching。
- OpenAI 兼容接口按前缀自动缓存，`as_text()` 拼成一段即可，静态部分每次相同就能命中。

---

## 五、CLI 接入（非 MCP 场景）

适用：自研 Agent / subprocess 调用 / 脚本化。

### 基本用法

```bash
# 所有命令都支持 --json 输出
alb --json shell "getprop ro.build.version.sdk"
# {"ok": true, "data": {"stdout": "33\n", "exit_code": 0, ...}, ...}

alb --json logcat --duration 60 --filter "*:E"
# {"ok": true, "artifacts": [...], "data": {"lines": 1234, ...}}

alb --json devices
# 例外：直接输出设备数组 [{"serial": "...", "state": "device", ...}]，没有 ok/data 外壳
```

### Python Agent 示例

```python
import subprocess, json

def alb(*args):
    r = subprocess.run(["uv", "run", "alb", "--json", *args],
                       capture_output=True, text=True)
    return json.loads(r.stdout)

devices = alb("devices")          # 设备数组，见上
for d in devices:
    logcat = alb("logcat", "--device", d["serial"], "--duration", "30")
    if logcat["ok"] and logcat["data"]["errors"] > 0:
        print(f"Device {d['serial']}: {logcat['data']['errors']} errors, see {logcat['artifacts'][0]}")
```

---

## 六、Web API 接入

FastAPI 自带 OpenAPI schema，适合其他语言客户端。

`alb-api` 没有命令行参数，监听地址只看环境变量 `ALB_API_HOST` / `ALB_API_PORT`，
默认 `0.0.0.0:8765`（`src/alb/api/server.py` `main`）。这个 API 除 `/agent/*` 外都没有鉴权，
只在本机用时绑 `127.0.0.1`：

```bash
ALB_API_HOST=127.0.0.1 ALB_API_PORT=8765 uv run alb-api
# → http://localhost:8765/docs (Swagger UI)
# → http://localhost:8765/openapi.json (规范)
```

```bash
curl http://localhost:8765/devices
curl "http://localhost:8765/api/log/search?pattern=FATAL&max=50"
```

接口清单见 [`web-api.md`](./web-api.md)。

---

## 七、CLAUDE.md / AGENTS.md 协作

本仓库的 `llm/CLAUDE.md` 和 `llm/AGENTS.md` 是给 **Claude Code / 通用 agent** 看的工具说明。

### 在你的项目里引用

假设你在某个 Android 项目里用 alb 调试，可以在项目根的 `CLAUDE.md` 里加：

```markdown
## 调试工具
本项目使用 [android-llm-bridge](https://github.com/TbusOS/android-llm-bridge) 做设备调试。
详见 @/path/to/android-llm-bridge/llm/CLAUDE.md
```

Claude Code 会自动读取 `@` 引用的文件，你的 Agent 就知道所有 alb tool 的用法。

---

## 八、常见 LLM 工作流

### 工作流 1 · 诊断一个 app crash

```
1. alb_status              → 确认当前设备
2. alb_app_start           → 启动目标 app
3. alb_logcat(duration=120, clear_before=True) → 抓一段覆盖复现过程的 logcat
4. [这段时间里复现 crash]
5. alb_anr_pull            → 拉 ANR（如果是 ANR）
   或 alb_tombstone_pull   → 拉 native crash
6. alb_log_search(pattern="FATAL") → 定位 crash 时间点附近的日志
7. 生成报告 + 产物路径
```

### 工作流 2 · 验证一个 patch

```
1. alb_app_install(apk_path=...)        → apk 在本机，直接装
2. alb_app_start(component=...)
3. alb_logcat(duration=60, filter="MyApp:* *:S")
4. alb_log_search(pattern="expected keyword")
5. 判断通过/失败
```

### 工作流 3 · Bringup 调试（板子起不来）

```
0. （人在终端）alb setup serial      → 串口要先配好；MCP 里没有 setup 类 tool
1. alb_uart_capture(duration=30)    → 捕获启动
2. alb_log_search(pattern="error|panic|fail")
3. alb_uart_shell(cmd="printenv")   → 停在 u-boot 时跑 u-boot 命令并读回输出
4. alb_uart_send(text="setenv bootargs ...") → 只发不读
5. 迭代
```

---

## 九、最佳实践清单（给 Agent 开发者）

### ✅ Do

- **先 `alb_status` / `alb_describe`** —— 每个新会话进来先了解环境
- **用 `--json` + 结构化解析** —— 不要 grep CLI 输出文本
- **检查 `error.suggestion` 决定下一步** —— 不要盲目重试
- **按 `artifacts` 路径读产物** —— 不要尝试从 stdout 复原
- **长任务用 workspace + 搜索** —— 不要一次性回传大日志
- **破坏性命令先确认目标** —— alb 不拦板子上的命令（见铁律 5）

### ❌ Don't

- **不要硬编码设备序列号** —— 用 profile 或 `alb_devices`
- **不要把「没被拦」当成「安全」** —— 板子侧没有拦截名单
- **不要把 ANR/崩溃栈直接塞 prompt** —— 用 `alb_log_search` 按需读
- **不要假设 adb 可用就不检查** —— `alb_status` 会告诉你
- **不要在 MCP 环境里调 CLI subprocess** —— 直接用 MCP tool

---

## 十、下一步

- 看 [`permissions.md`](./permissions.md) —— 权限系统细节
- 看 [`errors.md`](./errors.md) —— 错误码参考
- 看 [`tool-writing-guide.md`](./tool-writing-guide.md) —— 如何写新 tool
- 看 [`/llm/mcp-config-examples/`](../llm/mcp-config-examples/) —— 各家 MCP 客户端配置
