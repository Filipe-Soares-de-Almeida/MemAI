"""A project's backup shelf: snapshots, their metadata, and the zip archives that group them."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

from memai import contract
from memai.lite import home
from memai.store.paths import (
    GENERAL_PROJECT,
    _same_project,
    default_db_path,
    find_project,
    project_path,
)

BACKUPS_DIRNAME = "backups"
ARCHIVES_DIRNAME = "archive"
SHELF_META_FILE = "shelf.json"


def backups_dir(project: str = GENERAL_PROJECT) -> Path:
    """Where a project's backups go, created if needed: `<home>/backups` for
    GENERAL_PROJECT, `<home>/backups/<name>` for any other project."""
    root = home() / BACKUPS_DIRNAME
    if _same_project(project, GENERAL_PROJECT):
        out = root
    else:
        out = root / (find_project(project) or project)
    out.mkdir(parents=True, exist_ok=True)
    return out


def backup_name(project: str, kind: str = "") -> str:
    """`<project>-[<kind>-]<UTC stamp>.db`: the file a backup of `project` is
    written as."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{project}-{kind}-{stamp}.db" if kind else f"{project}-{stamp}.db"


def backup_files(project: str) -> list[Path]:
    """The backups of one project, newest first: the `.db` files in its
    backups_dir(), whatever they are named."""
    files = [p for p in backups_dir(project).glob("*.db") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _shelf_meta_path(project: str) -> Path:
    return backups_dir(project) / SHELF_META_FILE


def shelf_meta(project: str = GENERAL_PROJECT) -> dict:
    """What has been written ABOUT a project's backups: `{filename: {...}}`.

    A backup's own name carries when it was taken and what took it; a name
    somebody typed for it, and whether it is pinned, have nowhere in the file
    to live. They sit beside the shelf in `shelf.json`, keyed by filename --
    so an entry follows its file into an archive and back out.

    Missing or unreadable, the shelf simply has nothing written about it.
    """
    path = _shelf_meta_path(project)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_shelf_meta(project: str, data: dict) -> None:
    """Replace the sidecar, or remove it once nothing is written about the
    shelf. Written to a temp file and moved into place, so a reader never
    sees half of it."""
    path = _shelf_meta_path(project)
    if not data:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def set_shelf_meta(project: str, name: str, **fields) -> dict:
    """Write `fields` about one backup and return what it now holds.

    A field set back to its default -- an empty label, an unpinned file --
    is removed rather than stored, and an entry with nothing left in it goes
    with it, so the sidecar never grows a row per backup ever taken.
    """
    _inside(Path(name), backups_dir(project))
    data = shelf_meta(project)
    entry = dict(data.get(name) or {})
    for key, value in fields.items():
        if value in ("", None, False):
            entry.pop(key, None)
        else:
            entry[key] = value
    if entry:
        data[name] = entry
    else:
        data.pop(name, None)
    _write_shelf_meta(project, data)
    return entry


def forget_shelf_meta(project: str, names: list[str]) -> None:
    """Drop what was written about backups that are gone."""
    data = shelf_meta(project)
    if not any(n in data for n in names):
        return
    for n in names:
        data.pop(n, None)
    _write_shelf_meta(project, data)


def archives_dir(project: str = GENERAL_PROJECT) -> Path:
    """Where a project's zipped backups go, created if needed:
    `<backups_dir>/archive`. A subfolder, so backup_files() -- which globs
    `*.db` one level deep -- never sees what has been archived."""
    out = backups_dir(project) / ARCHIVES_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    return out


def archive_files(project: str) -> list[Path]:
    """A project's archives, newest first: the `.zip` files in archives_dir()."""
    files = [p for p in archives_dir(project).glob("*.zip") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def archive_name(project: str, when: date | None = None) -> str:
    """`<project>-<YYYY-MM>.zip`: the archive a backup taken in that month
    joins. One per month per project, so archiving twice in September adds to
    the same file rather than making a second one."""
    stamp = (when or datetime.now(UTC).date()).strftime("%Y-%m")
    return f"{project}-{stamp}.zip"


# How a batch of backups is split into zips when none is named: by the ISO
# week or the calendar month each was taken in.
ARCHIVE_GROUPS = ("week", "month")
ARCHIVE_LABEL_MAX = contract.ARCHIVE_LABEL_MAX
_ARCHIVE_LABEL = re.compile(r"[\w]([\w .\-]*[\w])?")


def archive_label(label: str) -> str:
    """A zip's name as typed, or ValueError when it is not a plain name.

    Letters, digits, spaces, `_`, `-` and `.` inside it, starting and ending
    on a letter or digit, at most ARCHIVE_LABEL_MAX long: nothing that is a
    path, a hidden file or a character Windows refuses in a file name.
    """
    label = (label or "").strip()
    if not label or len(label) > ARCHIVE_LABEL_MAX or not _ARCHIVE_LABEL.fullmatch(label):
        raise ValueError(
            f"zip name must be 1-{ARCHIVE_LABEL_MAX} letters, digits, spaces, '_', '-' or '.'")
    return label


def archive_label_name(project: str, label: str) -> str:
    """`<project>-<label>.zip`: the file a named archive is stored as."""
    return f"{project}-{archive_label(label)}.zip"


def archive_group_name(project: str, group: str, taken: datetime) -> str:
    """The archive a backup taken at `taken` joins under `group`:
    `<project>-<YYYY>-W<WW>.zip` by ISO week, `<project>-<YYYY>-<MM>.zip` by
    month."""
    if group == "week":
        year, week, _ = taken.isocalendar()
        return f"{project}-{year}-W{week:02d}.zip"
    if group == "month":
        return archive_name(project, taken.date())
    raise ValueError(f"group must be one of {ARCHIVE_GROUPS}")


def _inside(path: Path, root: Path) -> Path:
    """`path` resolved, or ValueError when it lands outside `root`.

    Every name below arrives from an HTTP payload, so a name is treated as
    hostile until it resolves under the folder it is supposed to be in.
    """
    full = (root / path).resolve()
    if not full.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes its folder: {path}")
    return full


def _member_mtime(info: zipfile.ZipInfo) -> datetime:
    """A member's timestamp as an aware datetime.

    A zip stores a DOS timestamp: local wall-clock time, no zone, rounded to
    two seconds. It is read back as local, which is the only reading that
    round-trips the file it was written from.
    """
    return datetime(*info.date_time).astimezone()


def archive_members(path: Path) -> list[dict]:
    """What one archive holds: name, uncompressed size and stored timestamp
    per member, in the order the zip lists them."""
    with zipfile.ZipFile(path) as zf:
        return [{"name": i.filename, "size": i.file_size,
                 "mtime": _member_mtime(i).isoformat()}
                for i in zf.infolist() if not i.is_dir()]


def _shelf_sources(project: str, names: list[str]) -> list[Path]:
    """The named backups as files on the shelf, or ValueError for any name
    that is not one. Nothing is touched until every name has been checked."""
    if not names:
        raise ValueError("no backups named")
    shelf = backups_dir(project)
    sources = []
    for name in names:
        full = _inside(Path(name), shelf)
        if full.suffix != ".db" or not full.is_file():
            raise ValueError(f"not a backup on this shelf: {name}")
        sources.append(full)
    return sources


def _zip_into(dest: Path, sources: list[Path]) -> None:
    """Append `sources` to the zip at `dest` (created if absent), then take
    them off the shelf. A file is deleted only after it is in the zip, so an
    interrupted run leaves it on the shelf rather than nowhere."""
    with zipfile.ZipFile(dest, "a", zipfile.ZIP_DEFLATED) as zf:
        held = set(zf.namelist())
        for src in sources:
            if src.name in held:
                raise ValueError(f"already archived: {src.name}")
            zf.write(src, src.name)
    for src in sources:
        src.unlink()


def archive_backups(project: str, names: list[str], when: date | None = None, *,
                    into: str | None = None, label: str | None = None) -> Path:
    """Move the named backups into ONE archive and return it.

    Each name is a file in the project's backups_dir; anything that is not
    there, or that resolves outside it, raises. Which zip receives them:

    - `into`: an archive that already exists in archives_dir(), appended to.
    - `label`: a new archive of that name; one by that name already existing
      raises, since `into` is how a zip is added to.
    - neither: the month of `when` (today by default), created on first use
      and appended to afterwards.
    """
    sources = _shelf_sources(project, names)
    archives = archives_dir(project)
    if into is not None:
        dest = _inside(Path(into), archives)
        if dest.suffix != ".zip" or not dest.is_file():
            raise ValueError(f"not an archive: {into}")
    elif label is not None:
        dest = archives / archive_label_name(project, label)
        if dest.exists():
            raise ValueError(f"an archive named '{dest.name}' already exists")
    else:
        dest = archives / archive_name(project, when)
    _zip_into(dest, sources)
    return dest


def _group_buckets(project: str, names: list[str], group: str) -> dict[Path, list[Path]]:
    """Which archive each named backup would join under `group`.

    A backup goes where the moment it was taken puts it (its file time, in
    UTC), not where today falls, so archiving old backups later leaves each
    in the period it belongs to.
    """
    if group not in ARCHIVE_GROUPS:
        raise ValueError(f"group must be one of {ARCHIVE_GROUPS}")
    archives = archives_dir(project)
    buckets: dict[Path, list[Path]] = {}
    for src in _shelf_sources(project, names):
        taken = datetime.fromtimestamp(src.stat().st_mtime, tz=UTC)
        buckets.setdefault(archives / archive_group_name(project, group, taken), []).append(src)
    return buckets


def archive_plan(project: str, names: list[str], group: str) -> dict[Path, list[str]]:
    """`{archive: [names it would receive]}` for archive_grouped, writing nothing."""
    return {dest: [m.name for m in members]
            for dest, members in _group_buckets(project, names, group).items()}


def archive_grouped(project: str, names: list[str], group: str) -> dict[Path, list[str]]:
    """Move the named backups into one archive per `group` and return
    `{archive: [names it received]}`. An archive that exists already is
    appended to."""
    buckets = _group_buckets(project, names, group)
    for dest, members in buckets.items():
        held = set()
        if dest.exists():
            with zipfile.ZipFile(dest) as zf:
                held = set(zf.namelist())
        clash = next((m.name for m in members if m.name in held), None)
        if clash:
            raise ValueError(f"already archived: {clash}")
    for dest, members in buckets.items():
        _zip_into(dest, members)
    return {dest: [m.name for m in members] for dest, members in buckets.items()}


def rename_archive(project: str, name: str, label: str) -> Path:
    """Rename an archive to `<project>-<label>.zip` and return its path.

    Renaming onto the name it already has changes nothing; onto another
    archive's name raises. What was written about the backups inside is
    keyed by their own filenames, so it stays attached.
    """
    archives = archives_dir(project)
    full = _inside(Path(name), archives)
    if full.suffix != ".zip" or not full.is_file():
        raise ValueError(f"not an archive: {name}")
    dest = archives / archive_label_name(project, label)
    if dest.exists():
        if not os.path.samefile(dest, full):
            raise ValueError(f"an archive named '{dest.name}' already exists")
        if dest.name == full.name:
            return full
    full.rename(dest)
    return dest


def delete_backups(project: str, names: list[str]) -> int:
    """Remove the named backups from the shelf, and what was written about
    them. Returns how many files went. Nothing is deleted until every name
    has been checked."""
    if not names:
        raise ValueError("no backups named")
    shelf = backups_dir(project)
    targets = []
    for name in names:
        full = _inside(Path(name), shelf)
        if full.suffix != ".db" or not full.is_file():
            raise ValueError(f"not a backup on this shelf: {name}")
        targets.append(full)
    for path in targets:
        path.unlink()
    forget_shelf_meta(project, [p.name for p in targets])
    return len(targets)


def restore_backup(project: str, name: str) -> None:
    """Copy a backup over the project it belongs to, through SQLite.

    Uses the online backup API rather than replacing the file: the live
    database has a WAL beside it and readers open on it, and a file swapped
    underneath that leaves the two out of step. The caller takes a copy of
    the current state first -- restoring is not undoable from here.
    """
    full = _inside(Path(name), backups_dir(project))
    if full.suffix != ".db" or not full.is_file():
        raise ValueError(f"not a backup on this shelf: {name}")
    src = sqlite3.connect(str(full), timeout=30.0)
    try:
        dst = sqlite3.connect(str(project_path(project)), timeout=30.0)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def unarchive(project: str, name: str) -> list[str]:
    """Put an archive's files back on the shelf and remove the archive.

    Returns the names restored. A member whose name is a path, or that would
    land on a file already on the shelf, raises before anything is written.
    """
    archives = archives_dir(project)
    full = _inside(Path(name), archives)
    if full.suffix != ".zip" or not full.is_file():
        raise ValueError(f"not an archive: {name}")
    shelf = backups_dir(project)
    with zipfile.ZipFile(full) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        for i in members:
            member = Path(i.filename)
            if member.name != i.filename:
                raise ValueError(f"archive holds a path, not a name: {i.filename}")
            if (shelf / member.name).exists():
                raise ValueError(f"already on the shelf: {member.name}")
        for i in members:
            zf.extract(i, shelf)
            # extract() stamps the file with now; restore the backup's own time so the shelf sorts right.
            stamp = _member_mtime(i).timestamp()
            os.utime(shelf / i.filename, (stamp, stamp))
    full.unlink()
    return [i.filename for i in members]


def delete_archive(project: str, name: str) -> int:
    """Remove an archive and everything in it. Returns how many files went."""
    full = _inside(Path(name), archives_dir(project))
    if full.suffix != ".zip" or not full.is_file():
        raise ValueError(f"not an archive: {name}")
    count = len(archive_members(full))
    full.unlink()
    return count


def backup_to(dest: Path, *, project: str | None = None) -> Path:
    """Copy a project into `dest` with VACUUM INTO: `project`, or the active one.

    Runs on an autocommit connection, since the statement refuses to run
    inside a transaction. Fails when `dest` already exists.
    """
    src = project_path(project) if project else default_db_path()
    conn = sqlite3.connect(str(src), timeout=30.0, isolation_level=None)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    return dest
