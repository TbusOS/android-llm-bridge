"""ui capability — screenshot + uiautomator dump.

Diagnostic-only. `alb` takes pictures and dumps the view tree; it does NOT
tap, swipe, type, or drive UI. Those belong to a different tool
(e.g. mobile-mcp). The separation is intentional — alb is a doctor, not a
fingers-on-screen operator.

See docs/capabilities/ui.md.
"""

from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.infra.workspace import iso_timestamp, workspace_path
from alb.transport.base import Transport


# ─── Models ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScreenshotData:
    path: str
    device_path: str
    size_bytes: int
    width: int
    height: int
    thumbnail_base64: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "device_path": self.device_path,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "thumbnail_base64": self.thumbnail_base64,
        }


@dataclass(frozen=True)
class UINode:
    index: int
    class_name: str
    resource_id: str
    text: str
    content_desc: str
    bounds: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    clickable: bool
    enabled: bool
    focused: bool
    selected: bool
    package: str
    children: tuple["UINode", ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "class": self.class_name,
            "resource_id": self.resource_id,
            "text": self.text,
            "content_desc": self.content_desc,
            "bounds": list(self.bounds),
            "clickable": self.clickable,
            "enabled": self.enabled,
            "focused": self.focused,
            "selected": self.selected,
            "package": self.package,
            "children": [c.to_dict() for c in self.children],
        }

    def walk(self) -> list["UINode"]:
        """Return a flat pre-order list of this node and all descendants."""
        out = [self]
        for c in self.children:
            out.extend(c.walk())
        return out


@dataclass(frozen=True)
class UIDumpData:
    path: str
    device_path: str
    size_bytes: int
    root: UINode | None
    top_activity: str | None
    package_name: str | None
    node_count: int
    rotation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "device_path": self.device_path,
            "size_bytes": self.size_bytes,
            "root": self.root.to_dict() if self.root else None,
            "top_activity": self.top_activity,
            "package_name": self.package_name,
            "node_count": self.node_count,
            "rotation": self.rotation,
        }


# ─── Public capability: screenshot ─────────────────────────────────


async def screenshot(
    transport: Transport,
    *,
    device: str | None = None,
    output: str | Path | None = None,
    include_thumbnail: bool = False,
    thumbnail_max_dim: int = 256,
) -> Result[ScreenshotData]:
    """Capture a PNG screenshot of the device.

    Flow:
      1. `screencap -p <remote_path>` on the device
      2. `pull` to local
      3. Read PNG header for width/height
      4. (optional) Generate base64-encoded thumbnail (requires Pillow)

    Args:
        transport: any Transport that supports shell + pull (adb, ssh, ...).
        device: serial, for the workspace folder.
        output: explicit local destination. When None, lands in
                workspace/devices/<serial>/screenshots/<ts>.png.
        include_thumbnail: MCP strategy X — default False to save tokens.
        thumbnail_max_dim: longest edge of the thumbnail (default 256 px).
    """
    start = perf_counter()

    remote_path = f"/sdcard/alb-screenshot-{iso_timestamp()}.png"
    cap = await transport.shell(f"screencap -p {remote_path}", timeout=30)
    if not cap.ok:
        return fail(
            code=cap.error_code or "SCREENCAP_FAILED",
            message=cap.stderr.strip() or "screencap returned non-zero",
            suggestion="Ensure the device is awake and unlocked",
            category="capability",
            details={"stderr": cap.stderr},
            timing_ms=_elapsed_ms(start),
        )

    if output is not None:
        local_path = Path(output).expanduser().resolve()
        local_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        local_path = workspace_path(
            "screenshots", f"{iso_timestamp()}.png", device=device
        )

    pull = await transport.pull(remote_path, local_path)
    # best-effort cleanup; ignore failure (device may not let us delete)
    await transport.shell(f"rm -f {remote_path}", timeout=10)

    if not pull.ok:
        return fail(
            code=pull.error_code or "SCREENSHOT_PULL_FAILED",
            message="Failed to pull screenshot to local",
            suggestion="Check local disk space and device connectivity",
            category="transport",
            details={"stderr": pull.stderr, "remote_path": remote_path},
            timing_ms=_elapsed_ms(start),
        )

    try:
        png_bytes = local_path.read_bytes()
    except OSError as e:
        return fail(
            code="SCREENSHOT_READ_FAILED",
            message=f"Could not read pulled screenshot: {e}",
            category="io",
            timing_ms=_elapsed_ms(start),
        )

    try:
        width, height = _extract_png_dims(png_bytes)
    except ValueError as e:
        return fail(
            code="SCREENSHOT_NOT_PNG",
            message=f"Pulled file does not look like a PNG: {e}",
            suggestion="Older Android versions may return raw bytes; try a newer device or use a different capture path",
            category="capability",
            details={"path": str(local_path), "size_bytes": len(png_bytes)},
            timing_ms=_elapsed_ms(start),
        )

    thumb_b64: str | None = None
    if include_thumbnail:
        thumb_b64 = _generate_thumbnail_base64(png_bytes, thumbnail_max_dim)

    data = ScreenshotData(
        path=str(local_path),
        device_path=remote_path,
        size_bytes=len(png_bytes),
        width=width,
        height=height,
        thumbnail_base64=thumb_b64,
    )
    return ok(
        data=data,
        artifacts=[local_path],
        timing_ms=_elapsed_ms(start),
    )


