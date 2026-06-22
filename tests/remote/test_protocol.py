"""Wire-protocol unit tests (ADR-050/052)."""

from __future__ import annotations

import pytest

from alb.remote import protocol as p
from alb.remote.protocol import (
    ChannelRole,
    ChannelType,
    ProtocolError,
    Verb,
    decode_control,
    default_role,
    encode_control,
    new_cid,
    new_csecret,
)


def _roundtrip(msg: dict) -> dict:
    return decode_control(encode_control(msg))


def test_every_builder_roundtrips_with_known_verb():
    msgs = [
        p.hello(agent_id="a", name="n", version=1, caps=["adb"], token="t"),
        p.hello_ok(server_version=1),
        p.heartbeat(),
        p.list_adb(),
        p.adb_list(devices=["serial1", "serial2"]),
        p.list_com(),
        p.com_list(ports=[{"com": "COM3", "baud": 115200}]),
        p.open_channel(
            cid="c1",
            csecret="s1",
            ctype=ChannelType.TCP,
            role=ChannelRole.DAEMON,
            params={"target": "127.0.0.1:5037"},
        ),
        p.channel_opened(cid="c1"),
        p.channel_error(cid="c1", reason="boom"),
        p.close_channel(cid="c1"),
        p.channel_closed(cid="c1"),
    ]
    for m in msgs:
        out = _roundtrip(m)
        assert out["verb"] == m["verb"]
        assert out["v"] == p.PROTOCOL_VERSION
        assert out["verb"] in {v.value for v in Verb}


def test_open_channel_carries_type_role_params_and_secret():
    m = _roundtrip(
        p.open_channel(
            cid="x",
            csecret="sekret",
            ctype=ChannelType.SERIAL,
            role=ChannelRole.GATEWAY,
            params={"com": "COM7", "baud": 1500000},
        )
    )
    assert m["channel_type"] == "serial"
    assert m["role"] == "gateway"
    assert m["params"]["baud"] == 1500000
    assert m["csecret"] == "sekret"  # per-channel secret rides the frame (DEBT-084)


def test_decode_rejects_non_json():
    with pytest.raises(ProtocolError):
        decode_control("not json {")


def test_decode_rejects_non_object():
    with pytest.raises(ProtocolError):
        decode_control("[1, 2, 3]")


def test_decode_rejects_unknown_verb():
    with pytest.raises(ProtocolError):
        decode_control('{"v": 1, "verb": "teleport"}')


def test_default_role_mapping():
    # ADR-052: tcp = daemon (no retry), serial = gateway (bounded retry)
    assert default_role(ChannelType.TCP) is ChannelRole.DAEMON
    assert default_role(ChannelType.SERIAL) is ChannelRole.GATEWAY


def test_new_cid_unique_and_hex():
    cids = {new_cid() for _ in range(1000)}
    assert len(cids) == 1000
    assert all(len(c) == 32 and all(ch in "0123456789abcdef" for ch in c) for c in cids)


def test_new_csecret_unique_and_high_entropy():
    secs = {new_csecret() for _ in range(1000)}
    assert len(secs) == 1000  # no collisions
    # token_urlsafe(32) → 32 bytes of entropy, ~43 url-safe chars
    assert all(len(s) >= 40 for s in secs)
