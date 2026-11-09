"""Prompt composition with explicit static / dynamic boundary.

Why this module exists (M1 design goal):
    LLM system prompts for tool-using agents split naturally into two kinds
    of content:

    * **Static** — role description, safety rules, tool-usage norms.  These
      rarely change across sessions and are ideal for API-side prompt caching
      (Anthropic `cache_control`, OpenAI prompt caching, Ollama keep-alive).
    * **Dynamic** — current device serial, transport, workspace path, tool
      count hash, time.  Every request is different.

    If the two are interleaved, caching breaks on every call.  This module
    enforces the invariant *all static blocks precede all dynamic blocks*
    and emits the cache-boundary marker at the right place for each backend.

Typical use (agent layer, M2)::

    prompt = (
        PromptBuilder()
        .add_static(ROLE_PROMPT)
        .add_static(SAFETY_RULES)
        .add_dynamic(f"Current device: {serial} via {transport}")
        .add_dynamic(f"Workspace: {ws}")
        .build()
    )
    # Anthropic
    system_blocks = prompt.as_anthropic()
    # Ollama / OpenAI-compat
    system_text = prompt.as_text()

Note:
    Does NOT own prompts for specific LLM backends — backends in
    `alb.agent.backends.*` consume `Prompt` and map to their wire format.
    This module only cares about composition + caching boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_ROLE",
    "DEFAULT_SAFETY_RULES",
    "DEFAULT_TOOL_NORMS",
    "Prompt",
    "PromptBlock",
    "PromptBuilder",
    "PromptOrderError",
    "default_agent_prompt",
]


# ─── Block model ─────────────────────────────────────────────────────


class PromptOrderError(ValueError):
    """Raised when a static block is appended after a dynamic block."""


@dataclass(frozen=True)
class PromptBlock:
    """One contiguous slice of a system prompt.

    Attributes:
        content: The text (no trailing newline needed; builder joins with \\n\\n).
        cacheable: True for static content (role/rules). False for per-request
            content (device / workspace / time).
        name: Optional tag for debugging (shows in `Prompt.debug_dump()`).
    """

    content: str
    cacheable: bool = True
    name: str = ""

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("PromptBlock.content must be non-empty")


@dataclass(frozen=True)
class Prompt:
    """An ordered sequence of PromptBlock objects with a cache boundary.

    Invariants (checked by PromptBuilder at build time):
      * All cacheable blocks precede all non-cacheable blocks.
      * At least one block.

    Output methods produce backend-specific shapes — backends should not
    inspect `blocks` directly.
    """

    blocks: tuple[PromptBlock, ...]

    # ── Shape helpers ───────────────────────────────────────────
    def cache_boundary(self) -> int:
        """Index of the first non-cacheable block (== len(blocks) if all static)."""
        for i, b in enumerate(self.blocks):
            if not b.cacheable:
                return i
        return len(self.blocks)

    def as_text(self) -> str:
        """Flatten to a single string — use for backends without caching."""
        return "\n\n".join(b.content for b in self.blocks)

    def as_anthropic(self) -> list[dict[str, Any]]:
        """Return a list of text-typed system blocks per Anthropic's schema.

        The last cacheable block gets `cache_control={"type":"ephemeral"}`.
        The SDK then caches everything up to (and including) that block.

        Empty static section → no cache_control.
        """
        boundary = self.cache_boundary()
        out: list[dict[str, Any]] = []
        for i, b in enumerate(self.blocks):
            item: dict[str, Any] = {"type": "text", "text": b.content}
            # Mark the last cacheable block as the cache breakpoint.
            if b.cacheable and i == boundary - 1:
                item["cache_control"] = {"type": "ephemeral"}
            out.append(item)
        return out

    def as_openai(self) -> str:
        """OpenAI-compatible chat/completions — single flattened string.

        OpenAI's automatic prefix caching works on raw token prefixes, so
        as long as callers feed the same static text every time, caching
        happens transparently.  No explicit markers needed.
        """
        return self.as_text()

    def debug_dump(self) -> str:
        """Multiline representation with cacheable flags — for `alb describe`."""
        lines = []
        for i, b in enumerate(self.blocks):
            tag = "STATIC" if b.cacheable else "DYNAMIC"
            label = f" [{b.name}]" if b.name else ""
            lines.append(f"[{i}] {tag}{label} ({len(b.content)} chars)")
            lines.append(b.content)
        lines.append(f"\n(cache_boundary at index {self.cache_boundary()})")
        return "\n".join(lines)


# ─── Builder ─────────────────────────────────────────────────────────


@dataclass
class PromptBuilder:
    """Fluent builder for `Prompt`.

    Enforces the static-before-dynamic invariant at construction time so
    bugs surface where the mistake is, not at runtime when cache misses
    quietly cost money.
    """

    _blocks: list[PromptBlock] = field(default_factory=list, repr=False)
    _seen_dynamic: bool = field(default=False, repr=False)

    def add_static(self, content: str, *, name: str = "") -> "PromptBuilder":
        """Append a cacheable block.  Must come before any dynamic block."""
        if self._seen_dynamic:
            raise PromptOrderError(
                "cannot add_static after add_dynamic — "
                "static blocks must precede dynamic for caching to work"
            )
        self._blocks.append(PromptBlock(content=content, cacheable=True, name=name))
        return self

    def add_dynamic(self, content: str, *, name: str = "") -> "PromptBuilder":
        """Append a non-cacheable block.  Any number allowed, must be last."""
        self._blocks.append(PromptBlock(content=content, cacheable=False, name=name))
        self._seen_dynamic = True
        return self

    def build(self) -> Prompt:
        if not self._blocks:
            raise ValueError("PromptBuilder.build(): no blocks added")
        return Prompt(blocks=tuple(self._blocks))


# ─── Default content (static) ────────────────────────────────────────
# These are used by `default_agent_prompt()` and `agent.loop.AgentLoop`.
# Keep them stable — changes invalidate every cached prefix.

DEFAULT_ROLE = """\
You are the Android-device assistant backed by the `alb` tool suite.

