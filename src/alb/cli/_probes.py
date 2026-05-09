"""Pure host-side probes shared between `alb setup ...` and `alb doctor`.

These functions return *data* (bool + detail string) — they don't print.
That keeps them composable: each CLI command renders the result in its
own style (table / panel / json / etc).
"""

from __future__ import annotations

import shutil
import socket


def check_binary(name: str) -> tuple[bool, str]:
    """Return (found, detail). Detail is the path or a short reason."""
    path = shutil.which(name)
    if path:
        return True, path
    return False, "not found in PATH"


def check_tcp_listen(host: str, port: int, *, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to host:port can be opened."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
