#!/usr/bin/env python3
"""Put a working admin dashboard into src/memai/webui/dist/.

install.bat and the non-Windows update command run this after the Python
install. It tries, in order:

1. `npm ci` and `npm run build`, when Node and npm are usable;
2. the prebuilt memai-webui-<version>.zip attached to this version's GitHub
   Release, checked against its .sha256;
3. the build already in dist/, left as it is.

`--zip <path>` installs a zip fetched by hand instead (air-gapped machines);
its .sha256 must sit beside it. The zip holds static files only and nothing
in it is executed. A new build is extracted beside dist/ and swapped in, so a
failure leaves the previous one intact.

Exit 0 when dist/ holds a dashboard, 2 when it does not. The MCP server works
either way; only the dashboard needs dist/.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "src" / "memai" / "webui" / "dist"
VERSION_FILE = ROOT / "src" / "memai" / "__init__.py"
REPO = "Filipe-Soares-de-Almeida/MemAI"
TIMEOUT = 30
NO_DASHBOARD = 2


def say(message: str) -> None:
    print(f"install-webui: {message}", flush=True)


def version(path: Path = VERSION_FILE) -> str:
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    if not found:
        raise SystemExit(f"no __version__ in {path}")
    return found.group(1)


def asset_url(ver: str, name: str) -> str:
    return f"https://github.com/{REPO}/releases/download/v{ver}/{name}"


def npm_build(root: Path = ROOT, *, which: Callable = shutil.which,
              run: Callable = subprocess.run) -> str | None:
    """Build with npm. None on success, else why it could not."""
    if not which("node"):
        return "node is not on PATH"
    npm = which("npm")
    if not npm:
        return "npm is not on PATH"
    for step in (["ci"], ["run", "build"]):
        say(f"npm {' '.join(step)}")
        try:
            done = run([npm, *step], cwd=str(root), stdin=subprocess.DEVNULL)
        except OSError as exc:
            return f"npm {' '.join(step)} could not start: {exc}"
        if done.returncode:
            return f"npm {' '.join(step)} exited with {done.returncode}"
    return None


def verify(archive: Path, checksum: Path) -> None:
    """Refuse `archive` unless `checksum` names it and its sha256 matches."""
    if not checksum.is_file():
        raise ValueError(f"{checksum.name} is missing; it must sit beside the zip")
    fields = checksum.read_text(encoding="ascii", errors="replace").split()
    if len(fields) != 2 or fields[1].lstrip("*") != archive.name:
        raise ValueError(f"{checksum.name} does not name {archive.name}")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != fields[0].lower():
        raise ValueError(f"{archive.name} does not match {checksum.name}")


def install_zip(archive: Path, dist: Path = DIST) -> None:
    """Extract `archive` beside `dist` and swap it in; `dist` survives a failure."""
    dist.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dist-new-", dir=dist.parent))
    try:
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                target = (staging / name).resolve()
                if not target.is_relative_to(staging.resolve()):
                    raise ValueError(f"{archive.name} holds a path outside the build: {name}")
            zf.extractall(staging)
        if not (staging / "index.html").is_file():
            raise ValueError(f"{archive.name} has no index.html at its root")
        old = dist.with_name(f".dist-old-{staging.name[-8:]}")
        if dist.exists():
            dist.rename(old)
        try:
            staging.rename(dist)
        except OSError:
            if old.exists():
                old.rename(dist)
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def fetch(url: str, dest: Path, *, urlopen: Callable = urllib.request.urlopen) -> None:
    with urlopen(url, timeout=TIMEOUT) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)


def download(ver: str, dist: Path = DIST, *, urlopen: Callable = urllib.request.urlopen) -> str | None:
    """Install this version's release asset. None on success, else why not."""
    name = f"memai-webui-{ver}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        archive, checksum = Path(tmp) / name, Path(tmp) / f"{name}.sha256"
        try:
            say(f"downloading {name}")
            fetch(asset_url(ver, name), archive, urlopen=urlopen)
            fetch(asset_url(ver, checksum.name), checksum, urlopen=urlopen)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return f"release v{ver} has no {name} (a checkout ahead of its last release has none)"
            return f"downloading {name} failed: HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            return f"downloading {name} failed: {getattr(exc, 'reason', exc)}"
        try:
            verify(archive, checksum)
            install_zip(archive, dist)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            return str(exc)
    return None


def main(argv: list[str] | None = None, *, dist: Path = DIST,
         build: Callable = npm_build, fetch_release: Callable = download) -> int:
    parser = argparse.ArgumentParser(description="Install the MemAI admin dashboard.")
    parser.add_argument("--zip", type=Path, help="install this memai-webui-<version>.zip")
    parser.add_argument("--version", help="release to download; defaults to memai.__version__")
    args = parser.parse_args(argv)

    if args.zip:
        try:
            verify(args.zip, args.zip.with_name(f"{args.zip.name}.sha256"))
            install_zip(args.zip, dist)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            say(f"{args.zip} not installed: {exc}")
            return NO_DASHBOARD
        say(f"installed {args.zip.name}")
        return 0

    why = build()
    if why is None:
        return 0
    say(f"cannot build the dashboard here: {why}")
    ver = args.version or version()
    why = fetch_release(ver, dist)
    if why is None:
        say(f"installed the prebuilt dashboard of v{ver}")
        return 0
    say(why)
    if (dist / "index.html").is_file():
        say("keeping the dashboard build already in place")
        return 0
    say("no dashboard installed; see 'The dashboard without Node' in .agents/install.md")
    return NO_DASHBOARD


if __name__ == "__main__":
    sys.exit(main())
