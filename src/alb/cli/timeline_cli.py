"""CLI: `alb timeline capture` / `alb timeline show`.

One clock for logcat, dmesg and the UART console, so "what did the kernel
print during the three seconds the app hung" is answerable afterwards. The
capture is in :mod:`alb.capabilities.timeline`; this file only maps flags to
it and prints plain text — no colours, no progress bars, because the output
is read as often by a model from a transcript as by a person at a terminal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from alb.capabilities.timeline import (
    ALL_SOURCES,
    capture_timeline,
    format_line,
    iter_timeline,
    timeline_file,
)
from alb.cli.common import get_transport, print_result, run_async
from alb.transport.base import Transport

app = typer.Typer(help="Merged logcat + dmesg + UART capture, every line stamped on one clock.")


def _serial_or_none(ctx: typer.Context) -> tuple[Transport | None, str]:
    """A serial transport if one can be built, else the reason it cannot.

    Not having a UART is a normal bench state, not a usage error, so the
    reason goes into the timeline as a note rather than aborting here.
    """
    try:
        return get_transport(ctx, override="serial"), ""
    except typer.BadParameter as e:
        return None, str(e)


@app.command("capture")
def cmd_capture(
    ctx: typer.Context,
    duration: int = typer.Option(60, "--duration", "-d", help="Seconds to capture (1..3600)."),
    no_logcat: bool = typer.Option(False, "--no-logcat", help="Skip logcat."),
    no_dmesg: bool = typer.Option(False, "--no-dmesg", help="Skip dmesg -w (needs root)."),
    no_uart: bool = typer.Option(False, "--no-uart", help="Skip the serial console."),
    device: str | None = typer.Option(None, "--device", help="adb serial for logcat / dmesg."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Directory to write into. Default: workspace/.../timeline/capture-<ts>/",
    ),
    logcat_filter: str | None = typer.Option(
        None, "--logcat-filter", help="logcat filter spec, e.g. '*:E' or 'MyTag:V *:S'."
    ),
) -> None:
    """Capture logcat + dmesg + UART concurrently into one timeline.jsonl.

    Start the capture FIRST, then reproduce (reboot, trigger the bug): the
    console is attached from t=0 so early boot is not missed. A source that
    is not available (no UART configured, dmesg without root) is noted in
    the timeline and the others are captured anyway.
    """
    sources = {
        s for s, skip in (("logcat", no_logcat), ("dmesg", no_dmesg), ("uart", no_uart)) if not skip
    }
    if not sources:
        typer.echo("[fail] every source was switched off; nothing to capture", err=True)
        raise typer.Exit(code=2)

    transport: Transport | None = None
    if sources & {"logcat", "dmesg"}:
        transport = get_transport(ctx, device_serial=device)
    serial: Transport | None = None
    serial_note = ""
    if "uart" in sources:
        serial, serial_note = _serial_or_none(ctx)

    json_mode = bool((ctx.obj or {}).get("json"))
    if not json_mode:
        typer.echo(
            f"capturing {', '.join(s for s in ALL_SOURCES if s in sources)} for {duration}s …"
        )
        if serial_note:
            typer.echo(f"  uart: {serial_note}")

    result = run_async(
        capture_timeline(
            duration,
            sources=sources,
            transport=transport,
            serial=serial,
            device=device,
            output=output,
            logcat_filter=logcat_filter,
        )
    )
    if json_mode or not result.ok:
        print_result(ctx, result)
        return

    summary = result.data
    assert summary is not None
    typer.echo(str(summary.directory))
    for src in summary.sources:
        typer.echo(f"  {src:<6} {summary.lines.get(src, 0):>8} lines")
    for note in summary.notes:
        typer.echo(f"  {note}")
    typer.echo(f"read it: alb timeline show {summary.directory}")


@app.command("show")
def cmd_show(
    ctx: typer.Context,
    directory: Path = typer.Argument(..., help="Capture directory (or the timeline.jsonl itself)."),
    src: str | None = typer.Option(
        None, "--src", help="Comma-separated sources to keep, e.g. uart,dmesg."
    ),
    grep: str | None = typer.Option(
        None, "--grep", help="Keep lines containing this text (plain substring, case-sensitive)."
    ),
    around: float | None = typer.Option(
        None, "--around", help="Centre time in seconds since capture start, e.g. 12.3."
    ),
    window: float = typer.Option(5.0, "--window", help="Seconds either side of --around."),
) -> None:
    """Print the merged lines in time order: `+12.345s uart   | <line>`."""
    file = timeline_file(directory)
    if not file.is_file():
        typer.echo(f"[fail] FILE_NOT_FOUND: no timeline.jsonl at {directory}", err=True)
        raise typer.Exit(code=1)

    sources = None
    if src:
        sources = {s.strip() for s in src.split(",") if s.strip()}
        unknown = sorted(sources - set(ALL_SOURCES))
        if unknown:
            typer.echo(
                f"[fail] INVALID_SOURCE: {', '.join(unknown)} (choose from {', '.join(ALL_SOURCES)})",
                err=True,
            )
            raise typer.Exit(code=2)

    json_mode = bool((ctx.obj or {}).get("json"))
    out = sys.stdout
    for rec in iter_timeline(file, sources=sources, grep=grep, around=around, window=window):
        # Device text goes out byte-for-byte, no markup layer in between: a
        # line like "[false]" must not vanish, and nothing may be re-wrapped.
        out.write((json.dumps(rec, ensure_ascii=False) if json_mode else format_line(rec)) + "\n")
    out.flush()
