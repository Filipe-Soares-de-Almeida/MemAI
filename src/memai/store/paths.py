"""Project names, the file each project lives in, and the project connect() opens by default."""

from __future__ import annotations

import os
from pathlib import Path

from memai.lite import home

# One project is one SQLite file: GENERAL_FILE for GENERAL_PROJECT, projects/<name>.db for others.
# ACTIVE_FILE in the home names the one connect() opens, re-read each call so a switch spreads.
GENERAL_PROJECT = "General"
GENERAL_FILE = "memai.db"
PROJECTS_DIRNAME = "projects"
ACTIVE_FILE = "active"
PROJECT_NAME_MAX = 80
# A project name is a file name, so it follows Windows rules: no reserved or control characters,
# no edge spaces, no trailing dot, no device names, and names differing only in case are one.
_PROJECT_BAD_CHARS = frozenset('<>:"/\\|?*') | frozenset(chr(c) for c in range(32))
_PROJECT_DEVICES = frozenset(
    ["con", "prn", "aux", "nul",
     *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))])


def project_name_error(name: object) -> str | None:
    """Why `name` cannot name a project, or None when it can."""
    text = str(name or "")
    if not text.strip():
        return "a project needs a name"
    if text != text.strip() or text.endswith("."):
        return "a project name cannot start or end with a space, or end with a dot"
    if len(text) > PROJECT_NAME_MAX:
        return f"a project name has at most {PROJECT_NAME_MAX} characters"
    bad = sorted(c for c in set(text) if c in _PROJECT_BAD_CHARS)
    if bad:
        shown = " ".join(repr(c) if ord(c) < 32 else c for c in bad)
        return f"a project name cannot contain {shown}"
    if text.split(".")[0].casefold() in _PROJECT_DEVICES:
        return f"'{text}' is a device name on Windows"
    return None


def _checked_project(name: object) -> str:
    error = project_name_error(name)
    if error:
        raise ValueError(error)
    return str(name)


def _same_project(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _projects_dir() -> Path:
    folder = home() / PROJECTS_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def find_project(name: str) -> str | None:
    """The project called `name`, spelled the way its file is, or None when
    there is none. Matched without regard to case; GENERAL_PROJECT is always
    there."""
    if _same_project(name, GENERAL_PROJECT):
        return GENERAL_PROJECT
    for path in _projects_dir().glob("*.db"):
        if path.is_file() and _same_project(path.stem, name):
            return path.stem
    return None


def project_name(name: str) -> str:
    """`name` spelled the way the project is, or ValueError when there is no
    such project."""
    found = find_project(_checked_project(name))
    if found is None:
        raise ValueError(f"no project named '{name}'")
    return found


def project_exists(name: str) -> bool:
    return project_name_error(name) is None and find_project(name) is not None


def project_path(name: str) -> Path:
    """The file behind a project name. GENERAL_PROJECT is `memai.db` in the
    home root; any other is `projects/<name>.db`, spelled as the file is when
    one exists."""
    name = _checked_project(name)
    if _same_project(name, GENERAL_PROJECT):
        return home() / GENERAL_FILE
    return _projects_dir() / f"{find_project(name) or name}.db"


def active_project() -> str:
    """The project connect() opens when handed no path.

    Read from ACTIVE_FILE in the home; GENERAL_PROJECT when the file is
    absent, or names something that is not a project here.
    """
    try:
        name = (home() / ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return GENERAL_PROJECT
    if project_name_error(name):
        return GENERAL_PROJECT
    return find_project(name) or GENERAL_PROJECT


def set_active_project(name: str) -> str:
    """Point every later connect() at `name`, which has to exist already.

    Written to a sibling file and renamed into place: a concurrent reader
    gets the old name or the new one, never part of either.
    """
    name = project_name(name)
    target = home() / ACTIVE_FILE
    tmp = target.with_name(f"{ACTIVE_FILE}.tmp")
    tmp.write_text(f"{name}\n", encoding="utf-8")
    os.replace(tmp, target)
    return name


def default_db_path() -> Path:
    """The active project's file: what connect() opens when handed no path."""
    return project_path(active_project())
