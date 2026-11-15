"""Creating, renaming, deleting and listing projects, each of which opens the store it names."""

from __future__ import annotations

from pathlib import Path

from memai.lite import home
from memai.store.backups import backup_name, backup_to, backups_dir
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
    set_active_project,
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


def _sidecars(path: Path) -> list[Path]:
    return [path.with_name(path.name + suffix) for suffix in ("", "-wal", "-shm")]


def delete_project(name: str, *, force: bool = False) -> Path | None:
    """Remove a project that is not the active one.

    GENERAL_PROJECT is never removed. Without `force` the project must hold no
    memory; with it, a copy is taken into the project's backups folder first
    and its path returned. The WAL and shared-memory files go with the database.
    """
    name = project_name(name)
    if name == GENERAL_PROJECT:
        raise ValueError("the General project cannot be deleted")
    if name == active_project():
        raise ValueError("switch to another project before deleting the active one")
    path = project_path(name)
    with connect(path) as conn:
        held = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    kept = None
    if held:
        if not force:
            raise ValueError(f"project '{name}' holds {held} memories; move them out first")
        kept = backup_to(backups_dir(name) / backup_name(name, "deleted"), project=name)
    for side in _sidecars(path):
        try:
            side.unlink()
        except FileNotFoundError:
            pass
    return kept


def rename_project(name: str, new_name: str) -> str:
    """Give a project another name, keeping its memories, backups and active status.

    GENERAL_PROJECT keeps its name. A name that differs only in case from the
    project's own is allowed; one that matches another project is refused.
    """
    name = project_name(name)
    new_name = _checked_project(new_name)
    if name == GENERAL_PROJECT:
        raise ValueError("the General project cannot be renamed")
    if _same_project(new_name, GENERAL_PROJECT):
        raise ValueError(f"project '{new_name}' already exists")
    taken = find_project(new_name)
    if taken is not None and not _same_project(taken, name):
        raise ValueError(f"project '{taken}' already exists")
    if new_name == name:
        return name
    was_active = name == active_project()
    old_path = project_path(name)
    new_path = old_path.with_name(f"{new_name}.db")
    with connect(old_path):
        pass
    old_shelf = backups_dir(name)
    new_shelf = old_shelf.with_name(new_name)
    try:
        for src in _sidecars(old_path):
            if src.exists():
                src.replace(new_path.with_name(new_path.name + src.name[len(old_path.name):]))
        if new_shelf.exists() and not _same_project(new_shelf.name, name):
            for item in old_shelf.iterdir():
                if not (new_shelf / item.name).exists():
                    item.replace(new_shelf / item.name)
            if not any(old_shelf.iterdir()):
                old_shelf.rmdir()
        else:
            old_shelf.replace(new_shelf)
    except OSError as exc:
        raise ValueError(f"project '{name}' is in use and cannot be renamed now: {exc}") from exc
    if was_active:
        set_active_project(new_name)
    return new_name


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
