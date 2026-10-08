"""Relations: creating and deleting the edge between two memories."""

from __future__ import annotations

from starlette.routing import Route

from memai import admin_schemas as schema
from memai.admin.api import api
from memai.store import connection, relations


def create_relation(request, payload) -> schema.RelationCreated:
    from_uid = (payload.get("from_uid") or "").strip()
    to_uid = (payload.get("to_uid") or "").strip()
    rel_type = (payload.get("relation_type") or "").strip()
    # The rules live in db.add_relation, so this endpoint and the MCP tool
    # refuse the same edges with the same words.
    with connection.connect() as conn:
        rel_id = relations.add_relation(conn, from_uid, to_uid, rel_type, note=payload.get("note", ""))
    return {"relation_id": rel_id}


def delete_relation(request, payload) -> schema.Ok:
    rel_id = request.path_params["rel_id"]
    with connection.connect() as conn:
        deleted = relations.delete_relation(conn, rel_id)
    if not deleted:
        raise ValueError(f"unknown relation: {rel_id}")
    return {"ok": True}


ROUTES = [
    Route("/api/relations", api(create_relation), methods=["POST"]),
    Route("/api/relations/{rel_id:int}", api(delete_relation), methods=["DELETE"]),
]
