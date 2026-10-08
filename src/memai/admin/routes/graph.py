"""The relations graph of a scope, its most-connected memories first."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db
from memai.admin.api import api
from memai.admin.shared import _int_param, _paths, _scope_echo, _snip, _subtree_param
from memai.store import queries

# The graph gets the whole scope (layout in a worker, one instanced GPU pass). `limit` cuts most-
# connected first; the ceiling only keeps a hand-typed number from straining SQLite.
GRAPH_LIMIT_MAX = 200_000


def graph(request, payload) -> schema.Graph:
    qp = request.query_params
    status = qp.get("status", "active")
    domain = qp.get("domain", "")
    type_ = qp.get("type", "")
    limit = _int_param(request, "limit", 0, 0, GRAPH_LIMIT_MAX)
    with db.connect() as conn:
        scope = _scope_echo(conn, domain)
        total, rows, edges = queries.graph_rows(
            conn, domain=domain, subtree=_subtree_param(request), status=status, type=type_,
            limit=limit)
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["from_uid"]] = degree.get(e["from_uid"], 0) + 1
        degree[e["to_uid"]] = degree.get(e["to_uid"], 0) + 1
    # `tags` and `also` feed the spotlight filter. A node is named by `title`, falling back to
    # `label` (the body's opening line); the spotlight reads both.
    nodes = [_paths({
        "uid": r["uid"], "type": r["type"], "domain": r["domain"],
        "also_domains": r["also_domains"],
        "status": r["status"], "confidence": r["confidence"],
        "tags": r["tags"],
        "title": r["title"].strip(),
        "label": _snip(r["content"].split("\n", 1)[0], 90),
        "degree": degree.get(r["uid"], 0),
        "created_at": r["created_at"],
    }) for r in rows]
    # A cut that says nothing reads as "this is everything".
    return cast(schema.Graph, {"nodes": nodes, "edges": edges,
            "total": total, "truncated": total > len(nodes), **scope})


ROUTES = [
    Route("/api/graph", api(graph)),
]
