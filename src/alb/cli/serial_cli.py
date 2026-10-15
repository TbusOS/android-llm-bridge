"""CLI: serial capture / send / shell (method G)."""

from __future__ import annotations

from pathlib import Path

import typer

from alb.capabilities.breakin import (
    DEFAULT_EXPECT,
    DEFAULT_PROMPT,
    DEFAULT_PROMPT_WAIT_S,
    DEFAULT_REPEAT_MS,
    DEFAULT_TIMEOUT_S,
    REBOOT_MODES,
    break_in,
    parse_send_keys,
)
from alb.capabilities.logging import capture_uart
from alb.capabilities.shell import execute as shell_execute
from alb.cli.common import get_transport, print_result, print_shell_result, run_async
from alb.transport.serial import SerialTransport

app = typer.Typer(help="UART / serial commands (method G).")


def _force_serial(ctx: typer.Context, device: str | None) -> SerialTransport:
    """Ensure the active transport is a SerialTransport regardless of profile."""
    t = get_transport(ctx, override="serial", device_serial=device)
    assert isinstance(t, SerialTransport), "expected SerialTransport"
    return t


@app.command("capture")
def cmd_capture(
    ctx: typer.Context,
    duration: int = typer.Option(30, "--duration", "-d"),
    device: str | None = typer.Option(None, "--device"),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Output path: either a directory (log goes inside as "
            "<ts>-uart.log) or a full file path. Default: workspace/logs/."
        ),
    ),
) -> None:
    """Capture UART bytes for N seconds (default: workspace/logs/, or --output to override)."""
    t = _force_serial(ctx, device)
    result = run_async(capture_uart(t, duration=duration, device=device, output=output))
    print_result(ctx, result)


