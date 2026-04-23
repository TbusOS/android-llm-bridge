"""Tests for the ui capability (screenshot + uiautomator dump)."""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.ui import (
    ScreenshotData,
    UIDumpData,
    UINode,
    _bool,
    _extract_png_dims,
    _parse_bounds,
    _parse_top_activity,
    _parse_uiautomator_stdout,
    _parse_uiautomator_xml,
    screenshot,
    ui_dump,
)
from alb.transport.base import ShellResult


# ─── PNG header parsing ───────────────────────────────────────────


def test_png_dims_basic() -> None:
    # PNG signature + IHDR length + "IHDR" + width=1080 height=2400
    data = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0d"
        + b"IHDR"
        + struct.pack(">II", 1080, 2400)
        + b"\x08\x06\x00\x00\x00"
    )
    assert _extract_png_dims(data) == (1080, 2400)


def test_png_dims_not_a_png() -> None:
    with pytest.raises(ValueError, match="signature"):
        _extract_png_dims(b"JFIF" + b"\x00" * 100)


def test_png_dims_too_short() -> None:
    with pytest.raises(ValueError, match="too short"):
        _extract_png_dims(b"\x89PNG\r\n\x1a\n")


# ─── Bounds parser ────────────────────────────────────────────────


def test_parse_bounds_basic() -> None:
    assert _parse_bounds("[0,0][1080,2400]") == (0, 0, 1080, 2400)


def test_parse_bounds_negative() -> None:
    # uiautomator emits negatives when a view is off-screen
    assert _parse_bounds("[-20,100][1080,2400]") == (-20, 100, 1080, 2400)


def test_parse_bounds_empty() -> None:
    assert _parse_bounds("") == (0, 0, 0, 0)


def test_parse_bounds_garbage() -> None:
    assert _parse_bounds("not-bounds") == (0, 0, 0, 0)


# ─── _bool helper ─────────────────────────────────────────────────


def test_bool_true() -> None:
    assert _bool("true") is True
    assert _bool("TRUE") is True


def test_bool_false() -> None:
    assert _bool("false") is False
    assert _bool(None) is False
    assert _bool("") is False


# ─── uiautomator stdout parser ────────────────────────────────────


def test_stdout_parser_canonical() -> None:
    s = "UI hierchary dumped to: /sdcard/window_dump.xml\n"
    assert _parse_uiautomator_stdout(s) == "/sdcard/window_dump.xml"


def test_stdout_parser_missing() -> None:
    assert _parse_uiautomator_stdout("error: device idle timeout\n") is None


# ─── top activity parser ──────────────────────────────────────────


def test_top_activity_basic() -> None:
    stdout = (
        "TASK 123 id=456\n"
        "  ACTIVITY com.android.settings/.homepage.SettingsHomepageActivity 12ab pid=789\n"
    )
    act, pkg = _parse_top_activity(stdout)
    assert act == "com.android.settings/.homepage.SettingsHomepageActivity"
    assert pkg == "com.android.settings"


def test_top_activity_no_activity() -> None:
    assert _parse_top_activity("TASK 123\n") == (None, None)


def test_top_activity_bare_package() -> None:
    # Defensive: some dumpsys outputs don't include '/'
    stdout = "  ACTIVITY com.android.launcher 0 pid=1\n"
    act, pkg = _parse_top_activity(stdout)
    assert act == "com.android.launcher"
    assert pkg is None


# ─── XML hierarchy parser ─────────────────────────────────────────


SAMPLE_XML = b"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="Hello" resource-id="com.example:id/title" class="android.widget.TextView" package="com.example" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[20,100][1060,180]" />
    <node index="1" text="Press me" resource-id="com.example:id/btn" class="android.widget.Button" package="com.example" content-desc="primary action" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="true" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[20,200][1060,280]" />
  </node>
