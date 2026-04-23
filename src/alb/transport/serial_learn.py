"""Prompt-regex auto-learner for UART endpoints.

Custom boards — bootloaders with unusual prompts, embedded Linux distros
with multi-line PS1, Android recovery variants — break the built-in
``DEFAULT_PATTERNS`` matcher. Writing a TOML override by hand is slow
and error-prone; this module observes a live session and **derives**
the prompt regex from what the board actually prints.

How it works
------------
1. Against an already-classified POSIX-ish shell state, we send a
   sequence of ``cd`` commands (``cd /`` ``cd /tmp`` ``cd /etc``…)
   and read what follows each one.
2. Each response ends in the prompt. We extract the trailing line
   (everything after the last ``\\n``) — that's one sample.
3. From ``N`` samples we compute the longest common suffix; that's
   the stable part of the prompt (``:/ #`` / ``> `` / etc).
4. The **varying** parts are almost always the path / hostname /
   username — we generalise those to ``[^\\s]*``.
5. We emit a strict, anchored regex plus the TOML config snippet the
   user can copy.

Why ``cd <path>`` as the trigger
--------------------------------
Cheap, side-effect-free (on POSIX shells), and reliably changes the
prompt on distros that show ``$PWD`` in PS1. Plain ``echo`` commands
also work but the prompt stays constant, which gives us less signal
about what's dynamic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LearnedPrompt:
    """Outcome of a learning pass.

    Attributes
    ----------
    samples
        Raw prompt strings captured (trailing-line of each response).
    common_suffix
        Longest common suffix across ``samples`` — the stable part.
    regex
        A suggested regex, anchored to end-of-buffer, ready for
        ``[transport.serial.prompts]`` in config.toml.
    confidence
        ``"high"`` when ≥ 3 samples agree on a non-trivial common
        suffix (≥ 2 chars incl. prompt char), ``"medium"`` when the
        suffix is short, ``"low"`` when samples disagreed a lot.
    toml_snippet
        Convenience: the exact TOML block to paste.
    """

    samples: list[str]
    common_suffix: str
    regex: str
    confidence: str
    toml_snippet: str


def learn_from_samples(
    samples: list[str],
    *,
    state_key: str = "shell_root",
) -> LearnedPrompt:
    """Derive a prompt regex from a list of captured prompt strings.

    Pure function — takes raw prompt strings (each typically ending
    in ``#`` / ``$`` / ``>``), returns a :class:`LearnedPrompt`. The
    I/O side lives in :func:`learn_prompt` which feeds real captures
    into here.

    Parameters
    ----------
    samples
        Raw prompt strings. Leading whitespace is preserved — we
        operate on bytes as captured.
    state_key
        Which :class:`SerialState` family this prompt belongs to.
        Used to label the generated TOML snippet. Common values:
        ``shell_root`` / ``shell_user`` / ``uboot`` / ``recovery``.
    """
    if not samples:
        return LearnedPrompt(
            samples=[],
            common_suffix="",
            regex="",
            confidence="low",
            toml_snippet="",
        )

    # Normalise: strip trailing whitespace-only chars that vary
    # between lines (e.g. CR), but preserve the single trailing
    # space that's part of most prompts.
    normed = [_normalise(s) for s in samples]

    suffix = _longest_common_suffix(normed)
    regex = _generalise_to_regex(normed, suffix) if suffix else ""

    unique_samples = len(set(normed))
    if len(samples) >= 3 and len(suffix) >= 2:
        confidence = "high"
    elif suffix and unique_samples >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    snippet = ""
    if regex:
        escaped = regex.replace("\\", "\\\\").replace('"', '\\"')
        snippet = (
            "[transport.serial.prompts]\n"
            f'{state_key} = "{escaped}"\n'
        )

    return LearnedPrompt(
        samples=normed,
        common_suffix=suffix,
        regex=regex,
        confidence=confidence,
        toml_snippet=snippet,
    )


def _normalise(s: str) -> str:
    """Strip CR (``\\r``) and terminal escape sequences that would
    make the suffix harder to compare. Keep spaces — prompts often
    end with a literal space.
    """
    # Drop ANSI CSI sequences (colour codes, cursor positions)
    s = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s)
    # Strip lone CR
    s = s.replace("\r", "")
    return s


def _longest_common_suffix(strings: list[str]) -> str:
    """Longest string that is a suffix of every input string."""
    if not strings:
        return ""
    shortest = min(strings, key=len)
    for i in range(len(shortest)):
        ch = shortest[-(i + 1)]
        if not all(s[-(i + 1)] == ch for s in strings if len(s) > i):
            break
        if not all(len(s) > i for s in strings):
            break
    else:
        return shortest
    return shortest[-i:] if i > 0 else ""


def _generalise_to_regex(samples: list[str], common_suffix: str) -> str:
    """Build an anchored regex: generalise the varying prefix.

    Observation: the part that varies across ``cd`` prompts is
    almost always a file-system path (or hostname). We replace it
    with ``[^\\s]*`` — matches non-whitespace, stops at the first
    whitespace before the common suffix starts.

    The common suffix is escaped literally so the regex tracks the
    exact prompt characters (``:/  #`` / ``> `` / ``=>``) the board
    uses.
    """
    if not common_suffix:
        return ""

    # Strip leading whitespace from the suffix we escape — usually a
    # leading space is part of the prompt and should be literal.
    escaped_tail = re.escape(common_suffix)

    # The prefix differs between samples, but every sample has SOME
    # prefix before the common suffix. Generalise with ``[^\\s]*``.
    # We also allow a leading newline-boundary via ``(?:^|\\n)`` so
    # the prompt is anchored to a line start — avoids matching the
    # suffix accidentally in mid-line command output.
    return rf"(?:^|\n)[^\s]*{escaped_tail}\s*$"


async def learn_prompt(
    transport,  # noqa: ANN001 — runtime type is SerialTransport, avoid cycle
    *,
    samples: int = 5,
    timeout_per_cmd: int = 5,
    state_key: str = "shell_root",
) -> LearnedPrompt:
    """Run a learning session against a live serial endpoint.

    Expects the endpoint to already be in a POSIX-ish shell state
    (SHELL_USER / SHELL_ROOT / CRASH). Sends ``samples`` different
    ``cd`` commands, captures each response's trailing prompt, and
    feeds them into :func:`learn_from_samples`.

    Parameters
    ----------
    transport
        A connected :class:`alb.transport.serial.SerialTransport`
        instance that already proved it can reach the shell (ideally
        :meth:`SerialTransport.detect_state` returned a shell state).
    samples
        Number of probe commands to run. 5 is a good default — enough
        for a high-confidence common suffix, not so many that a slow
        UART feels sluggish.
    timeout_per_cmd
        Per-command shell timeout (seconds). Short because all we
        care about is ``cd`` returning to a prompt.
    state_key
        Which prompt key to emit in the TOML snippet.
    """
    # Each probe command changes dir to a different valid location.
    # Cheap, safe, and causes PS1 to reflect a new path for most
    # shells with path-aware PS1.
    probes = [
        "cd / && pwd",
        "cd /tmp && pwd",
        "cd /etc && pwd",
        "cd /data && pwd",
        "cd / && pwd",
        "cd /system && pwd",
        "cd /var && pwd",
        "cd /root && pwd",
        "cd / && pwd",
    ][:samples]

    captured: list[str] = []
    for cmd in probes:
        # Use the private helper — we want the raw tail including prompt,
        # not the marker-wrapped stdout. Running via `transport.shell()`
        # would strip the prompt. Instead we call `detect_state()`
        # after each probe: it re-connects, handshakes, and snapshots
        # the tail — which is exactly the post-command prompt.
        await transport.shell(cmd, timeout=timeout_per_cmd)
        info = await transport.detect_state()
        tail = info.get("tail", "")
        # Keep only the last line (everything after the last newline).
        last_line = tail.rsplit("\n", 1)[-1]
        if last_line.strip():
            captured.append(last_line)

    return learn_from_samples(captured, state_key=state_key)
