"""CLI: `alb session list / show / replay` — inspect persisted ChatSessions.

ChatSession already lands meta.json + messages.jsonl under
`workspace/sessions/<id>/`. This module surfaces them on the CLI so users
don't have to `cat messages.jsonl | jq` themselves.

Three sub-commands:

    alb session list [--limit 20]
        Newest-first table of session_id / created / backend / model /
        device / turns.

    alb session show <session-id> [--full]
        Print meta + a head/tail summary (default: first 5 + last 5
        messages, eliding the middle). `--full` prints every message.

    alb session replay <session-id>
        Stream every message in order, rendered with role-coloured
        headers + content + tool_calls. Equivalent to
        `cat messages.jsonl | <pretty-printer>`.

The Web UI's `/sessions` endpoint reads the same files; CLI and Web
agree on the format because they both go through ChatSession / its
on-disk layout.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from alb.agent.session import ChatSession
from alb.infra.workspace import InvalidSessionId, session_path, workspace_root

app = typer.Typer(help="Inspect persisted chat sessions.")
console = Console()


# ─── Filesystem helpers ────────────────────────────────────────────


def _sessions_root() -> Path:
    return workspace_root() / "sessions"


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("rb") as f:
        for _ in f:
            n += 1
    return n


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).isoformat(timespec="seconds")


def _load_meta(meta_file: Path) -> dict[str, Any]:
    if not meta_file.exists():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _summarize_session(session_dir: Path) -> dict[str, Any]:
    meta = _load_meta(session_dir / "meta.json")
    messages = session_dir / "messages.jsonl"
    return {
        "session_id": meta.get("session_id") or session_dir.name,
        "created": meta.get("created"),
        "backend": meta.get("backend") or "",
        "model": meta.get("model") or "",
        "device": meta.get("device"),
        "turns": _count_lines(messages),
        "last_event_ts": _mtime_iso(messages),
    }


def _scan_sessions() -> list[dict[str, Any]]:
    """Return all session summaries, newest first.

    Sort key: meta.created (ISO 8601 sorts lexicographically); falls
    back to the directory name (session_id format `<utc-date>-<short-uuid>`
    sorts by time too).
    """
    root = _sessions_root()
    if not root.exists():
        return []
    out = [_summarize_session(p) for p in root.iterdir() if p.is_dir()]
    out.sort(
        key=lambda s: s.get("created") or s["session_id"],
        reverse=True,
    )
    return out


# ─── list ──────────────────────────────────────────────────────────


@app.command("list")
def cmd_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
) -> None:
    """List recent sessions, newest first."""
    sessions = _scan_sessions()[:limit]

    if (ctx.obj or {}).get("json"):
        print(json.dumps({"ok": True, "sessions": sessions}, indent=2))
        return

    if not sessions:
        console.print(
            Panel.fit(
                f"No sessions found under [dim]{_sessions_root()}[/]",
                title="alb session list",
                border_style="yellow",
            )
        )
        return

    table = Table(title=f"sessions (newest {len(sessions)} of total)")
    table.add_column("session_id", style="cyan", no_wrap=True)
    table.add_column("created", no_wrap=True)
    table.add_column("backend")
    table.add_column("model")
    table.add_column("device")
    table.add_column("turns", justify="right")
    for s in sessions:
        table.add_row(
            s["session_id"],
            (s.get("created") or "—")[:19],  # trim micros / TZ for width
            s.get("backend") or "—",
            s.get("model") or "—",
            s.get("device") or "—",
            str(s.get("turns") or 0),
        )
    console.print(table)


# ─── show ──────────────────────────────────────────────────────────


def _ensure_session_exists(session_id: str) -> Path:
    """Return the session dir; raise typer.Exit(1) on missing / invalid id.

    Validation runs through `infra.workspace.session_path` so traversal
    attempts (`../etc`, absolute paths, separators) are rejected at the
    same root layer that protects every other entry point (chat_cli /
    Web UI / future MCP tool). See L-035.
    """
    try:
        sdir = session_path(session_id, ensure_dir=False)
    except InvalidSessionId as e:
        console.print(f"[red]✗[/] invalid session id [bold]{session_id}[/]")
        console.print(f"  [dim]{e}[/]")
        raise typer.Exit(1) from None
    if not sdir.is_dir():
        console.print(
            f"[red]✗[/] no session [bold]{session_id}[/] under "
            f"[dim]{_sessions_root()}[/]"
        )
        console.print("  hint: run [bold]alb session list[/] to see available IDs")
        raise typer.Exit(1)
    return sdir


def _format_message(msg: Any, idx: int) -> str:
    """One-line-ish rendering of a single Message (rich markup)."""
    role = getattr(msg, "role", "?")
    color = {
        "system": "magenta",
        "user": "green",
        "assistant": "cyan",
        "tool": "yellow",
    }.get(role, "white")
    head = f"[bold {color}]#{idx} {role}[/]"
    if msg.name:
        head += f" [dim]({msg.name})[/]"
    body = msg.content.strip()
    if msg.tool_calls:
        tc_lines = [
            f"  [dim]→ tool_call[/] [bold]{tc.name}[/]"
            f"({json.dumps(tc.arguments, ensure_ascii=False)[:120]})"
            for tc in msg.tool_calls
        ]
        body = (body + "\n" if body else "") + "\n".join(tc_lines)
    return f"{head}\n{body}" if body else head


@app.command("show")
def cmd_show(
    ctx: typer.Context,
    session_id: str = typer.Argument(...),
    full: bool = typer.Option(
        False,
        "--full",
        help="Text-mode only: print every message (default: first 5 + last 5). "
        "JSON mode always returns all messages.",
    ),
) -> None:
    """Show meta + head/tail summary of one session.

    Output rules:
        - text mode default → first 5 + ellipsis + last 5 messages
        - text mode `--full` → every message rendered
        - `alb --json session show <id>` → always all messages (programmatic
          consumers get a stable shape; `--full` is ignored in JSON)
    """
    _ensure_session_exists(session_id)
    s = ChatSession.load(session_id)
    msgs = s.messages()

    if (ctx.obj or {}).get("json"):
        meta = _load_meta(s.meta_file)
        # JSON consumers always get the full list; --full is a human-render
        # flag (text mode elides middle when len > 10). Programmatic users
        # don't want their parser to hit a "5 head + 5 tail with elision
        # marker" surprise.
        print(json.dumps(
            {
                "ok": True,
                "meta": meta,
                "turns": len(msgs),
                "messages": [m.to_dict() for m in msgs],
            },
            indent=2,
            ensure_ascii=False,
        ))
        return

    meta = _load_meta(s.meta_file)
    info = (
        f"[bold]session_id[/] {s.session_id}\n"
        f"[bold]created[/]    {meta.get('created', '—')}\n"
        f"[bold]backend[/]    {s.backend or '—'} / {s.model or '—'}\n"
        f"[bold]device[/]     {s.device or '—'}\n"
        f"[bold]turns[/]      {len(msgs)}\n"
        f"[bold]dir[/]        [dim]{s.dir}[/]"
    )
    console.print(Panel.fit(info, title="alb session show", border_style="cyan"))

    if not msgs:
        console.print("[dim](no messages yet)[/]")
        return

    if full or len(msgs) <= 10:
        rendered = [(i, m) for i, m in enumerate(msgs)]
    else:
        head = [(i, msgs[i]) for i in range(5)]
        tail = [(i, msgs[i]) for i in range(len(msgs) - 5, len(msgs))]
        rendered = head + [(None, None)] + tail  # type: ignore[list-item]

    for i, m in rendered:
        if i is None:
            console.print(f"[dim]    … {len(msgs) - 10} message(s) elided …[/]")
            continue
        console.print(_format_message(m, i))
        console.print()


# ─── replay ────────────────────────────────────────────────────────


@app.command("replay")
def cmd_replay(
    ctx: typer.Context,
    session_id: str = typer.Argument(...),
) -> None:
    """Stream every message of a session in order (no eliding)."""
    _ensure_session_exists(session_id)
    s = ChatSession.load(session_id)
    msgs = s.messages()

    if (ctx.obj or {}).get("json"):
        print(json.dumps(
            {
                "ok": True,
                "session_id": s.session_id,
                "turns": len(msgs),
                "messages": [m.to_dict() for m in msgs],
            },
            indent=2,
            ensure_ascii=False,
        ))
        return

    if not msgs:
        console.print(f"[dim](session {session_id} has no messages)[/]")
        return

    for i, m in enumerate(msgs):
        console.print(_format_message(m, i))
        console.print()
