"""Rebuild the dashboard when its sources are newer than the build.

memai.admin serves webui/dist, which `npm run build` writes from the sources
in webui/. ensure_built() compares modification times: when any source or
build config is newer than dist/index.html it runs `npm run build`, preceded
by `npm ci` when package-lock.json is newer than node_modules. `git pull` and
`git checkout` stamp the files they write with the current time, so a pulled
change counts as newer.

Nothing here raises. Without a checkout (an installed wheel), without npm on
PATH, or when npm fails, it logs one line and the existing build is served.
MEMAI_ADMIN_BUILD=0 switches the check off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
OFF_VALUES = {"0", "false", "no", "off"}
BUILD_CONFIG = ("package.json", "package-lock.json", "vite.config.js")
NPM_TIMEOUT = 600


def repo_root() -> Path:
    """The checkout this module runs from: src/memai/ sits two levels below it."""
    return Path(__file__).resolve().parents[2]


def _say(message: str) -> None:
    """Print before npm writes to the same stream, so the lines stay in order."""
    print(message, flush=True)


def _newest(paths) -> float:
    return max((p.stat().st_mtime for p in paths if p.is_file()), default=0.0)


def _sources(root: Path):
    webui = root / "src" / "memai" / "webui"
    dist = webui / "dist"
    for path in webui.rglob("*"):
        if dist not in path.parents:
            yield path
    for name in BUILD_CONFIG:
        yield root / name


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.is_file() else 0.0


def ensure_built(root: Path | None = None, *,
                 run: Callable = subprocess.run,
                 which: Callable = shutil.which,
                 log: Callable[[str], None] = _say) -> bool:
    """Build the dashboard if it is missing or older than its sources.

    Returns True when the build in dist/ is current, False when it was left
    as it was: the check is off, there is no checkout, or npm failed.
    """
    if os.environ.get("MEMAI_ADMIN_BUILD", "").strip().lower() in OFF_VALUES:
        return False
    root = root or repo_root()
    if not all((root / name).is_file() for name in ("package.json", "vite.config.js")):
        return False

    built = _mtime(root / "src" / "memai" / "webui" / "dist" / "index.html")
    if _newest(_sources(root)) <= built:
        return True

    npm = which("npm")
    if not npm:
        log("memai admin: the dashboard sources changed, but npm is not on PATH; "
            "serving the existing build")
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
