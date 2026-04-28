"""Web API schema constants.

Central location for the protocol version + the canonical list of
REST endpoints and WebSocket message types. A JSON serialisation of
this file is what `GET /api/version` returns, so the shape here IS
the contract.

Bumping API_VERSION means a breaking change for clients that pin
against a specific version. Add / remove endpoints freely within the
same version — clients should feature-detect via the returned lists.
"""

from __future__ import annotations

from typing import Any, TypedDict


API_VERSION = "1"


class EndpointSpec(TypedDict, total=False):
    path: str
    method: str
    description: str


class WSMessageSpec(TypedDict, total=False):
    type: str
    direction: str  # "C→S" | "S→C"
    description: str


class WSSpec(TypedDict, total=False):
    path: str
    description: str
    messages: list[WSMessageSpec]


REST_ENDPOINTS: list[EndpointSpec] = [
    {"method": "GET",  "path": "/health",
     "description": "Server liveness + version"},
    {"method": "GET",  "path": "/api/version",
     "description": "Protocol schema (this document) in JSON form"},
    {"method": "POST", "path": "/chat",
     "description": "Agent chat, non-streaming. See ChatRequest."},
    {"method": "GET",  "path": "/playground/backends",
     "description": "Registered LLM backends with capabilities"},
    {"method": "GET",  "path": "/playground/backends/{backend}/models",
     "description": "Models installed on the given backend"},
    {"method": "POST", "path": "/playground/chat",
     "description": "Raw LLM chat (no agent loop), non-streaming"},
    {"method": "GET",  "path": "/sessions",
     "description": "List recent ChatSession dirs (Dashboard feed)"},
    {"method": "GET",  "path": "/devices",
     "description": "Devices visible through the active transport"},
    {"method": "GET",  "path": "/audit",
     "description": "Recent audit events (chat + terminal jsonl) for the Timeline"},
]

WS_ENDPOINTS: list[WSSpec] = [
    {
        "path": "/chat/ws",
        "description": "Streaming agent chat with tool dispatch.",
        "messages": [
            {"type": "<client-first>",   "direction": "C→S",
             "description": "ChatRequest JSON body"},
            {"type": "token",            "direction": "S→C",
             "description": "{delta} — partial assistant content"},
            {"type": "tool_call_start",  "direction": "S→C",
             "description": "{name, arguments} — a tool is about to run"},
            {"type": "tool_call_end",    "direction": "S→C",
             "description": "{name, result} — tool completed"},
            {"type": "done",             "direction": "S→C",
             "description": "Terminal event with content / usage / session_id"},
        ],
    },
    {
        "path": "/playground/chat/ws",
        "description": "Raw LLM streaming chat — bypasses agent loop.",
        "messages": [
            {"type": "<client-first>",   "direction": "C→S",
             "description": "PlaygroundChatRequest JSON"},
            {"type": "token",            "direction": "S→C",
             "description": "{delta}"},
            {"type": "done",             "direction": "S→C",
             "description": "Terminal — ok, content, thinking, metrics, error"},
        ],
    },
    {
        "path": "/metrics/stream",
        "description": "1 Hz device telemetry (CPU / mem / temp / IO / GPU / battery).",
        "messages": [
            {"type": "<client-first>",   "direction": "C→S",
             "description": "Optional {device, history_seconds}"},
            {"type": "history",          "direction": "S→C",
             "description": "One-shot replay + current interval_s"},
            {"type": "sample",           "direction": "S→C",
             "description": "One MetricSample per tick"},
            {"type": "control",          "direction": "C→S",
             "description": "action: pause / resume / set_interval (value_s)"},
            {"type": "control_ack",      "direction": "S→C",
             "description": "Echoes applied state"},
        ],
    },
    {
        "path": "/audit/stream",
        "description": "Live audit event stream — snapshot then incremental, "
                       "with client-side pause/resume.",
        "messages": [
            {"type": "<client-first>",   "direction": "C→S",
             "description": "Optional {minutes: 30} to size the snapshot window"},
            {"type": "snapshot",         "direction": "S→C",
             "description": "{since, until, events: [...]} — newest first"},
            {"type": "event",            "direction": "S→C",
             "description": "{data: <event>} — one live event"},
            {"type": "control",          "direction": "C→S",
             "description": "{action: pause|resume}"},
            {"type": "control_ack",      "direction": "S→C",
             "description": "{action, paused}"},
        ],
    },
    {
        "path": "/terminal/ws",
        "description": "PTY-backed interactive shell with HITL deny-list.",
        "messages": [
            {"type": "<client-first>",   "direction": "C→S",
             "description": "{device, rows, cols, read_only, session_id}"},
            {"type": "ready",            "direction": "S→C",
             "description": "Session ready; carries session_id + read_only"},
            {"type": "<binary>",         "direction": "C↔S",
             "description": "Raw bytes — stdin / stdout"},
            {"type": "resize",           "direction": "C→S",
             "description": "{rows, cols}"},
            {"type": "input",            "direction": "C→S",
             "description": "{data} — alt-form text input"},
            {"type": "hitl_request",     "direction": "S→C",
             "description": "{command, rule, reason} — client must respond"},
            {"type": "hitl_response",    "direction": "C→S",
             "description": "{approve, allow_session}"},
            {"type": "set_read_only",    "direction": "C→S",
             "description": "{value} — toggle read-only mode"},
            {"type": "control_ack",      "direction": "S→C",
             "description": "Echoes applied control state"},
            {"type": "control",          "direction": "C→S",
             "description": "action: close"},
            {"type": "closed",           "direction": "S→C",
             "description": "Terminal; includes exit_code and error if any"},
        ],
    },
]


def schema_dict(alb_version: str) -> dict[str, Any]:
    """Assemble the full /api/version payload."""
    return {
        "version": API_VERSION,
        "alb_version": alb_version,
        "rest": REST_ENDPOINTS,
        "ws": WS_ENDPOINTS,
        "reference": "docs/web-api.md",
    }
