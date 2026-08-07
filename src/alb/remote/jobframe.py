"""Framing for the job channel (ADR-056 §决定 2).

The adb and serial channels proxy a byte stream, so they need no framing at
all — whatever arrives is forwarded. A job channel is different: it carries
*two kinds* of thing on one connection, control messages (JSON) and bulk
payload (image bytes), and the receiver has to tell them apart.

Why a fixed 5-byte header rather than the obvious alternatives:

* "a JSON line, then the raw bytes" — `recv()` hands back arbitrary chunks,
  so a parser would have to cope with every interleaving of half-a-line and
  half-a-payload, and a payload byte that happens to be `\\n` is
  indistinguishable from a delimiter. The length prefix removes the ambiguity
  instead of managing it.
* WebSocket's own text/binary frame types — that would mean a second
  dial-back route, duplicating the per-channel secret check, the pending
  registry, the timeout and the error reply. A second authentication path is
  a far worse thing to own than 60 lines of codec.

Wire format, repeated:

    kind   1 byte   b"C" control (JSON, UTF-8) | b"D" data chunk
    length 4 bytes  big-endian payload length
    payload

Both ends are asymmetric in what they send but symmetric in framing, so the
same reader serves the hub and the agent (the agent mirrors this module in
clients/windows-agent/alb_agent.py — it cannot import alb).
"""

from __future__ import annotations

import json
import struct
from typing import Any, Protocol

HEADER_LEN = 5
KIND_CONTROL = b"C"
KIND_DATA = b"D"

# A single frame's payload ceiling. Chunks are sized by the sender (64 KiB in
# practice); the cap exists so a corrupt or hostile length field cannot make
# the receiver allocate an arbitrary buffer before any validation happens.
MAX_FRAME = 8 * 1024 * 1024

_HEADER = struct.Struct(">cI")


class JobProtocolError(Exception):
    """The peer sent a frame that cannot be interpreted."""


class ByteStream(Protocol):
    """The half of DataChannel this codec needs. `recv()` returns b"" at EOF."""

    async def recv(self) -> bytes: ...

    async def send(self, data: bytes) -> None: ...


def encode(kind: bytes, payload: bytes) -> bytes:
    """Frame one payload. `kind` must be KIND_CONTROL or KIND_DATA."""
    if kind not in (KIND_CONTROL, KIND_DATA):
        raise JobProtocolError(f"unknown frame kind {kind!r}")
    if len(payload) > MAX_FRAME:
        raise JobProtocolError(f"frame of {len(payload)} bytes exceeds the {MAX_FRAME} cap")
    return _HEADER.pack(kind, len(payload)) + payload


def encode_control(msg: dict[str, Any]) -> bytes:
    """Frame a control message. Compact separators keep the small-but-frequent
    progress frames from wasting tunnel bytes."""
    return encode(KIND_CONTROL, json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode())


def encode_data(chunk: bytes) -> bytes:
    return encode(KIND_DATA, chunk)


class FrameReader:
    """Reassembles frames from a chunked byte stream.

    Holds the partial-frame buffer, so one reader must own one channel for
    its whole life — sharing it across concurrent readers would interleave
    two halves of different frames.
    """

    __slots__ = ("_buf", "_stream")

    def __init__(self, stream: ByteStream) -> None:
        self._stream = stream
        self._buf = bytearray()

    async def read(self) -> tuple[bytes, bytes] | None:
        """Next `(kind, payload)`, or None at a clean EOF.

        A truncated frame is an error, not an EOF: the difference between "the
        peer finished" and "the peer died mid-image" is exactly what a flash
        caller must not guess at.
        """
        header = await self._fill(HEADER_LEN, allow_eof=True)
        if header is None:
            return None
        kind, length = _HEADER.unpack(bytes(header))
        if kind not in (KIND_CONTROL, KIND_DATA):
            raise JobProtocolError(f"unknown frame kind {kind!r}")
        if length > MAX_FRAME:
            raise JobProtocolError(f"frame claims {length} bytes, over the {MAX_FRAME} cap")
        payload = await self._fill(length, allow_eof=False)
        assert payload is not None  # allow_eof=False never returns None
        return kind, bytes(payload)

    async def read_control(self) -> dict[str, Any] | None:
        """Next frame, required to be control. None at clean EOF.

        Used where the protocol has no room for ambiguity (the opening
        request, every reply): a data frame arriving there means the peers
        disagree about the state machine, which is worth failing on rather
        than skipping past.
        """
        frame = await self.read()
        if frame is None:
            return None
        kind, payload = frame
        if kind != KIND_CONTROL:
            raise JobProtocolError("expected a control frame, got a data frame")
        try:
            msg = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise JobProtocolError(f"control frame is not valid JSON: {e}") from e
        if not isinstance(msg, dict):
            raise JobProtocolError("control frame must be a JSON object")
        return msg

    async def _fill(self, n: int, *, allow_eof: bool) -> bytearray | None:
        while len(self._buf) < n:
            chunk = await self._stream.recv()
            if not chunk:
                if allow_eof and not self._buf:
                    return None
                raise JobProtocolError(f"channel closed mid-frame ({len(self._buf)} of {n} bytes)")
            self._buf.extend(chunk)
        out = self._buf[:n]
        del self._buf[:n]
        return out
