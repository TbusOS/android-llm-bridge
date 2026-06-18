"""Remote device agent subsystem (ADR-050/051/052).

The Linux hub (alb-api) accepts an outbound "dial-home" WebSocket from a
remote agent running on the machine that physically holds the device, and
exposes that device on the hub as ordinary OS-level loopback sockets
(127.0.0.1:5037 for adb) so the existing transports / MCP tools / CLI reach
it via plain connect() — unchanged.

Modules:
  - protocol  : the wire protocol (control verbs + channel roles), single
                source of truth shared by hub and agent.
  - registry  : connected-agent registry + per-channel dial-back correlation.
  - forwarder : the OS-level loopback listener that bridges local TCP to the
                agent's adb server over a per-connection data channel.
"""

from __future__ import annotations
