"""Config + profile loading.

Two layers:
    ~/.config/alb/config.toml          — global defaults
    <workspace>/profiles/<name>.toml   — profile-specific (multi-device / env)

Env overrides:
    ALB_CONFIG   — absolute path to alternative config.toml
    ALB_PROFILE  — profile name to activate (default: "default")
    ALB_WORKSPACE — workspace root (see infra.workspace)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alb.infra.workspace import workspace_root


# ─── Data model ────────────────────────────────────────────────────
@dataclass(frozen=True)
class AdbConfig:
    bin_path: str = "adb"
    server_socket: str | None = None  # tcp:localhost:5037 for Xshell tunnel


@dataclass(frozen=True)
class SshConfig:
    default_user: str = "root"
    default_port: int = 22
    key_path: str | None = None
    known_hosts: str | None = None
    connect_timeout: int = 15


@dataclass(frozen=True)
class SerialConfig:
    default_baud: int = 115200
    default_tcp_host: str = "localhost"
    default_tcp_port: int = 9001
    pty_link_dir: str = ""  # resolves to workspace/cache/pty if empty


@dataclass(frozen=True)
class PermissionConfig:
    mode: str = "standard"  # strict | standard | permissive
    ask_on_ambiguous: bool = True
    log_denied: bool = True
    allow: list[dict[str, str]] = field(default_factory=list)
    deny: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DeviceEntry:
    serial: str
    alias: str = ""
    transport: str = "adb"  # adb | ssh | serial
    ssh_host: str | None = None
    ssh_port: int | None = None
    serial_tcp_port: int | None = None


@dataclass(frozen=True)
class Profile:
    name: str
    primary_transport: str = "adb"  # which transport to default to
    devices: list[DeviceEntry] = field(default_factory=list)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)


@dataclass(frozen=True)
class Config:
    default_profile: str = "default"
    workspace_root: Path = field(default_factory=workspace_root)
    adb: AdbConfig = field(default_factory=AdbConfig)
    ssh: SshConfig = field(default_factory=SshConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)


# ─── Path resolution ───────────────────────────────────────────────
def global_config_path() -> Path:
    env = os.environ.get("ALB_CONFIG")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "alb" / "config.toml"


def profile_path(name: str) -> Path:
    return workspace_root() / "profiles" / f"{name}.toml"


# ─── Loaders ───────────────────────────────────────────────────────
def load_config() -> Config:
    """Load the global config. Returns defaults if file missing."""
    path = global_config_path()
    if not path.exists():
        return Config()

    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        # Fall back to defaults rather than crashing; caller can re-run `alb init`.
        return Config()

    return Config(
        default_profile=raw.get("default_profile", "default"),
        workspace_root=Path(raw.get("workspace", {}).get("root") or workspace_root()).expanduser(),
        adb=AdbConfig(**(raw.get("transport", {}).get("adb") or {})),
        ssh=SshConfig(**(raw.get("transport", {}).get("ssh") or {})),
        serial=SerialConfig(**(raw.get("transport", {}).get("serial") or {})),
        permissions=_parse_perm(raw.get("permissions") or {}),
    )


def load_profile(name: str | None = None) -> Profile:
    """Load a profile by name. If name is None, uses env ALB_PROFILE or config.default_profile."""
    if name is None:
        name = os.environ.get("ALB_PROFILE") or load_config().default_profile

    path = profile_path(name)
    if not path.exists():
        # Profile absent is OK — return an empty profile with just the name.
        return Profile(name=name)

    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return Profile(name=name)

    prof_section = raw.get("profile") or {}
    devices_raw = raw.get("devices") or []
    devices = [DeviceEntry(**d) for d in devices_raw if "serial" in d]

    return Profile(
        name=prof_section.get("name", name),
        primary_transport=prof_section.get("primary_transport", "adb"),
        devices=devices,
        permissions=_parse_perm(raw.get("permissions") or {}),
    )


def _parse_perm(raw: dict[str, Any]) -> PermissionConfig:
    return PermissionConfig(
        mode=raw.get("mode", "standard"),
        ask_on_ambiguous=bool(raw.get("ask_on_ambiguous", True)),
        log_denied=bool(raw.get("log_denied", True)),
        allow=list(raw.get("allow") or []),
        deny=list(raw.get("deny") or []),
    )


# ─── Compose active settings (config + profile) ────────────────────
@dataclass(frozen=True)
class ActiveSettings:
    config: Config
    profile: Profile

    @property
    def primary_transport(self) -> str:
        return self.profile.primary_transport

    @property
    def permissions(self) -> PermissionConfig:
        # profile overrides config
        if self.profile.permissions.mode != "standard" or self.profile.permissions.allow:
            return self.profile.permissions
        return self.config.permissions


def load_active(profile_name: str | None = None) -> ActiveSettings:
    cfg = load_config()
    prof = load_profile(profile_name)
    return ActiveSettings(config=cfg, profile=prof)
