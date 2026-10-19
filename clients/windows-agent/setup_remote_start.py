"""Let the Linux hub start this agent over SSH (run once, on the agent host).

Why: the agent dials OUT to the hub, so nothing on the hub side can start it
when it is not running — someone has to walk over and double-click
run-agent.bat. This script sets up the one way in that is still safe:

1. authorizes ONE SSH public key (the hub's) for this Windows account, in the
   file the Windows OpenSSH server actually reads for it;
2. registers a scheduled task "alb-agent" that runs run-agent.bat in this
   user's desktop session.

The hub then runs `ssh <user>@<this host> schtasks /Run /TN alb-agent`. The
task, not ssh itself, starts the agent: a process started by an ssh session
gets no visible window and dies with the session, while the task's runs in
the logged-in desktop like a double-click (and also at every logon).

Nothing else is changed: no firewall rule, no sshd_config edit, no password.
`--remove` takes both changes back out.

Usage (double-click setup-remote-start.bat, or):
    py setup_remote_start.py                 # asks for the key
    py setup_remote_start.py --key "ssh-ed25519 AAAA... comment"
    py setup_remote_start.py --remove --key "ssh-ed25519 AAAA..."
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "alb-agent"
# Well-known SIDs, not names: group names are localized on non-English Windows.
ADMINISTRATORS_SID = "S-1-5-32-544"
SYSTEM_SID = "S-1-5-18"

# One public key line: "<type> <base64> [comment]". Anything with options in
# front (command="...", from=...) is refused rather than passed through — the
# key is pasted by hand, and an options prefix would change what it allows.
_KEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com)"
    r" ([A-Za-z0-9+/]+={0,3})(?: ([\x21-\x7e][\x20-\x7e]*))?$"
)


class SetupError(Exception):
    """A step that cannot go on; the message says what to do."""


# ── pure helpers (tested on any OS) ─────────────────────────────────────


def parse_key(text: str) -> tuple[str, str]:
    """(normalized key line, base64 body) or SetupError."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) != 1:
        raise SetupError("expected exactly one line: the PUBLIC key (the .pub file)")
    line = lines[0].lstrip("\ufeff")
    if "PRIVATE KEY" in line:
        raise SetupError("that is a private key - paste the .pub file's line instead")
    m = _KEY_RE.match(line)
    if m is None:
        raise SetupError(
            "not a public key line (expected 'ssh-ed25519 AAAA... comment', no options in front)"
        )
    return line, m.group(2)


def key_file_for(admin_member: bool, programdata: str, userprofile: str) -> Path:
    """The file Windows OpenSSH reads for this account. Its stock sshd_config
    sends every member of Administrators to one shared file and ignores the
    per-user one for them — a key put in ~/.ssh there is silently unused."""
    if admin_member:
        return Path(programdata) / "ssh" / "administrators_authorized_keys"
    return Path(userprofile) / ".ssh" / "authorized_keys"


def with_key(existing: str, line: str, body: str) -> str | None:
    """The file's new text with the key added, or None when it is already in."""
    if any(body in ln.split() for ln in existing.splitlines()):
        return None
    sep = "" if not existing or existing.endswith("\n") else "\n"
    return existing + sep + line + "\n"


def without_key(existing: str, body: str) -> str | None:
    """The file's new text with every line of that key removed, or None if
    there was none."""
    kept = [ln for ln in existing.splitlines() if body not in ln.split()]
    if len(kept) == len(existing.splitlines()):
        return None
    return "".join(ln + "\n" for ln in kept)


def is_admin_member(whoami_groups: str) -> bool:
    """From `whoami /groups`: the Administrators SID is listed even when this
    (non-elevated) token holds it for deny only — membership is what decides
    the key file, not elevation."""
    return ADMINISTRATORS_SID in whoami_groups


def task_xml(user: str, agent_dir: str) -> str:
    """Task Scheduler XML for run-agent.bat in this user's desktop session.

    - InteractiveToken: runs only when the user is logged on, in that session,
      so the agent window is there to see and close like a double-click.
    - ExecutionTimeLimit PT0S: no limit. The default stops a task after three
      days, and the agent is meant to stay up.
    - not stopped / not refused on battery (laptop benches).
    - ALB_AGENT_NO_PAUSE=1: run-agent.bat must never wait for a keypress
      when nobody started it by hand (see run-agent.bat).
    """
    user_x, dir_x = escape(user), escape(agent_dir)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>alb device agent (run-agent.bat); started from the hub over ssh</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user_x}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_x}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowHardTerminate>true</AllowHardTerminate>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c "set ALB_AGENT_NO_PAUSE=1&amp;&amp; run-agent.bat"</Arguments>
      <WorkingDirectory>{dir_x}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


