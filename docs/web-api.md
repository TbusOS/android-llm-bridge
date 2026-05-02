# alb Web API reference

**Schema version: `1`.** The running server reports its schema through
`GET /api/version` — always check that before wiring a client to a
specific version. REST paths and WS message types may be added freely
within a schema version; removals or shape changes bump the major.

The Web UI (see `docs/webui-preview.html`) is the primary client of this
API; anything it needs must be documented here.

## Base URL

```
http://<host>:8765
```

Default ports:

- `8765` — `alb-api` (FastAPI) on Linux dev servers
- `7001` — Windows standalone build (future M2.5)

`ALB_API_HOST` and `ALB_API_PORT` env vars override the defaults.

## Discovery

### GET /health

Tiny liveness probe.

```json
{"ok": "true", "version": "0.5.2", "api": "alb"}
```

### GET /api/version

Full schema snapshot. The Web UI hits this on first load and
disables any tab whose backing endpoint is missing.

```json
{
  "version": "1",
  "alb_version": "0.5.2",
  "rest": [
    {"method": "GET", "path": "/health", "description": "..."},
    ...
  ],
  "ws": [
    {"path": "/chat/ws", "description": "...", "messages": [...]},
    ...
  ],
  "reference": "docs/web-api.md"
}
```

### GET /api/ping

Minimal health beacon — returns `{"ok": "true", "v": "1"}`.

## Chat (agent loop)

### POST /chat

Non-streaming agent chat with tool dispatch.

Request body:

```json
{
  "message": "pull last 5 minutes of logcat errors",
  "session_id": "sess-abc",
  "strict_session": false,
  "tools": true,
  "backend": "ollama",
  "model": "qwen2.5:7b"
}
```

Response:

```json
{
  "ok": true,
  "reply": "Found 3 distinct crashes...",
  "session_id": "sess-abc",
  "backend": "ollama",
  "model": "qwen2.5:7b",
  "error": null,
  "usage": {...},
  "timing_ms": 4230,
  "turns": 2,
  "tool_calls": [...],
  "artifacts": ["workspace/devices/.../logcat-errors.log"]
}
```

### WS /chat/ws

Streaming agent chat.

| Direction | Message | Notes |
|---|---|---|
| C → S | `ChatRequest` JSON (same shape as POST body) | First frame |
| S → C | `{"type":"token","delta":"..."}` | Partial assistant content |
| S → C | `{"type":"tool_call_start","name":"...","arguments":{...}}` | Tool about to run |
| S → C | `{"type":"tool_call_end","name":"...","result":{...}}` | Tool completed |
| S → C | `{"type":"done","content":"...","session_id":"...","model":"...","backend":"...","usage":{...}}` | Terminal; always present |

Client closes after `done`. Server closes on disconnect.

## Playground (raw LLM)

Bypasses the agent loop — no tool injection, no auto-retry. Exists so
the UI can A/B compare parameter combinations cleanly.

### GET /playground/backends

Registered LLM backends.

```json
{
  "backends": [
    {"name":"ollama","status":"beta","host_compute_type":"cpu",
     "supports_tool_calls":true,"requires":["ollama daemon"],
     "description":"..."},
    ...
  ]
}
```

`host_compute_type` (ADR-027, formal 2026-05-02) is one of:
- `"cpu"` — alb-host runs the inference locally on CPU (Ollama, embedded llama.cpp)
- `"gpu"` — alb-host requires a local GPU
- `"remote"` — alb-host only sends HTTP; model runs elsewhere (openai-compat, anthropic)

This field replaces the old `runs_on_cpu: bool` which lied for HTTP-only
backends. UI may render as a 3-state badge.

### GET /playground/backends/{backend}/models

Models installed on the given backend (Ollama `/api/tags`).

```json
{
  "backend": "ollama",
  "models": [
    {"name":"qwen2.5:7b","size":4700000000,"modified_at":"..."}
  ]
}
```

`{"models": []}` means the backend doesn't expose a catalog — the UI
falls back to free-text model entry.

### POST /playground/chat

Non-streaming raw LLM. Body is `PlaygroundChatRequest`:

```json
{
  "backend": "ollama",
  "model": "qwen2.5:7b",
  "base_url": "http://host:11434",
  "messages": [{"role":"user","content":"hi"}],
  "system": "You are concise.",
  "temperature": 0.5,
  "top_p": 0.9,
  "top_k": 40,
  "repeat_penalty": 1.1,
  "seed": -1,
  "stop": ["</s>"],
  "num_ctx": 8192,
  "num_predict": -1,
  "think": false
}
```

All sampling fields are optional. `seed=-1` / `num_predict=-1` are
sentinel values meaning "use default" and are not passed to the
backend. Values outside safe ranges (e.g. `temperature=99`) are
clamped server-side — no 400.

Response:

```json
{
  "ok": true,
  "content": "...",
  "thinking": "...",
  "finish_reason": "stop",
  "model": "qwen2.5:7b",
  "backend": "ollama",
  "metrics": {
    "input_tokens": 10, "output_tokens": 460, "total_tokens": 470,
    "eval_duration_ms": 3240, "prompt_eval_duration_ms": 410,
    "total_duration_ms": 4130, "load_duration_ms": 0,
    "tokens_per_second": 142.0
  },
  "error": null
}
```

Errors (backend unreachable, model missing, etc.) set `ok=false` and
populate `error: {code, message, suggestion}`.

