"""ChatSession — JSONL-persisted chat history under workspace/sessions/<id>/.

Layout::

    workspace/sessions/<session-id>/
        messages.jsonl        # append-only, one Message per line
        meta.json             # session metadata (created, backend, model, device)
        summary.md            # M3: auto-generated session summary (not yet)

`messages.jsonl` doubles as the audit trail required by ADR-007 (long logs
stay on disk, not in the LLM window) and as the replay source for debugging
bad tool-call sequences.
"""

from __future__ import annotations

import json
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
    `ChatSession.load(session_id)` to resume.

    append() writes to `messages.jsonl` immediately so a crashed process
    doesn't lose partial history; there is no buffering layer.
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
        """Create a new session directory + meta.json and return the handle."""
        sid = new_session_id()
        s = cls(session_id=sid, backend=backend, model=model, device=device)
        s.dir.mkdir(parents=True, exist_ok=True)
        s._write_meta()
        return s

    @classmethod
    def load(cls, session_id: str) -> "ChatSession":
        """Resume an existing session by reading meta.json + messages.jsonl.

        Missing files are tolerated (fresh session); malformed JSONL lines
        are skipped with no error (best-effort replay).
        """
        s = cls(session_id=session_id)
        if s.meta_file.exists():
            try:
                meta = json.loads(s.meta_file.read_text())
            except json.JSONDecodeError:
                meta = {}
            s.backend = meta.get("backend") or ""
            s.model = meta.get("model") or ""
            s.device = meta.get("device")

        if s.messages_file.exists():
            for line in s.messages_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    s._messages.append(Message.from_dict(d))
                except (KeyError, TypeError):
                    continue
        return s

    # ── Message operations ──────────────────────────────────────────
    def append(self, message: Message) -> None:
        """Append to in-memory history and flush to messages.jsonl."""
        self._messages.append(message)
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.messages_file.open("a", encoding="utf-8") as f:
            json.dump(message.to_dict(), f, ensure_ascii=False)
            f.write("\n")

    def messages(self) -> list[Message]:
        """Snapshot of history (does not include system prompt)."""
        return list(self._messages)

    def clear(self) -> None:
        """Drop in-memory and on-disk history (keeps meta + directory).

        Used by `/clear` command in the REPL.
        """
        self._messages.clear()
        if self.messages_file.exists():
            self.messages_file.unlink()

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

    # ── Internal ────────────────────────────────────────────────────
    def _write_meta(self) -> None:
        meta = {
            "session_id": self.session_id,
            "created": datetime.now(timezone.utc).isoformat(),
            "backend": self.backend,
            "model": self.model,
            "device": self.device,
        }
        self.meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