# ─── Public capability: ui_dump ────────────────────────────────────


async def ui_dump(
    transport: Transport,
    *,
    device: str | None = None,
    output: str | Path | None = None,
) -> Result[UIDumpData]:
    """Dump the current view hierarchy as structured JSON.

    Flow:
      1. `uiautomator dump <remote_path>` on the device
      2. `pull` XML to local (kept as-is for debugging)
      3. Parse into UINode tree
      4. Also record top_activity via `dumpsys activity top` for context

    Returns a tree; use UINode.walk() for a flat listing.
    """
    start = perf_counter()

    remote_path = f"/sdcard/alb-ui-{iso_timestamp()}.xml"
    dump = await transport.shell(f"uiautomator dump {remote_path}", timeout=30)
    if not dump.ok:
        return fail(
            code=dump.error_code or "UIAUTOMATOR_FAILED",
            message=dump.stderr.strip() or "uiautomator dump returned non-zero",
            suggestion="uiautomator requires the device to be unlocked and the UI idle",
            category="capability",
            details={"stderr": dump.stderr},
            timing_ms=_elapsed_ms(start),
        )

    # uiautomator stdout is like "UI hierchary dumped to: /sdcard/xxx.xml"
    # (note the typo — it's in the upstream tool). Use whatever path it
    # actually wrote to, since some builds pick a different file.
    actual_remote = _parse_uiautomator_stdout(dump.stdout) or remote_path

    if output is not None:
        local_path = Path(output).expanduser().resolve()
        local_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        local_path = workspace_path(
            "ui-dumps", f"{iso_timestamp()}.xml", device=device
        )

    pull = await transport.pull(actual_remote, local_path)
    await transport.shell(f"rm -f {actual_remote}", timeout=10)

    if not pull.ok:
        return fail(
            code=pull.error_code or "UIDUMP_PULL_FAILED",
            message="Failed to pull uiautomator XML to local",
            suggestion="Check local disk and device connectivity",
            category="transport",
            details={"stderr": pull.stderr, "remote_path": actual_remote},
            timing_ms=_elapsed_ms(start),
        )

    try:
        xml_bytes = local_path.read_bytes()
    except OSError as e:
        return fail(
            code="UIDUMP_READ_FAILED",
            message=f"Could not read pulled XML: {e}",
            category="io",
            timing_ms=_elapsed_ms(start),
        )

    try:
        root, rotation = _parse_uiautomator_xml(xml_bytes)
    except ET.ParseError as e:
        return fail(
            code="UIDUMP_PARSE_FAILED",
            message=f"XML parse error: {e}",
            suggestion="The dump may have been truncated — retry",
            category="capability",
            details={"path": str(local_path)},
            timing_ms=_elapsed_ms(start),
        )

    top_act = None
    pkg = None
    act = await transport.shell(
        "dumpsys activity top | head -2",
        timeout=10,
    )
    if act.ok:
        top_act, pkg = _parse_top_activity(act.stdout)

    node_count = len(root.walk()) if root else 0
    data = UIDumpData(
        path=str(local_path),
        device_path=actual_remote,
        size_bytes=len(xml_bytes),
        root=root,
        top_activity=top_act,
        package_name=pkg,
        node_count=node_count,
        rotation=rotation,
    )
    return ok(
        data=data,
        artifacts=[local_path],
        timing_ms=_elapsed_ms(start),
    )


