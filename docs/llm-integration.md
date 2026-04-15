---
title: LLM / Agent 集成指南
type: design-doc
created: 2026-04-15
updated: 2026-04-15
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
~/.claude/mcp-settings.json
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

**Claude Code** （`~/.claude/mcp-settings.json`）
```json
{
  "mcpServers": {
    "alb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/android-llm-bridge", "alb-mcp"],
      "env": {
        "ALB_WORKSPACE": "~/alb-workspace",
        "ALB_PROFILE": "work"
      }
    }
  }
}
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

**Codex CLI** （`~/.codex/config.json`）
```json
{
  "mcp_servers": [
    {
      "name": "alb",
      "command": "uv",
      "args": ["run", "alb-mcp"],
      "cwd": "/path/to/android-llm-bridge"
    }
  ]
}
```

更多示例见 [`/llm/mcp-config-examples/`](../llm/mcp-config-examples/)。

### 暴露的 tool 列表（M1）

| Tool 名 | 说明 | 对应 capability |
|--------|-----|-----------------|
| `alb_status` | 查询当前设备 / transport / 活跃任务 | infra |
| `alb_describe` | 返回所有 tool 的 schema（LLM 入门用） | infra |
| `alb_devices` | 列出连接的设备 | transport |
| `alb_shell` | 执行 shell 命令 | shell |
| `alb_logcat` | 收集 logcat 到 workspace | logging |
| `alb_dmesg` | 收集 dmesg | logging |
| `alb_uart_capture` | 捕获串口输出（方案 G） | logging |
| `alb_log_search` | 在 workspace 已收集日志中搜索 | logging |
| `alb_log_tail` | 分页读取日志文件 | logging |
| `alb_push` / `alb_pull` | 文件传输 | filesync |
| `alb_rsync` | 目录增量同步（方案 C） | filesync |
| `alb_bugreport` | 一键 adb bugreport | diagnose |
| `alb_anr_pull` | 拉 /data/anr 下 ANR 文件 | diagnose |
| `alb_tombstone` | 拉 native crash tombstone | diagnose |
| `alb_reboot` | 重启设备（含 recovery/bootloader） | power |
| `alb_sleep_wake` | 触发休眠 / 唤醒测试 | power |
| `alb_battery` | 查电池状态 | power |
| `alb_app_install` / `alb_app_uninstall` | apk 管理 | app |
| `alb_app_start` / `alb_app_stop` | 启停 app | app |

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
alb describe            # 输出全部 tool JSON schema（LLM 进来先调这个）
alb status              # 当前环境状态快照
alb capabilities        # 列出所有能力
alb methods             # 列出所有传输方案
alb logs list           # 列出 workspace 里已收集的日志产物
```

`alb_describe` MCP tool 返回：
```json
{
  "version": "0.1.0",
  "transports": [
    {"name": "adb", "status": "stable", "methods": ["A", "B"]},
    ...
  ],
  "capabilities": [
    {"name": "shell", "tools": ["alb_shell"], "description": "..."},
    ...
  ],
  "tools": [
    {"name": "alb_logcat", "description": "...", "input_schema": {...}},
    ...
  ]
}
```

### 铁律 5 · 危险操作默认拦截

详见 [`permissions.md`](./permissions.md)。LLM 触发 deny 时返回：

```json
{
  "ok": false,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Command matches dangerous pattern: rm -rf /",
    "suggestion": "Scope to a specific path, or use --confirm flag, or run via: alb shell --allow-dangerous \"...\"",
    "category": "permission"
  }
}
```

### 铁律 6 · 长任务流式 + 产物落地

超过 30 秒的任务强制异步：

```python
# 错误姿势（会阻塞、context 爆）
logcat = await alb_logcat(duration=3600)   # 一小时 logcat，直接回传

# 正确姿势（产物落地 + 摘要）
result = await alb_logcat(duration=3600, stream=True)
# result.artifacts = [logfile_path]
# result.data = {lines: N, errors: M, ...}  ← 摘要元信息
# LLM 按需调用 alb_log_search(pattern="error")
```

### 铁律 7 · 反面规则先列

`llm/CLAUDE.md` 和每个 tool 的 description 都明确列出"不应做什么"：

- ❌ 不要在未 `alb_devices` 确认前假设设备在线
- ❌ 不要用 `alb_shell` 跑 `rm -rf /sdcard` —— 用 `alb_pull --delete` 明确意图
- ❌ 不要一次性 `alb_logcat(duration=86400)` —— 用 `alb_logcat_watch` 持续监控
- ❌ 不要假设 `/data/local/tmp` 永远可写 —— 先 `alb_shell("test -w /data/local/tmp")`

---

