# android-llm-bridge

> **A unified Android debugging bridge designed for LLM agents** — abstracts adb / ssh / UART over a single interface, accessible via MCP, CLI, or Web API.

<p align="center">
  <em>for Claude Code · Cursor · Codex · any MCP-compatible agent</em>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#supported-methods">Methods</a> ·
  <a href="#documentation">Docs</a> ·
  <a href="./README.md">中文</a>
</p>

---

## What is this

**android-llm-bridge** (`alb` for short) lets an AI agent safely and reliably operate a real Android device, regardless of how it is connected (USB adb, wireless adb, on-device sshd, or only UART serial).

### Why

Traditional `adb` / `ssh` works great for humans but is painful for LLMs:

- Free-form text output → parsing errors
- Errors are unstructured → agent can't recover
- Long logcat floods the context window
- No safety net for destructive commands (`rm -rf`, `reboot bootloader`)
- Each transport (USB / WiFi / serial) has its own commands to memorize

**alb fixes these**:
- Unified commands (`alb shell` / `alb logcat` / `alb pull`), transport auto-routed
- Structured JSON output with stable error codes
- Built-in permission system with pattern-based blocklist and multi-layer policy
- Tiered workspace storage for long logs (hot / warm / cold) with search
- First-class MCP server — one line of config for Claude Code, Cursor, etc.

---

## Features

| Feature | Description |
|---------|-------------|
| **Multi-transport abstraction** | adb USB · adb WiFi · on-device sshd · UART serial — same upper API, auto-routed |
| **LLM-first API** | Structured return, error catalog, self-describing CLI, auto-generated `SKILL.md` |
| **Three interfaces** | CLI (`alb`) · MCP server (`alb-mcp`) · Web API (FastAPI), sharing one business layer |
| **Permissions & safety** | Dangerous-command blocklist + tool-level `check_permissions` + layered policy |
| **Workspace conventions** | All artifacts under `workspace/devices/<serial>/...` — predictable paths |
| **Long-log friendly** | Tiered storage + paged access — no context overflow |
| **Web visualization** (later) | Device dashboard, live logs, perf charts |

---

## Supported Methods

M1 ships **A / B / C / G**. D / E / F are scaffolded for later.

| Method | Channel | Boot log | u-boot | Offline | Device hung | One-liner |
|--------|---------|:----:|:----:|:----:|:----:|-----------|
| **A · adb USB (with SSH reverse tunnel)** | adb | ❌ | ❌ | ✅ | ❌ | System-level baseline, must-have |
| **B · adb WiFi** | TCP | ❌ | ❌ | ❌ | ❌ | Wireless, ad-hoc |
| **C · On-device sshd** | ssh | ❌ | ❌ | ❌ | ❌ | Dev boost (rsync / tmux / sshfs) |
| **G · UART serial** | serial | ✅ | ✅ | ✅ | ✅ | **Last resort** — bringup / panic rescue |
| D · USB networking | IP-over-USB | - | - | - | - | _planned_ |
| E · scrcpy mirror | adb | - | - | - | - | _planned_ |
| F · frp / cloud relay | public net | - | - | - | - | _planned_ |

See [`docs/methods/00-comparison.md`](./docs/methods/00-comparison.md) for the full matrix.

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│  Interface layer                                        │
│  ├─ CLI (typer)          human or LLM direct use       │
│  ├─ MCP server           Claude Code / Cursor / Codex  │
│  ├─ Web API (FastAPI)    UI / external integrations    │
│  └─ Web UI (later)       visualization                 │
├────────────────────────────────────────────────────────┤
│  Capabilities (M1 ships 6)                             │
│  shell │ logging │ filesync │ diagnose │ power │ app   │
├────────────────────────────────────────────────────────┤
│  ⭐ Transport abstraction                               │
│  shell / stream_read / push / pull /                    │
│  forward / reboot / check_permissions                   │
│  ├─ AdbTransport       (A / B)                         │
│  ├─ SshTransport       (C / D / F)                     │
│  ├─ SerialTransport    (G)                             │
│  └─ HybridTransport    (smart routing)                 │
├────────────────────────────────────────────────────────┤
│  Drivers                                                │
│  adb · ssh · scp · rsync · pyserial · socat · tunnels   │
├────────────────────────────────────────────────────────┤
│  Infra                                                  │
│  config · workspace · profile · permissions · errors    │
│  event-bus · prompt-builder · memory (M2+)              │
└────────────────────────────────────────────────────────┘
```

See [`docs/architecture.md`](./docs/architecture.md) for the details.

---

## Quick start

> ⚠️ **Current state: M0 (skeleton + full design docs).** Code implementation is scheduled for M1. See [`docs/project-plan.md`](./docs/project-plan.md) for the roadmap.

### Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- An Android device reachable via at least one channel

### Install (after M1)

```bash
git clone https://github.com/<your>/android-llm-bridge
cd android-llm-bridge
uv sync
uv run alb --help
```

### Set up a transport

```bash
uv run alb setup adb         # Method A (USB)
uv run alb setup ssh         # Method C (on-device sshd)
uv run alb setup serial      # Method G (UART)
uv run alb setup wifi        # Method B
```

### Hook into Claude Code

```json
{
  "mcpServers": {
    "alb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/android-llm-bridge", "alb-mcp"]
    }
  }
}
```

---

## Documentation

| Category | Doc | Audience |
|----------|-----|----------|
| Start here | [README.md](./README.md) / README.en.md | Everyone |
| Overview | [docs/00-overview.md](./docs/00-overview.md) | Start here |
| Architecture | [docs/architecture.md](./docs/architecture.md) | Internals |
| Design decisions | [docs/design-decisions.md](./docs/design-decisions.md) | Why this way |
| LLM integration | [docs/llm-integration.md](./docs/llm-integration.md) | Agent devs |
| Permissions | [docs/permissions.md](./docs/permissions.md) | Ops / safety |
| Error catalog | [docs/errors.md](./docs/errors.md) | LLMs, debugging |
| Tool writing | [docs/tool-writing-guide.md](./docs/tool-writing-guide.md) | Contributors |
| Project plan | [docs/project-plan.md](./docs/project-plan.md) | Roadmap |
| Contributing | [docs/contributing.md](./docs/contributing.md) | Contributors |
| Methods | [docs/methods/](./docs/methods/) | Per-transport |
| Capabilities | [docs/capabilities/](./docs/capabilities/) | Per-feature |

---

## Roadmap

| Milestone | Deliverables | Status |
|-----------|--------------|--------|
| **M0** | Skeleton + complete design docs + architecture + roadmap | ✅ current |
| **M1** | 4 transports (A/B/C/G) + 6 capabilities + permissions + CLI + MCP skeleton | 🚧 in progress |
| **M2** | Web API + streaming / long-task framework + sub-agent parallel + perf + benchmark | 📋 planned |
| **M3** | Web UI + LLM-assisted log analysis + methods D/E/F | 📋 planned |

---

## License

[MIT](./LICENSE) © 2026 sky &lt;skyzhangbinghua@gmail.com&gt;
