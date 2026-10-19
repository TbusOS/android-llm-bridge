"""setup_remote_start.py (agent host): the parts that decide, tested anywhere.

The steps that change Windows (icacls, schtasks, the UAC relaunch) only run
there and are not exercised here. What is locked: which key text is
accepted, which file the key goes to, that adding / removing is exact and
repeatable, and what the scheduled task is told to do.
"""

from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "setup_remote_start.py"


def _load():
    spec = importlib.util.spec_from_file_location("setup_remote_start_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


srs = _load()

BODY = "AAAAC3NzaC1lZDI1NTE5AAAAIN9zSNAgB41h0lNqnIVLQ0LGCHMcg2fIhdjAM4vhIAp/"
KEY = f"ssh-ed25519 {BODY} alb-hub-to-bench"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


# ── the key text ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [KEY, f"  {KEY}  \n", f"\ufeff{KEY}", f"\n{KEY}\n\n", f"ssh-ed25519 {BODY}"],
)
def test_a_public_key_line_is_accepted(text):
    line, body = srs.parse_key(text)
    assert body == BODY and line.startswith("ssh-ed25519 ")


@pytest.mark.parametrize(
    "text, why",
    [
        ("", "exactly one line"),
        (f"{KEY}\n{KEY}", "exactly one line"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "private key"),
        (f'command="cmd /c whoami" {KEY}', "not a public key line"),
        (f'from="*" {KEY}', "not a public key line"),
        ("ssh-dss AAAAB3NzaC1kc3M= old", "not a public key line"),
        (f"ssh-ed25519 {BODY} x\x07y", "not a public key line"),
    ],
)
def test_anything_else_is_refused_with_a_reason(text, why):
    """Pasted by hand: an options prefix (command=, from=) would change what
    the key may do, and a private key must never end up in the file."""
    with pytest.raises(srs.SetupError, match=why):
        srs.parse_key(text)


# ── which file ──────────────────────────────────────────────────────────


def test_an_administrator_s_key_goes_to_the_shared_file():
    """Stock Windows OpenSSH reads only this file for Administrators members;
    a key in their ~/.ssh is silently ignored."""
    p = srs.key_file_for(True, r"C:\ProgramData", r"C:\Users\bench")
    assert p.name == "administrators_authorized_keys" and p.parent.name == "ssh"


def test_an_ordinary_user_s_key_goes_to_their_profile():
    p = srs.key_file_for(False, r"C:\ProgramData", "/home/u")
    assert p == Path("/home/u") / ".ssh" / "authorized_keys"


def test_membership_is_read_from_the_sid_not_the_localized_group_name():
    groups = (
        "BUILTIN\\Administrators  Alias  S-1-5-32-544  Group used for deny only\r\n"
        "BUILTIN\\Users           Alias  S-1-5-32-545  Mandatory group\r\n"
    )
    assert srs.is_admin_member(groups)
    assert srs.is_admin_member(groups.replace("Administrators", "管理员组"))
    assert not srs.is_admin_member("BUILTIN\\Users  Alias  S-1-5-32-545\r\n")


# ── adding / removing ───────────────────────────────────────────────────


def test_adding_is_exact_and_repeatable():
    other = "ssh-rsa AAAAB3NzaC1yc2E= someone-else"
    once = srs.with_key(other, KEY, BODY)  # no trailing newline before
    assert once == f"{other}\n{KEY}\n"
    assert srs.with_key(once, KEY, BODY) is None, "a second run must not add it twice"
    assert srs.with_key("", KEY, BODY) == f"{KEY}\n"


def test_removing_takes_only_that_key():
    other = "ssh-rsa AAAAB3NzaC1yc2E= someone-else"
    text = f"{other}\n{KEY}\n"
    assert srs.without_key(text, BODY) == f"{other}\n"
    assert srs.without_key(f"{other}\n", BODY) is None


def test_a_body_inside_another_token_is_not_taken_for_the_key():
    tricky = f"ssh-ed25519 X{BODY} not-the-same-key"
    assert srs.with_key(tricky, KEY, BODY) is not None
    assert srs.without_key(tricky, BODY) is None


# ── the scheduled task ─────────────────────────────────────────────────


def _task(user: str = "BENCH\\tester", agent_dir: str = r"D:\alb-windows-agent") -> ET.Element:
    return ET.fromstring(srs.task_xml(user, agent_dir).split("?>", 1)[1])


def test_the_task_runs_run_agent_bat_unattended_in_the_users_session():
    t = _task()
    assert t.findtext("t:Principals/t:Principal/t:LogonType", namespaces=NS) == "InteractiveToken"
    assert t.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=NS) == "LeastPrivilege"
    assert t.findtext("t:Principals/t:Principal/t:UserId", namespaces=NS) == "BENCH\\tester"
    assert t.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == "cmd.exe"
    args = t.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS)
    assert args == '/c "set ALB_AGENT_NO_PAUSE=1&& run-agent.bat"'
    assert t.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == (
        r"D:\alb-windows-agent"
    )


def test_the_task_is_not_stopped_after_three_days_or_on_battery():
    s = _task().find("t:Settings", NS)
    assert s is not None
    assert s.findtext("t:ExecutionTimeLimit", namespaces=NS) == "PT0S"
    assert s.findtext("t:DisallowStartIfOnBatteries", namespaces=NS) == "false"
    assert s.findtext("t:StopIfGoingOnBatteries", namespaces=NS) == "false"


def test_it_also_starts_at_this_users_logon():
    trig = _task().find("t:Triggers/t:LogonTrigger", NS)
    assert trig is not None and trig.findtext("t:UserId", namespaces=NS) == "BENCH\\tester"


def test_a_folder_name_with_xml_characters_stays_one_value():
    t = _task(agent_dir=r"D:\R&D <bench>")
    assert t.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == r"D:\R&D <bench>"


def test_off_windows_it_changes_nothing(capsys):
    assert srs.main(["--key", KEY]) == 2
    assert "run it there" in capsys.readouterr().out
