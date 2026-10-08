"""Diagrams: the list, one diagram, and every edit to its graph, layout, links and jumps."""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai.admin.api import api
from memai.admin.shared import _peer_card, _scope_echo, _subtree_param
from memai.store import connection, memories
from memai.store.diagrams import persist as diagram_persist


def _require(result: tuple) -> Any:
    """The db diagram writers return (value, errors); an error becomes a 400.

    Keeps every handler below down to one line of real work, and routes
    validation messages through the same ValueError channel the rest of
    this module uses.
    """
    value, errors = result
    if errors:
        raise ValueError("; ".join(errors))
    return value


def _diagram_json(conn: sqlite3.Connection, uid: str) -> dict | None:
    """A diagram plus a memory card per node link, ready for the editor."""
    data = diagram_persist.get_diagram(conn, uid)
    if data is None:
        return None
    for link in data["links"]:
        link["peer"] = _peer_card(conn, link["target_uid"]) or {
            "uid": link["target_uid"], "missing": True,
        }
        link.pop("target_content", None)  # the peer card already carries a snippet
    data["mermaid"] = diagram_persist.render_diagram_mermaid(conn, uid)
    return data


def _diagram_or_400(conn: sqlite3.Connection, uid: str) -> None:
    if diagram_persist.get_diagram_row(conn, uid) is None:
        raise ValueError(f"unknown diagram: {uid}")


def diagram_list(request, payload) -> schema.DiagramPage:
    """Every diagram with its size and its structural problems.

    Backs the dedicated diagram view: a flow is maintained by fixing its
    shape, which the confidence/dedup tooling for prose cannot see.
    """
    status = request.query_params.get("status", "active")
    domain = request.query_params.get("domain", "")
    with connection.connect() as conn:
        items = diagram_persist.diagram_overview(conn, domain=domain, status=status,
                                    subtree=_subtree_param(request))
        scope = _scope_echo(conn, domain)
    return cast(schema.DiagramPage, {
        "total": len(items),
        "with_issues": sum(1 for d in items if d["issues"]),
        "items": items,
        **scope,
    })


def diagram_detail(request, payload) -> schema.DiagramRecord:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        data = _diagram_json(conn, uid)
    if data is None:
        raise ValueError(f"unknown diagram: {uid}")
    return cast(schema.DiagramRecord, data)


def diagram_create(request, payload) -> schema.DiagramCreated:
    with connection.connect() as conn:
        uid = _require(diagram_persist.insert_diagram(
            conn,
            title=(payload.get("title") or "").strip(),
            nodes=payload.get("nodes") or [],
            edges=payload.get("edges") or [],
            summary=(payload.get("summary") or "").strip(),
            kind=(payload.get("kind") or "flowchart").strip(),
            domain=(payload.get("domain") or "").strip(),
            also=payload.get("also") or "",
            session=(payload.get("session") or "").strip(),
            tags=(payload.get("tags") or "").strip(),
        ))
        return {"uid": uid, "also": memories.get_domain_links(conn, uid)}


def diagram_graph(request, payload) -> schema.Ok:
    """Replace the whole graph; surviving nodes keep their positions."""
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        _require(diagram_persist.replace_diagram_graph(
            conn, uid, payload.get("nodes") or [], payload.get("edges") or []))
    return {"ok": True}


def diagram_meta(request, payload) -> schema.Ok:
    uid = request.path_params["uid"]
    if not {"title", "summary", "font_scale"} & set(payload):
        raise ValueError("nothing to update (fields: title, summary, font_scale)")
    with connection.connect() as conn:
        _require(diagram_persist.set_diagram_meta(
            conn, uid,
            title=payload.get("title") if "title" in payload else None,
            summary=payload.get("summary") if "summary" in payload else None,
            font_scale=payload.get("font_scale") if "font_scale" in payload else None,
        ))
    return {"ok": True}


def diagram_node(request, payload) -> schema.NodeSaved:
    uid = request.path_params["uid"]
    key = (payload.get("key") or "").strip()
    if not key:
        raise ValueError("key is required")
    with connection.connect() as conn:
        if payload.get("delete"):
            _require(diagram_persist.delete_diagram_node(conn, uid, key))
        else:
            _require(diagram_persist.upsert_diagram_node(
                conn, uid, key, label=payload.get("label"),
                shape=payload.get("shape"), note=payload.get("note")))
    return {"ok": True, "key": key}


def diagram_edge(request, payload) -> schema.Ok:
    uid = request.path_params["uid"]
    from_key = (payload.get("from") or payload.get("from_key") or "").strip()
    to_key = (payload.get("to") or payload.get("to_key") or "").strip()
    if not (from_key and to_key):
        raise ValueError("from and to are required")
    with connection.connect() as conn:
        if payload.get("delete"):
            _require(diagram_persist.delete_diagram_edge(conn, uid, from_key, to_key))
        else:
            _require(diagram_persist.upsert_diagram_edge(
                conn, uid, from_key, to_key, label=payload.get("label") or ""))
    return {"ok": True}


