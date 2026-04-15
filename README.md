# android-llm-bridge

> **让 LLM Agent 直接在线调试安卓设备** —— 一套可扩展的统一调试桥，抽象 adb / ssh / UART 串口多种传输，通过 MCP / CLI / Web API 三层接入大模型。

<p align="center">
  <em>for Claude Code · Cursor · Codex · 任意支持 MCP 的 Agent</em>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-支持的调试方案">方案对比</a> ·
  <a href="#-文档导航">文档</a> ·
  <a href="./README.en.md">English</a>
</p>

---

## 这是什么

**android-llm-bridge**（简称 **alb**）是一个**面向大模型**的安卓设备在线调试桥。核心定位：

> 让 AI Agent 像使用普通函数一样，安全、结构化地操作真实安卓设备 —— 不论设备怎么连接（USB adb / 无线 adb / 板子跑 sshd / 还是只有 UART 串口）。

### 为什么需要它

传统 adb/ssh 命令行给人用很好，但**给大模型用**会踩一堆坑：

- 输出是自由文本，LLM 解析容易出错
- 出错只有 `stderr` 一串英文，没有结构化 error code
- 长会话积累大量 logcat，context 一下塞爆
- 危险命令（`rm -rf`、`reboot bootloader`）没挡一层，LLM 误触就是一次刷机
- 多种连接方式（USB/WiFi/串口）各有各的命令，LLM 要记一堆
- 跑分、ANR、性能采集这些高级能力每次都要 LLM 自己拼命令

**alb 解决这些问题**：
- 统一命令（`alb shell` / `alb logcat` / `alb pull` 等），自动选最合适的传输通道
- 所有输出 `--json` 可选，返回 `{ok, data, error, artifacts}` 结构化
- 内置权限系统，危险命令默认拦截，可配置 allow/ask/deny 多层策略
- 长日志走 workspace 分层存储 + 换页机制，LLM 按需读取
- MCP server 原生支持，Claude Code / Cursor 一行配置即可用

---

## 核心特性

| 特性 | 说明 |
|-----|------|
| **多传输抽象** | adb USB / adb WiFi / 板子 sshd / UART 串口 —— 上层命令一致，底层自动路由 |
| **LLM-first API** | 结构化返回、错误码、自描述 CLI、`SKILL.md` 自动生成 |
| **三层接入** | CLI (`alb`) · MCP server (`alb-mcp`) · Web API (FastAPI)，共享同一套业务层 |
| **权限与安全** | 危险命令黑名单 + tool 级 check_permissions + 多层策略覆盖 |
| **产物规范化** | logcat / ANR / bugreport / 性能数据 全部入 `workspace/devices/<serial>/...` |
| **长日志友好** | 分层存储（热/温/冷）+ 搜索 + 归档，LLM 不怕 context 爆 |
| **Web 可视化**（后续） | 浏览器看设备状态、实时 log、性能曲线 |

---

## 支持的调试方案

第一版落地 **A / B / C / G** 四种方案，**D / E / F 接口预留**。

| 方案 | 通道 | 启动log | u-boot | 板子无网 | 板子死机 | 一句话定位 |
|------|------|:----:|:----:|:----:|:----:|----------|
| **A · adb USB（+ SSH 反向隧道）** | adb 协议 | ❌ | ❌ | ✅ | ❌ | 系统级基础，必装 |
| **B · adb WiFi** | TCP | ❌ | ❌ | ❌ | ❌ | 无线临时调试 |
| **C · 板子内 sshd** | ssh | ❌ | ❌ | ❌ | ❌ | 开发增强（rsync/tmux/sshfs） |
| **G · UART 串口** | 串口 | ✅ | ✅ | ✅ | ✅ | **最后底牌**，bringup / panic 救援 |
| D · USB 网络共享 | IP-USB | - | - | - | - | _预留_ |
| E · scrcpy 屏幕镜像 | adb | - | - | - | - | _预留_ |
| F · frp / 云中转 | 公网 | - | - | - | - | _预留_ |

**按使用场景选**：

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| 系统刷机 / 进 recovery / 进 fastboot | A | adb 在 recovery 下仍可用，sshd 不可 |
| 开发日常：改代码 → 推板子 → 看效果 | C + A | rsync 增量快、长 session；A 兜底 |
| 大量设备 CI / 自动化 | B + C | 无线省线、多设备并发 |
| 板子起不来 / 内核 panic / u-boot 调试 | **G** | 只有串口能看到底层日志 |
| 客户现场远程调试 | F（待实现） | 走公网中转 |
| 板子在 Windows 上，Linux 服务器调 | **A + G** | adb + 串口都走 Xshell 反向隧道 |

详见 [`docs/methods/00-comparison.md`](./docs/methods/00-comparison.md)。

---

## 架构概览

```
┌────────────────────────────────────────────────────────┐
│  接入层（壳）                                            │
│  ├─ CLI (typer)          给人 / LLM 直接用              │
│  ├─ MCP Server           给 Claude/Cursor/Codex 调用    │
│  ├─ Web API (FastAPI)    给 Web UI / 外部集成           │
│  └─ Web UI (后续)         可视化                         │
├────────────────────────────────────────────────────────┤
│  业务能力层 Capabilities（M1 六个）                      │
│  shell │ logging │ filesync │ diagnose │ power │ app   │
├────────────────────────────────────────────────────────┤
│  ⭐ 传输抽象层 Transport                                 │
│  interface: shell / stream_read / push / pull /         │
│             forward / reboot / check_permissions        │
│  ├─ AdbTransport       (方案 A / B)                     │
│  ├─ SshTransport       (方案 C / D / F)                 │
│  ├─ SerialTransport    (方案 G)                         │
│  └─ HybridTransport    (智能路由：按命令类型选通道)       │
├────────────────────────────────────────────────────────┤
│  底层驱动 Drivers                                       │
│  adb · ssh · scp · rsync · pyserial · socat · 隧道管理  │
├────────────────────────────────────────────────────────┤
│  基础设施 Infra                                         │
│  config · workspace · profile · permissions · errors    │
│  event-bus · prompt-builder · memory (M2+)             │
└────────────────────────────────────────────────────────┘
```

