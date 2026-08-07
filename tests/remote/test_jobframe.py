"""Job-channel framing (ADR-056 §决定 2).

The properties that matter: a payload can contain anything (including the
bytes a line-delimited protocol would choke on), arbitrary chunking of the
underlying stream is invisible, and a truncated frame is an ERROR rather
than a quiet EOF — a flash caller must never mistake "peer died mid-image"
for "peer finished".
"""

from __future__ import annotations

import pytest

from alb.remote.jobframe import (
    KIND_CONTROL,
    KIND_DATA,
    MAX_FRAME,
    FrameReader,
    JobProtocolError,
    encode,
    encode_control,
    encode_data,
)


class _Stream:
    """Feeds a canned byte string in fixed-size slices."""

    def __init__(self, data: bytes, *, chunk: int = 4096) -> None:
        self._data = data
        self._chunk = chunk
        self._pos = 0
        self.sent: list[bytes] = []

    async def recv(self) -> bytes:
        if self._pos >= len(self._data):
            return b""
        out = self._data[self._pos : self._pos + self._chunk]
        self._pos += len(out)
        return out

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


async def test_roundtrip_control_then_data():
    wire = encode_control({"op": "flash", "partition": "cfg"}) + encode_data(b"\x00\x01\x02")
    r = FrameReader(_Stream(wire))
    assert await r.read_control() == {"op": "flash", "partition": "cfg"}
    assert await r.read() == (KIND_DATA, b"\x00\x01\x02")
    assert await r.read() is None  # clean EOF


async def test_payload_may_contain_newlines_and_braces():
    """The reason for length-prefixing rather than line delimiting: image
    bytes routinely contain \\n and JSON punctuation."""
    payload = b'{"not":"json"}\n\n\xff\xfe' * 50
    r = FrameReader(_Stream(encode_data(payload)))
    assert await r.read() == (KIND_DATA, payload)


@pytest.mark.parametrize("chunk", [1, 2, 5, 7, 4096])
async def test_reassembles_across_any_chunking(chunk):
    """recv() splits wherever the transport feels like — including inside a
    header. One byte at a time is the pathological case."""
    wire = encode_control({"a": 1}) + encode_data(b"payload-bytes") + encode_control({"b": 2})
    r = FrameReader(_Stream(wire, chunk=chunk))
    assert await r.read_control() == {"a": 1}
    assert await r.read() == (KIND_DATA, b"payload-bytes")
    assert await r.read_control() == {"b": 2}
    assert await r.read() is None


async def test_empty_data_frame_is_legal():
    """A zero-length data frame is how "the payload ended" can be signalled
    without a separate control round-trip."""
    r = FrameReader(_Stream(encode_data(b"")))
    assert await r.read() == (KIND_DATA, b"")


async def test_truncated_payload_raises_not_eof():
    wire = encode_data(b"0123456789")[:-3]
    r = FrameReader(_Stream(wire))
    with pytest.raises(JobProtocolError, match="mid-frame"):
        await r.read()


async def test_truncated_header_raises_not_eof():
    r = FrameReader(_Stream(encode_data(b"x")[:3]))
    with pytest.raises(JobProtocolError, match="mid-frame"):
        await r.read()


async def test_unknown_kind_rejected():
    r = FrameReader(_Stream(b"X\x00\x00\x00\x01y"))
    with pytest.raises(JobProtocolError, match="unknown frame kind"):
        await r.read()


async def test_oversize_length_rejected_before_allocating():
    """A corrupt length field must not make the receiver buffer for it."""
    wire = KIND_DATA + (MAX_FRAME + 1).to_bytes(4, "big")
    r = FrameReader(_Stream(wire))
    with pytest.raises(JobProtocolError, match="over the"):
        await r.read()


async def test_encode_rejects_oversize_payload():
    with pytest.raises(JobProtocolError, match="exceeds"):
        encode(KIND_DATA, b"x" * (MAX_FRAME + 1))


async def test_encode_rejects_unknown_kind():
    with pytest.raises(JobProtocolError, match="unknown frame kind"):
        encode(b"Z", b"")


async def test_read_control_rejects_a_data_frame():
    """Where the state machine says "a reply comes next", a data frame means
    the two ends disagree — surfacing that beats skipping it."""
    r = FrameReader(_Stream(encode_data(b"nope")))
    with pytest.raises(JobProtocolError, match="expected a control frame"):
        await r.read_control()


async def test_read_control_rejects_non_object_json():
    r = FrameReader(_Stream(encode(KIND_CONTROL, b"[1,2,3]")))
    with pytest.raises(JobProtocolError, match="must be a JSON object"):
        await r.read_control()


async def test_read_control_rejects_bad_json():
    r = FrameReader(_Stream(encode(KIND_CONTROL, b"{not json")))
    with pytest.raises(JobProtocolError, match="not valid JSON"):
        await r.read_control()


async def test_control_frames_are_compact():
    """Progress frames are small and frequent; default json.dumps spacing
    would add bytes to every one of them."""
    assert b", " not in encode_control({"a": 1, "b": 2})
