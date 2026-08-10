"""Web API schema constants.

Central location for the protocol version + the canonical list of
REST endpoints and WebSocket message types. A JSON serialisation of
this file is what `GET /api/version` returns, so the shape here IS
the contract.

Bumping API_VERSION means a breaking change for clients that pin
against a specific version. Add / remove endpoints freely within the
same version — clients should feature-detect via the returned lists.

Path conventions (two coexisting generations, frozen by decision):
    - root-path endpoints (`/devices`, `/uart/...`, `/workspace/...`,
      `/sessions`, `/chat`, `/playground/...`) are the v1 legacy set.
      They stay where they are — moving them breaks shipped clients
      and would force an API_VERSION bump for zero functional gain.
    - every endpoint added since the 5/18 batch lives under
      `/api/<domain>/...` (`/api/app`, `/api/diag`, `/api/power`,
      `/api/log`, `/api/doctor`). New endpoints MUST follow this
      prefix form; do not start a third convention.
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
    {"method": "GET", "path": "/health", "description": "Server liveness + version"},
    {
        "method": "GET",
        "path": "/api/version",
        "description": "Protocol schema (this document) in JSON form",
    },
    {
        "method": "POST",
        "path": "/chat",
        "description": "Agent chat, non-streaming. See ChatRequest.",
    },
    {
        "method": "GET",
        "path": "/playground/backends",
        "description": "Registered LLM backends with capabilities",
    },
    {
        "method": "GET",
        "path": "/playground/backends/{backend}/models",
        "description": "Models installed on the given backend",
    },
    {
        "method": "GET",
        "path": "/playground/backends/{name}/health",
        "description": "Reachability probe for one backend (Dashboard LlmBackendCards)",
    },
    {
        "method": "POST",
        "path": "/playground/chat",
        "description": "Raw LLM chat (no agent loop), non-streaming",
    },
    {
        "method": "GET",
        "path": "/sessions",
        "description": "List recent ChatSession dirs (Dashboard feed)",
    },
    {
        "method": "GET",
        "path": "/sessions/{session_id}",
        "description": "Full session detail — meta + every message (SessionDetailPage replay)",
    },
    {
        "method": "GET",
        "path": "/devices",
        "description": "Devices visible through the active transport",
    },
    {
        "method": "GET",
        "path": "/audit",
        "description": "Recent audit events (chat + terminal jsonl) for the Timeline",
    },
    {
        "method": "GET",
        "path": "/metrics/summary",
        "description": "Windowed aggregation of tps_sample events (KPI throughput)",
    },
    {
        "method": "GET",
        "path": "/tools",
        "description": "List MCP tools registered by alb (KPI MCP tools count)",
    },
    {
        "method": "GET",
        "path": "/devices/{serial}/details",
        "description": "Composite device snapshot for the dashboard summary card",
    },
    {
        "method": "GET",
        "path": "/devices/{serial}/system",
        "description": "Full system snapshot — partitions / mounts / block devices / meminfo / storage / network / battery / thermal (PR-B)",
    },
    {
        "method": "POST",
        "path": "/devices/{serial}/screenshot",
        "description": "Capture PNG framebuffer + return base64 inline (PR-G)",
    },
    {
        "method": "GET",
        "path": "/devices/{serial}/screenshots",
        "description": "List past screenshots (newest first), with PNG dims",
    },
    {
        "method": "GET",
        "path": "/devices/{serial}/screenshots/{name}",
        "description": "Stream one screenshot's PNG bytes (image/png)",
    },
    {
        "method": "DELETE",
        "path": "/devices/{serial}/screenshots/{name}",
        "description": "Remove one captured screenshot (idempotent)",
    },
    {
        "method": "POST",
        "path": "/devices/{serial}/ui-dump",
        "description": "Dump current view hierarchy as JSON tree (PR-G)",
    },
    {
        "method": "POST",
        "path": "/uart/capture",
        "description": "Run a fresh UART capture for N seconds (PR-C.a)",
    },
    {"method": "GET", "path": "/uart/captures", "description": "List UART captures (newest first)"},
    {
        "method": "GET",
        "path": "/uart/captures/{name}",
        "description": "Read one UART capture's text content",
    },
    {
        "method": "DELETE",
        "path": "/uart/captures/{name}",
        "description": "Delete one UART capture file",
    },
    {
        "method": "GET",
        "path": "/devices/{serial}/files",
        "description": "List a device directory (ls -la parsed) (PR-H)",
    },
    {
        "method": "GET",
        "path": "/workspace/files",
        "description": "List a workspace directory under workspace_root (PR-H)",
    },
    {
        "method": "POST",
        "path": "/devices/{serial}/files/pull",
        "description": "Pull device→workspace via filesync (PR-H)",
    },
    {
        "method": "POST",
        "path": "/devices/{serial}/files/push",
        "description": "Push workspace→device; HITL gate on sensitive prefixes (PR-H)",
    },
    {
        "method": "GET",
        "path": "/workspace/files/download/{path}",
        "description": "Stream a workspace file as a download (PR-H)",
    },
    {
        "method": "GET",
        "path": "/workspace/files/preview/{path}",
        "description": "Inline UTF-8 text preview of a small workspace file (LOW-3)",
    },
    {
        "method": "POST",
        "path": "/workspace/files/upload",
        "description": "Upload a browser-local file into the workspace (drag-drop, 2 GiB cap)",
    },
    # ---- /api/<domain>/* generation (5/18 batch) -------------------
    {
        "method": "GET",
        "path": "/api/app/list",
        "description": "Installed packages (pm list), optional name filter / system apps",
    },
    {
        "method": "GET",
        "path": "/api/app/info",
        "description": "Single-package detail — version, install times, requested perms",
    },
    {
        "method": "POST",
        "path": "/api/app/start",
        "description": "Launch a package (am start for pkg/Activity, monkey for bare package)",
    },
    {
        "method": "POST",
        "path": "/api/app/stop",
        "description": "Force-stop a package (am force-stop)",
    },
    {
        "method": "POST",
        "path": "/api/app/clear-data",
        "description": "Wipe a package's user data (pm clear) — destructive, irreversible",
    },
    {
        "method": "POST",
        "path": "/api/app/uninstall",
        "description": "Uninstall a package; keep_data opt-in",
    },
    {
        "method": "POST",
        "path": "/api/app/install",
        "description": "Install an uploaded APK (multipart, 500 MB cap)",
    },
    {
        "method": "POST",
        "path": "/api/diag/bugreport",
        "description": "Capture a full bugreport into the workspace (long-running)",
    },
    {
        "method": "POST",
        "path": "/api/diag/anr",
        "description": "Pull /data/anr traces into the workspace",
    },
    {
        "method": "POST",
        "path": "/api/diag/tombstone",
        "description": "Pull /data/tombstones into the workspace",
    },
    {
        "method": "GET",
        "path": "/api/diag/artifacts",
        "description": "List previously captured diag artifacts (bugreport / anr / tombstone)",
    },
    {
        "method": "GET",
        "path": "/api/power/battery",
        "description": "Parsed dumpsys battery snapshot",
    },
    {
        "method": "POST",
        "path": "/api/power/reboot",
        "description": "Reboot the device; non-normal modes require allow_dangerous",
    },
    {
        "method": "POST",
        "path": "/api/power/sleep-wake",
        "description": "N sleep→wake cycles via KEYCODE_POWER / WAKEUP key events",
    },
    {
        "method": "GET",
        "path": "/api/log/search",
        "description": "Regex search over logcat (bounded window + match cap)",
    },
    {
        "method": "POST",
        "path": "/api/log/dmesg",
        "description": "Collect a fresh kernel dmesg snapshot into the workspace",
    },
    {
        "method": "GET",
        "path": "/api/info/{panel}",
        "description": "Per-panel device info (security/gpu/processes/cpu/…) — reuses the CLI/MCP info panels",
    },
    {
        "method": "GET",
        "path": "/api/doctor",
        "description": "Six-layer environment health probe (concurrent, ~worst single probe)",
    },
    {
        "method": "GET",
        "path": "/agent/status",
        "description": "Connected remote device agents + adb/serial forwarder state "
        "(web Connection Center)",
    },
    {
        "method": "POST",
        "path": "/api/agent/adb/restart",
        "description": "Ask the current agent to restart its local adb server and "
        "re-report devices (fire-and-forget; poll /agent/status). "
        "?kill_conflicts=true also clears adb-flavoured foreign "
        "processes holding the exclusive USB interface",
    },
    {
        "method": "GET",
        "path": "/api/flash/status",
        "description": "Whether a fastboot-capable agent is connected and whether a "
        "job is already running (answered from advertised caps, so "
        "it is instant rather than a timeout)",
    },
    # The three below stream application/x-ndjson: one JSON object per line,
    # progress lines first and the terminal {\"ev\":\"done\"} verdict last. A
    # client that reads only the last line still gets the full outcome.
    {
        "method": "POST",
        "path": "/api/flash/devices",
        "description": "fastboot devices on the agent host — the only way to see a "
        "board that dropped off adb by entering fastboot (NDJSON stream)",
    },
    {
        "method": "GET",
        "path": "/api/board-config/scan",
        "description": "Partitions whose head parses as KEY=\"VALUE\" — detection is by "
        "CONTENT, not by name, because the by-name label differs per product",
    },
    {
        "method": "GET",
        "path": "/api/board-config/read",
        "description": "Read and parse a config partition's head — the readback the "
        "flash path does not perform (Writing OKAY is not a verification)",
    },
    {
        "method": "POST",
        "path": "/api/flash/getvar",
        "description": "fastboot getvar <name> (empty = all); the device's answer is "
        "passed through untouched — the verb is protocol level, what the values mean "
        "is platform-specific (NDJSON stream)",
    },
    {
        "method": "POST",
        "path": "/api/flash/reboot",
        "description": "fastboot reboot [target]; empty target returns the board to "
        "the system — the way out of fastboot (NDJSON stream)",
    },
    {
        "method": "POST",
        "path": "/api/flash/flash",
        "description": "Write a workspace image to one partition; digest is verified "
        "on the agent before anything is written (NDJSON stream). Every job "
        "records workspace/devices/*/flash/<job>-<ts>/ with timeline.jsonl "
        "(job events + UART lines on one clock), uart.log and job.json; the "
        "terminal frame carries the path in `artifacts`",
    },
]

WS_ENDPOINTS: list[WSSpec] = [
    {
        "path": "/chat/ws",
        "description": "Streaming agent chat with tool dispatch.",
        "messages": [
            {"type": "<client-first>", "direction": "C→S", "description": "ChatRequest JSON body"},
            {
                "type": "token",
                "direction": "S→C",
                "description": "{delta} — partial assistant content",
            },
            {
                "type": "tool_call_start",
                "direction": "S→C",
                "description": "{name, arguments} — a tool is about to run",
            },
            {
                "type": "tool_call_end",
                "direction": "S→C",
                "description": "{name, result} — tool completed",
            },
            {
                "type": "done",
                "direction": "S→C",
                "description": "Terminal event with content / usage / session_id",
            },
        ],
    },
    {
        "path": "/playground/chat/ws",
        "description": "Raw LLM streaming chat — bypasses agent loop.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "PlaygroundChatRequest JSON",
            },
            {"type": "token", "direction": "S→C", "description": "{delta}"},
            {
                "type": "done",
                "direction": "S→C",
                "description": "Terminal — ok, content, thinking, metrics, error",
            },
        ],
    },
    {
        "path": "/metrics/stream",
        "description": "1 Hz device telemetry (CPU / mem / temp / IO / GPU / battery).",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "Optional {device, history_seconds}",
            },
            {
                "type": "history",
                "direction": "S→C",
                "description": "One-shot replay + current interval_s",
            },
            {"type": "sample", "direction": "S→C", "description": "One MetricSample per tick"},
            {
                "type": "control",
                "direction": "C→S",
                "description": "action: pause / resume / set_interval (value_s)",
            },
            {"type": "control_ack", "direction": "S→C", "description": "Echoes applied state"},
        ],
    },
    {
        "path": "/audit/stream",
        "description": "Live audit event stream — snapshot then incremental, "
        "with client-side pause/resume.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "Optional {minutes: 30} to size the snapshot window",
            },
            {
                "type": "snapshot",
                "direction": "S→C",
                "description": "{since, until, events: [...]} — newest first",
            },
            {
                "type": "event",
                "direction": "S→C",
                "description": "{data: <event>} — one live event",
            },
            {"type": "control", "direction": "C→S", "description": "{action: pause|resume}"},
            {"type": "control_ack", "direction": "S→C", "description": "{action, paused}"},
        ],
    },
    {
        "path": "/terminal/ws",
        "description": "PTY-backed interactive shell with HITL deny-list.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "{device, rows, cols, read_only, session_id}",
            },
            {
                "type": "ready",
                "direction": "S→C",
                "description": "Session ready; carries session_id + read_only",
            },
            {"type": "<binary>", "direction": "C↔S", "description": "Raw bytes — stdin / stdout"},
            {"type": "resize", "direction": "C→S", "description": "{rows, cols}"},
            {"type": "input", "direction": "C→S", "description": "{data} — alt-form text input"},
            {
                "type": "hitl_request",
                "direction": "S→C",
                "description": "{command, rule, reason} — client must respond",
            },
            {
                "type": "hitl_response",
                "direction": "C→S",
                "description": "{approve, allow_session}",
            },
            {
                "type": "set_read_only",
                "direction": "C→S",
                "description": "{value} — toggle read-only mode",
            },
            {
                "type": "control_ack",
                "direction": "S→C",
                "description": "Echoes applied control state",
            },
            {"type": "control", "direction": "C→S", "description": "action: close"},
            {
                "type": "closed",
                "direction": "S→C",
                "description": "Terminal; includes exit_code and error if any",
            },
        ],
    },
    {
        "path": "/uart/stream",
        "description": "Live UART byte stream (PR-C.b/c). Read-only by "
        "default; client-first {write:true} opens "
        "bidirectional mode so xterm.js can poke "
        "u-boot / sysrq / fastboot prompts.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "Optional {device, write:false|true}",
            },
            {
                "type": "ready",
                "direction": "S→C",
                "description": "{device, transport, write} — stream is open",
            },
            {
                "type": "<binary>",
                "direction": "S→C",
                "description": "Raw UART bytes (verbatim, ANSI preserved)",
            },
            {
                "type": "<binary>",
                "direction": "C→S",
                "description": "Raw bytes to write to UART (only when "
                "write=true was set in client-first frame)",
            },
            {
                "type": "control",
                "direction": "C→S",
                "description": "{type: 'close'} — client-initiated shutdown",
            },
            {
                "type": "write_dropped",
                "direction": "S→C",
                "description": "{reason, max_bytes, got_bytes} — bidirectional "
                "write frame > 64 KB cap, dropped silently",
            },
            {
                "type": "closed",
                "direction": "S→C",
                "description": "Terminal; carries reason / error",
            },
        ],
    },
    {
        "path": "/logcat/stream",
        "description": "Live adb logcat byte stream (PR-D). Sibling of "
        "/uart/stream — same protocol, default transport.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "Optional {device, filter, tags}",
            },
            {"type": "ready", "direction": "S→C", "description": "{device, transport, filter}"},
            {
                "type": "<binary>",
                "direction": "S→C",
                "description": "Raw logcat bytes (line-delimited)",
            },
            {"type": "control", "direction": "C→S", "description": "{type: 'close'}"},
            {
                "type": "closed",
                "direction": "S→C",
                "description": "Terminal; carries reason / error",
            },
        ],
    },
    {
        "path": "/devices/{serial}/files/push/stream",
        "description": "Streaming push to device (MID-6). Progress + cancel "
        "via {type:'cancel'}; HITL gate on sensitive prefixes.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "{local: workspace-rel, remote: device-path, force?}",
            },
            {
                "type": "ready",
                "direction": "S→C",
                "description": "{serial, direction, local, remote}",
            },
            {
                "type": "progress",
                "direction": "S→C",
                "description": "{percent, bytes_transferred, file}",
            },
            {
                "type": "cancel",
                "direction": "C→S",
                "description": "Abort the in-flight push; SIGTERMs adb",
            },
            {
                "type": "closed",
                "direction": "S→C",
                "description": "Terminal; reason in {done, cancelled, "
                "init_failed, bad_config, sensitive_path, "
                "unsupported_transport, error}",
            },
        ],
    },
    {
        "path": "/devices/{serial}/files/pull/stream",
        "description": "Streaming pull from device (MID-6). Same shape as "
        "push/stream; `local` defaults under workspace pulls/.",
        "messages": [
            {
                "type": "<client-first>",
                "direction": "C→S",
                "description": "{remote: device-path, local?: workspace-rel}",
            },
            {
                "type": "ready",
                "direction": "S→C",
                "description": "{serial, direction, local, remote}",
            },
            {
                "type": "progress",
                "direction": "S→C",
                "description": "{percent, bytes_transferred, file}",
            },
            {
                "type": "cancel",
                "direction": "C→S",
                "description": "Abort the in-flight pull; SIGTERMs adb",
            },
            {
                "type": "closed",
                "direction": "S→C",
                "description": "Terminal; reason same as push/stream",
            },
        ],
    },
    {
        "path": "/agent/connect",
        "description": "Remote device agent dial-home signaling channel "
        "(ADR-050/051) — control frames only; data rides "
        "per-channel /agent/channel connections.",
        "messages": [
            {
                "type": "hello",
                "direction": "C→S",
                "description": "{agent_id, name, agent_version, caps, token} — "
                "first frame; token checked before any registration",
            },
            {
                "type": "hello_ok",
                "direction": "S→C",
                "description": "{server_version} — handshake accepted",
            },
            {
                "type": "heartbeat",
                "direction": "C→S",
                "description": "Liveness ping; hub drops the agent on timeout",
            },
            {
                "type": "open_channel",
                "direction": "S→C",
                "description": "{cid, csecret, channel_type, role, params} — hub asks "
                "the agent to dial back a data channel; csecret "
                "authenticates that dial-back (DEBT-084)",
            },
            {
                "type": "adb_list",
                "direction": "C→S",
                "description": "{devices} — reply to a list_adb request",
            },
        ],
    },
    {
        "path": "/agent/channel",
        "description": "Remote device agent per-channel DATA connection "
        "(ADR-050) — raw bytes, correlated to a pending "
        "open_channel by the ?cid= query param and authenticated "
        "by the ?csecret= per-channel secret (DEBT-084).",
        "messages": [
            {
                "type": "<raw-bytes>",
                "direction": "C↔S",
                "description": "Bidirectional raw byte shuttle for one channel "
                "(e.g. adb 127.0.0.1:5037); no framing",
            },
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
