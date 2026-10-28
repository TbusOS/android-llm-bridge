#!/usr/bin/env python3
"""Build the subset web fonts used by the v3 UI (Material 3 skin).

Android renders Latin with Roboto and Chinese with Noto Sans CJK, so the UI
uses the same pair: Roboto Flex (text), Roboto Mono (logs, ids) and Noto Sans
SC. All three are SIL OFL fonts from the google/fonts repository; the full
files are 1.8 MB / 0.2 MB / 17.8 MB, so only what the UI can show is kept:

  * Noto Sans SC: every CJK character in the UI sources and mockups, plus the
    3755 level-1 GB2312 characters (the common set), so Chinese inside logs,
    file names and chat uses the same face instead of a mixed fallback.
  * Roboto Flex: Latin, with every axis but weight / width / optical size
    pinned to its default.
  * Roboto Mono: Latin.

The source fonts are not committed. To rebuild after UI text changes:

  curl -LO https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf
  curl -LO 'https://github.com/google/fonts/raw/main/ofl/robotoflex/RobotoFlex%5BGRAD,XOPQ,XTRA,YOPQ,YTAS,YTDE,YTFI,YTLC,YTUC,opsz,slnt,wdth,wght%5D.ttf'
  curl -LO https://github.com/google/fonts/raw/main/ofl/robotomono/RobotoMono%5Bwght%5D.ttf
  python3 -I web/scripts/build_v3_fonts.py --src <dir with the three .ttf>

Output: web/src/styles/v3/fonts/*.woff2 (then run web/scripts/sync_v3_assets.mjs).
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "web" / "src" / "styles" / "v3" / "fonts"
TEXT_SOURCES = [REPO / "web" / "src", REPO / "docs"]
TEXT_GLOBS = ("*.tsx", "*.ts", "*.css", "webui-preview-v3*.html")

LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
    "U+2000-206F,U+2074,U+20AC,U+2122,U+2190-2199,U+2212,U+2215,U+2248,"
    "U+2264-2265,U+2318,U+2325,U+2326,U+2303,U+25B2-25BC,U+2713-2717"
)
# CJK punctuation, full-width forms, middle dot, arrows used in the copy.
CJK_EXTRA = "U+3000-303F,U+FF00-FFEF,U+00B7,U+2014,U+2018-201D,U+2026,U+2190-2193,U+2192"
CJK_RE = re.compile(r"[㐀-䶿一-鿿　-〿＀-￯]")


def gb2312_level1() -> str:
    """The 3755 level-1 GB2312 characters (rows 16-55)."""
    chars = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.append(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return "".join(chars)


def ui_cjk_text() -> str:
    found: set[str] = set()
    for base in TEXT_SOURCES:
        for pattern in TEXT_GLOBS:
            for path in base.rglob(pattern):
                if "node_modules" in path.parts or "app" in path.relative_to(REPO).parts[:2]:
                    continue
                found.update(CJK_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return "".join(sorted(found))


def write_subset(font: TTFont, out: Path, *, text: str = "", unicodes: str = "") -> None:
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.hinting = False
    sub = subset.Subsetter(options)
    codepoints = subset.parse_unicodes(unicodes) if unicodes else []
    sub.populate(text=text, unicodes=codepoints)
    sub.subset(font)
    out.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(out)
    print(f"{out.relative_to(REPO)}  {out.stat().st_size:>9} bytes")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, required=True, help="directory holding the three source .ttf files")
    args = ap.parse_args()

    def find(prefix: str) -> Path:
        hits = sorted(args.src.glob(prefix + "*.ttf"))
        if not hits:
            sys.exit(f"no {prefix}*.ttf in {args.src}")
        return hits[0]

    ui = ui_cjk_text()
    common = gb2312_level1()
    print(f"CJK characters: {len(ui)} from the UI sources, {len(common)} level-1 GB2312")

    noto = TTFont(find("NotoSansSC"))
    write_subset(noto, OUT / "NotoSansSC-v3.woff2", text=ui + common, unicodes=CJK_EXTRA + "," + LATIN)

    flex = TTFont(find("RobotoFlex"))
    keep = {"wght", "wdth", "opsz"}
    pins = {a.axisTag: None if a.axisTag in keep else a.defaultValue for a in flex["fvar"].axes}
    pins = {k: v for k, v in pins.items() if v is not None}
    flex = instancer.instantiateVariableFont(flex, pins)
    # Subsetting the instancer's in-memory result trips over its lazily
    # loaded glyph variations; a save / reload round trip settles them.
    buf = io.BytesIO()
    flex.save(buf)
    buf.seek(0)
    write_subset(TTFont(buf), OUT / "RobotoFlex-v3.woff2", unicodes=LATIN)

    mono = TTFont(find("RobotoMono"))
    write_subset(mono, OUT / "RobotoMono-v3.woff2", unicodes=LATIN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
