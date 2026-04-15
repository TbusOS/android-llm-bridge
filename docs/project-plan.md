---
title: 项目计划 / 路线图
type: roadmap
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [roadmap, milestones, planning]
---

# 项目计划

> 里程碑 · 交付物 · 验收标准 · 风险 · 时间预估。

> ⚠️ 本文中所有时间均为**预估**（不是承诺），标注方式："预估 2-3 周"。实际进度以 commit 和 issue 为准。

---

## 一、里程碑总览

| 里程碑 | 代号 | 主题 | 状态 |
|-------|------|------|-----|
| **M0** | 设计 | 架构设计、技术方案、文档、骨架 | ✅ 当前 |
| **M1** | 核心可用 | 四传输 + 六能力 + 权限 + CLI + MCP | 🚧 |
| **M2** | 扩展能力 | Web API + 长任务 + 子 Agent + 性能 + 跑分 | 📋 |
| **M3** | 可视化 + 智能 | Web UI + LLM-assisted 分析 + 方案 D/E/F | 📋 |
| **M4+** | 生态 | 插件机制 + 多语言 binding + 多板厂适配 | 💭 |

---

## 二、M0 · 设计阶段（✅ 当前）

### 目标
搭好骨架 + 把"怎么做"写清楚，让其他人可以接手实现。

### 交付物

| # | 产物 | 状态 |
|---|------|-----|
| 1 | 仓库骨架（目录、pyproject、LICENSE、.gitignore） | ✅ |
| 2 | `README.md` + `README.en.md` 双语 | ✅ |
| 3 | `docs/00-overview.md` 总览 | ✅ |
| 4 | `docs/architecture.md` 分层架构 | ✅ |
| 5 | `docs/design-decisions.md` 15 条 ADR | ✅ |
| 6 | `docs/llm-integration.md` LLM 接入指南 | ✅ |
| 7 | `docs/permissions.md` 权限设计 | ✅ |
| 8 | `docs/errors.md` 错误码全集 | ✅ |
| 9 | `docs/tool-writing-guide.md` Tool 编写规范 | ✅ |
| 10 | `docs/methods/` 四方案 + 三占位文档 | 🚧 |
| 11 | `docs/capabilities/` 六能力文档 | 🚧 |
| 12 | `docs/contributing.md` 贡献指南 | 🚧 |
| 13 | `llm/CLAUDE.md` + `llm/AGENTS.md` | 🚧 |
| 14 | Python 骨架（`src/alb/__init__.py` 各模块） | 🚧 |
| 15 | `pyproject.toml` 完整依赖列表 | 🚧 |

### 验收标准
- 新人读完 docs 能独立开始 M1 的某个子任务
- 所有 ADR 有依据，不留"为什么这样"的疑问
- 仓库 `uv sync` 成功，`uv run alb --help` 至少能打印 placeholder

### 时间预估
2 天（主要是文档）。

---

## 三、M1 · 核心可用（🚧 进行中）

### 目标
第一个 end-to-end 可用版本：**能让 LLM 通过 MCP 完成基础调试工作流**。

### 交付物

#### 1. 基础设施（`src/alb/infra/`）

| 模块 | 功能 | 借鉴来源 |
|-----|------|---------|
| `result.py` | `Result[T]` / `ErrorInfo` / `ok()` / `fail()` | CLI-Anything |
| `errors.py` | `ERROR_CODES` 注册表 + 查询函数 | - |
| `permissions.py` | 黑名单 + 多层策略 + `check_permissions` | Claude Code |
| `registry.py` | `TRANSPORTS` / `CAPABILITIES` 元数据表 | claude-code2 |
| `workspace.py` | `workspace_path()` + ISO 时间戳 | - |
| `config.py` | 读写 `config.toml` / profile | - |
| `events.py` | 进程内 pub/sub（基础版） | - |

#### 2. 传输层（`src/alb/transport/`）

| 实现 | 方案 | 核心功能 |
|-----|------|---------|
| `base.py` | ABC | 接口契约 |
| `adb.py` | A / B | shell / push / pull / logcat 流 / reboot |
| `ssh.py` | C | shell / scp / rsync / 端口转发 / 持久 session |
| `serial.py` | G | 串口读写 / uart capture / u-boot 交互 |
| `hybrid.py` | 路由 | 按命令类型自动选 transport |

#### 3. 业务能力（`src/alb/capabilities/`）

| 模块 | 主要函数 |
|-----|---------|
| `shell.py` | `execute(cmd, timeout)` |
| `logging.py` | `collect_logcat` / `collect_dmesg` / `uart_capture` / `log_search` / `log_tail` |
| `filesync.py` | `push` / `pull` / `rsync_sync` |
| `diagnose.py` | `bugreport` / `anr_pull` / `tombstone_pull` |
| `power.py` | `reboot` / `sleep_wake_test` / `battery_status` |
| `app.py` | `install` / `uninstall` / `start` / `stop` / `list` |

#### 4. 接入层

