"""Generate SKILL.md from the live registry + capability docstrings.

Inspired by CLI-Anything. SKILL.md is a machine-readable surface-area
description that an LLM client can pre-read to learn what this MCP server
offers, without needing to actually launch the server.

Usage:
    alb skills generate             # writes src/alb/skills/SKILL.md
    alb skills show                 # prints the generated path

Design:
- Pull capability metadata from alb.infra.registry.
- For each capability, also import the implementation module and grab
  module-level docstrings.
- The result is a stable, deterministic Markdown file, safe to commit or
  regenerate in CI.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import asdict
from pathlib import Path

from alb import __version__
from alb.infra.registry import CAPABILITIES, TRANSPORTS


HEADER_TEMPLATE = """\
---
name: android-llm-bridge
description: >
  Unified Android debugging bridge for LLM agents. Abstracts adb / ssh /
  UART over a single interface; exposes structured tools via MCP, CLI,
  and a future Web API.
version: {version}
homepage: https://github.com/TbusOS/android-llm-bridge
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

- Every tool returns `{{"ok", "data", "error", "artifacts", "timing_ms"}}`.
- On failure read `error.code` (stable enum) and `error.suggestion`
  (actionable). Full catalog: docs/errors.md.
- Long outputs land in `workspace/devices/<serial>/<category>/`.
  Read them with `alb_log_search` / `alb_log_tail`, don't flood your
  context window.
- Dangerous commands are blocked by default. Use `allow_dangerous=True`
  for ASK-level ops; DENY is never bypassable.
"""


def _import_module_safely(dotted: str):
    """Best-effort import. Returns None if the module isn't importable."""
    try:
        return importlib.import_module(dotted)
    except Exception:  # noqa: BLE001
        return None


def _module_doc(mod) -> str:  # type: ignore[no-untyped-def]
    if mod is None:
        return ""
    doc = inspect.getdoc(mod) or ""
    return doc.strip()


def render() -> str:
    """Return the full SKILL.md as a string."""
    out: list[str] = [HEADER_TEMPLATE.format(version=__version__)]

    # ── Transports table ───────────────────────────────────────
    out.append("\n## Supported transports\n")
    out.append("| name | methods | status | requires |")
    out.append("|------|---------|--------|----------|")
    for t in TRANSPORTS:
        out.append(
            f"| {t.name} | {','.join(t.methods_supported) or '—'} | "
            f"{t.status} | {', '.join(t.requires) or '—'} |"
        )

    # ── Capabilities ──────────────────────────────────────────
    out.append("\n## Capabilities\n")
    for c in CAPABILITIES:
        out.append(f"### `{c.name}` — {c.description}")
        out.append("")
        out.append(f"- **Status**: {c.status}")
        out.append(f"- **CLI**: `{c.cli_command}`")
        out.append(
            f"- **Supported transports**: {', '.join(c.supported_transports) or '—'}"
        )
        if c.mcp_tools:
            out.append("- **MCP tools**:")
            for tool in c.mcp_tools:
                out.append(f"  - `{tool}`")
        # Module docstring if available
        mod = _import_module_safely(c.impl_path)
        doc = _module_doc(mod)
        if doc:
            first_para = doc.split("\n\n", 1)[0]
            out.append("")
            for line in first_para.splitlines():
                out.append(f"> {line}")
        out.append("")

    # ── Error catalog summary ─────────────────────────────────
    out.append("\n## Error codes (most common)\n")
    out.append("| code | category | default suggestion |")
    out.append("|------|----------|-------------------|")
    from alb.infra.errors import ERROR_CODES

    for code in sorted(ERROR_CODES):
        spec = ERROR_CODES[code]
        sugg = spec.default_suggestion.replace("|", r"\|")
        out.append(f"| `{spec.code}` | {spec.category} | {sugg} |")

    out.append("")
    out.append("Full catalog: [docs/errors.md](../../../docs/errors.md)\n")
    return "\n".join(out) + "\n"


def default_output_path() -> Path:
    return Path(__file__).parent / "SKILL.md"


def generate(output: Path | None = None) -> Path:
    """Write SKILL.md. Returns the path written."""
    dest = output or default_output_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(), encoding="utf-8")
    return dest


# Optional: dump registry as JSON for machine-consumption alongside the md.
def dump_registry_json(output: Path | None = None) -> Path:
    import json

    dest = output or (default_output_path().parent / "SKILL.json")
    payload = {
        "version": __version__,
        "transports": [asdict(t) for t in TRANSPORTS],
        "capabilities": [asdict(c) for c in CAPABILITIES],
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


if __name__ == "__main__":
    md = generate()
    js = dump_registry_json()
    print(f"wrote {md}")
    print(f"wrote {js}")
