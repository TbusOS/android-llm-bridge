# Changelog

> 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。
> 版本编号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### M1 · 进行中
- 计划：4 传输（A/B/C/G）实现
- 计划：6 能力（shell/logging/filesync/diagnose/power/app）
- 计划：权限系统
- 计划：CLI + MCP 骨架 → 可用

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
