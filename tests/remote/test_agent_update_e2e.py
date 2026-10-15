"""Remote agent update, end to end on this host (ADR-060).

Real processes all the way: an old agent runs from a temp folder and dials a
small stand-in hub; the stand-in pushes a bundle over a real job channel; the
old agent self-checks the new build, starts the update helper and stops; the
helper swaps the files and starts the new build. What the hub sees in the
next hello decides each test.

Only the Windows-specific parts (console windows, taskkill, run-agent.bat)
are out of reach here; they are checked on the bench.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc to clean up its processes"
)

websockets = pytest.importorskip("websockets")
from websockets.asyncio.server import serve  # noqa: E402

_SRC = Path(__file__).resolve().parents[2] / "clients" / "windows-agent"
_HDR = struct.Struct(">cI")


def _build(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _agent_text(*, marker: str, fast: bool = True, broken_hello: bool = False) -> bytes:
    """The repo's agent, made distinct (a marker comment changes the build).
    `fast` shortens the helper's waits — the helper is the OLD build's code,
    so this is where its timing comes from. `broken_hello` makes a build that
    passes its self-check but can never get past the handshake."""
    text = (_SRC / "alb_agent.py").read_text(encoding="utf-8")
    if fast:
        for old, new in (
            ("_UPDATE_HEALTH_TIMEOUT_S = 60.0", "_UPDATE_HEALTH_TIMEOUT_S = 8.0"),
            ("_UPDATE_SETTLE_S = 3.0", "_UPDATE_SETTLE_S = 0.3"),
        ):
            assert old in text
            text = text.replace(old, new)
    if broken_hello:
        old = '    return _frame(\n        "hello",\n'
        assert old in text
        text = text.replace(old, '    return _frame(\n        "hullo",\n')
    return (text + f"\n# build marker: {marker}\n").encode("utf-8")


class StandInHub:
    """Answers hello, records it, and can run one job on the current agent."""

    def __init__(self) -> None:
        self.hellos: list[dict[str, Any]] = []
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._session: Any = None
        self._channels: dict[str, asyncio.Future[Any]] = {}
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(10)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        async with serve(self._handler, "127.0.0.1", 0, max_size=None) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop.wait()

    async def _handler(self, ws: Any) -> None:
        path = ws.request.path
        if path.startswith("/agent/channel"):
            cid = path.split("cid=", 1)[1]
            done: asyncio.Future[None] = self._loop.create_future()
            self._channels[cid].set_result((ws, done))
            await done
            return
        hello = json.loads(await ws.recv())
        if hello.get("verb") != "hello":
            await ws.close(code=1008)
            return
        self.hellos.append(hello)
        await ws.send(json.dumps({"v": 1, "verb": "hello_ok", "server_version": 1}))
        self._session = ws
        with contextlib.suppress(Exception):
            async for _ in ws:
                pass

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/agent/connect"

    def wait_hello(self, pred: Any, timeout: float = 30.0, *, after: int = 0) -> dict[str, Any]:
        """The latest hello matching `pred`, among those after the first
        `after` (identical hellos are equal dicts — count, do not search)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for h in reversed(self.hellos[after:]):
                if pred(h):
                    return h
            time.sleep(0.1)
        seen = [(h.get("agent_id"), h.get("build"), h.get("update")) for h in self.hellos]
        raise AssertionError(f"no matching hello within {timeout}s; saw {seen}")

    def run_job(self, request: dict[str, Any], image: bytes) -> list[dict[str, Any]]:
        fut = asyncio.run_coroutine_threadsafe(self._job(request, image), self._loop)
        return fut.result(timeout=120)

    async def _job(self, request: dict[str, Any], image: bytes) -> list[dict[str, Any]]:
        cid = uuid.uuid4().hex
        waiter: asyncio.Future[Any] = self._loop.create_future()
        self._channels[cid] = waiter
        await self._session.send(
            json.dumps(
                {
                    "v": 1,
                    "verb": "open_channel",
                    "cid": cid,
                    "csecret": "s",
                    "channel_type": "job",
                    "role": "job",
                    "params": {"op": request["op"]},
                }
            )
        )
        ws, done = await asyncio.wait_for(waiter, 30)
        try:
            body = json.dumps(request).encode()
            await ws.send(_HDR.pack(b"C", len(body)) + body)
            for i in range(0, len(image), 65536):
                chunk = image[i : i + 65536]
                await ws.send(_HDR.pack(b"D", len(chunk)) + chunk)
            await ws.send(_HDR.pack(b"D", 0))
            frames: list[dict[str, Any]] = []
            buf = b""
            while not frames or frames[-1].get("ev") != "done":
                buf += await asyncio.wait_for(ws.recv(), 90)
                while len(buf) >= 5:
                    _kind, n = _HDR.unpack(buf[:5])
                    if len(buf) < 5 + n:
                        break
                    frames.append(json.loads(buf[5 : 5 + n]))
                    buf = buf[5 + n :]
            return frames
        finally:
            done.set_result(None)

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=10)


