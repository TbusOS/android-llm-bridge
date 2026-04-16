"""Transport layer — the architectural core.

All ways of reaching an Android device are abstracted as `Transport`:
- AdbTransport    : methods A (USB) and B (WiFi)
- SshTransport    : methods C / D / F
- SerialTransport : method G (UART)
- HybridTransport : smart routing

See docs/architecture.md §二 for the full interface contract.
"""

from alb.transport.base import ShellResult, Transport
from alb.transport.hybrid import HybridTransport

__all__ = ["HybridTransport", "ShellResult", "Transport"]
