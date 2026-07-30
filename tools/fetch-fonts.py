#!/usr/bin/env python3
"""Refresh src/memai/webui/fonts/ with the Roboto faces the admin UI asks for.

The faces ARE tracked -- a clone has them, and so does the wheel. This
script is how they got there and how they get updated; it is not a step
anybody has to run to use MemAI. Run it to pull a newer release, then
regenerate the width table the SVG renderer reads:

    python tools/fetch-fonts.py
    python tools/gen-roboto-metrics.py

That second step is not optional. src/memai/roboto_metrics.json is
extracted from these exact files, and diagram_svg.measure() wraps text
against it -- refreshing one without the other makes the SVG renderer
break lines where the canvas does not.

This is the ONLY part of MemAI that touches the network on purpose, which
is why it is a separate script rather than something the server does.

Roboto and Roboto Mono are Font Software under the SIL Open Font License
1.1 -- see webui/fonts/OFL.txt, which ships beside them because the
licence requires the notice to travel with every copy. The OFL permits
bundling outright; what it forbids is selling the fonts by themselves.

Only the latin subset of each face is downloaded -- the admin UI is
English, and the translated catalogs under webui/i18n/ have so far needed
nothing outside it. Add the subset you need to SUBSET_FIRST_RANGE if that
changes.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Roboto:wght@400;500;700"
    "&family=Roboto+Mono:wght@400;500;600"
    "&display=swap"
)

# Google serves woff2 only to browsers it recognises; anything older gets
# ttf, and an unknown agent gets the legacy formats.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# The latin block always starts here, which is how the right @font-face is
# picked out of the half-dozen subsets Google returns per weight.
SUBSET_FIRST_RANGE = "U+0000-00FF"

# (css font-family, css font-weight) -> filename admin.css asks for
WANTED = {
    ("Roboto", "400"): "roboto-400.woff2",
    ("Roboto", "500"): "roboto-500.woff2",
    ("Roboto", "700"): "roboto-700.woff2",
    ("Roboto Mono", "400"): "roboto-mono-400.woff2",
    ("Roboto Mono", "500"): "roboto-mono-500.woff2",
    ("Roboto Mono", "600"): "roboto-mono-600.woff2",
}

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "memai" / "webui" / "fonts"

BLOCK = re.compile(r"@font-face\s*\{(.*?)\}", re.S)


def _field(block: str, name: str) -> str:
    m = re.search(rf"{name}\s*:\s*([^;]+);", block)
    return m.group(1).strip().strip("'\"") if m else ""


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 - fixed host
        return res.read()


def main() -> int:
    print(f"fetching {CSS_URL}")
    try:
        css = _fetch(CSS_URL).decode("utf-8")
    except urllib.error.URLError as exc:
        print(f"  failed: {exc}\n  the dashboard still works -- it falls back to the "
              f"system font stack.", file=sys.stderr)
        return 1

    found: dict[tuple[str, str], str] = {}
    for block in BLOCK.findall(css):
        if not _field(block, "unicode-range").startswith(SUBSET_FIRST_RANGE):
            continue
        key = (_field(block, "font-family"), _field(block, "font-weight"))
        if key not in WANTED or key in found:
            continue
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if url:
            found[key] = url.group(1)

    missing = set(WANTED) - set(found)
    if missing:
        print(f"  could not find in the stylesheet: {sorted(missing)}", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for key, url in found.items():
        target = OUT_DIR / WANTED[key]
        try:
            data = _fetch(url)
        except urllib.error.URLError as exc:
            print(f"  {target.name}: failed ({exc})", file=sys.stderr)
            continue
        target.write_bytes(data)
        print(f"  {target.name}  {len(data) / 1024:.1f} KB")
        written += 1

    print(f"\n{written}/{len(WANTED)} faces in {OUT_DIR}")
    if written:
        print("Roboto & Roboto Mono, SIL Open Font License 1.1 -- see OFL.txt "
              "beside them.")
        print("Now run tools/gen-roboto-metrics.py: the width table the SVG "
              "renderer wraps against is extracted from these files.")
    return 0 if written == len(WANTED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
