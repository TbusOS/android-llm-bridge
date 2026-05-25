"""Cross-route regression for the "no echo of rejected user input"
contract (L-051 · sec MID-1 5/25).

`is_safe_device` / `is_safe_session_id` / `_resolve_workspace_path`
reject paths must NOT echo the rejected bytes back in the HTTPException
detail — those bytes can contain control chars / unicode RTL override /
HTML tags, and any future consumer rendering ``detail`` unsafely turns
this into reflected XSS / spoof. We want to catch a *new* route author
that inlines ``f"... {device!r}"`` style detail by re-running the same
sneaky payload against every audited endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(workspace):
    app = create_app()
    with TestClient(app) as c:
        yield c


# Two payload shapes:
#   QUERY = full <script>…</script> can ride query strings safely
#   PATH  = no slashes (would split path segments) but still ASCII <>
SNEAKY_QUERY = "<script>alert(1)</script>"
SNEAKY_PATH = "<scriptx>"

# 5/25 AM-2 (code MID-8): expand fuzz beyond just `<script>` so we
# catch any future regression that lets unicode RTL override / NUL /
# CRLF (HTTP header injection) ride into HTTPException detail.
EXTRA_SNEAKY_PAYLOADS = [
    "abc‮exe.png",          # unicode RTL override (filename spoof)
    "abc\x00.zip",                # NUL byte
    "abc\r\nSet-Cookie: x=1",     # CR/LF header injection
    "‮‭‬mix",      # bidi format-controls cluster
]


@pytest.mark.parametrize(
    "method, url, body, expected_detail",
    [
        # diag_route — mirror here so the matrix is one place future
        # route authors check (covered by test_diag_route too).
        (
            "post",
            f"/api/diag/bugreport?device={SNEAKY_QUERY}",
            None,
            "invalid device serial",
        ),
        (
            "get",
            f"/api/diag/artifacts?device={SNEAKY_QUERY}",
            None,
            "invalid device serial",
        ),
        # power_route — battery is GET so no body schema; reboot would
        # need body validation before device check.
        (
            "get",
            f"/api/power/battery?device={SNEAKY_QUERY}",
            None,
            "invalid device serial",
        ),
        # app_route — listing
        (
            "get",
            f"/api/app/list?device={SNEAKY_QUERY}",
            None,
            "invalid device serial",
        ),
        # log_search_route — prefix /api/log + endpoint /search
        (
            "get",
            f"/api/log/search?device={SNEAKY_QUERY}&pattern=err",
            None,
            "invalid device serial",
        ),
        # uart_route — captures listing
        (
            "get",
            f"/uart/captures?device={SNEAKY_QUERY}",
            None,
            "invalid device serial",
        ),
        # screenshots_route — listing (path param, no slashes allowed)
        (
            "get",
            f"/devices/{SNEAKY_PATH}/screenshots",
            None,
            "invalid device serial",
        ),
        # sessions_route — detail (path param, no slashes allowed)
        (
            "get",
            f"/sessions/{SNEAKY_PATH}",
            None,
            "invalid session id",
        ),
    ],
)
def test_reject_path_does_not_echo_user_input(
    client, method, url, body, expected_detail
) -> None:
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(url, **kwargs)
    assert r.status_code == 400, (
        f"{method} {url} expected 400, got {r.status_code} · body={r.text}"
    )
    body_json = r.json()
    assert body_json["detail"] == expected_detail, (
        f"{method} {url} detail should be exactly '{expected_detail}', "
        f"got: {body_json['detail']!r}"
    )
    # Critical: the sneaky payload must NOT appear anywhere in the body
    # (covers detail / nested fields / accidental echoes).
    assert "<script" not in r.text, (
        f"{method} {url} body leaked the sneaky payload: {r.text}"
    )


def test_files_path_traversal_reject_does_not_echo_rel(client, workspace) -> None:
    """files_route._resolve_workspace_path refuses paths that escape
    workspace — the rejected `rel` was user-controlled bytes; don't
    echo back. Hit via /workspace/files/download/{path:path}."""
    # `..%2Fetc%2Fpasswd` decodes to `../etc/passwd` inside the path
    # segment; resolve() takes it above workspace root → reject.
    r = client.get("/workspace/files/download/..%2F..%2Fetc%2Fpasswd<scriptx>")
    assert r.status_code == 400, f"expected 400, got {r.status_code} · {r.text}"
    body = r.json()
    assert body["detail"] == "path escapes workspace"
    assert "<script" not in r.text


@pytest.mark.parametrize("payload", EXTRA_SNEAKY_PAYLOADS)
def test_extra_sneaky_payloads_not_echoed(client, payload) -> None:
    """5/25 AM-2 (code MID-8): broaden coverage beyond the basic
    `<script>` payload — verify RTL / NUL / CRLF byte sequences also
    don't leak into the detail body. Picks a representative endpoint
    (diag bugreport) since all sibling endpoints share the same
    is_safe_device gate."""
    import urllib.parse

    encoded = urllib.parse.quote(payload, safe="")
    r = client.post(f"/api/diag/bugreport?device={encoded}")
    assert r.status_code == 400, (
        f"payload {payload!r} expected 400, got {r.status_code} · {r.text}"
    )
    assert r.json()["detail"] == "invalid device serial"
    assert payload not in r.text, (
        f"payload {payload!r} leaked into response body: {r.text}"
    )
