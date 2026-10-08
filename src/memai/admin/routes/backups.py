"""The backup shelf: take, list, name, pin, archive, restore and delete backups."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db
from memai.admin.api import api
from memai.admin.shared import _backup, _file_size


def backup(request, payload) -> schema.BackupTaken:
    dest = _backup()
    return {"ok": True, "project": db.active_project(), "path": str(dest),
            "size": _file_size(dest)}


def _shelf_row(path, meta: dict | None = None) -> dict:
    row = {"name": path.name, "size": _file_size(path),
           "mtime": datetime.fromtimestamp(path.stat().st_mtime,
                                           tz=UTC).isoformat()}
    for key in ("label", "pinned"):
        value = (meta or {}).get(key)
        if value:
            row[key] = value
    return row


def backups(request, payload) -> schema.Backups:
    """The whole backup shelf of the active project, and its archives.

    `health` carries a short list of backups for the summary strip; this is
    the one the shelf is drawn from, so it is not truncated -- a file the
    list does not show cannot be selected or archived. Each archive reports
    what it costs on disk and what it holds uncompressed, because the second
    number is what archiving it saved.
    """
    project = db.active_project()
    meta = db.shelf_meta(project)
    archives = []
    for path in db.archive_files(project):
        members = [{**m, **{k: v for k, v in (meta.get(m["name"]) or {}).items()
                            if k == "label"}}
                   for m in db.archive_members(path)]
        archives.append({**_shelf_row(path), "count": len(members),
                         "raw": sum(m["size"] for m in members),
                         "members": members})
    return cast(schema.Backups, {
        "project": project,
        "shelf": [_shelf_row(p, meta.get(p.name)) for p in db.backup_files(project)],
        "archives": archives})


def archive(request, payload) -> schema.ArchivePlan | schema.Archived:
    """Zip the named backups and take them off the shelf.

    `group` says where they go: "month" (the default) or "week" split them
    into one zip per period they were taken in, "name" puts them in a new zip
    called `label`, "existing" appends them to the zip `into`. `archives`
    lists every zip touched with what it received; `archive` is the first.

    `dry_run` with a week or month grouping only reports `plan`: the zips the
    grouping would write, each with what it would receive and whether it
    exists already.
    """
    names = payload.get("names") or []
    if not isinstance(names, list) or not names:
        raise ValueError("names must be a non-empty list")
    names = [str(n) for n in names]
    group = payload.get("group") or "month"
    project = db.active_project()
    if payload.get("dry_run"):
        plan = db.archive_plan(project, names, group)
        return {"ok": True, "plan": [
            {"name": dest.name, "added": len(got), "exists": dest.exists()}
            for dest, got in plan.items()]}
    raw = 0
    shelf = db.backups_dir(project)
    for name in names:
        path = shelf / name
        if path.is_file():
            raw += _file_size(path)
    if group == "name":
        label = payload.get("label")
        if not isinstance(label, str):
            raise ValueError("label (string) required to name a zip")
        landed = {db.archive_backups(project, names, label=label): names}
    elif group == "existing":
        into = payload.get("into")
        if not isinstance(into, str) or not into:
            raise ValueError("into (zip name) required to add to a zip")
        landed = {db.archive_backups(project, names, into=into): names}
    else:
        landed = db.archive_grouped(project, names, group)
    archives = [{"name": dest.name, "added": len(got), "size": _file_size(dest)}
                for dest, got in landed.items()]
    return cast(schema.Archived, {"ok": True, "archive": archives[0]["name"], "archives": archives,
                                  "added": len(names), "raw": raw,
                                  "size": sum(a["size"] for a in archives)})


def archive_rename(request, payload) -> schema.Renamed:
    """Give an archive another name; the file keeps its `<project>-` prefix."""
    name = str(payload.get("name") or "")
    dest = db.rename_archive(db.active_project(), name, str(payload.get("label") or ""))
    return {"ok": True, "name": dest.name}


def name_backup(request, payload) -> schema.BackupNamed:
    """Give one backup a name, or take the one it has away."""
    name = str(payload.get("name") or "")
    label = str(payload.get("label") or "").strip()[:120]
    entry = db.set_shelf_meta(db.active_project(), name, label=label)
    return {"ok": True, "name": name, "label": entry.get("label", "")}


def pin_backup(request, payload) -> schema.BackupPinned:
    """Pin or unpin one backup. A pinned backup cannot be ticked, so nothing
    that acts on a selection can reach it."""
    name = str(payload.get("name") or "")
    pinned = bool(payload.get("pinned"))
    entry = db.set_shelf_meta(db.active_project(), name, pinned=pinned)
    return {"ok": True, "name": name, "pinned": bool(entry.get("pinned"))}


def delete_backups(request, payload) -> schema.BackupsDeleted:
    """Remove backups from the shelf for good."""
    names = payload.get("names") or []
    if not isinstance(names, list) or not names:
        raise ValueError("names must be a non-empty list")
    project = db.active_project()
    freed = 0
    shelf = db.backups_dir(project)
    for name in names:
        path = shelf / str(name)
        if path.is_file():
            freed += _file_size(path)
    count = db.delete_backups(project, [str(n) for n in names])
    return {"ok": True, "deleted": count, "freed": freed}


def restore_backup(request, payload) -> schema.BackupRestored:
    """Put a backup back over the active project, keeping the current state.

    The copy is taken FIRST and named for what it is: restoring replaces
    every memory in the store, and this file is the only way back to what
    was there a moment ago.
    """
    name = str(payload.get("name") or "")
    kept = _backup("pre-restore")
    db.restore_backup(db.active_project(), name)
    return {"ok": True, "name": name, "kept": kept.name}


def unarchive(request, payload) -> schema.Unarchived:
    """Put an archive's files back on the shelf and remove the archive."""
    name = str(payload.get("name") or "")
    restored = db.unarchive(db.active_project(), name)
    return {"ok": True, "name": name, "restored": restored}


def archive_delete(request, payload) -> schema.ArchiveDeleted:
    """Remove an archive and everything inside it."""
    name = str(payload.get("name") or "")
    count = db.delete_archive(db.active_project(), name)
    return {"ok": True, "name": name, "count": count}


ROUTES = [
    Route("/api/maintenance/backup", api(backup), methods=["POST"]),
    Route("/api/maintenance/backups", api(backups)),
    Route("/api/maintenance/archive", api(archive), methods=["POST"]),
    Route("/api/maintenance/unarchive", api(unarchive), methods=["POST"]),
    Route("/api/maintenance/archive-delete", api(archive_delete), methods=["POST"]),
    Route("/api/maintenance/archive-rename", api(archive_rename), methods=["POST"]),
    Route("/api/maintenance/backup-name", api(name_backup), methods=["POST"]),
    Route("/api/maintenance/backup-pin", api(pin_backup), methods=["POST"]),
    Route("/api/maintenance/backup-delete", api(delete_backups), methods=["POST"]),
    Route("/api/maintenance/backup-restore", api(restore_backup), methods=["POST"]),
]
