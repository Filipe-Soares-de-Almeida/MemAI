"""The folder of generated diagram renders and the age-based sweep that prunes it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from memai.lite import home


def renders_dir() -> Path:
    """Where generated SVG files go: a subdirectory, never the home root.

    The home root holds the database, its WAL and the backups, and a
    housekeeping sweep that deletes by age has no business running in a
    directory containing those. Keeping the renders in their own folder is
    what lets prune_renders() be a simple rule instead of a careful one.
    """
    out = home() / "renders"
    out.mkdir(parents=True, exist_ok=True)
    return out


_RETENTION_DAYS = {"1d": 1, "7d": 7, "30d": 30}
# the two things this folder ever holds: a bare SVG, and the same drawing
# wrapped in a pan/zoom shell
RENDER_SUFFIXES = (".svg", ".html")


def renders_usage() -> dict:
    """What the render folder currently costs, for the maintenance view."""
    files = [p for p in renders_dir().iterdir()
             if p.is_file() and p.suffix in RENDER_SUFFIXES]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def prune_renders_all() -> dict:
    """Empty the render folder regardless of age.

    Separate from prune_renders rather than a magic mode, because "keep
    nothing" and "keep for N days" are different intentions and folding
    them together is how a retention setting of 1 day ends up meaning
    'delete everything' by accident.
    """
    return {**_sweep_renders(lambda _stat: True, keep=None), "mode": "all"}


def _sweep_renders(should_delete, *, keep: Path | None) -> dict:
    """The one place that deletes a render, so the guards live once.

    Narrow deliberately, because this removes files and MEMAI_HOME is
    whatever an environment variable says it is: only inside renders/, only
    a suffix in RENDER_SUFFIXES, only a real file, never a symlink, never
    recursive, and never `keep` -- the render being written right now.
    """
    pruned = 0
    freed = 0
    for path in renders_dir().iterdir():
        if path.suffix not in RENDER_SUFFIXES:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if keep is not None and path.resolve() == keep.resolve():
            continue
        try:
            stat = path.stat()
            if not should_delete(stat):
                continue
            path.unlink()
        except OSError:
            # a file another process is holding is not this sweep's problem
            continue
        pruned += 1
        freed += stat.st_size
    return {"pruned": pruned, "bytes": freed}


def prune_renders(mode: str, *, keep: Path | None = None) -> dict:
    """Delete generated renders older than the retention window.

    'never' removes nothing. Returns what it did rather than staying quiet:
    a sweep that silently deletes is one the user stops trusting.
    """
    days = _RETENTION_DAYS.get(mode)
    if days is None:
        return {"pruned": 0, "bytes": 0, "mode": mode}
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    swept = _sweep_renders(lambda stat: stat.st_mtime < cutoff, keep=keep)
    return {**swept, "mode": mode}
