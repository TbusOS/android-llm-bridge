"""Tests for alb.infra.prompt_builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from alb.infra.prompt_builder import (
    DEFAULT_ROLE,
    DEFAULT_SAFETY_RULES,
    DEFAULT_TOOL_NORMS,
    Prompt,
    PromptBlock,
    PromptBuilder,
    PromptOrderError,
    default_agent_prompt,
)


# ─── PromptBlock ─────────────────────────────────────────────────────


def test_block_rejects_empty_content() -> None:
    with pytest.raises(ValueError):
        PromptBlock(content="")


def test_block_defaults_cacheable_true() -> None:
    b = PromptBlock(content="hello")
    assert b.cacheable is True
    assert b.name == ""


# ─── PromptBuilder ordering invariant ────────────────────────────────


def test_empty_build_rejected() -> None:
    with pytest.raises(ValueError):
        PromptBuilder().build()


def test_static_after_dynamic_rejected() -> None:
    b = PromptBuilder().add_dynamic("now")
    with pytest.raises(PromptOrderError):
        b.add_static("you are X")


def test_static_before_dynamic_ok() -> None:
    p = (
        PromptBuilder()
        .add_static("role")
        .add_static("rules")
        .add_dynamic("device")
        .build()
    )
    assert len(p.blocks) == 3
    assert [b.cacheable for b in p.blocks] == [True, True, False]


def test_static_only_ok() -> None:
    p = PromptBuilder().add_static("only static").build()
    assert len(p.blocks) == 1
    assert p.cache_boundary() == 1


def test_dynamic_only_ok() -> None:
    p = PromptBuilder().add_dynamic("only dynamic").build()
    assert len(p.blocks) == 1
    assert p.cache_boundary() == 0


# ─── Output formats ──────────────────────────────────────────────────


def test_as_text_joins_with_double_newline() -> None:
    p = (
        PromptBuilder()
        .add_static("A")
        .add_static("B")
        .add_dynamic("C")
        .build()
    )
    assert p.as_text() == "A\n\nB\n\nC"


def test_as_anthropic_marks_last_static_block() -> None:
    p = (
        PromptBuilder()
        .add_static("role")
        .add_static("rules")
        .add_dynamic("device")
        .build()
    )
    out = p.as_anthropic()
    assert len(out) == 3
    assert out[0] == {"type": "text", "text": "role"}
    assert out[1] == {
        "type": "text",
        "text": "rules",
        "cache_control": {"type": "ephemeral"},
    }
    assert out[2] == {"type": "text", "text": "device"}  # no cache_control


def test_as_anthropic_no_static_means_no_cache_control() -> None:
    p = PromptBuilder().add_dynamic("only dynamic").build()
    out = p.as_anthropic()
    assert out == [{"type": "text", "text": "only dynamic"}]


def test_as_anthropic_all_static_marks_last() -> None:
    p = PromptBuilder().add_static("a").add_static("b").build()
    out = p.as_anthropic()
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}


def test_as_openai_same_as_text() -> None:
    p = PromptBuilder().add_static("x").add_dynamic("y").build()
    assert p.as_openai() == p.as_text() == "x\n\ny"


def test_cache_boundary_correctness() -> None:
    p = (
        PromptBuilder()
        .add_static("a")
        .add_static("b")
        .add_static("c")
        .add_dynamic("d")
        .add_dynamic("e")
        .build()
    )
    assert p.cache_boundary() == 3


def test_debug_dump_labels_blocks() -> None:
    p = (
        PromptBuilder()
        .add_static("role", name="role")
        .add_dynamic("now", name="time")
        .build()
    )
    dump = p.debug_dump()
    assert "STATIC" in dump
    assert "DYNAMIC" in dump
    assert "[role]" in dump
    assert "[time]" in dump
    assert "cache_boundary at index 1" in dump


# ─── default_agent_prompt ────────────────────────────────────────────


def test_default_prompt_has_role_safety_norms() -> None:
    p = default_agent_prompt(
        device_serial="abc123",
        transport_name="adb",
        workspace_root=Path("/tmp/ws"),
        tool_count=21,
    )
    text = p.as_text()
    assert DEFAULT_ROLE in text
    assert DEFAULT_SAFETY_RULES in text
    assert DEFAULT_TOOL_NORMS in text


def test_default_prompt_device_line_connected() -> None:
    p = default_agent_prompt(device_serial="abc123", transport_name="adb")
    text = p.as_text()
    assert "Current device: abc123 (via adb)" in text


def test_default_prompt_device_line_no_device() -> None:
    p = default_agent_prompt(transport_name="ssh")
    text = p.as_text()
    assert "No device selected" in text
    assert "primary transport: ssh" in text


def test_default_prompt_omits_workspace_if_none() -> None:
    p = default_agent_prompt(transport_name="adb")
    text = p.as_text()
    assert "Workspace root:" not in text


def test_default_prompt_includes_workspace_if_given() -> None:
    p = default_agent_prompt(transport_name="adb", workspace_root=Path("/home/ws"))
    assert "Workspace root: /home/ws" in p.as_text()


def test_default_prompt_includes_tool_count_if_given() -> None:
    p = default_agent_prompt(transport_name="adb", tool_count=21)
    assert "Tools available: 21" in p.as_text()


def test_default_prompt_static_count_at_least_three() -> None:
    """Role + safety + tool_norms are all static."""
    p = default_agent_prompt(transport_name="adb")
    # At least 3 static blocks; dynamic count depends on what was supplied.
    boundary = p.cache_boundary()
    assert boundary >= 3


def test_default_prompt_extras_appended_correctly() -> None:
    p = default_agent_prompt(
        transport_name="adb",
        extra_static=["project rule X"],
        extra_dynamic=["recent error: TIMEOUT_SHELL 3 times"],
    )
    static_texts = [b.content for b in p.blocks if b.cacheable]
    dynamic_texts = [b.content for b in p.blocks if not b.cacheable]
    assert "project rule X" in static_texts
    assert "recent error: TIMEOUT_SHELL 3 times" in dynamic_texts


def test_default_prompt_anthropic_shape_has_cache_boundary() -> None:
    p = default_agent_prompt(
        device_serial="x",
        transport_name="adb",
        workspace_root=Path("/ws"),
        tool_count=5,
    )
    blocks = p.as_anthropic()
    # Exactly one block should carry cache_control (the last static one).
    with_cache = [b for b in blocks if "cache_control" in b]
    assert len(with_cache) == 1
    # The first static block (role) should NOT carry cache_control.
    assert "cache_control" not in blocks[0]


def test_builder_returns_self_for_chaining() -> None:
    b = PromptBuilder()
    assert b.add_static("x") is b
    assert b.add_dynamic("y") is b


def test_prompt_is_immutable() -> None:
    p = PromptBuilder().add_static("x").build()
    # blocks is a tuple -- can't append
    with pytest.raises(AttributeError):
        p.blocks.append(PromptBlock(content="y"))  # type: ignore[attr-defined]
