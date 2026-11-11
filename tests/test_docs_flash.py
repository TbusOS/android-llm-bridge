"""docs/flash.md covers every `alb flash` subcommand.

The page is how people find out what `alb flash` can do; a subcommand added
to the CLI without a line there exists only for whoever reads --help. The
names come from the typer app itself, so a new command fails this test until
the page mentions it."""

from __future__ import annotations

import re
from pathlib import Path

from alb.cli.flash_cli import app

_DOC = Path(__file__).resolve().parents[1] / "docs" / "flash.md"


def _commands() -> set[str]:
    names = {c.name or (c.callback.__name__ if c.callback else "") for c in app.registered_commands}
    return {n for n in names if n}


def test_the_command_list_is_read_from_the_cli() -> None:
    """Guard the guard: an empty set would pass the test below."""
    assert {"write", "plan", "tool", "tools"} <= _commands()


def test_every_flash_subcommand_is_documented() -> None:
    text = _DOC.read_text(encoding="utf-8")
    missing = sorted(c for c in _commands() if not re.search(rf"`alb flash {c}\b", text))
    assert missing == [], f"add these to docs/flash.md: {missing}"
