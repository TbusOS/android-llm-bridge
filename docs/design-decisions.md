---
title: 关键设计决策记录 (ADR)
type: design-doc
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [adr, decisions, rationale]
---

# 关键设计决策记录

> 每条决策回答：**什么 / 为什么 / 考虑过什么替代 / 代价**。
> 参考 [ADR 模式](https://adr.github.io/)，但本文档是聚合版，单条不 break out。

---

## ADR-001 · 核心语言选 Python 3.11+

**决策**：核心实现使用 Python 3.11+。

**为什么**：
1. **MCP SDK 官方一等公民** —— Anthropic 官方 `mcp` 包优先维护 Python 和 TypeScript 两种语言
2. **LLM 生成准确率最高** —— HumanEval / MBPP 基准 Python 顶部，后续让 LLM 帮我们扩展新能力时出错率低
3. **Android 调试生态主导** —— adbutils / uiautomator2 / Appium / pyserial 都是工业级 Python 库
4. **subprocess 流式直观** —— 90% 是 wrap adb/ssh/socat 输出流，`asyncio.subprocess` + AsyncIterator 是 LLM 最熟模式
5. **错误现场对 LLM 友好** —— traceback 完整带变量上下文，LLM 能自修复

**替代考虑**：
- **TypeScript**：MCP 也是一等；但 adb/ssh/串口库生态差一档，subprocess 流处理不如 Python 直观
- **Go**：单二进制诱人；但 MCP 生态弱、LLM 写 Go 错误率高、对"开发工具"单二进制优势不大
- **Rust**：性能最好；但 LLM 训练数据少、开发速度慢、Android 生态弱

**代价**：
- 需要 venv / 依赖管理（用 uv 解决）
- 启动略慢（可接受）

---

## ADR-002 · 包管理用 uv（不用 pip / poetry）

**决策**：`uv` 管理依赖、虚拟环境、运行入口。

**为什么**：
1. **快 10-100×** —— LLM 装依赖 / 跑测试迭代不卡
2. **零配置 venv** —— `uv run alb xxx` 自动激活，LLM 不用记命令
3. **可重现** —— `uv.lock` 锁到哈希
4. **PEP 723 内联依赖** —— 单文件脚本自带依赖声明，demo 零摩擦
5. **2024 起新项目主流**

**替代考虑**：
- **pip + venv**：传统方案；慢、手动管理 venv
- **poetry**：稳定；比 uv 慢，lock 文件格式非标准
- **hatch**：现代；但生态和速度不如 uv

**代价**：
- uv 相对新（2023 发布），某些边缘 CI 可能没装（`uv sync` 的 fallback 用标准 pip 可解决）

---

## ADR-003 · 架构三层：Interface / Capability / Transport

**决策**：接入层（CLI/MCP/API）只是薄壳，业务逻辑集中在 capability 层，底层走 transport 抽象。

**为什么**：
1. **避免三重实现** —— CLI/MCP/API 如果各自实现业务，三层漂移会发生
2. **新接入层零成本** —— 加 Slack Bot / Discord Bot / 命令行 TUI 只要包一层
3. **Transport 抽象是**"一次写，所有方案受益"** —— 加 D/E/F 只要实现 base.py，所有 capability 立即可用

**替代考虑**：
- **CLI 直接调底层**：最简单；但 MCP/API 要重写一遍业务，三层漂移
- **全走 HTTP API**：CLI 也调 API；简单统一，但本地调试依赖 HTTP server 启动，繁琐
- **RPC / gRPC**：跨进程；本项目主要单进程，过度设计

**代价**：
- 要写额外的薄壳 —— 但每层壳只是 10 行左右的装饰器

---

## ADR-004 · Transport 抽象层作为架构核心

**决策**：所有"操作设备"的能力经 `Transport` ABC，禁止 capability 直接调 adb / ssh / serial 命令。

**为什么**：
1. **多传输场景很常见** —— 开发日常 adb + sshd 混用，bringup 要串口救命
2. **能力复用**：`alb.logging.collect_logcat()` 不该关心底层是 adb logcat 还是 ssh logcat
3. **未来扩展** —— D (USB net) / E (scrcpy) / F (云中转) 只是新 Transport 子类
4. **HybridTransport 智能路由** —— 可以按命令类型自动选最佳通道

**替代考虑**：
- **能力里 if adb else if ssh**：最直接；但每加一个传输就要改所有能力，扩展性爆炸
- **Strategy 模式**：类似的抽象；Python 里 ABC 就是 Strategy，命名上直接

**代价**：
- ABC 接口要想清楚，一旦定了不能随便改 —— **这是为什么 M0 花大力气写架构文档**

---

## ADR-005 · 第一版方案选 A + B + C + G

**决策**：M1 只落地 adb USB（+SSH 隧道）、adb WiFi、板子 sshd、UART 串口四种，D/E/F 留接口。

**为什么**：
1. **A 必装** —— 刷机 / recovery / bringup 场景唯一选择
2. **G 必装** —— 板子死机 / u-boot / panic 救援的唯一手段，能力不可替代
3. **B 顺手** —— 本质是 A 的无线变种，共用 AdbTransport，成本低
4. **C 开发强需求** —— rsync / tmux / sshfs 是 A 完全顶不到的生产力
5. **D/E/F 先占位** —— 使用频率低 / 生态复杂，避免第一版过载

**替代考虑**：
- **只做 A** ：保守；但失去 UART 救场能力，减损价值大
- **全做七种** ：理想；工作量 3-4 倍，延迟第一版，违反 MVP 原则

**代价**：
- D/E/F 的用户需要等 M3

---

## ADR-006 · 权限系统作为第一版必做（借鉴 Claude Code）

**决策**：M1 内置权限系统（黑名单 pattern + tool 级 check_permissions + 多层策略覆盖）。

**为什么**：
1. **LLM 会误触** —— `rm -rf /` / `reboot bootloader` / `fastboot erase` / `setprop persist.*` 一旦执行后果严重
2. **Claude Code 的成熟设计** —— `utils/permissions/` 有双层、多来源、降级机制可抄
3. **开源给别人用** —— 没权限系统 ≈ 不敢给 LLM 用

**替代考虑**：
- **不做** ：最快；但不负责任，M2 再加也要重构权限切点
- **只做黑名单**：简单；但不够灵活，不支持用户自定义策略

**代价**：
- 每个 capability / transport 要考虑权限切点 —— 但有统一 `check_permissions()` 钩子可以集中处理

**详见** [`permissions.md`](./permissions.md)。

---

## ADR-007 · 长日志用"workspace 分层 + LLM 换页"（借鉴 MemGPT）

**决策**：logcat / 串口 / dmesg 等长日志不直接回传给 LLM，而是落 workspace 路径 + 返回路径 + 提供搜索/换页 tool。

**为什么**：
1. **Context 爆** —— 一次 logcat 可能几 MB，LLM 窗口放不下
2. **多数场景不需要全读** —— LLM 通常只关心"过去 10 分钟的 error"或"某 tag 的日志"
3. **MemGPT 换页思想** —— 工具返回"产物路径 + 摘要"，LLM 按需调 `alb log search` / `alb log tail`

**替代考虑**：
- **全量回传** ：最朴素；一次调用就能把 context 塞满
- **自动摘要后回传** ：对 LLM 透明；但摘要算法是个大坑，第一版不做

**代价**：
- LLM 要学会"按需换页"的模式 —— 通过 tool description 和 CLAUDE.md 引导

**M3 扩展**：后台定期提取"关键事件"（crash/ANR）到结构化摘要。

---

## ADR-008 · 结构化 Result 替代异常（LLM-first）

**决策**：所有 tool / capability 返回 `Result(ok, data, error, artifacts, timing_ms)`，不抛未包装异常。

**为什么**：
1. **LLM 看异常很吃力** —— Python traceback 虽然信息丰富，但 LLM 判断要不要重试时不如结构化字段直接
2. **失败可行动** —— `error.suggestion` 字段直接告诉 LLM 下一步做什么（"run: alb setup adb"）
3. **一致性** —— CLI / MCP / API 三层统一处理

**替代考虑**：
- **抛异常** ：Python 惯例；LLM 判断成本高、重试逻辑分散
- **Result[T, E] 泛型**（Rust 风格）：优雅；Python 类型系统弱化了这个优势

**代价**：
- 每个函数要包 Result —— 有辅助函数 `ok()` / `fail()` 降低样板

**详见** [`errors.md`](./errors.md)。

---

## ADR-009 · MCP 作为 LLM 接入标准（不自造协议）

**决策**：优先支持 MCP (Model Context Protocol)，Claude Code / Cursor / Codex 等客户端可直接连。

**为什么**：
1. **Anthropic 官方标准** —— 已有多家客户端支持
2. **Stdio / HTTP 双模式** —— 开发期 stdio，远程 HTTP
3. **工具发现机制** —— schema 自动暴露，无需手动登记
4. **避免生态重复** —— 不自造 JSON-RPC / REST 方言

**替代考虑**：
- **自造 HTTP API** ：最灵活；但每个 LLM 客户端要适配，浪费
- **OpenAI Functions** ：标准之一；但绑定 OpenAI 生态

**代价**：
- MCP 还在快速迭代，有版本升级成本 —— 但 `mcp` Python SDK 抽象掉了大部分变化

**Web API（FastAPI）仍然保留**，作为非 MCP 场景（Web UI / 第三方集成）的接入。

---

## ADR-010 · 产物路径规范化（`workspace/devices/<serial>/...`）

**决策**：所有工具产生的文件走统一规范路径，不允许随意落盘。

**为什么**：
1. **LLM 可预测** —— 不用每次问"logcat 存哪了"，直接读 `workspace/devices/<serial>/logs/*.txt`
2. **`--clean` 可管理** —— 统一目录方便清理
3. **多设备隔离** —— 按序列号分目录避免混淆
4. **归档策略可统一** —— 冷数据自动压到 `workspace/archive/`

**替代考虑**：
- **随机 /tmp 目录** ：最简单；但违反全局 CLAUDE.md 规则且 LLM 无法预测
- **当前工作目录** ：灵活；但多项目时污染 cwd

**代价**：
- 每个能力要知道 workspace 根路径 —— 有统一 `workspace_path()` 辅助函数

---

## ADR-011 · SKILL.md 自动生成（借鉴 CLI-Anything）

**决策**：从 typer 命令树 / MCP tool 定义自动生成 `src/alb/skills/SKILL.md`，LLM 客户端可 read-only 访问。

**为什么**：
1. **避免文档漂移** —— 代码是真相源，文档自动生成
2. **LLM 离线可读** —— 不用联网查文档
3. **Frontmatter + 表格** —— LLM 解析友好

**替代考虑**：
- **手写文档** ：初期快；长期和代码漂移
- **只靠 `--help`** ：基础方案；结构化差，LLM 要 parse 文本

**代价**：
- 要写 `generator.py` —— 但只是遍历装饰器元数据

---

## ADR-012 · 元数据驱动注册表（借鉴 claude-code2）

**决策**：`TRANSPORTS` / `CAPABILITIES` / `ERROR_CODES` 以 dataclass 元数据表集中注册，不散落在各文件里。

**为什么**：
1. **自动生成支持矩阵** —— 方案对比表、能力清单、错误码表直接从代码生成
2. **避免手工维护文档偏差** —— 文档的"支持方案"跟代码不同步是常见 bug
3. **运行时 feature flag** —— 可以按 status=planned / stable 条件启用

**替代考虑**：
- **遍历模块自动发现** ：也可；但显式注册表更清晰

**代价**：
- 加新模块要同时在 registry 里登记 —— 用 lint / pre-commit 强制

---

## ADR-013 · 从反面定义 LLM 规则（借鉴 my-claude-cli）

**决策**：`llm/CLAUDE.md` 用"不应做什么"开头，而非抽象鼓励"要小心"。

**为什么**：
1. **具体约束胜于抽象原则** —— "不要用 `rm -rf /sdcard`" 比 "请小心删除" 有效
2. **Claude Code 自身实践** —— 官方 CLAUDE.md 大量用"Don't X"句式

**替代考虑**：
- **全写正面原则** ：读感好；但约束力弱

**代价**：
- 无

---

## ADR-014 · Web UI 和 MCP/CLI 分工

**决策**：
- **CLI**：LLM 直接用 / 人也能用
- **MCP**：LLM 客户端首选接入
- **Web API**：给 Web UI / 外部 HTTP 集成
- **Web UI**（M3）：**给人看**的可视化（设备看板 / 实时日志 / 性能曲线）

**为什么**：
1. **分工明确** —— LLM 用 CLI/MCP（文本），人用 Web UI（图形）
2. **Web 不是 LLM 的首选** —— MCP 比 HTTP API 更直接、更语义化

**代价**：
- 无 —— 各取所长

---

## ADR-015 · Undo / 快照机制（M2）

**决策**：M1 不实现，但 transport 接口保留钩子，M2 给"危险但可逆"的操作加快照（如覆盖文件 / 删除文件）。

**为什么**：
1. **LLM 偶尔会覆盖不该覆盖的** —— 有 undo 可以兜底
2. **Claude Code 的快照机制** 给了参考

**代价**：
- 需要 workspace 存快照，占磁盘 —— 用 TTL 清理

---

## 附录 · 被驳回的提案

| 提案 | 驳回理由 |
|-----|---------|
| 用 Go 写核心 | MCP 生态弱、LLM 写 Go 错误率高、单二进制优势不值得 |
| 不做 Transport 抽象，CLI 直调 adb | 扩展到 ssh/serial 时要重写全部能力 |
| 日志直接回传给 LLM 不落盘 | context 爆 + 不持久化无法复盘 |
| 不做权限系统（相信 LLM 不会误触） | 不负责任、开源给别人用会出事 |
| 只支持 MCP，不做 CLI | 本地调试 / 脚本化 / 人工操作仍需要 CLI |
| 做 Android 客户端 app | 偏离定位，alb 是"桥"，不是板子上的 app |

---

## 下一步

- 回到总览 → [`00-overview.md`](./00-overview.md)
- 看架构细节 → [`architecture.md`](./architecture.md)
- 看 LLM 怎么用 → [`llm-integration.md`](./llm-integration.md)