# ─── Parsing helpers ───────────────────────────────────────────────


def _extract_png_dims(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG byte stream.

    PNG layout: 8-byte signature + IHDR chunk. Width at [16:20], height [20:24]
    as big-endian unsigned 32-bit integers.
    """
    if len(data) < 24:
        raise ValueError(f"too short ({len(data)} bytes)")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("missing PNG signature")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _generate_thumbnail_base64(png_bytes: bytes, max_dim: int) -> str | None:
    """Make a small base64 PNG; require Pillow. Return None if unavailable."""
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return None
    import io

    with Image.open(io.BytesIO(png_bytes)) as im:
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _parse_uiautomator_stdout(stdout: str) -> str | None:
    """`uiautomator dump` echoes 'UI hierchary dumped to: <path>'."""
    for line in stdout.splitlines():
        if "dumped to:" in line:
            return line.split("dumped to:", 1)[1].strip()
    return None


def _parse_uiautomator_xml(xml_bytes: bytes) -> tuple[UINode | None, int]:
    """Parse the uiautomator dump.

    Returns (root_node, rotation). root_node is the top child of <hierarchy>
    (typically a single FrameLayout).
    """
    root_el = ET.fromstring(xml_bytes)
    rotation = int(root_el.attrib.get("rotation", "0"))
    children = [_xml_to_uinode(c) for c in root_el]
    if not children:
        return None, rotation
    if len(children) == 1:
        return children[0], rotation
    # Multiple top-level nodes — wrap in a synthetic container so callers
    # always see a single root.
    synth = UINode(
        index=0,
        class_name="alb.SyntheticHierarchyRoot",
        resource_id="",
        text="",
        content_desc="",
        bounds=(0, 0, 0, 0),
        clickable=False,
        enabled=True,
        focused=False,
        selected=False,
        package="",
        children=tuple(children),
    )
    return synth, rotation


def _xml_to_uinode(el: ET.Element) -> UINode:
    a = el.attrib
    return UINode(
        index=int(a.get("index", "0") or 0),
        class_name=a.get("class", ""),
        resource_id=a.get("resource-id", ""),
        text=a.get("text", ""),
        content_desc=a.get("content-desc", ""),
        bounds=_parse_bounds(a.get("bounds", "")),
        clickable=_bool(a.get("clickable")),
        enabled=_bool(a.get("enabled")),
        focused=_bool(a.get("focused")),
        selected=_bool(a.get("selected")),
        package=a.get("package", ""),
        children=tuple(_xml_to_uinode(c) for c in el),
    )


def _parse_bounds(s: str) -> tuple[int, int, int, int]:
    """`[x1,y1][x2,y2]` → (x1, y1, x2, y2)."""
    if not s:
        return (0, 0, 0, 0)
    try:
        cleaned = s.replace("][", ",").strip("[]")
        parts = [int(p) for p in cleaned.split(",")]
        if len(parts) != 4:
            return (0, 0, 0, 0)
        return (parts[0], parts[1], parts[2], parts[3])
    except ValueError:
        return (0, 0, 0, 0)


def _bool(s: str | None) -> bool:
    return (s or "").strip().lower() == "true"


def _parse_top_activity(stdout: str) -> tuple[str | None, str | None]:
    """Extract 'ACTIVITY <pkg>/<class>' from `dumpsys activity top` head."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("ACTIVITY "):
            rest = line.split(" ", 1)[1].strip()
            ident = rest.split(" ")[0]
            if "/" in ident:
                pkg, _, cls = ident.partition("/")
                full = f"{pkg}/{cls}" if cls else pkg
                return full, pkg
            return ident, None
    return None, None


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)