| 模块 | 功能 |
|-----|------|
| `src/alb/cli/` | typer 实现 `alb` 命令树，含 `--json` 统一输出 |
| `src/alb/mcp/server.py` | MCP server 入口，注册所有 `@tool()` |
| `src/alb/mcp/tools/*.py` | 每个 capability 对应 MCP tool |
| `src/alb/api/` | FastAPI 骨架（仅基础路由，M2 扩展） |
| `src/alb/skills/` | `SKILL.md` 自动生成（`generator.py`） |

#### 5. 引导脚本（`scripts/`）

| 脚本 | 功能 |
|-----|------|
| `setup-method-adb.sh` | 检测 platform-tools / 配置 ADB_SERVER_SOCKET / 验证设备 |
| `setup-method-wifi.sh` | `adb tcpip 5555` + `adb connect` |
| `setup-method-ssh.sh` | 生成 key / 配 authorized_keys / 建 config |
| `setup-method-serial.sh` | 检测 socat / 建 PTY / picocom 封装 |
| `install.sh` | 一键装（含 uv 检查 / sync / PATH） |
| `uninstall.sh` | 清理（workspace 可选保留） |

#### 6. LLM 接入配置（`llm/`）

| 文件 | 功能 |
|-----|------|
| `CLAUDE.md` | Claude Code 用（反面规则 + tool 使用示例） |
| `AGENTS.md` | 通用 agent 规范 |
| `mcp-config-examples/claude-code.json` | Claude Code 示例 |
| `mcp-config-examples/cursor.json` | Cursor 示例 |
| `mcp-config-examples/codex.json` | Codex CLI 示例 |
| `commands/*.md` | slash 命令模板（给项目用） |

#### 7. 测试

| 类别 | 覆盖 |
|-----|------|
| 单元测试 | 每个 capability 至少 4 case（happy / permission / timeout / error） |
| 集成测试 | mock transport 跑通 6 个能力 end-to-end |
| CLI 测试 | `typer.testing.CliRunner` 测主命令 |
| MCP 测试 | mcp SDK 的 test client 测 tool list + 调用 |

### 验收标准

- [ ] `uv run alb setup adb` 能完成方案 A 配置
- [ ] `uv run alb devices` 列设备
- [ ] `uv run alb shell "getprop ro.build.version.sdk"` 返回结构化结果
- [ ] `uv run alb logcat -d 30` 保存到 workspace
- [ ] `uv run alb bugreport` 拉 bugreport.zip
- [ ] `uv run alb reboot` 重启设备（权限系统放行）
- [ ] `uv run alb shell "rm -rf /sdcard"` 被权限系统拦截
- [ ] `uv run alb serial connect` 能看到 UART 输出
- [ ] MCP server 在 Claude Code 里 `tools/list` 返回全部 tool
- [ ] Claude Code 里输入 "帮我收集 30 秒 logcat" 能走完流程
- [ ] `alb describe` 输出的 JSON schema 完整
- [ ] 单元测试覆盖率 ≥ 70%
- [ ] 所有 tool 的 description 含 "When to use" + "LLM notes"

### 时间预估
预估 3-4 周（按单人 2 天/周节奏）。

### 风险

| 风险 | 缓解 |
|-----|------|
| MCP SDK API 变动 | 抽象一层 `alb.mcp.adapter`，变化隔离 |
| `pyserial-asyncio` 稳定性 | 备选：pure `pyserial` + thread |
| adb logcat 流在长时间稳定性 | 加自动重连 + 分段写文件 |
| Windows 端 ser2net 兼容性 | 提供 `com0com` 和 `hub4com` 两个替代方案 |
| Python 3.11 下某些 asyncio API 差异 | `asyncio.timeout()` 用向后兼容包装 |

---

## 四、M2 · 扩展能力（📋 规划中）

### 目标
支持**长任务 + 并发多设备 + 性能监控 + 跑分集成**。

### 交付物

| 类别 | 内容 |
|-----|------|
| 长任务框架 | `BackgroundTask` / `TaskManager`；支持取消 / 状态查询 / 进度推送 |
| 流式 API | WebSocket (Web API) / SSE 端点；logcat 持续监控 |
| 子 Agent | 多设备并行操作的任务隔离（借鉴 Claude Code `spawnMultiAgent`） |
| `perf` 能力 | CPU / MEM / FPS / 温度 / 电流 持续采集，CSV 产物 |
| `benchmark` 能力 | AnTuTu / GeekBench 集成；自定义跑分框架 |
| `network` 能力 | 端口转发 / tcpdump 抓包 / 弱网模拟 |
| `ui` 能力 | 截图 / 录屏 / 坐标点击 / 文本输入（配合 uiautomator2） |
| Web API 完整 | FastAPI 所有路由齐全，OpenAPI 规范完备 |
| Undo / 快照 | 危险操作前快照（filesync.push 覆盖 / rm 等） |

### 验收标准
- 能持续监控一台设备 1 小时 logcat 不丢行
- 同时对 10 台设备并发 `app_install`，成功率 ≥ 99%
- 性能采集数据可导出图表（CSV + 基础 plotly 脚本）
- Web API OpenAPI 验证通过，docs 页面可访问

### 时间预估
预估 4-6 周。

---

## 五、M3 · 可视化 + 智能（📋 规划中）