Your job is to help the user debug, configure, and operate Android devices
by invoking `alb` tools (shell / logcat / filesync / diagnose / power / app).
You translate the user's intent into one or more tool calls and report back
concise results.\
"""

DEFAULT_SAFETY_RULES = """\
Rules:
  * Prefer tool calls over answering from memory.  The user's device state
    is authoritative — your training data is not.
  * Do NOT attempt to analyse long log contents yourself.  Return the saved
    file path and, if needed, a short summary; leave deep analysis to the
    user or to a larger model.
  * If a tool returns `error.suggestion`, follow it before retrying.
  * Destructive operations (`rm`, `reboot bootloader`, `fastboot erase`,
    `pm uninstall`) require user confirmation if the permission system
    asks — never silently proceed.\
"""

# Which language the answer is written in.  The web page sends its UI
# language; everything else gets Chinese (user decision 2026-11-03: the
# model answered an English "hello" in English, and the people using this
# read Chinese).  Small local models follow the rule more reliably when it
# is also written in the target language.
REPLY_LANGUAGE_RULES: dict[str, str] = {
    "zh": (
        "Language: reply in Simplified Chinese, even when the user writes in "
        "another language, unless the user asks for a different language.\n"
        "默认用简体中文回答。工具名、命令、文件路径、日志原文保持原样,不要翻译。"
    ),
    "en": (
        "Language: reply in English unless the user asks for a different "
        "language.  Keep tool names, commands, file paths and log lines as "
        "they are."
    ),
    "auto": "Language: reply in the language the user writes in.",
}
DEFAULT_REPLY_LANGUAGE = "zh"

# The same request again, at the end of each user message the model sees.
# The rule above alone was not enough: Ling's chat template puts all tool
# definitions (~6k tokens for 36 tools) between the system prompt and the
# user's message, and an English "hello" got an English answer 3 times out
# of 3; with this note, Chinese 3 out of 3 (2026-11-03, real model).
REPLY_LANGUAGE_NOTES: dict[str, str | None] = {
    "zh": "(请用简体中文回答)",
    "en": "(Please answer in English.)",
    "auto": None,
}


def reply_language_note(reply_language: str) -> str | None:
    """Note for AgentLoop(user_note=...); unknown values count as "zh"."""
    if reply_language in REPLY_LANGUAGE_NOTES:
        return REPLY_LANGUAGE_NOTES[reply_language]
    return REPLY_LANGUAGE_NOTES[DEFAULT_REPLY_LANGUAGE]


# The model line in the system prompt is not enough once the chat has a
# turn or two: asked "你的模型id是啥" right after "你好", Ling-3.0-tiny
# called alb_describe in 8 of 10 runs and said in 6 it had no model ID
# (as the first message: 19 of 19 right, 2026-11-03). Tool definitions sit
# between the system prompt and the chat, so the note at the end of each
# user message is the text it reads last, the same reason the language
# request is there.
_MODEL_NOTES: dict[str, tuple[str, str]] = {
    # (model part, context part); {model} / {ctx} filled in
    "zh": (
        "你是语言模型 {model}",
        ",上下文 {ctx} token",
    ),
    "en": (
        "You are the language model {model}",
        ", context window {ctx} tokens",
    ),
}
_MODEL_NOTE_TAIL = {
    "zh": ";问到你的模型、模型 ID 或上下文时直接用这些值回答,不用调工具。",
    "en": "; when asked about your model, model ID or context, answer with these values, no tool needed.",
}


# The board selected in the web page. Without it the model did not know
# which board it was on: asked for the battery level it first called
# alb_battery(device=""), which failed, then alb_status to find the serial,
# then alb_battery again — 3 tool calls where 1 does (2026-11-05, 8.1 s).
# It goes here and not in the system prompt: the chat template puts the
# ~6k-token tool list after the system prompt, so a serial there would make
# the model re-read the tool list whenever the board changes, and the
# hub's warm-up (which runs before any board is known) would no longer
# match the first chat.
_DEVICE_NOTES = {
    "zh": "当前板子 {device}:工具的 device 参数填它,不用先调 alb_status 或 alb_devices 查。",
    "en": "The current board is {device}: pass it as the device argument; no need to call alb_status or alb_devices first.",
}


def agent_user_note(
    reply_language: str,
    *,
    model: str | None = None,
    context_tokens: int | None = None,
    device: str | None = None,
) -> str | None:
    """Note for AgentLoop(user_note=...): the reply language request and,
    when known, which model this is and which board the page has selected.

        zh → "(请用简体中文回答。你是语言模型 X,上下文 N token;问到…不用调工具。当前板子 S:…)"

    "auto" has no note at all, as before: a sentence in either language
    pulls the reply toward it."""
    lang = reply_language if reply_language in REPLY_LANGUAGE_NOTES else DEFAULT_REPLY_LANGUAGE
    base = REPLY_LANGUAGE_NOTES[lang]
    if base is None or lang not in _MODEL_NOTES:
        return base
    parts: list[str] = []
    if model:
        who, ctx = _MODEL_NOTES[lang]
        text = who.format(model=model)
        if context_tokens:
            text += ctx.format(ctx=context_tokens)
        parts.append(text + _MODEL_NOTE_TAIL[lang])
    if device:
        parts.append(_DEVICE_NOTES[lang].format(device=device))
    if not parts:
        return base
    # base is "(…)" or "(…)"; put the rest inside the same brackets
    if lang == "zh":
        return f"{base[:-1]}。{''.join(parts)})"
    return f"{base[:-1]} {' '.join(parts)})"

DEFAULT_TOOL_NORMS = """\
Tool usage norms:
  * Long captures (> 5 min logcat) should return a file path, not the raw
    log content.  Use `alb_log_search` / `alb_log_tail` to inspect.
  * When picking between transports (adb / ssh / serial), prefer the one
    already configured as primary.  Switch only if the primary can't do it.
  * Every tool call's result includes `artifacts` — relay the paths back
    to the user so they can open the files.\
