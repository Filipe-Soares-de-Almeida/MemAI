"""Projects: list, create, switch to and delete them, and move memories between them."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db, portable
from memai.admin.api import api
from memai.admin.shared import BULK_MAX


def projects(request, payload) -> schema.Projects:
    """Every project in the home, with its active-row count, and which is active."""
    return {"active": db.active_project(), "projects": db.list_projects(counts=True)}


def project_create(request, payload) -> schema.ProjectCreated:
    """Create an empty project, named as typed apart from surrounding spaces.
    `activate` switches to it in the same call."""
    name = str(payload.get("name") or "").strip()
    db.create_project(name)
    if payload.get("activate"):
        db.set_active_project(name)
    return {"ok": True, "name": name, **projects(request, payload)}


def project_activate(request, payload) -> schema.ProjectActivated:
    """Point every process on this home at `name` from its next connect on."""
    name = str(payload.get("name") or "").strip()
    return {"ok": True, "active": db.set_active_project(name)}


def project_delete(request, payload) -> schema.ProjectDeleted:
    """Remove an empty, inactive project. Refuses anything else -- see db.delete_project."""
    db.delete_project(request.path_params["name"])
    return {"ok": True, **projects(request, payload)}


def project_move(request, payload) -> schema.ProjectMove:
    """Carry memories out of the active project into `target` -- see portable.move.

    `uids` is a list of at most BULK_MAX, `domain` a path; either or both.
    `dry_run` is the default and only reports; `create` makes a target that
    does not exist yet.
    """
    uids = payload.get("uids") or []
    if not isinstance(uids, list):
        raise ValueError("uids must be a list")
    if len(uids) > BULK_MAX:
        raise ValueError(f"at most {BULK_MAX} uids per operation")
    return cast(schema.ProjectMove, portable.move(
        db.active_project(), str(payload.get("target") or "").strip(),
        uids=[str(u) for u in uids], domain=str(payload.get("domain") or "").strip(),
        dry_run=bool(payload.get("dry_run", True)), create=bool(payload.get("create"))))


ROUTES = [
    Route("/api/projects", api(projects), methods=["GET"]),
    Route("/api/projects", api(project_create), methods=["POST"]),
    Route("/api/projects/active", api(project_activate), methods=["POST"]),
    Route("/api/projects/move", api(project_move), methods=["POST"]),
    Route("/api/projects/{name}", api(project_delete), methods=["DELETE"]),
]