### 目标
给**人**一个直观看板；给 LLM 更智能的日志分析能力；落地剩余方案。

### 交付物

| 类别 | 内容 |
|-----|------|
| Web UI | Vue 3 / React（二选一）+ TypeScript；设备卡片看板；实时 logcat 流；性能曲线；ANR 时间线 |
| LLM-assisted 日志分析 | 后台任务提取 crash / ANR 关键事件（借鉴 MemGPT / Claude Code SessionMemory） |
| 历史检索 | `alb log search` 全文索引 (用 Whoosh / sqlite FTS5) |
| Known issues 记忆库 | 从 history.jsonl 自动沉淀（Evo-Memory） |
| 方案 D | USB 网络共享（IP-over-USB） |
| 方案 E | scrcpy 集成（远程屏幕 + 录屏） |
| 方案 F | frp / ngrok 云中转（客户现场调试） |
| Docker 镜像 | 官方 Docker image，一键拉起 MCP + API + UI |

### 验收标准
- Web UI 能在 Chrome / Firefox / Safari 正常运行
- 打开设备页可看到实时 logcat 滚动
- ANR 发生时 UI 能高亮并弹出分析报告
- 方案 D/E/F 文档齐全、代码可用
- LLM 在长对话中不再因 logcat context 爆炸

### 时间预估
预估 6-8 周（前端工作量大）。

---

## 六、M4+ · 生态（💭 远期）

可能方向（不承诺）：

- **插件机制** —— 第三方 transport / capability 通过 entry points 扩展
- **多语言 binding** —— TypeScript / Go SDK 调用 Web API
- **多板厂适配** —— Rockchip / Qualcomm / MediaTek 特定能力（如各家 bootloader 差异）
- **iOS 支持** —— 扩展到 iOS 调试（与 libimobiledevice 集成）
- **CI 模板** —— GitHub Actions / GitLab CI 一键接入

---

## 七、开发节奏

### 建议的单人开发节奏（M1）

| 周 | 重点 |
|----|------|
| W1 | infra 全部 + `transport/base.py` + `adb.py` 基础 |
| W2 | `adb.py` 完整 + `ssh.py` + `capabilities/shell.py` + `filesync.py` |
| W3 | `serial.py` + `capabilities/logging.py`（含 uart）+ `diagnose.py` |
| W4 | `power.py` + `app.py` + CLI 全部 + MCP server |

之后 1-2 周补测试、集成、文档、CI。

### 并行拓扑

如果多人：

```
┌─────────────────────────────────────────────────┐
│ Infra layer (result/errors/permissions/config)   │  — 1 人前 2 天
└─────────────────────────────────────────────────┘
         ↓                      ↓                    ↓
   Transport              Capability             LLM interfaces
   (adb/ssh/serial)       (shell/logging/...)    (CLI/MCP/API)
   2 人并行               3 人并行               1 人 (等 capability 完成)
```

---

## 八、贡献策略

- **M0 阶段**：只接受文档 PR（架构 / 方案补充），避免代码混乱
- **M1 阶段**：按 capability / transport 发 issue 让人认领，一 issue 一 PR
- **M2 阶段**：开放性能 / 跑分 / 网络能力给各家板厂贡献
- **M3 阶段**：Web UI 欢迎前端社区贡献

---

## 九、质量门槛

| 门槛 | 要求 |
|-----|------|
| 代码风格 | `ruff check` + `ruff format` 零 warning |
| 类型检查 | `mypy --strict` 关键模块 |
| 单元测试 | 覆盖率 ≥ 70%（M1），≥ 80%（M2+） |
| 文档同步 | 新 tool 必须有 `docs/capabilities/*.md` 和 errors.md 条目 |
| PR checklist | 见 `contributing.md` |
| Commit 规范 | 见 `contributing.md`（不带 LLM 署名） |

---

## 十、进度跟踪

真实进度看：

- GitHub Issues（里程碑标签）
- `git log --oneline` 看 commit
- `alb workspace inspect`（M1 后）自动生成实现状态矩阵

本文档每月更新一次（`updated:` 字段），实际进度在 issue / PR 里。

---

## 十一、FAQ

**Q: 为啥第一版不做 Web UI？**
A: Web UI 是给**人**看的，而本项目主要服务**LLM**。LLM 用 MCP/CLI 就够了。先把核心能力做稳，Web UI 留给 M3。

**Q: 为啥 M1 包含 MCP 但不包含 Web API 完整版？**
A: MCP 是 LLM 原生协议，必须第一版就有。Web API 骨架有，但完整路由 + Swagger 等到 M2 和 Web UI 配套。

**Q: M2 "子 Agent 并行" 具体指啥？**
A: 让 LLM 能对多台设备同时操作（比如"对所有测试设备装这个 apk"），每个子 agent 独立 session，互不干扰。借鉴 Claude Code 的 `spawnMultiAgent` 设计。

**Q: 我是贡献者，该从哪个子任务入手？**
A: 优先找 "good-first-issue" 标签的，或者认领一个单独 capability（比如实现 `capabilities/app.py`）—— 依赖较少、闭环清晰。
