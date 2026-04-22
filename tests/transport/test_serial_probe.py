"""Tests for the UART baud-rate probe.

We exercise every pure helper (ascii_density, pick_best, TCP hint
generator) with synthetic inputs. The actual :func:`probe_bauds`
needs a real serial device, so we only check that it raises cleanly
when handed a non-device path — the deeper integration test lives
in the internal repo against a real board.
"""

from __future__ import annotations

import pytest

from alb.transport.serial_probe import (
    DEFAULT_RATES,
    ProbeResult,
    _ascii_density,
    pick_best,
    probe_bauds,
    probe_hint_for_tcp,
)
from alb.transport.serial_state import SerialState


# ─── _ascii_density ────────────────────────────────────────────────


def test_ascii_density_all_printable() -> None:
    assert _ascii_density(b"hello world\n") == 1.0


def test_ascii_density_all_high_bytes() -> None:
    assert _ascii_density(bytes(range(128, 200))) == 0.0


def test_ascii_density_mixed() -> None:
    # 5 good ('hello') + 5 bad (\xff bytes) → 0.5
    data = b"hello" + b"\xff" * 5
    assert _ascii_density(data) == 0.5


def test_ascii_density_empty() -> None:
    assert _ascii_density(b"") == 0.0


def test_ascii_density_accepts_common_control() -> None:
    # tab, LF, CR, ESC are expected on shells
    assert _ascii_density(b"x\t\ny\rz\x1b") == 1.0


# ─── ProbeResult properties ───────────────────────────────────────


def _ok_result(**kwargs) -> ProbeResult:
    defaults = dict(
        baud=115200, bytes_received=128, duration_s=2.0,
        ascii_density=0.95, state=SerialState.SHELL_ROOT,
        sample=b"root@h:/ # ",
    )
    defaults.update(kwargs)
    return ProbeResult(**defaults)


def test_probe_result_ok_requires_bytes_and_no_error() -> None:
    assert _ok_result().ok
    assert not _ok_result(bytes_received=0).ok
    assert not _ok_result(error="busy").ok


def test_recommended_candidate_when_strong_state() -> None:
    for s in (
        SerialState.SHELL_ROOT, SerialState.SHELL_USER,
        SerialState.UBOOT, SerialState.RECOVERY,
        SerialState.KERNEL_BOOT, SerialState.LINUX_INIT,
        SerialState.LOGIN_PROMPT, SerialState.PANIC,
    ):
        assert _ok_result(state=s).is_recommended_candidate, f"should recommend {s}"


def test_recommended_when_density_high_and_enough_bytes() -> None:
    r = _ok_result(state=SerialState.UNKNOWN, ascii_density=0.95, bytes_received=128)
    assert r.is_recommended_candidate


def test_not_recommended_when_density_high_but_too_few_bytes() -> None:
    r = _ok_result(state=SerialState.UNKNOWN, ascii_density=0.99, bytes_received=30)
    assert not r.is_recommended_candidate


def test_not_recommended_when_density_low() -> None:
    r = _ok_result(state=SerialState.UNKNOWN, ascii_density=0.3, bytes_received=1024)
    assert not r.is_recommended_candidate


# ─── pick_best ─────────────────────────────────────────────────────


def test_pick_best_prefers_strong_state_over_higher_density() -> None:
    shell = _ok_result(
        baud=1500000, state=SerialState.SHELL_ROOT,
        ascii_density=0.92, bytes_received=200,
    )
    quiet_clean = _ok_result(
        baud=115200, state=SerialState.UNKNOWN,
        ascii_density=0.99, bytes_received=200,
    )
    best = pick_best([quiet_clean, shell])
    assert best is shell


def test_pick_best_breaks_tie_by_density() -> None:
    a = _ok_result(
        baud=115200, state=SerialState.UNKNOWN,
        ascii_density=0.80, bytes_received=200,
    )
    b = _ok_result(
        baud=921600, state=SerialState.UNKNOWN,
        ascii_density=0.95, bytes_received=200,
    )
    best = pick_best([a, b])
    assert best is b


def test_pick_best_returns_none_when_all_empty() -> None:
    all_idle = [
        ProbeResult(
            baud=r, bytes_received=0, duration_s=2.0,
            ascii_density=0.0, state=SerialState.IDLE, sample=b"",
        )
        for r in (115200, 921600, 1500000)
    ]
    assert pick_best(all_idle) is None


def test_pick_best_skips_errored_results() -> None:
    bad = ProbeResult(
        baud=115200, bytes_received=0, duration_s=0.0,
        ascii_density=0.0, state=SerialState.UNKNOWN,
        sample=b"", error="permission denied",
    )
    good = _ok_result(baud=1500000)
    assert pick_best([bad, good]) is good


# ─── probe_bauds guards ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_bauds_rejects_non_dev_path() -> None:
    with pytest.raises(ValueError, match="/dev/tty"):
        await probe_bauds("localhost:19001")


# ─── probe_hint_for_tcp ────────────────────────────────────────────


def test_tcp_hint_mentions_each_rate() -> None:
    hint = probe_hint_for_tcp("localhost", 19001, rates=(115200, 1500000))
    assert "115200" in hint
    assert "1500000" in hint
    assert "localhost:19001" in hint
    assert "--baud 115200" in hint
    assert "--baud 1500000" in hint


def test_tcp_hint_recommends_status_call() -> None:
    hint = probe_hint_for_tcp("localhost", 19001)
    assert "alb" in hint
    assert "status" in hint


def test_default_rates_not_empty_and_unique() -> None:
    assert len(DEFAULT_RATES) > 0
    assert len(set(DEFAULT_RATES)) == len(DEFAULT_RATES)
