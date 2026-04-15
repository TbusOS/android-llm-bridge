---
title: MCP 客户端接入示例
type: reference
owner: sky
---

# MCP 客户端配置示例

本目录收集各家 LLM 客户端接入 `android-llm-bridge` 的 MCP 配置模板。

## 文件一览

| 文件 | 客户端 | 配置文件位置 |
|-----|------|------------|
| [claude-code.json](./claude-code.json) | Claude Code | `~/.claude/mcp-settings.json` |
| [cursor.json](./cursor.json) | Cursor | `~/.cursor/mcp.json` |
| [codex.json](./codex.json) | OpenAI Codex CLI | `~/.codex/config.json` |

## 使用

1. 复制对应文件内容到你的客户端配置文件
2. 把 `/path/to/android-llm-bridge` 换成你 clone 的实际路径
3. 按需调整 `ALB_WORKSPACE` / `ALB_PROFILE`
4. 首次使用前先手动跑 `alb setup <method>` 配置一种传输
5. 重启 LLM 客户端

## 验证

在客户端里问："use alb to list devices"，如果能拿到 `alb_devices` 的结果就接入成功。

## 贡献你的客户端

欢迎 PR 添加其他 MCP 兼容客户端（Cline / Continue / Aider / ...）的配置示例。
模板：一个 JSON 文件 + 主要字段注释（`_comment` / `_notes` 前缀）。