@app.command("send")
def cmd_send(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Text to send (newline appended)."),
    no_newline: bool = typer.Option(False, "--no-newline", help="Skip the trailing newline."),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Send a raw string over UART (no prompt waiting, no execution check)."""
    t = _force_serial(ctx, device)
    payload = text if no_newline else text + "\n"
    result = run_async(t.send_raw(payload.encode("utf-8")))
    # Reuse print_result by wrapping in an ok/fail Result-like object via the
    # shell path — simpler: print raw ShellResult.
    if result.ok:
        # Say what we actually verified. A UART never acknowledges, so "sent"
        # is the strongest true claim: if the console was busy / not at a
        # prompt, the board may have ignored the bytes entirely. Use
        # `alb serial shell` when you need to know the command really ran.
        typer.echo(
            f"[ok] sent {len(payload)} bytes over the serial link "
            "(delivery to the UART confirmed; execution by the board is not)"
        )
    else:
        typer.echo(f"[fail] {result.error_code}: {result.stderr}", err=True)
        raise typer.Exit(code=1)


@app.command("break-in")
def cmd_break_in(
    ctx: typer.Context,
    reboot: str | None = typer.Option(
        None,
        "--reboot",
        help=(
            "How to restart the board once the watch is up: adb | serial. "
            "Omit it and reset / power-cycle the board by hand when told to."
        ),
    ),
    expect: str = typer.Option(
        DEFAULT_EXPECT,
        "--expect",
        help="Regex announcing the key-press window (matched on the raw UART bytes).",
    ),
    send: str = typer.Option(
        " ",
        "--send",
        help=(
            "Keys to type once the window opens, repeated until a prompt appears. "
            "Escapes accepted: \\r \\n \\t \\e \\xHH (e.g. '\\x03' for Ctrl-C)."
        ),
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help=f"Regex for the console prompt at the end of the stream. Default: {DEFAULT_PROMPT}",
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_S, "--timeout", help="Seconds to wait for the window to appear."
    ),
    repeat_ms: int = typer.Option(
        DEFAULT_REPEAT_MS, "--repeat-ms", help="Interval between repeated keypresses."
    ),
    prompt_wait: float = typer.Option(
        DEFAULT_PROMPT_WAIT_S,
        "--prompt-wait",
        help="Seconds after the match to keep typing and wait for the prompt.",
    ),
    device: str | None = typer.Option(None, "--device"),
    adb_device: str | None = typer.Option(
        None, "--adb-device", help="adb serial for --reboot adb (default: the profile's device)."
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Where the full UART log goes (dir or file). Default: workspace/logs/.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation --reboot asks for"),
) -> None:
    """Stop the board in its bootloader: catch the autoboot countdown and type a key.

    The reader is opened BEFORE the reboot is triggered, so the countdown
    (a 1-3 s window after reset) is never missed. Other alb readers keep
    working meanwhile — the link is shared, not taken over.

    Ends in one of: stopped at a prompt (exit 0, the prompt line is shown),
    matched but no prompt followed, pattern never seen (exit 1 with the last
    lines received — some bootloaders simply do not offer a key-press stop),
    or a link error. The full UART log of the attempt is saved either way.
    """
    try:
        keys = parse_send_keys(send)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--send") from None
    if reboot is not None and reboot not in REBOOT_MODES:
        raise typer.BadParameter(f"must be one of {', '.join(REBOOT_MODES)}", param_hint="--reboot")

    # Confirm before anything is built or opened: --reboot restarts the board.
    if reboot is not None and not yes:
        how = "`adb reboot`" if reboot == "adb" else "`reboot` typed on the UART"
        typer.echo(f"about to reboot the board via {how} and stop it in the bootloader")
        typer.confirm("proceed?", abort=True)

    t = _force_serial(ctx, device)
    adb_t = (
        get_transport(ctx, override="adb", device_serial=adb_device) if reboot == "adb" else None
    )
    json_mode = bool((ctx.obj or {}).get("json"))

    def _ready() -> None:
        if json_mode:
            return
        if reboot is None:
            typer.echo(
                f"[..] UART reader is up — reset or power-cycle the board now "
                f"(waiting up to {timeout:g}s for {expect!r})",
                err=True,
            )
        else:
            typer.echo(f"[..] UART reader is up — rebooting via {reboot}", err=True)

    result = run_async(
        break_in(
            t,
            expect=expect,
            send=keys,
            reboot=reboot,
            reboot_transport=adb_t,
            timeout_s=timeout,
            prompt=prompt,
            repeat_ms=repeat_ms,
            prompt_wait_s=prompt_wait,
            device=device,
            output=output,
            on_ready=_ready,
        )
    )
    if json_mode:
        print_result(ctx, result)
        return

    from rich.markup import escape
    from rich.panel import Panel
    from rich.text import Text

    from alb.cli.common import console, err_console

    log_path = result.artifacts[0] if result.artifacts else None

    if result.ok and result.data is not None:
        d = result.data
        typer.echo(
            f"[ok] stopped at the bootloader prompt after {d.sends} keypress(es), "
            f"{d.duration_ms} ms"
        )
        typer.echo(f"  matched: {d.matched!r}")
        typer.echo(f"  prompt:  {d.prompt_line!r}")
        if log_path is not None:
            typer.echo(f"  uart log: {log_path}")
        typer.echo(
            "next: alb serial shell 'printenv'   # run a command at the prompt and read "
            "its output\n"
            "      alb serial send 'help'        # type a line, no waiting"
        )
        return

    err = result.error
    if err is not None:
        err_console.print(f"[red]✗ {escape(err.code)}[/] — {escape(err.message)}", soft_wrap=True)
        if err.suggestion:
            err_console.print(f"[yellow]suggestion:[/] {escape(err.suggestion)}", soft_wrap=True)
        tail = str(err.details.get("tail") or "")
        if tail.strip():
            # Device text, not markup: a Text() body is shown verbatim.
            console.print(Panel(Text(tail), title="last lines received", border_style="red"))
        else:
            console.print("[dim](nothing was received on the UART)[/]")
    if log_path is not None:
        typer.echo(f"  uart log: {log_path}")
    raise typer.Exit(code=1)


@app.command("shell")
def cmd_shell(
    ctx: typer.Context,
    cmd: str = typer.Argument(..., help="Command to run over serial shell."),
    timeout: int = typer.Option(30, "--timeout"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Execute a shell command via UART (prompt-based, best-effort)."""
    t = _force_serial(ctx, device)
    result = run_async(shell_execute(t, cmd, timeout=timeout))
    print_shell_result(ctx, result, terminate_last_line=True)


@app.command("health")
def cmd_health(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Check serial connectivity + endpoint info."""
    t = _force_serial(ctx, device)
    import json

    info = run_async(t.health())
    if (ctx.obj or {}).get("json"):
        typer.echo(json.dumps(info, indent=2, default=str))
        return
    from rich.table import Table

    from alb.cli.common import console

    table = Table(title="serial transport health")
    table.add_column("key")
    table.add_column("value")
    for k, v in info.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command("learn")
def cmd_learn(
    ctx: typer.Context,
    samples: int = typer.Option(
        5,
        "--samples",
        "-n",
        help="How many probe commands to run to infer the prompt.",
    ),
    state_key: str = typer.Option(
        "shell_root",
        "--state-key",
        help=(
            "Which state's prompt are we learning? Typical: shell_root / "
            "shell_user. The label ends up in the generated TOML snippet."
        ),
    ),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Auto-derive a prompt regex from the live board.

    When a board's prompt isn't matched by alb's built-ins (custom
    ROM PS1, weird bootloader, embedded Linux with a bespoke login
    banner), the right fix is a TOML override in ``config.toml``.
    Writing that regex by hand is fiddly. This command watches the
    board's actual output across ``--samples`` probe commands,
    computes the stable part of the prompt, generalises the varying
    parts (typically the CWD) to ``[^\\s]*``, and prints a ready-to-
    paste TOML snippet.

    Requires the endpoint is already in a POSIX-ish shell state —
    run ``alb serial status`` first to confirm.
    """
    import json

    from alb.transport.serial_learn import learn_prompt

    t = _force_serial(ctx, device)
    learned = run_async(learn_prompt(t, samples=samples, state_key=state_key))

    if (ctx.obj or {}).get("json"):
        typer.echo(json.dumps({
            "samples": learned.samples,
            "common_suffix": learned.common_suffix,
            "regex": learned.regex,
            "confidence": learned.confidence,
            "toml_snippet": learned.toml_snippet,
        }, indent=2))
        return

    from rich.console import Group
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table

    from alb.cli.common import console

    if not learned.regex:
        console.print(Panel(
            "No prompt could be derived — not enough usable samples.\n"
            "Check that `alb serial status` shows a shell state first,\n"
            "then retry with more --samples.",
            title="serial learn — nothing learned",
            border_style="red",
        ))
        raise typer.Exit(code=1)

    sample_tbl = Table(show_header=True, header_style="bold")
    sample_tbl.add_column("#", justify="right")
    sample_tbl.add_column("captured prompt")
    for i, s in enumerate(learned.samples, 1):
        sample_tbl.add_row(str(i), repr(s))

    conf_colour = {"high": "green", "medium": "yellow", "low": "red"}[learned.confidence]
    summary = Table.grid(padding=(0, 1))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("state_key",      state_key)
    summary.add_row("samples",        str(len(learned.samples)))
    summary.add_row("common suffix",  repr(learned.common_suffix))
    summary.add_row("regex",          learned.regex)
    summary.add_row("confidence",     f"[{conf_colour}]{learned.confidence}[/{conf_colour}]")

    console.print(Panel(Group(
        summary,
        sample_tbl,
    ), title="serial learn — derived", border_style=conf_colour))

    console.print(Panel(
        Syntax(learned.toml_snippet, "toml"),
        title="copy into ~/.config/alb/config.toml",
        border_style="green",
    ))


@app.command("probe")
def cmd_probe(
    ctx: typer.Context,
    device: str | None = typer.Option(
        None,
        "--device",
        help=(
            "Local serial path (/dev/ttyUSB0 …). If omitted and the current "
            "transport is TCP/ser2net, an actionable hint is printed instead "
            "of probing — that topology can't auto-cycle bauds."
        ),
    ),
    rates: str = typer.Option(
        "",
        "--rates",
        help=(
            "Comma-separated baud rates to try. Default: "
            "115200,921600,1500000,230400,9600,460800,3000000"
        ),
    ),
    duration: float = typer.Option(
        2.0,
        "--duration",
        help="Seconds to listen at each rate (2.0 is plenty for a live UART).",
    ),
) -> None:
    """Auto-cycle baud rates to discover what this UART speaks.

    Classic bringup problem: you get a new board, plug in the UART,
    open a terminal at 115200 … and see garbage. The baud is wrong
    and there's no hint of what the right one is.

    ``alb serial probe`` tries a list of common rates in sequence,
    measures ASCII printability + state-machine classification at each,
    and reports the ranking. The winning row gets a ★ marker.

    Works directly on ``--device /dev/ttyUSB0`` style paths. For our
    TCP-bridge topology (Xshell -R 19001 + Windows-side Python bridge),
    the bridge fixes the baud at launch — so ``probe`` prints the
    exact manual commands to try instead.
    """
    import json

    from alb.transport.serial_probe import (
        DEFAULT_RATES,
        pick_best,
        probe_bauds,
        probe_hint_for_tcp,
    )

    rate_tuple = DEFAULT_RATES
    if rates:
        try:
            rate_tuple = tuple(int(r) for r in rates.split(",") if r.strip())
        except ValueError:
            typer.echo("[fail] --rates must be comma-separated integers", err=True)
            raise typer.Exit(code=2)

    # Figure out what device to probe. Local path → probe directly.
    # No device given → look at the active transport: TCP means print
    # hint, local means use its device.
    if device is None:
        t = _force_serial(ctx, None)
        if t.device:
            device = t.device
        else:
            hint = probe_hint_for_tcp(
                tcp_host=t.tcp_host or "localhost",
                tcp_port=t.tcp_port or 19001,
                rates=rate_tuple,
            )
            if (ctx.obj or {}).get("json"):
                typer.echo(json.dumps({
                    "mode": "tcp",
                    "endpoint": f"{t.tcp_host}:{t.tcp_port}",
                    "hint": hint,
                    "rates": list(rate_tuple),
                }, indent=2))
            else:
                from alb.cli.common import console
                from rich.panel import Panel
                console.print(Panel(
                    hint,
                    title="serial probe — TCP bridge mode",
                    border_style="yellow",
                ))
            return

    assert device is not None
    results = run_async(probe_bauds(
        device, rates=rate_tuple, duration_s=duration,
    ))
    best = pick_best(results)

    if (ctx.obj or {}).get("json"):
        payload = {
            "device": device,
            "duration_per_rate": duration,
            "results": [
                {
                    "baud": r.baud,
                    "bytes_received": r.bytes_received,
                    "duration_s": round(r.duration_s, 3),
                    "ascii_density": round(r.ascii_density, 3),
                    "state": r.state.value,
                    "sample": r.sample.decode("utf-8", errors="replace"),
                    "error": r.error,
                    "recommended": r is best,
                }
                for r in results
            ],
            "best": best.baud if best else None,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    from rich.panel import Panel
    from rich.table import Table

    from alb.cli.common import console

    table = Table(title=f"baud probe on {device}", show_header=True)
    table.add_column("baud", justify="right")
    table.add_column("bytes", justify="right")
    table.add_column("rate b/s", justify="right")
    table.add_column("ascii %", justify="right")
    table.add_column("state")
    table.add_column("sample / note")

    for r in results:
        is_best = r is best
        tag = "[green]★[/green] " if is_best else "  "
        ascii_pct = f"{r.ascii_density * 100:.1f}%" if r.bytes_received else "—"
        rate = (
            f"{int(r.bytes_received / r.duration_s):,}" if r.duration_s > 0 else "—"
        )
        note = r.error or r.sample.decode("utf-8", errors="replace").replace("\n", " ⏎ ")[:60]
        state_colour = {
            "shell_root": "green", "shell_user": "green",
            "uboot": "cyan", "recovery": "cyan", "fastboot": "cyan",
            "kernel_boot": "yellow", "linux_init": "yellow", "spl": "yellow",
            "login_prompt": "yellow",
            "crash": "dark_orange",
            "panic": "red", "corrupted": "red",
            "idle": "bright_black", "unknown": "bright_black",
        }.get(r.state.value, "white")
        table.add_row(
            f"{tag}{r.baud:,}",
            str(r.bytes_received),
            rate,
            ascii_pct,
            f"[{state_colour}]{r.state.value}[/{state_colour}]",
            note or "",
        )

    console.print(table)
    if best:
        console.print(Panel(
            f"Recommended baud: [bold green]{best.baud:,}[/bold green] "
            f"(state={best.state.value}, ascii={best.ascii_density*100:.1f}%)\n\n"
            "Update ~/.config/alb/config.toml:\n"
            "  [transport.serial]\n"
            f"  default_baud = {best.baud}",
            title="result",
            border_style="green",
        ))
    else:
        console.print(Panel(
            "No baud produced usable output. Check:\n"
            "  - board is powered on\n"
            "  - UART TX / RX not swapped\n"
            "  - device path is correct (ls -la /dev/serial/by-id/)\n"
            "  - user has dialout group membership",
            title="no signal",
            border_style="red",
        ))


@app.command("status")
def cmd_status(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
    tail: int = typer.Option(
        400,
        "--tail",
        help="How many bytes of recent output to display.",
    ),
) -> None:
    """Detect what's currently at the UART endpoint.

    Runs handshake, classifies the state (shell / u-boot / kernel boot /
    panic / …) and prints a one-page report. Doesn't send any command.

    Typical use:

        alb serial status                # what is this board doing?
        alb --json serial status         # machine-readable for scripts

    States that matter most:

    - ``shell_root`` / ``shell_user``: shell is ready, run `alb serial shell`
    - ``uboot``: u-boot prompt; use `alb serial shell` for u-boot commands
    - ``kernel_boot``: board is still booting, wait and retry
    - ``panic``: board crashed — only tail is readable
    - ``corrupted``: likely wrong baud; try `alb setup serial --baud N`
    - ``idle``: no output; check power / cable / bridge
    """
    import json

    t = _force_serial(ctx, device)
    info = run_async(t.detect_state())

    # Trim tail for display if requested smaller than full
    if isinstance(info.get("tail"), str) and len(info["tail"]) > tail:
        info["tail"] = "…" + info["tail"][-tail:]

    if (ctx.obj or {}).get("json"):
        typer.echo(json.dumps(info, indent=2, default=str))
        return

    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table

    from alb.cli.common import console

    # Colour-code the state pill based on severity.
    state = info.get("state", "error")
    colour = {
        "shell_root": "green",  "shell_user": "green",
        "uboot": "cyan",        "recovery": "cyan",        "fastboot": "cyan",
        "kernel_boot": "yellow","linux_init": "yellow",    "spl": "yellow",
        "login_prompt": "yellow",
        "crash": "dark_orange",
        "panic": "red",         "corrupted": "red",
        "idle": "bright_black", "unknown": "bright_black",
    }.get(state, "white")

    summary = Table.grid(padding=(0, 1))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("state",    f"[{colour}]{state}[/{colour}]")
    summary.add_row("endpoint", str(info.get("endpoint", "")))
    summary.add_row("baud",     str(info.get("baud", "")))
    summary.add_row("duration", f"{info.get('duration_ms', '?')} ms")
    if not info.get("ok"):
        summary.add_row("error", str(info.get("error", "")))
        summary.add_row("code",  str(info.get("error_code", "")))

    body: list = [summary]
    if info.get("history"):
        history = Table(show_header=True, header_style="bold")
        history.add_column("from")
        history.add_column("→")
        history.add_column("to")
        history.add_column("wall time")
        for t_ in info["history"]:
            history.add_row(t_["from"], "→", t_["to"], t_.get("wall_time", ""))
        body.append(history)
    if info.get("tail"):
        body.append(Panel(info["tail"], title="tail", border_style="bright_black"))

    console.print(Panel(Group(*body), title="serial status", border_style=colour))