def diagram_layout(request, payload) -> schema.LayoutSaved:
    """Persist dragged positions and resized boxes -- nothing else.

    `reset_boxes` is the way back: a list of node keys, or true for the
    whole flow, drops the stored sizes so the shapes' defaults apply.
    """
    uid = request.path_params["uid"]
    positions = payload.get("positions")
    reset = payload.get("reset_boxes")
    if not isinstance(positions, dict) and reset is None:
        raise ValueError("positions must be an object of {node_key: {x, y, w?, h?}}")
    with connection.connect() as conn:
        _diagram_or_400(conn, uid)
        moved = diagram_persist.set_node_positions(conn, uid, positions) if positions else 0
        if reset is not None:
            moved += diagram_persist.reset_node_boxes(conn, uid, reset if isinstance(reset, list) else None)
    return {"ok": True, "moved": moved}


def diagram_relayout(request, payload) -> schema.LayoutSaved:
    """Throw away hand-dragged positions and rebuild the layered arrangement."""
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        _diagram_or_400(conn, uid)
        moved = diagram_persist.relayout_diagram(conn, uid)
    return {"ok": True, "moved": moved}


def diagram_link(request, payload) -> schema.Ok:
    uid = request.path_params["uid"]
    node_key = (payload.get("node_key") or "").strip()
    target_uid = (payload.get("target_uid") or "").strip()
    if not (node_key and target_uid):
        raise ValueError("node_key and target_uid are required")
    with connection.connect() as conn:
        if payload.get("delete"):
            if not diagram_persist.delete_node_link(conn, uid, node_key, target_uid):
                raise ValueError(f"no link from node '{node_key}' to {target_uid}")
        else:
            _require(diagram_persist.add_node_link(
                conn, uid, node_key, target_uid,
                (payload.get("relation_type") or "explains").strip()))
    return {"ok": True}


def diagram_jump(request, payload) -> schema.Ok:
    """Create or drop a jump from a step of this diagram into another one.

    `node_key` is always the step on THIS diagram and `peer_uid`/
    `peer_node` the other end -- the same shape get_diagram_jumps() hands
    the editor, so a row it drew can be deleted from whichever side it was
    read on. Creating is directional (this diagram jumps out); deleting is
    not (see db.delete_diagram_jump).
    """
    uid = request.path_params["uid"]
    node_key = (payload.get("node_key") or "").strip()
    peer_uid = (payload.get("peer_uid") or "").strip()
    peer_node = (payload.get("peer_node") or "").strip()
    if not peer_uid:
        raise ValueError("peer_uid is required")
    delete = bool(payload.get("delete"))
    # An empty node_key on this side means the whole diagram, which only a jump's receiving end
    # has, and it must be able to cut it; creating one still names the step it leaves from.
    if not (node_key or delete):
        raise ValueError("node_key is required")
    with connection.connect() as conn:
        if delete:
            if not diagram_persist.delete_diagram_jump(conn, uid, node_key, peer_uid, peer_node):
                raise ValueError(f"no jump between '{node_key}' and {peer_uid}")
        else:
            _require(diagram_persist.add_diagram_jump(
                conn, uid, node_key, peer_uid, peer_node,
                label=(payload.get("label") or "").strip()))
    return {"ok": True}


def diagram_mermaid(request, payload) -> schema.Mermaid:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        _diagram_or_400(conn, uid)
        return {"uid": uid, "mermaid": diagram_persist.render_diagram_mermaid(conn, uid)}


ROUTES = [
    Route("/api/diagrams", api(diagram_list), methods=["GET"]),
    Route("/api/diagrams", api(diagram_create), methods=["POST"]),
    Route("/api/diagrams/{uid}", api(diagram_detail), methods=["GET"]),
    Route("/api/diagrams/{uid}/graph", api(diagram_graph), methods=["POST"]),
    Route("/api/diagrams/{uid}/meta", api(diagram_meta), methods=["POST"]),
    Route("/api/diagrams/{uid}/node", api(diagram_node), methods=["POST"]),
    Route("/api/diagrams/{uid}/edge", api(diagram_edge), methods=["POST"]),
    Route("/api/diagrams/{uid}/layout", api(diagram_layout), methods=["POST"]),
    Route("/api/diagrams/{uid}/relayout", api(diagram_relayout), methods=["POST"]),
    Route("/api/diagrams/{uid}/link", api(diagram_link), methods=["POST"]),
    Route("/api/diagrams/{uid}/jump", api(diagram_jump), methods=["POST"]),
    Route("/api/diagrams/{uid}/mermaid", api(diagram_mermaid), methods=["GET"]),
]
