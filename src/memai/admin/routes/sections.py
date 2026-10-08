"""Sections: reading bodies into fields, the queue of bodies that do not read, field edits."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai.admin.api import api
from memai.admin.shared import _backup
from memai.store import connection, memories, sections


def sectionize(request, payload) -> schema.Sectionized:
    """Read every sectioned body in the store into its fields, once.

    Takes a backup first: the run rewrites the bodies whose fields are
    buried under a header line, and a backup is the only copy of the store
    as it stood before that. The per-body previous text is in `edits`
    either way, so one memory can be put back without the file.
    """
    dest = _backup("sectionize")
    with connection.connect() as c:
        result = memories.migrate_sections(c)
    return cast(schema.Sectionized, {"ok": True, "backup": str(dest), **result})


def section_queue(request, payload) -> schema.SectionQueue:
    """The bodies that do not conform, and whether the store has been read."""
    with connection.connect() as conn:
        return cast(schema.SectionQueue, {"ok": True, "migrated": sections.sections_read(conn),
                                          "unread": sections.unread_sections(conn),
                                          "queue": sections.section_queue(conn)})


def edit_sections(request, payload) -> schema.Ok:
    """Rewrite one memory's body from the fields its type is made of.

    The way out of the queue. `sections` maps each field's key to its text;
    every field the type names has to carry something.
    """
    uid = request.path_params["uid"]
    values = payload.get("sections")
    if not isinstance(values, dict):
        raise ValueError("sections must be an object of key -> text")
    with connection.connect() as conn:
        if memories.get_memory(conn, uid) is None:
            raise ValueError(f"unknown memory: {uid}")
        memories.set_sections(conn, uid, {k: str(v) for k, v in values.items()},
                        note=payload.get("note", ""))
    return {"ok": True}


ROUTES = [
    Route("/api/maintenance/sectionize", api(sectionize), methods=["POST"]),
    Route("/api/maintenance/sections-queue", api(section_queue)),
    Route("/api/memories/{uid}/sections", api(edit_sections), methods=["POST"]),
]