def _bundle(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def _repo_files() -> dict[str, bytes]:
    return {
        p.name: p.read_bytes()
        for p in _SRC.iterdir()
        if p.is_file() and p.suffix in (".py", ".bat", ".md", ".txt", ".example")
    }


def _reap_all_under(folder: Path) -> None:
    """Kill every process started from `folder`: agents AND the update
    helper. A helper left alive takes the killed agent for "never reached
    the hub", rolls back and starts another one after the test is gone."""
    needle = str(folder).encode()
    for _ in range(5):
        found = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if needle in cmdline:
                found = True
                with contextlib.suppress(OSError):
                    os.kill(int(entry.name), signal.SIGKILL)
        if not found:
            return
        time.sleep(0.5)


@pytest.fixture
def bench(tmp_path):
    """A temp agent folder wired to a stand-in hub; every agent process the
    test caused is killed afterwards, whoever started it."""
    hub = StandInHub()
    install = tmp_path / "agent"
    install.mkdir()
    for name, data in _repo_files().items():
        (install / name).write_bytes(data)
    conf = f"hub_url={hub.url}\nagent_id=bench-e2e\nname=e2e\nstatus_port=0\nlog_file=agent.log\n"
    (install / "agent.conf").write_text(conf, encoding="utf-8")
    procs: list[subprocess.Popen[bytes]] = []

    def start(agent_bytes: bytes) -> subprocess.Popen[bytes]:
        (install / "alb_agent.py").write_bytes(agent_bytes)
        env = {k: v for k, v in os.environ.items() if k != "ALB_AGENT_LAUNCHER"}
        p = subprocess.Popen(
            [sys.executable, str(install / "alb_agent.py")],
            cwd=install,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        return p

    yield hub, install, start
    _reap_all_under(tmp_path)
    for p in procs:
        with contextlib.suppress(Exception):
            p.kill()
            p.wait(timeout=5)
    hub.close()


def _update(hub: StandInHub, files: dict[str, bytes]) -> dict[str, Any]:
    image = _bundle(files)
    frames = hub.run_job(
        {"op": "agent-update", "size": len(image), "sha256": hashlib.sha256(image).hexdigest()},
        image,
    )
    return frames[-1]


def _log(install: Path) -> str:
    p = install / ".alb-agent" / "update.log"
    return p.read_text(encoding="utf-8") if p.exists() else "(no update.log)"


def test_update_swaps_the_files_and_the_new_build_reconnects(bench):
    hub, install, start = bench
    old = _agent_text(marker="old")
    new = _agent_text(marker="new")
    first = start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))

    done = _update(hub, {**_repo_files(), "alb_agent.py": new})
    assert done["ok"], done
    assert done["data"]["restarting"] is True
    assert done["data"]["changed"] == ["alb_agent.py"]

    hello = hub.wait_hello(lambda h: h.get("build") == _build(new), timeout=40)
    assert (install / "alb_agent.py").read_bytes() == new
    # the old process stopped on request (75), it was not killed
    assert first.wait(timeout=10) == 75
    result = json.loads((install / ".alb-agent" / "update-result.json").read_text())
    deadline = time.monotonic() + 15
    while result["outcome"] != "updated" and time.monotonic() < deadline:
        time.sleep(0.2)
        result = json.loads((install / ".alb-agent" / "update-result.json").read_text())
    assert result["outcome"] == "updated", _log(install)
    assert hello["update"]["id"] == done["data"]["update_id"]
    assert "updated: build" in _log(install)