"""


# ─── Convenience constructor ─────────────────────────────────────────


def default_agent_prompt(
    *,
    device_serial: str | None = None,
    transport_name: str = "unknown",
    workspace_root: Path | None = None,
    tool_count: int | None = None,
    extra_static: list[str] | None = None,
    extra_dynamic: list[str] | None = None,
    reply_language: str = DEFAULT_REPLY_LANGUAGE,
    model: str | None = None,
    backend: str | None = None,
    context_tokens: int | None = None,
) -> Prompt:
    """Assemble the standard agent system prompt.

    Args:
        device_serial: current target device serial; None if none selected.
        transport_name: name of the primary transport ("adb"/"ssh"/...).
        workspace_root: path to the workspace root (for the dynamic block).
        tool_count: number of tools registered; helps the model judge how
            rich the tool surface is.
        extra_static: optional extra static blocks (e.g. project-specific
            conventions from the user's CLAUDE.md).  Appended after the
            built-in static blocks.
        extra_dynamic: optional extra dynamic blocks (e.g. recent error
            summary from session history).  Appended after the built-in
            dynamic blocks.
        reply_language: "zh" (default), "en" or "auto" (follow the user);
            see REPLY_LANGUAGE_RULES.  Unknown values fall back to "zh".
        model / backend / context_tokens: what the model runs as, so it
            can answer "which model are you" / "how much context do you
            have".  Without them it cannot know, and a small model makes
            something up (2026-11-03: "I have no model ID").

    The cache boundary lands after all static blocks, so:
        static = role + safety + norms + extra_static
        dynamic = device + workspace + tools + extra_dynamic
    """
    b = (
        PromptBuilder()
        .add_static(DEFAULT_ROLE, name="role")
        .add_static(DEFAULT_SAFETY_RULES, name="safety")
        .add_static(DEFAULT_TOOL_NORMS, name="tool_norms")
        .add_static(
            REPLY_LANGUAGE_RULES.get(reply_language)
            or REPLY_LANGUAGE_RULES[DEFAULT_REPLY_LANGUAGE],
            name="language",
        )
    )
    for i, blk in enumerate(extra_static or []):
        b.add_static(blk, name=f"extra_static_{i}")

    # Dynamic section
    device_line = (
        f"Current device: {device_serial} (via {transport_name})"
        if device_serial
        else f"No device selected (primary transport: {transport_name})"
    )
    b.add_dynamic(device_line, name="device")

    if model:
        # Spelled out: "Model: <name>" alone was not enough — asked for its
        # "模型 ID", Ling-3.0-tiny still said it had none (2026-11-03).
        line = (
            f"Model: you are the language model {model}"
            + (f", served by {backend}" if backend else "")
            + f". {model} is your model name (model ID)."
        )
        if context_tokens:
            line += (
                f" Context window: {context_tokens} tokens per request"
                " (system prompt, tool definitions, history and the answer together)."
            )
        line += (
            " When asked which model you are, your model ID or how much context"
            " you have, answer with these values."
        )
        b.add_dynamic(line, name="model")

    if workspace_root is not None:
        b.add_dynamic(f"Workspace root: {workspace_root}", name="workspace")

    if tool_count is not None:
        b.add_dynamic(
            f"Tools available: {tool_count} (see response `tools` field for schema)",
            name="tools",
        )

    for i, blk in enumerate(extra_dynamic or []):
        b.add_dynamic(blk, name=f"extra_dynamic_{i}")

    return b.build()