关键设计：**上层业务 / MCP server / Web API 共享同一 Python 函数定义**，三层只是薄壳 —— 详见 [`docs/architecture.md`](./docs/architecture.md)。

---

## 快速开始

> ⚠️ **当前状态：M0（骨架 + 完整技术方案）**。代码实现在 M1 推进中。本仓库当前主要是**技术方案 + 架构文档**，详见 [`docs/project-plan.md`](./docs/project-plan.md) 了解里程碑进度。

### 前置要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（包管理）
- 调试对象：一台 Android 设备 + 至少一种通道（USB 线 / 同网段 / 串口线）

### 安装（M1 实现后）

```bash
git clone https://github.com/<your>/android-llm-bridge
cd android-llm-bridge
uv sync                    # 装依赖
uv run alb --help          # 看看能做什么
```

### 配一种传输方案

选一种先用起来（以方案 A 为例）：

```bash
uv run alb setup adb       # 引导式：检测 platform-tools / 配环境变量 / 验证设备
uv run alb devices         # 列出设备
uv run alb shell "getprop ro.build.version.sdk"
```

其他方案：
```bash
uv run alb setup ssh       # 板子跑 sshd（方案 C）
uv run alb setup serial    # UART 串口（方案 G）
uv run alb setup wifi      # adb over WiFi（方案 B）
```

### 接入 Claude Code（MCP）

```json
// ~/.claude/mcp-settings.json
{
  "mcpServers": {
    "alb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/android-llm-bridge", "alb-mcp"]
    }
  }
}
```

在 Claude Code 里直接让它"帮我看看板子最近的 ANR"、"拉一份 bugreport"、"监控 15 分钟的 CPU 占用"即可，无需手写 adb 命令。

---

## 文档导航

| 类别 | 文档 | 给谁看 |
|-----|------|-------|
| **起步** | [README.md](./README.md) / [README.en.md](./README.en.md) | 所有人 |
| **总览** | [docs/00-overview.md](./docs/00-overview.md) | 先看这个 |
| **架构** | [docs/architecture.md](./docs/architecture.md) | 想了解内部设计 |
| **设计决策** | [docs/design-decisions.md](./docs/design-decisions.md) | 想知道为啥这么做 |
| **LLM 集成** | [docs/llm-integration.md](./docs/llm-integration.md) | Agent / AI 开发者 |
| **权限系统** | [docs/permissions.md](./docs/permissions.md) | 安全 / 部署人员 |
| **错误码** | [docs/errors.md](./docs/errors.md) | LLM / 排错时查表 |
| **Tool 写法** | [docs/tool-writing-guide.md](./docs/tool-writing-guide.md) | 贡献者 |
| **项目计划** | [docs/project-plan.md](./docs/project-plan.md) | 想了解里程碑进度 |
| **贡献指南** | [docs/contributing.md](./docs/contributing.md) | 贡献者 |
| **调试方案** | [docs/methods/](./docs/methods/) | 按方案查手册 |
| **业务能力** | [docs/capabilities/](./docs/capabilities/) | 按能力查手册 |

---

## 路线图

| 里程碑 | 交付内容 | 状态 |
|-------|---------|-----|
| **M0** | 仓库骨架 + 完整技术方案文档 + 架构图 + 里程碑规划 | ✅ 当前 |
| **M1** | 四种传输（A/B/C/G）+ 六能力（shell/logging/filesync/diagnose/power/app）+ 权限系统 + CLI + MCP 骨架 | 🚧 进行中 |
| **M2** | Web API + 长任务框架（流式 logcat / 大文件传输）+ 子 Agent 并行 + 性能能力（perf）+ 跑分（benchmark） | 📋 规划中 |
| **M3** | Web UI（设备看板 / 实时日志 / 性能曲线）+ LLM-assisted 日志分析 + 方案 D/E/F | 📋 规划中 |

详见 [`docs/project-plan.md`](./docs/project-plan.md)。

---

## 贡献

欢迎贡献新的传输方案、业务能力、LLM 集成示例！开始前请阅读：

1. [`docs/contributing.md`](./docs/contributing.md) —— 开发流程 / 代码风格 / PR checklist
2. [`docs/tool-writing-guide.md`](./docs/tool-writing-guide.md) —— LLM 友好 tool 怎么写
3. [`llm/CLAUDE.md`](./llm/CLAUDE.md) —— 如果用 Claude Code 协作开发

---

## 致谢

本项目在设计阶段参考了以下优秀项目的理念：

- [Claude Code](https://claude.com/claude-code) —— 权限系统分层设计、tool schema、session memory 思想
- [MCP](https://modelcontextprotocol.io) —— Anthropic Model Context Protocol，本项目的 LLM 接入标准
- [CLI-Anything](https://github.com/cli-anything) —— SKILL.md 自动生成、core/CLI 解耦思路
- 学术思想：MemGPT（分层上下文）、Evo-Memory（失败学习）、MemoryBank（衰减归档）

---

## License

[MIT](./LICENSE) © 2026 sky &lt;skyzhangbinghua@gmail.com&gt;
