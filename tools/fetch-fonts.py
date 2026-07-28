#!/usr/bin/env python3
"""Populate src/memai/webui/fonts/ with the Roboto faces the admin UI asks for.

Run once, by hand, if the dashboard is not rendering in Roboto and the
font is not installed on the machine:

    python tools/fetch-fonts.py

This is the ONLY part of MemAI that touches the network on purpose, which
is why it is a separate script rather than something the server does: the
admin UI must work offline, and it does -- admin.css falls back to an
installed Roboto and then to the system stack, so a machine that never
runs this script still gets a usable dashboard, just not the specified
typeface.

The files are deliberately untracked (see .gitignore). Roboto and Roboto
Mono are licensed Apache-2.0 by Google; redistributing them is allowed,
but a repository is not the place to keep binaries a script can fetch.

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
        print("Roboto & Roboto Mono © Google, Apache-2.0. Untracked by design.")
    return 0 if written == len(WANTED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
