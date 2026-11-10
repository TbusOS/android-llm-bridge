"""docs/configuration.md must keep up with the code.

The configuration reference is the one page that lists every setting. It
used to be missing: of the 43 ``ALB_*`` variables the code knew on
2026-11-10, 23 were in no document, so a person (or an AI) had no way to
learn that e.g. ``ALB_SERIAL_DEVICE`` is the only way to pin a local
``/dev/ttyUSB*``. These tests fail as soon as the code and the page drift.

What counts as a variable "the code reads": every ``ALB_[A-Z0-9_]+`` name in
a **string literal** (including docstrings and the text parts of f-strings)
of ``src/alb/**/*.py`` and ``clients/windows-agent/*.py``. Comments do not
count. A name only mentioned in a help text or an error suggestion still
counts on purpose: a user is told to set it, so the page must say what it
does — or that it does nothing (``ALB_OPENAI_COMPAT_MODEL`` is such a case).

NOT_CONFIG lists names that match the pattern but are not settings. It is
empty today; add a name only with the reason next to it.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

from alb.infra import config as config_mod

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "configuration.md"
_AGENT = _ROOT / "clients" / "windows-agent" / "alb_agent.py"
# A whole name only: "ALB_SSH_*" (a prefix with a wildcard) is not a name.
_NAME = re.compile(r"(?<![A-Za-z0-9_])ALB_[A-Z0-9_]*[A-Z0-9](?![A-Za-z0-9_*])")

NOT_CONFIG: dict[str, str] = {}


def _code_files() -> list[Path]:
    return sorted((_ROOT / "src" / "alb").rglob("*.py")) + sorted(
        (_ROOT / "clients" / "windows-agent").glob("*.py")
    )


def code_env_names() -> dict[str, list[str]]:
    """{ALB_* name: files whose string literals mention it}."""
    found: dict[str, list[str]] = {}
    for path in _code_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in _NAME.findall(node.value):
                    files = found.setdefault(name, [])
                    rel = str(path.relative_to(_ROOT))
                    if rel not in files:
                        files.append(rel)
    return found


def _doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_scan_sees_the_code() -> None:
    files = _code_files()
    assert len(files) >= 100, f"only {len(files)} files scanned: is _ROOT right?"
    names = code_env_names()
    # Fixed points that must be found, or the scan itself is broken.
    for known in ("ALB_SERIAL_DEVICE", "ALB_AGENT_TOKEN", "ALB_AGENT_LAUNCHER"):
        assert known in names, f"{known} not found in the code scan — scan is broken"


def test_every_code_env_var_is_documented() -> None:
    doc = _doc_text()
    documented = set(_NAME.findall(doc))
    names = code_env_names()
    missing = sorted(n for n in names if n not in documented and n not in NOT_CONFIG)
    assert not missing, (
        f"{len(missing)} ALB_* variable(s) used in the code but missing from "
        f"docs/configuration.md — add a row (作用 / 默认值 / 谁读 / 代码):\n"
        + "\n".join(f"  {n}  ({', '.join(names[n])})" for n in missing)
    )


def test_doc_has_no_env_var_the_code_does_not_know() -> None:
    """The other direction: a variable removed from the code must leave the page."""
    names = code_env_names()
    stale = sorted(set(_NAME.findall(_doc_text())) - set(names))
    assert not stale, (
        "docs/configuration.md describes ALB_* variable(s) that no code mentions "
        "any more — remove them or say where they went:\n" + "\n".join(f"  {n}" for n in stale)
    )


def _config_file_keys() -> list[str]:
    """Keys of config.toml / profile sections, as the TOML spells them.

    These dataclasses are built with ``Cls(**table)``, so their field names
    are exactly the keys a user writes."""
    classes = (
        config_mod.AdbConfig,
        config_mod.SshConfig,
        config_mod.SerialConfig,
        config_mod.PermissionConfig,
        config_mod.DeviceEntry,
    )
    keys = [f.name for cls in classes for f in dataclasses.fields(cls)]
    # Read with raw.get(...) in config.py rather than through a dataclass.
    keys += ["default_profile", "root", "primary_transport", "name"]
    return keys


def _agent_conf_keys() -> list[str]:
    """agent.conf keys from alb_agent.py's _CONFIG_KEYS / _BOARD_CONFIG_KEYS.

    Read with ast, not imported: the agent is a standalone script."""
    tree = ast.parse(_AGENT.read_text(encoding="utf-8"), filename=str(_AGENT))
    tables: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        target = value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target in ("_CONFIG_KEYS", "_BOARD_CONFIG_KEYS") and isinstance(value, ast.Dict):
            tables[target] = [k.value for k in value.keys if isinstance(k, ast.Constant)]
    assert set(tables) == {"_CONFIG_KEYS", "_BOARD_CONFIG_KEYS"}, (
        f"agent.conf key tables not found in {_AGENT.name}: found {sorted(tables)}"
    )
    return tables["_CONFIG_KEYS"] + [f"{k}.<board>" for k in tables["_BOARD_CONFIG_KEYS"]]


def test_every_config_file_key_is_documented() -> None:
    """Each key must appear in backticks, e.g. `default_baud` — a bare word
    like name or serial would match anywhere in the prose."""
    doc = _doc_text()
    missing = sorted(
        {f"config.toml/profile: {k}" for k in _config_file_keys() if f"`{k}`" not in doc}
        | {f"agent.conf: {k}" for k in _agent_conf_keys() if f"`{k}`" not in doc}
    )
    assert not missing, (
        "config-file key(s) missing from docs/configuration.md (write each in backticks):\n"
        + "\n".join(f"  {m}" for m in missing)
    )
