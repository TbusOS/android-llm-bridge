"""ChatSession — JSONL-persisted chat history under workspace/sessions/<id>/.

Layout::

    workspace/sessions/<session-id>/
        messages.jsonl        # append-only, one Message per line
        meta.json             # session metadata (created, backend, model, device)
        summary.md            # M3: auto-generated session summary

`messages.jsonl` doubles as the audit trail required by ADR-007 (long logs
stay on disk, not in the LLM window) and as the replay source for debugging
bad tool-call sequences.

Status: SKELETON.  Public surface (`create` / `load` / `append` / `messages`)
is stable; persistence is a no-op stub until M3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from alb.agent.backend import Message
from alb.infra.workspace import session_path


def new_session_id() -> str:
    """`<utc-date>-<short-uuid>` — sorts lexicographically by creation time."""
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    short = uuid.uuid4().hex[:8]
    return f"{date}-{short}"


@dataclass
class ChatSession:
    """Persisted chat session.

    Construct via `ChatSession.create()` for new sessions or
    `ChatSession.load(session_id)` to resume.  Both will be fully wired up
    in M3 — for now they're placeholders so downstream code can import them.
    """

    session_id: str
    backend: str = ""
    model: str = ""
    device: str | None = None
    _messages: list[Message] = field(default_factory=list, repr=False)

    # ── Construction ────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        *,
        backend: str = "",
        model: str = "",
        device: str | None = None,
    ) -> "ChatSession":
        """Create a new session directory and return the handle."""
        sid = new_session_id()
        session_path(sid)  # ensure directory exists
        return cls(session_id=sid, backend=backend, model=model, device=device)

    @classmethod
    def load(cls, session_id: str) -> "ChatSession":
        """Resume an existing session by reading `messages.jsonl`.

        M3 implementation will parse the JSONL.  For now returns an empty
        handle so the API surface is testable.
        """
        return cls(session_id=session_id)

    # ── Message operations ──────────────────────────────────────────
    def append(self, message: Message) -> None:
        """Append to in-memory history and (M3) flush to messages.jsonl."""
        self._messages.append(message)

    def messages(self) -> list[Message]:
        """Snapshot of history (does not include system prompt)."""
        return list(self._messages)

    # ── Paths ───────────────────────────────────────────────────────
    @property
    def dir(self) -> Path:
        return session_path(self.session_id)

    @property
    def messages_file(self) -> Path:
        return self.dir / "messages.jsonl"

    @property
    def meta_file(self) -> Path:
        return self.dir / "meta.json"
