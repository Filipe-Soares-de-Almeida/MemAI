"""Creating, deleting and listing projects, each of which opens the store it names."""

from __future__ import annotations

from pathlib import Path

from memai.lite import home
from memai.store.connection import connect
from memai.store.paths import (
    GENERAL_FILE,
    GENERAL_PROJECT,
    _checked_project,
    _projects_dir,
    _same_project,
    active_project,
    find_project,
    project_name,
    project_name_error,
    project_path,
)


def create_project(name: str) -> Path:
    """A new, empty project with the schema in place. Refuses a name already
    taken, in any casing."""
    name = _checked_project(name)
    if find_project(name) is not None:
        raise ValueError(f"project '{name}' already exists")
    path = project_path(name)
    with connect(path):
        pass
    return path


def delete_project(name: str) -> None:
    """Remove a project that holds no memory and is not the active one.

    GENERAL_PROJECT is never removed. The WAL and shared-memory files go with
    the database.
    """
    name = project_name(name)
    if name == GENERAL_PROJECT:
        raise ValueError("the General project cannot be deleted")
    if name == active_project():
        raise ValueError("switch to another project before deleting the active one")
    path = project_path(name)
    with connect(path) as conn:
        held = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    if held:
        raise ValueError(f"project '{name}' holds {held} memories; move them out first")
    for suffix in ("", "-wal", "-shm"):
        try:
            path.with_name(path.name + suffix).unlink()
        except FileNotFoundError:
            pass


def list_projects(*, counts: bool = False) -> list[dict]:
    """Every project in the home: GENERAL_PROJECT first, the rest by name.

    Per entry: `name`, `path`, `size` in bytes (0 until the first connect
    creates the file), `active` and `general`. With `counts`, also
    `memories` -- the active rows, which opens each project to ask.
    """
    active = active_project()
    found = [(GENERAL_PROJECT, home() / GENERAL_FILE)]
    found += sorted(((p.stem, p) for p in _projects_dir().glob("*.db")
                     if p.is_file() and project_name_error(p.stem) is None
                     and not _same_project(p.stem, GENERAL_PROJECT)),
                    key=lambda item: item[0].casefold())
    out = []
    for name, path in found:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entry = {"name": name, "path": str(path), "size": size,
                 "active": name == active, "general": name == GENERAL_PROJECT}
        if counts:
            with connect(path) as conn:
                entry["memories"] = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
        out.append(entry)
    return out