def test_a_build_that_never_reaches_the_hub_is_rolled_back(bench):
    hub, install, start = bench
    old = _agent_text(marker="old")
    broken = _agent_text(marker="broken", broken_hello=True)
    start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))
    seen = len(hub.hellos)

    done = _update(hub, {**_repo_files(), "alb_agent.py": broken})
    assert done["ok"] and done["data"]["restarting"], done

    hello = hub.wait_hello(lambda h: h.get("build") == _build(old), timeout=60, after=seen)
    assert hello["update"]["outcome"] == "rolled_back", _log(install)
    assert (install / "alb_agent.py").read_bytes() == old
    assert "rolling back" in _log(install)


def test_a_bundle_that_fails_its_self_check_changes_nothing(bench):
    hub, install, start = bench
    old = _agent_text(marker="old")
    proc = start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))

    done = _update(hub, {**_repo_files(), "alb_agent.py": b"def broken(:\n"})
    assert not done["ok"]
    assert done["code"] == "AGENT_UPDATE_CHECK_FAILED"
    assert "SyntaxError" in done["error"]
    time.sleep(1.5)
    assert proc.poll() is None, "the old build must keep running"
    assert (install / "alb_agent.py").read_bytes() == old
    assert not (install / ".alb-agent" / "update-plan.json").exists()


def test_identical_files_do_not_restart_anything(bench):
    hub, install, start = bench
    old = _agent_text(marker="same")
    proc = start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))
    files = {n: (install / n).read_bytes() for n in _repo_files()}

    done = _update(hub, files)
    assert done["ok"] and done["data"]["restarting"] is False, done
    time.sleep(1.5)
    assert proc.poll() is None


def test_a_second_start_stops_the_first_instance_of_the_same_agent(bench):
    hub, _install, start = bench
    old = _agent_text(marker="one")
    first = start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))
    seen = len(hub.hellos)

    second = start(old)
    hub.wait_hello(lambda h: True, timeout=30, after=seen)
    assert first.wait(timeout=20) == 75
    assert second.poll() is None


def test_another_agent_with_its_own_folder_is_left_alone(bench, tmp_path):
    hub, install, start = bench
    other = tmp_path / "other"
    shutil.copytree(install, other)
    conf = (other / "agent.conf").read_text().replace("bench-e2e", "bench-other")
    (other / "agent.conf").write_text(conf)
    text = _agent_text(marker="a")
    (other / "alb_agent.py").write_bytes(text)
    neighbour = subprocess.Popen(
        [sys.executable, str(other / "alb_agent.py")],
        cwd=other,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        hub.wait_hello(lambda h: h.get("agent_id") == "bench-other")
        start(text)
        hub.wait_hello(lambda h: h.get("agent_id") == "bench-e2e")
        time.sleep(2.0)
        assert neighbour.poll() is None, "an agent with its own config must not be stopped"
    finally:
        neighbour.kill()
        neighbour.wait(timeout=5)


def test_a_pushed_config_that_changes_the_agent_id_is_refused(bench):
    """The hub waits for THIS agent_id to come back."""
    hub, install, start = bench
    old = _agent_text(marker="old")
    proc = start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))
    conf = (install / "agent.conf").read_bytes().replace(b"bench-e2e", b"someone-else")

    done = _update(hub, {**_repo_files(), "alb_agent.py": old, "agent.conf": conf})
    assert not done["ok"] and done["code"] == "AGENT_UPDATE_REJECTED", done
    assert "keep agent_id the same" in done["error"]
    time.sleep(1.5)
    assert proc.poll() is None


def test_a_pushed_config_is_installed_and_read_on_restart(bench):
    hub, install, start = bench
    old = _agent_text(marker="old")
    start(old)
    hub.wait_hello(lambda h: h.get("build") == _build(old))
    seen = len(hub.hellos)
    conf = (install / "agent.conf").read_bytes().replace(b"name=e2e", b"name=renamed")

    done = _update(hub, {**_repo_files(), "alb_agent.py": old, "agent.conf": conf})
    assert done["ok"] and done["data"]["changed"] == ["agent.conf"], done
    hello = hub.wait_hello(lambda h: h.get("name") == "renamed", timeout=40, after=seen)
    assert hello["agent_id"] == "bench-e2e"
    assert (install / "agent.conf").read_bytes() == conf