</hierarchy>
"""


def test_xml_parser_tree_shape() -> None:
    root, rotation = _parse_uiautomator_xml(SAMPLE_XML)
    assert rotation == 0
    assert root is not None
    assert root.class_name == "android.widget.FrameLayout"
    assert len(root.children) == 2
    assert root.children[0].text == "Hello"
    assert root.children[1].text == "Press me"


def test_xml_parser_attributes() -> None:
    root, _ = _parse_uiautomator_xml(SAMPLE_XML)
    assert root is not None
    btn = root.children[1]
    assert btn.resource_id == "com.example:id/btn"
    assert btn.content_desc == "primary action"
    assert btn.clickable is True
    assert btn.focused is True
    assert btn.enabled is True
    assert btn.bounds == (20, 200, 1060, 280)
    assert btn.package == "com.example"


def test_xml_parser_walk_order() -> None:
    root, _ = _parse_uiautomator_xml(SAMPLE_XML)
    assert root is not None
    flat = root.walk()
    # pre-order: root, child0, child1
    assert [n.class_name for n in flat] == [
        "android.widget.FrameLayout",
        "android.widget.TextView",
        "android.widget.Button",
    ]


def test_xml_parser_empty_hierarchy() -> None:
    root, rotation = _parse_uiautomator_xml(b"<hierarchy rotation='90' />")
    assert rotation == 90
    assert root is None


def test_xml_parser_multiple_top_nodes_synthesised() -> None:
    xml = b"""<hierarchy rotation="0">
      <node index="0" class="A" bounds="[0,0][10,10]" />
      <node index="1" class="B" bounds="[0,0][10,10]" />
    </hierarchy>"""
    root, _ = _parse_uiautomator_xml(xml)
    assert root is not None
    assert root.class_name == "alb.SyntheticHierarchyRoot"
    assert len(root.children) == 2


# ─── Data class serialisation ─────────────────────────────────────


def test_uinode_to_dict_round_trip() -> None:
    n = UINode(
        index=0,
        class_name="Foo",
        resource_id="x:id/y",
        text="t",
        content_desc="",
        bounds=(1, 2, 3, 4),
        clickable=True,
        enabled=True,
        focused=False,
        selected=False,
        package="pkg",
    )
    d = n.to_dict()
    assert d["class"] == "Foo"
    assert d["bounds"] == [1, 2, 3, 4]
    assert d["clickable"] is True
    assert d["children"] == []


def test_screenshot_data_to_dict() -> None:
    s = ScreenshotData(
        path="/x.png", device_path="/sdcard/x.png",
        size_bytes=100, width=10, height=20, thumbnail_base64=None,
    )
    assert s.to_dict() == {
        "path": "/x.png",
        "device_path": "/sdcard/x.png",
        "size_bytes": 100,
        "width": 10,
        "height": 20,
        "thumbnail_base64": None,
    }


def test_ui_dump_data_to_dict_no_root() -> None:
    d = UIDumpData(
        path="/x.xml", device_path="/sdcard/x.xml",
        size_bytes=50, root=None, top_activity=None,
        package_name=None, node_count=0, rotation=90,
    )
    assert d.to_dict()["root"] is None
    assert d.to_dict()["rotation"] == 90


# ─── Fake transport for integration-level tests ───────────────────


def _mk_transport(
    shell_responses: dict[str, ShellResult],
    pull_payload: bytes = b"",
    pull_ok: bool = True,
) -> AsyncMock:
    t = AsyncMock()
    t.name = "adb"

    async def shell(cmd: str, timeout: int = 30) -> ShellResult:
        for prefix, result in shell_responses.items():
            if cmd.startswith(prefix):
                return result
        return ShellResult(
            ok=False, exit_code=1, stderr=f"unhandled: {cmd}",
            duration_ms=0, error_code="ADB_COMMAND_FAILED",
        )

    async def pull(remote: str, local: Path) -> ShellResult:
        if not pull_ok:
            return ShellResult(
                ok=False, stderr="pull failed", error_code="ADB_COMMAND_FAILED",
            )
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(pull_payload)
        return ShellResult(ok=True, exit_code=0, duration_ms=5)

    t.shell = shell
    t.pull = pull
    return t


def _png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0d"
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 100  # filler
    )


@pytest.mark.asyncio
async def test_screenshot_happy_path(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "screencap": ShellResult(ok=True, exit_code=0, duration_ms=10),
            "rm -f": ShellResult(ok=True, exit_code=0, duration_ms=1),
        },
        pull_payload=_png_bytes(1080, 2400),
    )
    out = tmp_path / "shot.png"
    r = await screenshot(t, output=out)
    assert r.ok, r.error
    assert r.data is not None
    assert r.data.width == 1080
    assert r.data.height == 2400
    assert r.data.thumbnail_base64 is None  # default strategy X
    assert Path(r.data.path).exists()


@pytest.mark.asyncio
async def test_screenshot_screencap_fails(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "screencap": ShellResult(
                ok=False, exit_code=1, stderr="can't lock display",
                error_code="ADB_COMMAND_FAILED",
            ),
        },
    )
    r = await screenshot(t, output=tmp_path / "x.png")
    assert not r.ok
    assert r.error is not None
    assert "can't lock display" in r.error.message


@pytest.mark.asyncio
async def test_screenshot_pull_fails(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "screencap": ShellResult(ok=True, exit_code=0),
            "rm -f": ShellResult(ok=True, exit_code=0),
        },
        pull_ok=False,
    )
    r = await screenshot(t, output=tmp_path / "x.png")
    assert not r.ok
    assert r.error is not None
    assert r.error.code in {"ADB_COMMAND_FAILED", "SCREENSHOT_PULL_FAILED"}


@pytest.mark.asyncio
async def test_screenshot_not_a_png(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "screencap": ShellResult(ok=True, exit_code=0),
            "rm -f": ShellResult(ok=True, exit_code=0),
        },
        pull_payload=b"JFIFnot-a-png" + b"\x00" * 100,
    )
    r = await screenshot(t, output=tmp_path / "x.png")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "SCREENSHOT_NOT_PNG"


@pytest.mark.asyncio
async def test_ui_dump_happy_path(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "uiautomator dump": ShellResult(
                ok=True, exit_code=0,
                stdout="UI hierchary dumped to: /sdcard/alb-ui.xml\n",
            ),
            "rm -f": ShellResult(ok=True, exit_code=0),
            "dumpsys activity top": ShellResult(
                ok=True, exit_code=0,
                stdout="  ACTIVITY com.example/.MainActivity 1 pid=2\n",
            ),
        },
        pull_payload=SAMPLE_XML,
    )
    out = tmp_path / "ui.xml"
    r = await ui_dump(t, output=out)
    assert r.ok, r.error
    assert r.data is not None
    assert r.data.node_count == 3  # root + 2 children
    assert r.data.top_activity == "com.example/.MainActivity"
    assert r.data.package_name == "com.example"
    assert r.data.root is not None
    assert r.data.root.class_name == "android.widget.FrameLayout"


@pytest.mark.asyncio
async def test_ui_dump_parse_fail(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "uiautomator dump": ShellResult(
                ok=True, exit_code=0,
                stdout="UI hierchary dumped to: /sdcard/ui.xml\n",
            ),
            "rm -f": ShellResult(ok=True, exit_code=0),
            "dumpsys activity top": ShellResult(ok=True, exit_code=0, stdout=""),
        },
        pull_payload=b"<hierarchy><node class='x' bounds='[0,0][1,1]'",  # truncated
    )
    r = await ui_dump(t, output=tmp_path / "ui.xml")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "UIDUMP_PARSE_FAILED"


@pytest.mark.asyncio
async def test_ui_dump_uiautomator_fails(tmp_path: Path) -> None:
    t = _mk_transport(
        {
            "uiautomator dump": ShellResult(
                ok=False, exit_code=1, stderr="ERROR: null root node",
                error_code="ADB_COMMAND_FAILED",
            ),
        },
    )
    r = await ui_dump(t, output=tmp_path / "ui.xml")
    assert not r.ok
    assert r.error is not None
    assert "null root" in r.error.message
