#!/usr/bin/env python3
"""Pack the built dashboard into memai-webui-<version>.zip and its .sha256.

The release workflow attaches both files to the GitHub Release, so a machine
that cannot run npm installs the dashboard its version was tagged with:

    python tools/package-webui.py --out <dir>

The zip holds the contents of src/memai/webui/dist/ at its root, entries
sorted and dated 1980-01-01, so the same build always packs to the same
bytes. The .sha256 file is one `<hex>  <name>` line, the format
`sha256sum -c` reads. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "src" / "memai" / "webui" / "dist"
VERSION_FILE = ROOT / "src" / "memai" / "__init__.py"
REQUIRED = ("index.html", "THIRD-PARTY-NOTICES.txt", "build.json")
EPOCH = (1980, 1, 1, 0, 0, 0)


def version(path: Path = VERSION_FILE) -> str:
    """The package version from `__version__ = "x.y.z"`."""
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    if not found:
        raise SystemExit(f"no __version__ in {path}")
    return found.group(1)


def asset_name(ver: str) -> str:
    return f"memai-webui-{ver}.zip"


def pack(dist: Path, out: Path, ver: str) -> tuple[Path, Path]:
    """Write the zip and its .sha256 into `out`; returns both paths."""
    missing = [name for name in REQUIRED if not (dist / name).is_file()]
    if missing:
        raise SystemExit(f"{dist} is not a dashboard build: missing {', '.join(missing)}")
    out.mkdir(parents=True, exist_ok=True)
    archive = out / asset_name(ver)
    files = sorted(p for p in dist.rglob("*") if p.is_file())
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(dist).as_posix(), EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = out / f"{archive.name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii", newline="\n")
    return archive, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=DIST, help="the dashboard build")
    parser.add_argument("--out", type=Path, default=ROOT / "build", help="where to write")
    parser.add_argument("--version", default=None, help="defaults to memai.__version__")
    args = parser.parse_args(argv)
    archive, checksum = pack(args.dist, args.out, args.version or version())
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    sys.exit(main())