### WS /playground/chat/ws

Streaming raw LLM.

| Direction | Message |
|---|---|
| C → S | `PlaygroundChatRequest` JSON (first frame) |
| S → C | `{"type":"token","delta":"..."}` |
| S → C | `{"type":"done","ok":true,"content":"...","thinking":"...","finish_reason":"stop","model":"...","backend":"...","metrics":{...},"error":null}` |

## Metrics (live telemetry)

### WS /metrics/stream

1 Hz sampling by default. Multiple clients subscribed to the same
device share ONE server-side sampling loop (see `capabilities/
metrics.py` — `get_streamer()` registry).

| Direction | Message | Notes |
|---|---|---|
| C → S | `{"device":"<serial>","history_seconds":60}` | First frame, optional |
| S → C | `{"v":"1","type":"history","interval_s":1.0,"samples":[...]}` | One-shot replay + current interval |
| S → C | `{"type":"sample","data":MetricSample}` | One per tick |
| C → S | `{"type":"control","action":"pause"}` | |
| C → S | `{"type":"control","action":"resume"}` | |
| C → S | `{"type":"control","action":"set_interval","value_s":0.5}` | Clamped [0.1, 60]s |
| S → C | `{"type":"control_ack","action":"...","interval_s":1.0,"paused":false}` | |

`MetricSample` fields: `ts_ms, cpu_pct_total, cpu_freq_khz[], cpu_temp_c,
mem_used_kb, mem_total_kb, mem_avail_kb, swap_used_kb, gpu_freq_hz,
gpu_util_pct, net_rx_bytes_per_s, net_tx_bytes_per_s,
disk_read_kb_per_s, disk_write_kb_per_s, battery_temp_c`.

CPU / network / disk per-second fields are **zero on the first sample**
(we need two samples to compute a delta).

## Terminal (interactive PTY)

### WS /terminal/ws

Spawns `adb shell` (or equivalent) attached to a fresh PTY. Server-side
HITL guard buffers each line and pattern-matches against a deny-list
before forwarding to the shell.

| Direction | Message | Notes |
|---|---|---|
| C → S | `{"device":"<serial>","transport":"adb","rows":24,"cols":80,"read_only":false,"session_id":"..."}` | First frame, optional |
| S → C | `{"v":"1","type":"ready","device":"...","transport":"adb","session_id":"...","read_only":false}` | Session started |
| C ↔ S | binary frames | Raw stdin / stdout bytes |
| C → S | `{"type":"resize","rows":30,"cols":120}` | PTY resize |
| C → S | `{"type":"input","data":"text\n"}` | Alt-form input (UTF-8 text) |
| S → C | `{"type":"hitl_request","command":"...","rule":"rm-rf-root","reason":"..."}` | Dangerous command held |
| C → S | `{"type":"hitl_response","approve":true,"allow_session":false}` | Decision |
| C → S | `{"type":"set_read_only","value":true}` | Toggle mid-session |
| S → C | `{"type":"control_ack","action":"set_read_only","read_only":true}` | |
| C → S | `{"type":"control","action":"close"}` | Graceful close |
| S → C | `{"type":"closed","exit_code":0}` | Shell ended |

**HITL deny-list** (server-side): rm targeting `/system /vendor /boot
/data /root /sdcard /product /odm /dev /sys /proc`, `dd`, `mkfs*`,
`reboot*`, persistent `setprop`, `setenforce`, `mount -o *rw`, partition
tools (`fdisk / parted / sgdisk / sfdisk`), `fastboot flash|erase`,
`avbctl disable-*`.

**Read-only mode allowlist** — when on, any command not matching one of
these leaf patterns is HITL'd: `ls / cat / head / tail / file / stat /
wc / grep / awk / sed / sort / uniq / cut / tr / ps / top / free /
df / du / uptime / uname / whoami / id / env / date / getprop /
dumpsys / service / pm / cmd / ip / netstat / ss / ifconfig / ping /
logcat / dmesg / echo / printf / true / false / exit / clear /
history / alias / which / type / help / cd / pwd`.

**Audit trail**: every input line, hitl request, approval, and denial
is appended to `workspace/sessions/<session_id>/terminal.jsonl`.

## Error codes (cross-endpoint)

| Code | Where | Meaning |
|---|---|---|
| `INVALID_REQUEST` | chat / playground | request body failed validation |
| `UNKNOWN_BACKEND` | playground | backend not in registry |
| `BACKEND_NOT_IMPLEMENTED` | playground | registered but no Python class yet |
| `BACKEND_UNREACHABLE` | playground | ollama daemon down, etc. |
| `BACKEND_HTTP_ERROR` | playground | backend returned 4xx/5xx |
| `BACKEND_TIMEOUT` | playground | request timed out |
| `TRANSPORT_NO_PTY` | terminal | transport doesn't support interactive shell |
| `PTY_SPAWN_FAILED` | terminal | fork / exec failed |
| `PLAYGROUND_INTERNAL` | playground WS | unexpected server error |

## Versioning policy

- Additive changes (new endpoints, new fields, new WS message types)
  keep the same `API_VERSION`.
- Removed endpoints / renamed / reshaped fields bump `API_VERSION` and
  the client falls back or errors out.
- Clients should tolerate unknown fields in responses — we may add
  metrics / artifacts without a version bump.
