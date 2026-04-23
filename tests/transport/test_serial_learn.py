"""Tests for the prompt auto-learner.

The pure helpers — ``learn_from_samples``, ``_longest_common_suffix``,
``_generalise_to_regex`` — are fully exercised with synthetic input.
The I/O-facing ``learn_prompt`` is integration-level and runs against
a real board (manual / in internal repo).
"""

from __future__ import annotations

import re

from alb.transport.serial_learn import (
    LearnedPrompt,
    _generalise_to_regex,
    _longest_common_suffix,
    _normalise,
    learn_from_samples,
)


# ─── _longest_common_suffix ────────────────────────────────────────


def test_common_suffix_basic() -> None:
    assert _longest_common_suffix(["abc xyz", "def xyz"]) == " xyz"


def test_common_suffix_one_string_is_whole_string() -> None:
    assert _longest_common_suffix(["just one"]) == "just one"


def test_common_suffix_all_identical() -> None:
    assert _longest_common_suffix(["abc", "abc", "abc"]) == "abc"


def test_common_suffix_none_match() -> None:
    assert _longest_common_suffix(["abc", "xyz"]) == ""


def test_common_suffix_empty_input() -> None:
    assert _longest_common_suffix([]) == ""


def test_common_suffix_shell_prompts() -> None:
    # realistic Android shell prompts with different CWDs
    samples = [
        "root@localhost:/ # ",
        "root@localhost:/tmp # ",
        "root@localhost:/etc # ",
    ]
    # The common suffix is " # " (space hash space) — the colon and
    # slash after the hostname are the same but after them comes the
    # CWD which differs.
    suffix = _longest_common_suffix(samples)
    assert suffix.endswith(" # ")


# ─── _normalise ────────────────────────────────────────────────────


def test_normalise_strips_cr() -> None:
    assert _normalise("hello\r world") == "hello world"


def test_normalise_strips_ansi_escape() -> None:
    # \x1b[32m → green color; \x1b[0m → reset
    assert _normalise("\x1b[32mroot@h\x1b[0m:/ # ") == "root@h:/ # "


def test_normalise_preserves_trailing_space() -> None:
    assert _normalise("$ ") == "$ "


# ─── _generalise_to_regex ─────────────────────────────────────────


def test_generalise_anchors_to_line_start_and_eob() -> None:
    samples = ["root@h:/ # ", "root@h:/tmp # "]
    suffix = " # "
    rx = _generalise_to_regex(samples, suffix)
    assert rx.startswith("(?:^|\\n)")
    assert rx.endswith(r"\s*$")


def test_generalise_escapes_special_chars_in_suffix() -> None:
    rx = _generalise_to_regex(["x$ ", "y$ "], "$ ")
    # `$` and ` ` both escaped
    assert r"\$" in rx
    # Full regex compiles
    re.compile(rx)


def test_generalised_regex_matches_all_samples() -> None:
    samples = [
        "root@localhost:/ # ",
        "root@localhost:/tmp # ",
        "root@localhost:/etc/sysconfig # ",
    ]
    suffix = _longest_common_suffix(samples)
    rx = _generalise_to_regex(samples, suffix)
    pattern = re.compile(rx.encode(), re.MULTILINE)
    for s in samples:
        assert pattern.search(s.encode()), f"regex {rx!r} did not match {s!r}"


def test_generalised_regex_does_not_match_in_mid_line() -> None:
    """`echo ' # '` output should NOT be treated as a prompt — the
    regex requires line start and end-of-buffer anchoring."""
    samples = ["root@h:/ # ", "root@h:/tmp # "]
    rx = _generalise_to_regex(samples, _longest_common_suffix(samples))
    pattern = re.compile(rx.encode(), re.MULTILINE)
    bogus = b"echo 'something # inside output'\n"
    assert not pattern.search(bogus)


def test_generalise_empty_suffix_returns_empty_regex() -> None:
    assert _generalise_to_regex(["a", "b"], "") == ""


# ─── learn_from_samples ───────────────────────────────────────────


def test_learn_high_confidence_three_samples_stable_suffix() -> None:
    samples = [
        "root@h:/ # ",
        "root@h:/tmp # ",
        "root@h:/etc # ",
        "root@h:/data # ",
        "root@h:/var # ",
    ]
    out = learn_from_samples(samples)
    assert out.confidence == "high"
    assert out.common_suffix.endswith(" # ")
    assert out.regex  # non-empty
    # TOML snippet includes the state key and is valid TOML
    assert "[transport.serial.prompts]" in out.toml_snippet
    assert "shell_root" in out.toml_snippet
    assert out.regex.replace("\\", "\\\\") in out.toml_snippet


def test_learn_low_confidence_with_empty_samples() -> None:
    out = learn_from_samples([])
    assert out.confidence == "low"
    assert out.regex == ""
    assert out.toml_snippet == ""


def test_learn_medium_confidence_with_two_diverse_samples() -> None:
    out = learn_from_samples(["~ # ", "~/tmp # "])
    # Two samples, short common suffix — heuristic picks medium or
    # high; accept either non-low outcome.
    assert out.confidence in {"high", "medium"}
    assert out.regex


def test_learn_low_confidence_when_samples_lack_suffix() -> None:
    out = learn_from_samples(["hello", "world"])
    assert out.confidence == "low"
    assert out.regex == ""


def test_learn_respects_custom_state_key() -> None:
    out = learn_from_samples(
        ["=> ", "=> ", "=> "],
        state_key="uboot",
    )
    assert "uboot" in out.toml_snippet
    assert "shell_root" not in out.toml_snippet


def test_learn_regex_matches_unseen_but_similar_prompt() -> None:
    """The point of learning is that the regex generalises — a new
    CWD the learner never saw should still match."""
    samples = [
        "myboard:/ # ",
        "myboard:/tmp # ",
        "myboard:/var/log # ",
    ]
    out = learn_from_samples(samples)
    pattern = re.compile(out.regex.encode(), re.MULTILINE)
    unseen = b"\nmyboard:/some/new/dir # "
    assert pattern.search(unseen)


def test_learned_regex_rejects_totally_different_board_prompt() -> None:
    samples = ["ubuntu@h:~$ ", "ubuntu@h:/tmp$ "]
    out = learn_from_samples(samples, state_key="shell_user")
    pattern = re.compile(out.regex.encode(), re.MULTILINE)
    # Different ending character (# vs $) must NOT match
    bogus = b"\nfoo@bar:/ # "
    assert not pattern.search(bogus)


# ─── LearnedPrompt dataclass ─────────────────────────────────────


def test_learned_prompt_is_frozen() -> None:
    import dataclasses
    lp = LearnedPrompt(samples=[], common_suffix="", regex="", confidence="low", toml_snippet="")
    assert dataclasses.is_dataclass(lp)
    # Frozen: can't mutate
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        lp.confidence = "high"  # type: ignore[misc]