# ── Windows side ────────────────────────────────────────────────────────


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, errors="replace", check=False)


def _is_elevated() -> bool:
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _relaunch_elevated(argv: list[str]) -> None:
    """Same script, same arguments, in an administrator window (UAC prompt)."""
    import ctypes

    params = subprocess.list2cmdline([str(Path(__file__).resolve()), *argv, "--elevated"])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    if rc <= 32:  # ShellExecute's documented failure range; 5 = UAC declined
        raise SetupError("could not get an administrator window (UAC declined?)")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""


def _lock_admin_key_file(path: Path) -> None:
    """OpenSSH ignores administrators_authorized_keys unless only
    Administrators and SYSTEM can write it."""
    r = _run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant",
            f"*{ADMINISTRATORS_SID}:F",
            "/grant",
            f"*{SYSTEM_SID}:F",
        ]
    )
    if r.returncode != 0:
        raise SetupError(f"icacls failed on {path}: {(r.stdout + r.stderr).strip()}")


def _sshd_state() -> str:
    """'running' / 'stopped' / 'missing' for the OpenSSH server service."""
    r = _run(["sc", "query", "sshd"])
    if r.returncode != 0:
        return "missing"
    return "running" if "RUNNING" in r.stdout else "stopped"


def _account() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _register_task(agent_dir: Path) -> None:
    xml = task_xml(_account(), str(agent_dir))
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "alb-agent-task.xml"
        f.write_text(xml, encoding="utf-16")
        r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(f), "/F"])
    if r.returncode != 0:
        raise SetupError(f"schtasks /Create failed: {(r.stdout + r.stderr).strip()}")


def _delete_task() -> bool:
    if _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode != 0:
        return False
    r = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if r.returncode != 0:
        raise SetupError(f"schtasks /Delete failed: {(r.stdout + r.stderr).strip()}")
    return True


def _ask_key(given: str | None) -> tuple[str, str]:
    if given is None:
        print("Paste the hub's PUBLIC key line (starts with ssh-ed25519), then Enter:")
        given = input("> ")
    return parse_key(given)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key", help="the public key line (asked for when omitted)")
    ap.add_argument("--agent-dir", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--remove", action="store_true", help="take the key and the task out")
    ap.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    rc = _setup(args, argv)
    if args.elevated:
        # This window was opened by the UAC relaunch, not by the user.
        input("\nPress Enter to close this window.")
    return rc


def _setup(args: argparse.Namespace, argv: list[str]) -> int:
    if os.name != "nt":
        print("this script sets up a Windows agent host; run it there")
        return 2
    agent_dir = Path(args.agent_dir).resolve()
    if not (agent_dir / "run-agent.bat").is_file():
        print(f"no run-agent.bat in {agent_dir} - put this script in the agent folder")
        return 2
    try:
        line, body = _ask_key(args.key)
        admin = is_admin_member(_run(["whoami", "/groups"]).stdout)
        key_file = key_file_for(
            admin, os.environ.get("PROGRAMDATA", r"C:\ProgramData"), os.path.expanduser("~")
        )
        if admin and not _is_elevated():
            print("this account is an administrator: its SSH keys live in a file only an")
            print("administrator can write. Continuing in an administrator window...")
            _relaunch_elevated(
                [a for a in argv if a != "--elevated"] + ([] if args.key else ["--key", line])
            )
            return 0
        if args.remove:
            changed = without_key(_read(key_file), body)
            if changed is not None:
                key_file.write_text(changed, encoding="utf-8")
            removed_task = _delete_task()
            print(f"key   : {'removed from' if changed is not None else 'was not in'} {key_file}")
            print(f"task  : {'deleted' if removed_task else 'was not registered'} ({TASK_NAME})")
            return 0

        sshd = _sshd_state()
        changed = with_key(_read(key_file), line, body)
        if changed is not None:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(changed, encoding="utf-8")
        if admin:
            _lock_admin_key_file(key_file)
        _register_task(agent_dir)
    except SetupError as e:
        print(f"[x] {e}")
        return 1

    print(f"[v] key   :{'added to' if changed is not None else 'already in'} {key_file}")
    print(f"[v] task  : {TASK_NAME} -> {agent_dir}\\run-agent.bat (also runs at logon)")
    if sshd != "running":
        print(f"[!] OpenSSH Server (sshd) is {sshd}: the hub cannot log in until it runs.")
        print("    Settings > Apps > Optional features > OpenSSH Server, then in an")
        print(
            "    administrator PowerShell: Start-Service sshd; Set-Service sshd -StartupType Automatic"
        )
    print()
    print("Send these two lines to whoever runs the hub (nothing here is secret):")
    print(f"    windows user : {_account()}")
    print(f"    agent folder : {agent_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
