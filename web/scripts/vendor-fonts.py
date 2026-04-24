#!/usr/bin/env python3
"""Vendor the Latin subsets of Poppins / Lora / JetBrains Mono into
web/public/fonts/ so the running Web UI makes zero external requests.

Chinese text falls back to the user's installed system fonts (Noto Sans
CJK / PingFang SC / Microsoft YaHei), which keeps the bundle small. If a
deployment truly needs self-hosted Chinese fonts, add Noto Sans SC /
Noto Serif SC to FAMILIES below; it costs ~4 MB.

Usage:
    cd web
    python3 scripts/vendor-fonts.py

Run once per Google Fonts refresh. The resulting `public/fonts.css` is
committed and served as-is.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "public"
FONTS_DIR = PUBLIC / "fonts"
CSS_OUT = PUBLIC / "fonts.css"

FAMILIES = [
    "Poppins:wght@400;500;600;700",
    "Lora:ital,wght@0,400;0,500;0,600;1,400",
    "JetBrains+Mono:wght@400;500",
]

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CSS_HEADER = """/* Font declarations — vendored from Google Fonts so the Web UI runs
 * offline. Regenerate with: python3 scripts/vendor-fonts.py
 *
 * Chinese text falls back to installed system fonts (Noto Sans CJK /
 * PingFang SC / Microsoft YaHei) — deliberate to keep the bundle small.
 */

"""


def fetch_css() -> str:
    params = "&".join(f"family={f}" for f in FAMILIES)
    url = f"https://fonts.googleapis.com/css2?{params}&display=swap"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def download_woff2(url: str, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        dest.write_bytes(r.read())


def rewrite_to_local(css: str) -> str:
    """Pull woff2 from Google, stash locally, rewrite url() to /app/fonts/..."""
    urls = re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", css)
    unique = sorted(set(urls))
    print(f"Found {len(unique)} woff2 URLs", flush=True)

    url_to_name: dict[str, str] = {}
    for i, u in enumerate(unique):
        fname = Path(u).name
        # Some Google URLs have identical filenames across families — prefix
        # with a short hash to avoid collisions.
        if any(v == fname for v in url_to_name.values()):
            fname = f"{i:03d}-{fname}"
        url_to_name[u] = fname
        dest = FONTS_DIR / fname
        print(f"  ↓ {fname}", flush=True)
        download_woff2(u, dest)

    for u, fname in url_to_name.items():
        css = css.replace(u, f"/app/fonts/{fname}")
    return css


def main() -> int:
    print("Fetching Google Fonts CSS …", flush=True)
    css = fetch_css()
    print("Rewriting to local …", flush=True)
    css = rewrite_to_local(css)
    CSS_OUT.write_text(CSS_HEADER + css, encoding="utf-8")
    print(f"✓ wrote {CSS_OUT} ({len(css)} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
