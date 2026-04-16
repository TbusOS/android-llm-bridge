# Changelog

> 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。
> 版本编号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### M1 · 核心完成（W1-W3）
- ✅ 4 个传输 beta：AdbTransport（A/B）、SshTransport（C）、SerialTransport（G）、HybridTransport（智能路由）
- ✅ 6 个能力 beta：shell / logging（含 UART）/ filesync（含 rsync）/ diagnose / power / app
- ✅ 权限系统：黑名单 + 多层策略 + 每个 transport 自定义 check_permissions
- ✅ CLI：21 个顶层命令 + 7 个子命令组
- ✅ MCP 服务器：21 个工具全注册，stdio 运行
- ✅ 一键安装 / 卸载脚本（完全用户态，不碰系统）
- ✅ `alb setup {adb,wifi,ssh,serial}` 引导式配置
- ✅ SKILL.md 自动生成（`alb skills generate`）
- 🚧 剩余：更多集成测试（M1 核心能力已全部完成）

### M1 · prompt_builder（2026-04-16）
- ✅ `src/alb/infra/prompt_builder.py` 系统提示词组装器
  - `PromptBlock` / `Prompt` / `PromptBuilder`（链式 fluent 接口）
  - 静态在前、动态在后的不变式（builder 强制检查，违反即 `PromptOrderError`）
  - 三种输出：`as_text()` / `as_anthropic()`（cache_control 自动放在最后一个静态块）/ `as_openai()`
  - `default_agent_prompt(device, transport, workspace, tool_count, extra_static, extra_dynamic)` 一行生成 agent 默认提示词
  - 常量 `DEFAULT_ROLE` / `DEFAULT_SAFETY_RULES` / `DEFAULT_TOOL_NORMS`
- ✅ `src/alb/agent/loop.py` 接线 prompt_builder（示例 docstring + 不再重复定义 DEFAULT_SYSTEM_PROMPT）
- ✅ 25 单测：顺序不变式 / 三种输出格式 / cache_boundary 位置 / default 各字段组合

### M1 · HybridTransport beta（2026-04-16）
- ✅ `src/alb/transport/hybrid.py` 智能路由：shell→primary；logcat→adb→ssh；uart→serial；dmesg/kmsg→adb→ssh→serial；push/pull→ssh→adb；forward→adb→ssh；reboot recovery/bootloader→adb only
- ✅ `pick_for(op, hint)` 公开接口 + 未覆盖能力走 `TRANSPORT_NOT_SUPPORTED` 拒绝
- ✅ 聚合 supports_boot_log / supports_recovery + 聚合 health 快照
- ✅ registry hybrid 状态 planned → beta
- ✅ 30 单测，覆盖路由决策 + fallback + 拒绝路径 + 委托 check_permissions + health 聚合

### M1 · Agent 层架构预留（2026-04-16）
- ✅ `src/alb/agent/` 骨架：`LLMBackend` ABC + `AgentLoop` + `ChatSession` + `Message`/`ToolCall`/`ToolSpec`/`ChatResponse` 数据原语
- ✅ `BackendSpec` + `BACKENDS` registry（ollama / openai-compat / llama-cpp / anthropic，全部 planned）
- ✅ ADR-016 · 可插拔 LLM Backend + 本地小模型支持
- ✅ `docs/agent.md` 完整设计文档（模块 / 选型 / 接入 / 路线图 / 风险）
- ✅ `docs/project-plan.md` M2/M3/M4+ 拆入 agent 落地 + Web 三层特性（Tier 1/2/3）
- ✅ `docs/architecture.md` 分层图加入 L1.5 Agent 层

### M1 · 开源中立清理（品牌示例字样统一）（2026-04-16）
- ✅ 移除历史遗留的品牌示例字样，统一为 com.example（docs/capabilities/*, tests/capabilities/test_app.py, docs/methods/01-ssh-tunnel-adb.md）
- ✅ `git-filter-repo` 精确改写历史，清理旧 commit diff 里的品牌示例残留（commit hash 已全部重生）

---

## [0.0.1] · 2026-04-15

### Added
- M0 · 初始仓库骨架
- 完整技术方案文档（`docs/` 下 15 篇）
  - 总览 / 架构 / 设计决策 / LLM 集成
  - 权限系统 / 错误码 / tool 编写指南
  - 项目计划（M0-M3 里程碑）
  - 方案对比 + 4 方案详细（A/B/C/G）+ 3 方案占位（D/E/F）
  - 6 业务能力文档（shell/logging/filesync/diagnose/power/app）
  - 贡献指南
- LLM 集成配置模板（Claude Code / Cursor / Codex）
- `llm/CLAUDE.md` + `llm/AGENTS.md`
- Python 骨架（`pyproject.toml` + `src/alb/` 各模块 `__init__.py`）
  - `infra/`: result / errors / permissions / registry / workspace
  - `transport/`: base.py (ABC)
  - `capabilities/` `cli/` `mcp/` `api/` 占位
- 冒烟测试（验证包可 import、registry 非空、权限黑名单生效）
- MIT License
