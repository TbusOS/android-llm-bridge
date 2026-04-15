---
name: android-llm-bridge
description: >
  Unified Android debugging bridge for LLM agents. Abstracts adb / ssh /
  UART over a single interface; exposes structured tools via MCP, CLI,
  and a future Web API.
version: 0.0.1
homepage: https://github.com/skyzhangbinghua/android-llm-bridge
license: MIT
---

# android-llm-bridge · SKILL.md

> This file is auto-generated from the registry. Do not edit by hand —
> regenerate with: `alb skills generate`.

## Invocation styles

- **MCP** (recommended): configure `alb-mcp` in your client. All tools
  appear with `alb_` prefix. See llm/mcp-config-examples/.
- **CLI**: `uv run alb <group> <command>` inside the repo.
- **HTTP** (M2): FastAPI surface; see docs/architecture.md.

## Global conventions

- Every tool returns `{"ok", "data", "error", "artifacts", "timing_ms"}`.
- On failure read `error.code` (stable enum) and `error.suggestion` (actionable).
- Long outputs land in `workspace/devices/<serial>/<category>/`.
  Read them with `alb_log_search` / `alb_log_tail`.
- Dangerous commands are blocked by default. Use `allow_dangerous=True`
  for ASK-level ops; DENY is never bypassable.

## Supported transports

| name | methods | status | requires |
|------|---------|--------|----------|
| adb | A,B | beta | adb binary |
| ssh | C,D,F | beta | asyncssh, rsync (for rsync_sync) |
| serial | G | beta | pyserial-asyncio, ser2net (Windows for TCP mode) |
| hybrid | — | planned | — |

## Capabilities (M1 ships 6)

| name | CLI | MCP tools | transports |
|------|-----|-----------|------------|
| shell | `alb shell` | `alb_shell` | adb, ssh, serial |
| logging | `alb logcat / dmesg / log search / log tail` + `alb serial capture` | `alb_logcat`, `alb_dmesg`, `alb_uart_capture`, `alb_log_search`, `alb_log_tail` | adb, ssh, serial |
| filesync | `alb fs push / pull / rsync` | `alb_push`, `alb_pull`, `alb_rsync` | adb, ssh |
| diagnose | `alb diag bugreport / devinfo / anr pull / tombstone pull` | `alb_bugreport`, `alb_anr_pull`, `alb_tombstone_pull`, `alb_devinfo` | adb, ssh |
| power | `alb power reboot / wait-boot / battery / sleep-wake` | `alb_reboot`, `alb_wait_boot`, `alb_battery`, `alb_sleep_wake_test` | adb, ssh, serial |
| app | `alb app install / uninstall / start / stop / list / info / clear-data` | `alb_app_*` | adb, ssh |

Full per-capability reference: [docs/capabilities/](../../../docs/capabilities/)
Full error catalog: [docs/errors.md](../../../docs/errors.md)
