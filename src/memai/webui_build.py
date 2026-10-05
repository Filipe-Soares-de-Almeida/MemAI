"""Rebuild the dashboard when its build stops matching its sources.

memai.admin serves webui/dist, which `npm run build` writes from the sources
in webui/. The build carries dist/build.json, the version and a hash of the
sources that made it (tools/build-stamp.mjs writes it). ensure_built()
recomputes that hash with source_hash(); when the stamp is missing or differs
it runs `npm run build`, preceded by `npm ci` when package-lock.json is newer
than node_modules. A file touched but unchanged does not count; a checkout
that rewinds a file does.

Nothing here raises. Without a checkout (an installed wheel) it does nothing.
Without npm on PATH, a build stamped with another version is replaced by this
version's prebuilt release asset (tools/install-webui.py); otherwise, or when
npm fails, it logs one line and the existing build is served.
MEMAI_ADMIN_BUILD=0 switches the check off.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from memai import __version__

CREATE_NO_WINDOW = 0x08000000
OFF_VALUES = {"0", "false", "no", "off"}
# Mirrors BUILD_CONFIG in tools/build-stamp.mjs.
BUILD_CONFIG = ("package.json", "package-lock.json", "vite.config.js", "tsconfig.json")
STAMP = "build.json"
NPM_TIMEOUT = 600


def repo_root() -> Path:
    """The checkout this module runs from: src/memai/ sits two levels below it."""
    return Path(__file__).resolve().parents[2]


def _say(message: str) -> None:
    """Print before npm writes to the same stream, so the lines stay in order."""
    print(message, flush=True)


def _dist(root: Path) -> Path:
    return root / "src" / "memai" / "webui" / "dist"


def _inputs(root: Path) -> list[tuple[str, Path]]:
    webui = root / "src" / "memai" / "webui"
    dist = webui / "dist"
    files = [p for p in webui.rglob("*") if p.is_file() and dist not in p.parents]
    files += [root / name for name in BUILD_CONFIG if (root / name).is_file()]
    return sorted((p.relative_to(root).as_posix(), p) for p in files)


def source_hash(root: Path) -> str:
    """sha256 over each input's relative path and contents, CRLF read as LF.

    Mirrors sourceHash() in tools/build-stamp.mjs byte for byte.
    """
    digest = hashlib.sha256()
    for name, path in _inputs(root):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def read_stamp(root: Path) -> dict:
    """dist/build.json, or {} when it is missing or unreadable."""
    try:
        stamp = json.loads((_dist(root) / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stamp if isinstance(stamp, dict) else {}


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.is_file() else 0.0


def _fetch_prebuilt(root: Path, version: str) -> str | None:
    """Install this version's release asset; None on success, else why not."""
    script = root / "tools" / "install-webui.py"
    if not script.is_file():
        return "tools/install-webui.py is missing"
    try:
        spec = importlib.util.spec_from_file_location("memai_install_webui", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.download(version, _dist(root))
    except Exception as exc:  # the dashboard must start whatever the fetch does
        return f"the prebuilt fetch failed: {exc}"


def ensure_built(root: Path | None = None, *,
                 run: Callable = subprocess.run,
                 which: Callable = shutil.which,
                 log: Callable[[str], None] = _say,
                 fetch: Callable[[Path, str], str | None] = _fetch_prebuilt,
                 version: str = __version__) -> bool:
    """Build the dashboard if its stamp is missing or does not match the sources.

    Returns True when the build in dist/ is current, False when it was left
    as it was: the check is off, there is no checkout, or npm failed.
    """
    if os.environ.get("MEMAI_ADMIN_BUILD", "").strip().lower() in OFF_VALUES:
        return False
    root = root or repo_root()
    if not all((root / name).is_file() for name in ("package.json", "vite.config.js")):
        return False

    stamp = read_stamp(root)
    if stamp.get("sources") == source_hash(root):
        return True

    npm = which("npm")
    if not npm:
        if stamp.get("version") != version:
            why = fetch(root, version)
            if why is None:
                log(f"memai admin: installed the prebuilt dashboard of v{version}")
                return True
            log(f"memai admin: npm is not on PATH and {why}; serving the existing build")
            return False
        log("memai admin: the dashboard sources changed, but npm is not on PATH; "
            "serving the existing build, which may not match this server")
        return False

    steps = [["run", "build"]]
    if _mtime(root / "package-lock.json") > _mtime(root / "node_modules" / ".package-lock.json"):
        steps.insert(0, ["ci"])

    flags = {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    for step in steps:
        command = "npm " + " ".join(step)
        log(f"memai admin: the dashboard sources changed, running `{command}`")
        try:
            done = run([npm, *step], cwd=str(root), stdin=subprocess.DEVNULL,
                       timeout=NPM_TIMEOUT, check=False, **flags)
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"memai admin: `{command}` could not run ({exc}); "
                "serving the existing build")
            return False
        if done.returncode != 0:
            log(f"memai admin: `{command}` failed with exit code {done.returncode}; "
                "serving the existing build")
            return False
    return True
