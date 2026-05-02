"""Metadata-driven registry for transports and capabilities.

Inspired by claude-code2 — single source of truth for:
- Support matrix (shown in docs, CLI `alb describe`, MCP describe)
- Runtime feature flags (status=planned tools can be hidden)
- Auto-generated documentation

Implementation will be fleshed out in M1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["stable", "beta", "planned"]


@dataclass(frozen=True)
class TransportSpec:
    name: str
    impl_path: str
    methods_supported: list[str]       # ["A", "B"] etc. (从 docs/methods/)
    status: Status
    requires: list[str] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    impl_path: str
    cli_command: str
    mcp_tools: list[str]
    supported_transports: list[str]    # ["adb", "ssh", "serial"]
    status: Status
    description: str = ""


@dataclass(frozen=True)
class BackendSpec:
    """LLM backend for the agent layer (`alb.agent`).

    Same shape as TransportSpec — lets `alb describe` enumerate what LLMs
    the chat UI can talk to.  See docs/agent.md for the design rationale.
    """

    name: str
    impl_path: str
    # ADR-027 (formal 2026-05-02): host-side compute requirement, not
    # "where the model runs". Three states:
    #   "cpu"    — alb-host does the inference locally on CPU (Ollama
    #              CPU build, llama-cpp on CPU, embedded GGUF)
    #   "gpu"    — alb-host requires a local GPU (llama-cpp on CUDA,
    #              future MLX/Metal-only backends)
    #   "remote" — alb-host only sends HTTP; inference happens elsewhere
    #              (OpenAI-compat / Anthropic / future Bedrock)
    # Replaces the old `runs_on_cpu: bool` whose name lied for remote
    # backends (was True for openai-compat even though the model ran on
    # the upstream server). Frontend renders as a 3-state badge.
    host_compute_type: str
    supports_tool_calls: bool
    status: Status
    requires: list[str] = field(default_factory=list)
    description: str = ""


# ─── Transport registry ──────────────────────────────────────────────
TRANSPORTS: list[TransportSpec] = [
    TransportSpec(
        name="adb",
        impl_path="alb.transport.adb.AdbTransport",
        methods_supported=["A", "B"],
        status="beta",
        requires=["adb binary"],
        description="adb USB / WiFi. Recommended for flashing, recovery, bootloader.",
    ),
    TransportSpec(
        name="ssh",
        impl_path="alb.transport.ssh.SshTransport",
        methods_supported=["C", "D", "F"],
        status="beta",
        requires=["asyncssh", "rsync (for rsync_sync)"],
        description="On-device sshd. Rsync / tmux / sshfs / multi-user / port forwarding.",
    ),
    TransportSpec(
        name="serial",
        impl_path="alb.transport.serial.SerialTransport",
        methods_supported=["G"],
        status="beta",
        requires=["pyserial-asyncio (for local /dev/tty*)", "ser2net (Windows side, for TCP mode)"],
        description="UART serial. Only method that can see boot log, u-boot, kernel panic.",
    ),
    TransportSpec(
        name="hybrid",
        impl_path="alb.transport.hybrid.HybridTransport",
        methods_supported=[],  # routes among others
        status="beta",
        requires=["at least one concrete sub-transport (adb/ssh/serial)"],
        description="Smart router: picks best sub-transport per op "
                    "(logcat→adb, rsync→ssh, uart/bootloader→serial/adb).",
    ),
]


# ─── Capability registry ─────────────────────────────────────────────
CAPABILITIES: list[CapabilitySpec] = [
    CapabilitySpec(
        name="shell",
        impl_path="alb.capabilities.shell",
        cli_command="alb shell",
        mcp_tools=["alb_shell"],
        supported_transports=["adb", "ssh", "serial"],
        status="beta",
        description="Execute shell command with structured output.",
    ),
    CapabilitySpec(
        name="logging",
        impl_path="alb.capabilities.logging",
        cli_command="alb logcat / dmesg / uart-capture / log-search / log-tail",
        mcp_tools=[
            "alb_logcat",
            "alb_dmesg",
            "alb_uart_capture",
            "alb_log_search",
            "alb_log_tail",
        ],
        supported_transports=["adb", "ssh", "serial"],
        status="beta",
        description="Log collection, search, and paginated reading.",
    ),
    CapabilitySpec(
        name="filesync",
        impl_path="alb.capabilities.filesync",
        cli_command="alb push / pull / rsync",
        mcp_tools=["alb_push", "alb_pull", "alb_rsync"],
        supported_transports=["adb", "ssh"],
        status="beta",
        description="File transfer with auto-routing (rsync / scp / adb push).",
    ),
    CapabilitySpec(
        name="diagnose",
        impl_path="alb.capabilities.diagnose",
        cli_command="alb bugreport / anr pull / tombstone",
        mcp_tools=[
            "alb_bugreport",
            "alb_anr_pull",
            "alb_tombstone",
            "alb_devinfo",
        ],
        supported_transports=["adb", "ssh"],
        status="beta",
        description="Standard Android diagnostics (bugreport/ANR/tombstone).",
    ),
    CapabilitySpec(
        name="power",
        impl_path="alb.capabilities.power",
        cli_command="alb reboot / sleep-wake / battery",
        mcp_tools=[
            "alb_reboot",
            "alb_sleep_wake_test",
            "alb_battery",
            "alb_wait_boot",
        ],
        supported_transports=["adb", "ssh", "serial"],
        status="beta",
        description="Power state: reboot / sleep-wake / battery.",
    ),
    CapabilitySpec(
        name="app",
        impl_path="alb.capabilities.app",
        cli_command="alb app install/uninstall/start/stop/list",
        mcp_tools=[
            "alb_app_install",
            "alb_app_uninstall",
            "alb_app_start",
            "alb_app_stop",
            "alb_app_list",
            "alb_app_info",
            "alb_app_clear_data",
        ],
        supported_transports=["adb", "ssh"],
        status="beta",
        description="APK management.",
    ),
    # M2+ 规划
    CapabilitySpec(
        name="perf",
        impl_path="alb.capabilities.perf",
        cli_command="alb perf",
        mcp_tools=["alb_perf_snapshot", "alb_perf_watch"],
        supported_transports=["adb", "ssh"],
        status="planned",
        description="CPU/MEM/FPS/temperature/current continuous sampling.",
    ),
    CapabilitySpec(
        name="benchmark",
        impl_path="alb.capabilities.benchmark",
        cli_command="alb bench",
        mcp_tools=["alb_bench_run", "alb_bench_report"],
        supported_transports=["adb"],
        status="planned",
        description="Benchmark integration (AnTuTu, GeekBench, custom).",
    ),
]


def transports_by_status(status: Status) -> list[TransportSpec]:
    return [t for t in TRANSPORTS if t.status == status]


def capabilities_by_status(status: Status) -> list[CapabilitySpec]:
    return [c for c in CAPABILITIES if c.status == status]


# ─── Agent backend registry ──────────────────────────────────────────
# All planned; concrete implementations land in M2 (ollama / openai-compat)
# and M3 (llama.cpp / anthropic).  See docs/agent.md.
BACKENDS: list[BackendSpec] = [
    BackendSpec(
        name="ollama",
        impl_path="alb.agent.backends.ollama.OllamaBackend",
        host_compute_type="cpu",  # alb-host runs Ollama daemon locally
        supports_tool_calls=True,
        status="beta",
        requires=["ollama daemon (HTTP)", "a pulled model (e.g. qwen2.5:3b)"],
        description="Local Ollama HTTP API.  Recommended for CPU-only servers.",
    ),
    BackendSpec(
        name="openai-compat",
        impl_path="alb.agent.backends.openai_compat.OpenAICompatBackend",
        # alb-host only sends HTTP; the model runs on whatever the
        # configured base_url points to (vLLM cluster, LM Studio on
        # the dev's laptop, llamafile, etc.).
        host_compute_type="remote",
        supports_tool_calls=True,
        # Implementation is shipped (M3 step 1, 2026-04-30), but
        # status stays 'planned' on purpose: the default base_url
        # http://localhost:8080/v1 is unreachable on most dev
        # machines, and a registered+down card permanently red would
        # train users to ignore the dashboard's failure signal. Flip
        # to 'beta' once the dashboard learns a "byo-server"
        # presentation (ADR seed: status='beta-byo' + reason
        # 'not_configured') OR once we ship a default cloud target.
        status="planned",
        requires=["OpenAI-compatible endpoint (vLLM / llamafile / LM Studio)"],
        description="Any OpenAI-compatible chat/completions endpoint.",
    ),
    BackendSpec(
        name="llama-cpp",
        impl_path="alb.agent.backends.llama_cpp.LlamaCppBackend",
        # Default 'cpu' for the embedded build; a future GPU-compiled
        # variant would register a separate "llama-cpp-cuda" entry
        # with host_compute_type="gpu".
        host_compute_type="cpu",
        supports_tool_calls=True,
        status="planned",
        requires=["llama-cpp-python", "a local GGUF model file"],
        description="Embedded llama.cpp — no daemon, pure in-process.",
    ),
    BackendSpec(
        name="anthropic",
        impl_path="alb.agent.backends.anthropic.AnthropicBackend",
        # Cloud SaaS — alb-host is just an HTTP client.
        host_compute_type="remote",
        supports_tool_calls=True,
        # M3 step 2 ship 2026-05-02. Backend is implemented and tested
        # (see tests/agent/test_anthropic.py — 34 cases). Users still
        # need to set ANTHROPIC_API_KEY; the dashboard reflects "no
        # key configured" as a clear unreachable state, not silent red.
        status="beta",
        requires=["ANTHROPIC_API_KEY"],
        description="Claude API — Anthropic-hosted Claude models.",
    ),
]


def backends_by_status(status: Status) -> list[BackendSpec]:
    return [b for b in BACKENDS if b.status == status]
