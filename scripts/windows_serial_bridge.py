"""Minimal Windows COM <-> TCP bridge (ser2net replacement).

Use this on Windows to expose a local COM port as a TCP listener, paired with
an Xshell reverse tunnel (-R <port>:localhost:<port>) to reach an alb instance
on a Linux host. See docs/methods/07-uart-serial.md for the full architecture.

Why this instead of ser2net:
    - Single ~100-line file, no sourceforge download
    - Runs anywhere Python + pyserial are available (Windows / Linux / macOS)
    - Clear log messages to diagnose connection issues
    - Single-client: a new connection cleanly replaces the old one
    - Fast Ctrl-C exit (sub-second) via socket timeout + signal handler

Setup (Windows):
    1. pip install pyserial
    2. python windows_serial_bridge.py --com COM3 --baud 1500000 --port 19001
       (RK3576 UART typically 1500000; most other SoCs 115200)
       (Port 9001 is often reserved by WinNAT/Hyper-V on Windows 10+ —
        pick something >10000 if 9001 gives WinError 10013.)
    3. In Xshell: Connection → SSH → Tunnelling → Add
       Type=Remote, source=localhost:<port>, dest=localhost:<port>

On the Linux side (alb host):
    alb setup serial --tcp-host localhost --tcp-port 19001 --baud 1500000
    alb serial capture --duration 10

Bandwidth is UART-level (< 200 KB/s even at 1.5 Mbaud), so performance is
dominated by the serial line, not the TCP/SSH path.

Default bind host is 127.0.0.1 — for Xshell reverse-tunnel scenarios this
is exactly what's needed, and it avoids the Windows 10 non-admin bind-0.0.0.0
permission error. Pass --host 0.0.0.0 if you need remote-LAN access.
"""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not installed.  Run: pip install pyserial")


_shutdown = threading.Event()


def _install_signal_handlers() -> None:
    def _on_signal(signum, frame):  # noqa: ANN001
        _shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass  # non-main thread / unsupported platform


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--com", required=True, help="COM port (e.g. COM3)")
    ap.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="TCP bind host (default 127.0.0.1 — loopback is enough for reverse tunnel)",
    )
    ap.add_argument(
        "--port", type=int, default=19001,
        help="TCP listen port (default 19001 — avoids Windows WinNAT-reserved ranges)",
    )
    args = ap.parse_args()

    _install_signal_handlers()

    ser = serial.Serial(args.com, args.baud, timeout=0.05)
    print(f"[bridge] opened {args.com} @ {args.baud} baud", flush=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    srv.settimeout(0.5)  # wake periodically so Ctrl-C / SIGTERM exit fast
    print(f"[bridge] listening on {args.host}:{args.port}  (Ctrl-C to quit)", flush=True)

    try:
        while not _shutdown.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            print(f"[bridge] client {addr} connected", flush=True)
            try:
                _pump(ser, conn)
            except Exception as e:  # noqa: BLE001 — keep bridge running across client crashes
                print(f"[bridge] pump ended: {type(e).__name__}: {e}", flush=True)
            finally:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
                print(f"[bridge] client {addr} disconnected", flush=True)
    finally:
        srv.close()
        ser.close()
        print("[bridge] bye", flush=True)


def _pump(ser: "serial.Serial", conn: socket.socket) -> None:
    """Shuttle bytes both directions until either side closes or shutdown."""
    stop = threading.Event()

    def com_to_tcp() -> None:
        try:
            while not stop.is_set() and not _shutdown.is_set():
                data = ser.read(4096)
                if data:
                    conn.sendall(data)
                else:
                    time.sleep(0.01)
        except Exception:
            stop.set()

    t = threading.Thread(target=com_to_tcp, daemon=True)
    t.start()

    conn.settimeout(0.1)
    try:
        while not stop.is_set() and not _shutdown.is_set():
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            ser.write(data)
    finally:
        stop.set()
        t.join(timeout=0.5)


if __name__ == "__main__":
    main()