## 四、System Prompt 静态/动态边界（MCP 缓存优化）

MCP server 构造的提示词分两层：

```
┌─ 静态层（可 API-cache） ──────────────────────────┐
│  • Tool 列表和 schema（所有 @tool() 装饰）       │
│  • CLAUDE.md 规则                                │
│  • errors.md 错误码索引                          │
│  • 产物路径规范                                  │
└─────────────────────────────────────────────────┘
         + SYSTEM_PROMPT_DYNAMIC_BOUNDARY +
┌─ 动态层（每次会话变） ────────────────────────────┐
│  • 当前设备序列号 / 型号 / Android 版本          │
│  • 当前 transport                                │
│  • 活跃的长任务列表                              │
│  • 最近的错误摘要                                │
│  • 用户当前 profile                              │
└─────────────────────────────────────────────────┘
```

对 Claude API 有意义：静态层可以走 prompt caching，每会话省 token。

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
```

### Python Agent 示例

```python
import subprocess, json

def alb(*args) -> dict:
    r = subprocess.run(["uv", "run", "alb", "--json", *args],
                       capture_output=True, text=True)
    return json.loads(r.stdout)

devices = alb("devices")["data"]["devices"]
for d in devices:
    logcat = alb("logcat", "--device", d["serial"], "--duration", "30")
    if logcat["data"]["errors"] > 0:
        print(f"Device {d['serial']}: {logcat['data']['errors']} errors, see {logcat['artifacts'][0]}")
```

---

## 六、Web API 接入（M2 起）

FastAPI 自带 OpenAPI schema，适合其他语言客户端。

```bash
uv run alb-api --host 0.0.0.0 --port 7000
# → http://localhost:7000/docs (Swagger UI)
# → http://localhost:7000/openapi.json (规范)
```

```bash
curl -X POST http://localhost:7000/logging/logcat \
  -H "Content-Type: application/json" \
  -d '{"duration": 60, "filter": "*:E"}'
```

---

## 七、CLAUDE.md / AGENTS.md 协作

本仓库的 `llm/CLAUDE.md` 和 `llm/AGENTS.md` 是给 **Claude Code / 通用 agent** 看的工具说明。

### 在你的项目里引用

假设你在某个 Android 项目里用 alb 调试，可以在项目根的 `CLAUDE.md` 里加：

```markdown
## 调试工具
本项目使用 [android-llm-bridge](https://github.com/xxx/android-llm-bridge) 做设备调试。
详见 @/path/to/android-llm-bridge/llm/CLAUDE.md
```

Claude Code 会自动读取 `@` 引用的文件，你的 Agent 就知道所有 alb tool 的用法。

---

## 八、常见 LLM 工作流

### 工作流 1 · 诊断一个 app crash

```
1. alb_status              → 确认当前设备
2. alb_app_start           → 启动目标 app
3. alb_logcat_watch        → 开始监控，等待 crash signal
4. [crash 发生]
5. alb_anr_pull            → 拉 ANR（如果是 ANR）
   或 alb_tombstone        → 拉 native crash
6. alb_log_search --pattern "FATAL" → 定位 crash 时间点附近的日志
7. 生成报告 + 产物路径
```

### 工作流 2 · 验证一个 patch

```
1. alb_push <apk> <remote>
2. alb_app_install
3. alb_app_start
4. alb_logcat --duration 60 --filter "MyApp:*"
5. alb_log_search --pattern "expected keyword"
6. 判断通过/失败
```

### 工作流 3 · Bringup 调试（板子起不来）

```
1. alb_setup --method serial  → 配置串口
2. alb_uart_capture --duration 30  → 捕获启动
3. alb_log_search --pattern "error|panic|fail"
4. alb_serial_send "setenv bootargs ..."  → 修改启动参数（如果在 u-boot）
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
- **危险命令加 `--confirm`** —— 尊重权限系统

### ❌ Don't

- **不要硬编码设备序列号** —— 用 profile 或 `alb_devices`
- **不要绕过权限系统** —— 有正当需求在 profile 配置 allow
- **不要把 ANR/崩溃栈直接塞 prompt** —— 用 `alb_log_search` 按需读
- **不要假设 adb 可用就不检查** —— `alb_status` 会告诉你
- **不要在 MCP 环境里调 CLI subprocess** —— 直接用 MCP tool

---

## 十、下一步

- 看 [`permissions.md`](./permissions.md) —— 权限系统细节
- 看 [`errors.md`](./errors.md) —— 错误码参考
- 看 [`tool-writing-guide.md`](./tool-writing-guide.md) —— 如何写新 tool
- 看 [`/llm/mcp-config-examples/`](../llm/mcp-config-examples/) —— 各家 MCP 客户端配置
