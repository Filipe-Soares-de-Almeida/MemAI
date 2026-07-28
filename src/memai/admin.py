"""memai admin dashboard -- local web UI over the memory store.

A Starlette + uvicorn app (both already shipped as dependencies of the
`mcp` SDK, so this adds no new requirements) exposing a JSON API over
db.py plus a static single-page UI (webui/). It is a *maintenance*
surface: everything the MCP tools can do, plus operations that only
make sense for a human curator -- bulk confidence triage, domain
renames/merges, relation pruning, dedup review, FTS/vector rebuilds,
VACUUM/backup, and an audit trail over the edits table.

Handlers do blocking SQLite work directly inside async endpoints; this
is deliberate. The server is a single-user localhost tool, requests
are short (the store is a few MB), and staying synchronous end-to-end
preserves db.py's one-transaction-per-connect model: an exception
before the context manager exits means nothing is committed.

Destructive parity with the MCP tools is kept: archive (forget) is the
default "delete", and purge demands the literal confirmation phrase
"DELETE <uid>" typed by the operator, same guardrail as server.py.

Run with `memai-admin` (default http://127.0.0.1:8765); binds to
loopback unless --host says otherwise. Honors MEMAI_HOME.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from memai import db, embed

# Windows' registry-derived mimetypes map serves .js as text/plain, which
# browsers refuse to execute as an ES module. Force the correct types.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

WEBUI_DIR = Path(__file__).parent / "webui"
SNIPPET_LIMIT = 280
DEDUP_SNIPPET = 480

KNOWN_TYPES = ("note", "checkpoint", "anti_pattern", "reasoning", "handoff", "diagram")
CONFIDENCES = ("unverified", "confirmed", "contradicted")
STATUSES = ("active", "archived")

# The relations graph is laid out in the browser by an O(n^2) force
# simulation, so handing over the whole store freezes the tab rather than
# drawing anything. Most-connected first, because a graph of unconnected
# dots is the useless half of a big store.
GRAPH_NODE_CAP = 400

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


# ---------------------------------------------------------------- helpers

def _snip(text: str, limit: int = SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _summary(row, limit: int = SNIPPET_LIMIT) -> dict:
    d = dict(row)
    d["content_len"] = len(d.get("content", ""))
    d["content"] = _snip(d.get("content", ""), limit)
    return d


def _peer_card(conn: sqlite3.Connection, uid: str) -> dict | None:
    row = db.get_memory(conn, uid)
    if row is None:
        return None
    return {
        "uid": row["uid"], "type": row["type"], "domain": row["domain"],
        "status": row["status"], "confidence": row["confidence"],
        "snippet": _snip(row["content"], 160), "created_at": row["created_at"],
    }


def _int_param(request, name: str, default: int, lo: int, hi: int) -> int:
    try:
        val = int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _backups_dir() -> Path:
    d = db.default_db_path().parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _raw_connect() -> sqlite3.Connection:
    """Autocommit connection for statements that refuse to run inside a
    transaction (VACUUM, VACUUM INTO, wal_checkpoint)."""
    return sqlite3.connect(str(db.default_db_path()), timeout=30.0, isolation_level=None)


def api(handler):
    """Wrap a sync (request, payload) handler into an async JSON endpoint.

    ValueError -> 400 with the message (validation/guardrail failures);
    anything else -> 500. Body is parsed as JSON for mutating methods.
    """
    async def endpoint(request):
        payload = {}
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
        try:
            return JSONResponse(handler(request, payload))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # pragma: no cover - defensive
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    return endpoint


# ---------------------------------------------------------------- overview

def overview(request, payload) -> dict:
    dbfile = db.default_db_path()
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM memories GROUP BY status").fetchall())
        by_type = dict(conn.execute(
            "SELECT type, COUNT(*) FROM memories WHERE status='active' GROUP BY type").fetchall())
        by_confidence = dict(conn.execute(
            "SELECT confidence, COUNT(*) FROM memories WHERE status='active' GROUP BY confidence").fetchall())
        relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        edits = conn.execute("SELECT COUNT(*) FROM edits").fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session) FROM memories WHERE session <> ''").fetchone()[0]
        activity = [
            {"day": r[0], "count": r[1]}
            for r in reversed(conn.execute(
                """SELECT substr(created_at, 1, 10) AS day, COUNT(*)
                   FROM memories GROUP BY day ORDER BY day DESC LIMIT 45""").fetchall())
        ]
        domains = [dict(r) for r in db.list_domains(conn)]
        recent = [_summary(r, 150) for r in db.list_recent(conn, limit=8)]
        vec_ok = db._vec_ready(conn)
        vec_rows = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0] if vec_ok else 0
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    return {
        "totals": {
            "memories": total,
            "active": by_status.get("active", 0),
            "archived": by_status.get("archived", 0),
            "relations": relations,
            "edits": edits,
            "sessions": sessions,
            "domains": len(domains),
        },
        "by_type": by_type,
        "by_confidence": by_confidence,
        "activity": activity,
        "domains": domains[:10],
        "recent": recent,
        "db": {
            "path": str(dbfile),
            "size": _file_size(dbfile),
            "wal_size": _file_size(dbfile.with_name(dbfile.name + "-wal")),
            "embed_model": meta.get("embed_model", ""),
            "embed_dim": meta.get("embed_dim", ""),
            "embed_available": embed.embedding_dim() is not None,
            "vec_rows": vec_rows,
            "vec_ready": vec_ok,
        },
    }


# ---------------------------------------------------------------- memories

def list_memories(request, payload) -> dict:
    qp = request.query_params
    q = qp.get("q", "").strip()
    domain = qp.get("domain", "")
    type_ = qp.get("type", "")
    status = qp.get("status", "")           # "" = all
    confidence = qp.get("confidence", "")
    session = qp.get("session", "")
    sort = qp.get("sort", "created_at")
    if sort not in ("created_at", "updated_at"):
        sort = "created_at"
    direction = "ASC" if qp.get("dir", "desc").lower() == "asc" else "DESC"
    limit = _int_param(request, "limit", 50, 1, 200)
    offset = _int_param(request, "offset", 0, 0, 1_000_000)

    with db.connect() as conn:
        if q:
            hits = db.search_hybrid(conn, q, domain=domain, type=type_, status=status, limit=200)
            if confidence:
                hits = [h for h in hits if h["confidence"] == confidence]
            if session:
                hits = [h for h in hits if h["session"] == session]
            total = len(hits)
            items = [_summary(h) for h in hits[offset:offset + limit]]
            return {"total": total, "items": items, "searched": True}

        where, params = ["1=1"], []
        for field, value in (("domain", domain), ("type", type_),
                             ("status", status), ("confidence", confidence),
                             ("session", session)):
            if value:
                where.append(f"AND {field} = ?")
                params.append(value)
        clause = " ".join(where)
        total = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM memories WHERE {clause} ORDER BY {sort} {direction} LIMIT ? OFFSET ?",
            [*params, limit, offset]).fetchall()
    return {"total": total, "items": [_summary(r) for r in rows], "searched": False}


def memory_detail(request, payload) -> dict:
    uid = request.path_params["uid"]
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            raise ValueError(f"unknown memory: {uid}")
        result = dict(row)
        result["edit_history"] = [dict(e) for e in db.get_edit_history(conn, uid)]
        rels = []
        for r in db.get_relations(conn, uid):
            other = r["to_uid"] if r["from_uid"] == uid else r["from_uid"]
            rels.append({
                **dict(r),
                "direction": "out" if r["from_uid"] == uid else "in",
                "peer": _peer_card(conn, other) or {"uid": other, "missing": True},
            })
        result["relations"] = rels
        if result.get("superseded_by"):
            result["superseded_by_peer"] = _peer_card(conn, result["superseded_by"])
        if row["type"] == db.DIAGRAM_TYPE:
            result["diagram"] = _diagram_json(conn, uid)
        else:
            result["referenced_by_diagrams"] = [
                dict(r) for r in db.diagrams_referencing(conn, uid)
            ]
    return result


def create_memory(request, payload) -> dict:
    type_ = (payload.get("type") or "").strip()
    content = (payload.get("content") or "").strip()
    confidence = payload.get("confidence") or "unverified"
    if not type_:
        raise ValueError("type is required")
    if type_ not in KNOWN_TYPES:
        raise ValueError(f"type must be one of {KNOWN_TYPES}")
    if type_ == db.DIAGRAM_TYPE:
        # a diagram row with no graph behind it is a broken half-state: its
        # content is generated, so there would be nothing to generate from
        raise ValueError("create a diagram through POST /api/diagrams -- it needs a graph")
    if not content:
        raise ValueError("content is required")
    if confidence not in CONFIDENCES:
        raise ValueError(f"confidence must be one of {CONFIDENCES}")
    with db.connect() as conn:
        uid = db.insert_memory(
            conn, type=type_, content=content,
            domain=(payload.get("domain") or "").strip(),
            session=(payload.get("session") or "").strip(),
            tags=(payload.get("tags") or "").strip(),
            confidence=confidence,
        )
    return {"uid": uid}


def edit_content(request, payload) -> dict:
    uid = request.path_params["uid"]
    content = payload.get("content", "")
    if not content.strip():
        raise ValueError("content cannot be empty")
    with db.connect() as conn:
        if db.is_diagram(conn, uid):
            raise ValueError(
                "this memory is a diagram: its content is generated from the graph, "
                "so a hand-written version would be overwritten by the next change. "
                "Edit the flow instead."
            )
        ok = db.update_memory_content(conn, uid, content, note=payload.get("note", ""))
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def edit_meta(request, payload) -> dict:
    """Update domain/tags/session/type. Domain or tags changes re-embed the
    row (the vector is computed over content+tags+domain) and every change
    leaves an audit entry in edits, so curation stays traceable."""
    uid = request.path_params["uid"]
    allowed = ("domain", "tags", "session", "type")
    updates = {k: str(payload[k]).strip() for k in allowed if k in payload}
    if not updates:
        raise ValueError(f"nothing to update (fields: {allowed})")
    if "type" in updates and not updates["type"]:
        raise ValueError("type cannot be empty")
    if "type" in updates and updates["type"] not in KNOWN_TYPES:
        raise ValueError(f"type must be one of {KNOWN_TYPES}")
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            raise ValueError(f"unknown memory: {uid}")
        if "type" in updates and updates["type"] != row["type"] and (
            db.DIAGRAM_TYPE in (updates["type"], row["type"])
        ):
            # retyping away from 'diagram' orphans the graph; retyping into
            # it claims a generated content field with nothing generating it
            raise ValueError("a diagram's type cannot be changed")
        if "domain" in updates:
            updates["domain"] = db.apply_domain_case(conn, updates["domain"])
        changed = {k: v for k, v in updates.items() if v != row[k]}
        if not changed:
            return {"ok": True, "changed": []}
        sets = ", ".join(f"{k} = ?" for k in changed)
        conn.execute(
            f"UPDATE memories SET {sets}, updated_at = ? WHERE uid = ?",
            [*changed.values(), db.now_iso(), uid])
        note = "meta: " + "; ".join(f"{k} '{row[k]}' → '{v}'" for k, v in changed.items())
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
            (uid, db.now_iso(), row["content"], row["content"], note))
        if "domain" in changed or "tags" in changed:
            db._upsert_vector(
                conn, row["rowid_pk"], row["content"],
                changed.get("tags", row["tags"]), changed.get("domain", row["domain"]))
    return {"ok": True, "changed": list(changed)}


def edit_confidence(request, payload) -> dict:
    uid = request.path_params["uid"]
    confidence = payload.get("confidence", "")
    if confidence not in CONFIDENCES:
        raise ValueError(f"confidence must be one of {CONFIDENCES}")
    with db.connect() as conn:
        ok = db.set_confidence(conn, uid, confidence)
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def edit_status(request, payload) -> dict:
    uid = request.path_params["uid"]
    status = payload.get("status", "")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    reason = (payload.get("reason") or "").strip()
    verb = "archived" if status == "archived" else "restored"
    with db.connect() as conn:
        ok = db.set_status(
            conn, uid, status,
            superseded_by=(payload.get("superseded_by") or "").strip() or None,
            note=f"{verb}: {reason}" if reason else "")
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def purge(request, payload) -> dict:
    """Same guardrail as the MCP purge_memory tool: the operator must type
    the literal phrase 'DELETE <uid>' -- the UI never pre-fills it."""
    uid = request.path_params["uid"]
    expected = f"DELETE {uid}"
    if payload.get("confirm", "") != expected:
        raise ValueError(f"confirm phrase must exactly equal '{expected}'")
    with db.connect() as conn:
        ok = db.purge_memory(conn, uid)
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def bulk(request, payload) -> dict:
    uids = payload.get("uids") or []
    action = payload.get("action", "")
    if not isinstance(uids, list) or not uids:
        raise ValueError("uids must be a non-empty list")
    if len(uids) > 500:
        raise ValueError("at most 500 uids per operation")
    reason = (payload.get("reason") or "").strip()
    done = 0
    with db.connect() as conn:
        for uid in uids:
            if action == "confidence":
                value = payload.get("value", "")
                if value not in CONFIDENCES:
                    raise ValueError(f"value must be one of {CONFIDENCES}")
                done += 1 if db.set_confidence(conn, uid, value) else 0
            elif action == "archive":
                done += 1 if db.set_status(
                    conn, uid, "archived",
                    note=f"archived: {reason}" if reason else "") else 0
            elif action == "restore":
                done += 1 if db.set_status(
                    conn, uid, "active",
                    note=f"restored: {reason}" if reason else "") else 0
            else:
                raise ValueError("action must be confidence|archive|restore")
    return {"ok": True, "affected": done}


# ---------------------------------------------------------------- relations

def create_relation(request, payload) -> dict:
    from_uid = (payload.get("from_uid") or "").strip()
    to_uid = (payload.get("to_uid") or "").strip()
    rel_type = (payload.get("relation_type") or "").strip()
    if not (from_uid and to_uid and rel_type):
        raise ValueError("from_uid, to_uid and relation_type are required")
    if from_uid == to_uid:
        raise ValueError("a memory cannot relate to itself")
    with db.connect() as conn:
        for uid in (from_uid, to_uid):
            if db.get_memory(conn, uid) is None:
                raise ValueError(f"unknown memory: {uid}")
        dup = conn.execute(
            "SELECT id FROM relations WHERE from_uid = ? AND to_uid = ? AND relation_type = ?",
            (from_uid, to_uid, rel_type)).fetchone()
        if dup:
            raise ValueError(f"identical relation already exists (id {dup['id']})")
        rel_id = db.add_relation(conn, from_uid, to_uid, rel_type, note=payload.get("note", ""))
    return {"relation_id": rel_id}


def delete_relation(request, payload) -> dict:
    rel_id = request.path_params["rel_id"]
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM relations WHERE id = ?", (rel_id,))
    if cur.rowcount == 0:
        raise ValueError(f"unknown relation: {rel_id}")
    return {"ok": True}


def graph(request, payload) -> dict:
    qp = request.query_params
    status = qp.get("status", "active")
    domain = qp.get("domain", "")
    type_ = qp.get("type", "")
    where, params = ["1=1"], []
    for field, value in (("status", status), ("domain", domain), ("type", type_)):
        if value:
            where.append(f"AND {field} = ?")
            params.append(value)
    limit = _int_param(request, "limit", GRAPH_NODE_CAP, 1, 2000)
    with db.connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {' '.join(where)}", params).fetchone()[0]
        # `deg` orders the cut, not the payload: the degree reported per
        # node below counts only edges between nodes that made it in, so
        # what the legend says matches what is drawn.
        rows = conn.execute(
            f"SELECT uid, type, domain, status, confidence, content, created_at, "
            f"       (SELECT COUNT(*) FROM relations r "
            f"        WHERE r.from_uid = memories.uid OR r.to_uid = memories.uid) AS deg "
            f"FROM memories WHERE {' '.join(where)} "
            f"ORDER BY deg DESC, created_at DESC LIMIT ?", [*params, limit]).fetchall()
        uids = {r["uid"] for r in rows}
        edges = [
            dict(r) for r in conn.execute(
                "SELECT id, from_uid, to_uid, relation_type, note FROM relations").fetchall()
            if r["from_uid"] in uids and r["to_uid"] in uids
        ]
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["from_uid"]] = degree.get(e["from_uid"], 0) + 1
        degree[e["to_uid"]] = degree.get(e["to_uid"], 0) + 1
    nodes = [{
        "uid": r["uid"], "type": r["type"], "domain": r["domain"],
        "status": r["status"], "confidence": r["confidence"],
        "label": _snip(r["content"].split("\n", 1)[0], 90),
        "degree": degree.get(r["uid"], 0),
        "created_at": r["created_at"],
    } for r in rows]
    # A cap that says nothing reads as "this is everything".
    return {"nodes": nodes, "edges": edges,
            "total": total, "truncated": total > len(nodes)}


# ---------------------------------------------------------------- diagrams

def _require(result: tuple) -> object:
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
    data = db.get_diagram(conn, uid)
    if data is None:
        return None
    for link in data["links"]:
        link["peer"] = _peer_card(conn, link["target_uid"]) or {
            "uid": link["target_uid"], "missing": True,
        }
        link.pop("target_content", None)  # the peer card already carries a snippet
    data["mermaid"] = db.render_diagram_mermaid(conn, uid)
    return data


def _diagram_or_400(conn: sqlite3.Connection, uid: str) -> None:
    if db.get_diagram_row(conn, uid) is None:
        raise ValueError(f"unknown diagram: {uid}")


def diagram_list(request, payload) -> dict:
    """Every diagram with its size and its structural problems.

    Backs the dedicated diagram view: a flow is maintained by fixing its
    shape, which the confidence/dedup tooling for prose cannot see.
    """
    status = request.query_params.get("status", "active")
    domain = request.query_params.get("domain", "")
    with db.connect() as conn:
        items = db.diagram_overview(conn, domain=domain, status=status)
    return {
        "total": len(items),
        "with_issues": sum(1 for d in items if d["issues"]),
        "items": items,
    }


def diagram_detail(request, payload) -> dict:
    uid = request.path_params["uid"]
    with db.connect() as conn:
        data = _diagram_json(conn, uid)
    if data is None:
        raise ValueError(f"unknown diagram: {uid}")
    return data


def diagram_create(request, payload) -> dict:
    with db.connect() as conn:
        uid = _require(db.insert_diagram(
            conn,
            title=(payload.get("title") or "").strip(),
            nodes=payload.get("nodes") or [],
            edges=payload.get("edges") or [],
            summary=(payload.get("summary") or "").strip(),
            kind=(payload.get("kind") or "flowchart").strip(),
            domain=(payload.get("domain") or "").strip(),
            session=(payload.get("session") or "").strip(),
            tags=(payload.get("tags") or "").strip(),
        ))
    return {"uid": uid}


def diagram_graph(request, payload) -> dict:
    """Replace the whole graph; surviving nodes keep their positions."""
    uid = request.path_params["uid"]
    with db.connect() as conn:
        _require(db.replace_diagram_graph(
            conn, uid, payload.get("nodes") or [], payload.get("edges") or []))
    return {"ok": True}


def diagram_meta(request, payload) -> dict:
    uid = request.path_params["uid"]
    if not {"title", "summary", "font_scale"} & set(payload):
        raise ValueError("nothing to update (fields: title, summary, font_scale)")
    with db.connect() as conn:
        _require(db.set_diagram_meta(
            conn, uid,
            title=payload.get("title") if "title" in payload else None,
            summary=payload.get("summary") if "summary" in payload else None,
            font_scale=payload.get("font_scale") if "font_scale" in payload else None,
        ))
    return {"ok": True}


def diagram_node(request, payload) -> dict:
    uid = request.path_params["uid"]
    key = (payload.get("key") or "").strip()
    if not key:
        raise ValueError("key is required")
    with db.connect() as conn:
        if payload.get("delete"):
            _require(db.delete_diagram_node(conn, uid, key))
        else:
            _require(db.upsert_diagram_node(
                conn, uid, key, label=payload.get("label"),
                shape=payload.get("shape"), note=payload.get("note")))
    return {"ok": True, "key": key}


def diagram_edge(request, payload) -> dict:
    uid = request.path_params["uid"]
    from_key = (payload.get("from") or payload.get("from_key") or "").strip()
    to_key = (payload.get("to") or payload.get("to_key") or "").strip()
    if not (from_key and to_key):
        raise ValueError("from and to are required")
    with db.connect() as conn:
        if payload.get("delete"):
            _require(db.delete_diagram_edge(conn, uid, from_key, to_key))
        else:
            _require(db.upsert_diagram_edge(
                conn, uid, from_key, to_key, label=payload.get("label") or ""))
    return {"ok": True}


def diagram_layout(request, payload) -> dict:
    """Persist dragged positions and resized boxes -- nothing else.

    `reset_boxes` is the way back: a list of node keys, or true for the
    whole flow, drops the stored sizes so the shapes' defaults apply.
    """
    uid = request.path_params["uid"]
    positions = payload.get("positions")
    reset = payload.get("reset_boxes")
    if not isinstance(positions, dict) and reset is None:
        raise ValueError("positions must be an object of {node_key: {x, y, w?, h?}}")
    with db.connect() as conn:
        _diagram_or_400(conn, uid)
        moved = db.set_node_positions(conn, uid, positions) if positions else 0
        if reset is not None:
            moved += db.reset_node_boxes(conn, uid, reset if isinstance(reset, list) else None)
    return {"ok": True, "moved": moved}


def diagram_relayout(request, payload) -> dict:
    """Throw away hand-dragged positions and rebuild the layered arrangement."""
    uid = request.path_params["uid"]
    with db.connect() as conn:
        _diagram_or_400(conn, uid)
        moved = db.relayout_diagram(conn, uid)
    return {"ok": True, "moved": moved}


def diagram_link(request, payload) -> dict:
    uid = request.path_params["uid"]
    node_key = (payload.get("node_key") or "").strip()
    target_uid = (payload.get("target_uid") or "").strip()
    if not (node_key and target_uid):
        raise ValueError("node_key and target_uid are required")
    with db.connect() as conn:
        if payload.get("delete"):
            if not db.delete_node_link(conn, uid, node_key, target_uid):
                raise ValueError(f"no link from node '{node_key}' to {target_uid}")
        else:
            _require(db.add_node_link(
                conn, uid, node_key, target_uid,
                (payload.get("relation_type") or "explains").strip()))
    return {"ok": True}


def diagram_mermaid(request, payload) -> dict:
    uid = request.path_params["uid"]
    with db.connect() as conn:
        _diagram_or_400(conn, uid)
        return {"uid": uid, "mermaid": db.render_diagram_mermaid(conn, uid)}


# ---------------------------------------------------------------- domains

def domains(request, payload) -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT domain, status, type, COUNT(*) AS n, MAX(created_at) AS latest
               FROM memories WHERE domain <> ''
               GROUP BY domain, status, type""").fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        d = agg.setdefault(r["domain"], {
            "domain": r["domain"], "active": 0, "archived": 0,
            "types": {}, "latest_at": ""})
        if r["status"] == "active":
            d["active"] += r["n"]
        else:
            d["archived"] += r["n"]
        d["types"][r["type"]] = d["types"].get(r["type"], 0) + r["n"]
        d["latest_at"] = max(d["latest_at"], r["latest"])
    by_lower: dict[str, list[str]] = {}
    for name in agg:
        by_lower.setdefault(name.strip().lower(), []).append(name)
    for names in by_lower.values():
        if len(names) > 1:
            for n in names:
                agg[n]["collides_with"] = [x for x in names if x != n]
    result = sorted(agg.values(), key=lambda d: d["latest_at"], reverse=True)
    return {"domains": result}


def _rename_domain_rows(conn: sqlite3.Connection, src: str, dst: str) -> int:
    """Move every memory from domain `src` to `dst`: UPDATE + audit + re-embed.

    Domain is part of the embedding source, so each row is re-embedded.
    If `dst` already has rows this is a merge. Returns the count moved.
    """
    rows = conn.execute(
        "SELECT rowid_pk, uid, content, tags FROM memories WHERE domain = ?", (src,)).fetchall()
    now = db.now_iso()
    conn.execute(
        "UPDATE memories SET domain = ?, updated_at = ? WHERE domain = ?", (dst, now, src))
    for r in rows:
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
            (r["uid"], now, r["content"], r["content"], f"meta: domain '{src}' → '{dst}'"))
        db._upsert_vector(conn, r["rowid_pk"], r["content"], r["tags"], dst)
    return len(rows)


def rename_domain(request, payload) -> dict:
    """Rename or merge a domain. Every affected row is re-embedded (domain
    is part of the embedding source) and audited in edits."""
    src = (payload.get("from") or "").strip()
    dst = (payload.get("to") or "").strip()
    if not src:
        raise ValueError("'from' is required")
    if not dst:
        raise ValueError("'to' is required")
    if src == dst:
        raise ValueError("source and target are the same")
    with db.connect() as conn:
        exists = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE domain = ?", (src,)).fetchone()[0] > 0
        if not exists:
            raise ValueError(f"no memories in domain '{src}'")
        merged = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE domain = ?", (dst,)).fetchone()[0] > 0
        affected = _rename_domain_rows(conn, src, dst)
    return {"ok": True, "affected": affected, "merged": merged}


def _normalize_plan(mode: str, counts: dict[str, int]) -> list[dict]:
    """Compute the per-domain moves that bring `counts` in line with `mode`.

    Each entry: {from, to, count, action}. action is 'merge' when the
    target already exists or more than one source collapses into it,
    otherwise 'rename'. Domains that already conform are omitted.
    """
    existing = set(counts)
    targets: dict[str, list[str]] = {}
    for d in counts:
        targets.setdefault(db.case_domain(mode, d), []).append(d)
    plan: list[dict] = []
    for target, srcs in targets.items():
        changing = [s for s in srcs if s != target]
        if not changing:
            continue
        merge = (target in existing) or len(srcs) > 1
        for s in changing:
            plan.append({
                "from": s, "to": target, "count": counts[s],
                "action": "merge" if merge else "rename",
            })
    return sorted(plan, key=lambda e: e["from"].lower())


def normalize_domains(request, payload) -> dict:
    """Bring already-stored domains in line with the casing policy.

    dry_run (default true) returns the plan for preview -- what renames
    and what merges -- without touching data. dry_run=false applies it,
    reusing the rename/merge path (UPDATE + audit + re-embed). No-op when
    the policy is 'preserve' or everything already conforms.
    """
    dry_run = bool(payload.get("dry_run", True))
    with db.connect() as conn:
        mode = db.get_domain_case(conn)
        counts = {
            r["domain"]: r["n"] for r in conn.execute(
                "SELECT domain, COUNT(*) AS n FROM memories WHERE domain <> '' GROUP BY domain").fetchall()
        }
        plan = _normalize_plan(mode, counts)
        if dry_run:
            return {"mode": mode, "dry_run": True, "plan": plan,
                    "renames": sum(1 for e in plan if e["action"] == "rename"),
                    "merges": sum(1 for e in plan if e["action"] == "merge")}
        affected = sum(_rename_domain_rows(conn, e["from"], e["to"]) for e in plan)
    return {"ok": True, "mode": mode, "moved": len(plan), "affected": affected}


# ------------------------------------------------------------------ config

def get_config(request, payload) -> dict:
    with db.connect() as conn:
        return {"domain_case": db.get_domain_case(conn)}


def set_config(request, payload) -> dict:
    mode = payload.get("domain_case")
    if mode is None:
        raise ValueError("domain_case is required")
    with db.connect() as conn:
        return {"domain_case": db.set_domain_case(conn, mode)}


# ------------------------------------------------------------- maintenance

def _fts_check(conn: sqlite3.Connection) -> tuple[bool, str]:
    """FTS5 integrity-check; the 2-arg form also verifies the index against
    the external content table where supported.

    `detail` is empty when the check passes: the "all good" wording is a UI
    string and belongs in webui/i18n, not in an API response. Only the
    failure detail crosses the wire, because that is SQLite's own message
    and translating it would lose the thing an operator needs to read.
    """
    try:
        try:
            conn.execute("INSERT INTO memories_fts(memories_fts, rank) VALUES ('integrity-check', 1)")
        except sqlite3.OperationalError:
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
        return True, ""
    except sqlite3.DatabaseError as exc:
        return False, str(exc)


def health(request, payload) -> dict:
    dbfile = db.default_db_path()
    with db.connect() as conn:
        quick = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
        integrity_ok = quick == ["ok"]
        fts_ok, fts_detail = _fts_check(conn)
        mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        vec_ready = db._vec_ready(conn)
        vec_count = missing_vec = orphan_vec = 0
        if vec_ready:
            vec_count = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
            missing_vec = conn.execute(
                """SELECT COUNT(*) FROM memories
                   WHERE rowid_pk NOT IN (SELECT rowid FROM memories_vec)""").fetchone()[0]
            orphan_vec = conn.execute(
                """SELECT COUNT(*) FROM memories_vec
                   WHERE rowid NOT IN (SELECT rowid_pk FROM memories)""").fetchone()[0]
        orphan_rels = conn.execute(
            """SELECT COUNT(*) FROM relations
               WHERE from_uid NOT IN (SELECT uid FROM memories)
                  OR to_uid NOT IN (SELECT uid FROM memories)""").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    backups = sorted(
        ({"name": p.name, "size": _file_size(p),
          "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()}
         for p in _backups_dir().glob("*.db")),
        key=lambda b: b["name"], reverse=True)
    return {
        # same rule as _fts_check: "ok" is quick_check's way of saying
        # nothing is wrong, and the UI has its own words for that
        "integrity": {"ok": integrity_ok,
                      "detail": "" if integrity_ok else "; ".join(quick)[:400]},
        "fts": {"ok": fts_ok, "detail": fts_detail, "rows": fts_count, "expected": mem_count},
        "vectors": {
            "ready": vec_ready, "rows": vec_count, "missing": missing_vec,
            "orphans": orphan_vec, "expected": mem_count,
            "model": meta.get("embed_model", ""), "dim": meta.get("embed_dim", ""),
            "model_available": embed.embedding_dim() is not None,
        },
        "relations": {"orphans": orphan_rels},
        "file": {
            "path": str(dbfile),
            "size": _file_size(dbfile),
            "wal_size": _file_size(dbfile.with_name(dbfile.name + "-wal")),
            "reclaimable": page_size * freelist,
        },
        "backups": backups[:12],
    }


def fts_rebuild(request, payload) -> dict:
    with db.connect() as conn:
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
        count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    return {"ok": True, "rows": count}


def reembed(request, payload) -> dict:
    """mode=missing backfills absent vectors; mode=all drops and rebuilds
    every vector (useful after content surgery or a model change)."""
    mode = payload.get("mode", "missing")
    if mode not in ("missing", "all"):
        raise ValueError("mode must be missing|all")
    if embed.embedding_dim() is None:
        raise ValueError("embedding model unavailable in this process")
    with db.connect() as conn:
        if not db._vec_ready(conn):
            raise ValueError("sqlite-vec extension unavailable")
        if mode == "all":
            conn.execute("DELETE FROM memories_vec")
        before = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
        db._ensure_vec(conn)
        after = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
    return {"ok": True, "embedded": after - before, "total": after}


def clean_orphans(request, payload) -> dict:
    with db.connect() as conn:
        cur = conn.execute(
            """DELETE FROM relations
               WHERE from_uid NOT IN (SELECT uid FROM memories)
                  OR to_uid NOT IN (SELECT uid FROM memories)""")
        rels = cur.rowcount
        vecs = 0
        if db._vec_ready(conn):
            cur = conn.execute(
                "DELETE FROM memories_vec WHERE rowid NOT IN (SELECT rowid_pk FROM memories)")
            vecs = cur.rowcount
        cur = conn.execute(
            """DELETE FROM optimization_suggestions
               WHERE status = 'pending'
                 AND target_uid IS NOT NULL
                 AND target_uid NOT IN (SELECT uid FROM memories)""")
        sugs = cur.rowcount
        cur = conn.execute(
            """DELETE FROM diagram_node_links
               WHERE memory_uid NOT IN (SELECT uid FROM memories)
                  OR target_uid NOT IN (SELECT uid FROM memories)
                  OR node_key NOT IN (
                        SELECT node_key FROM diagram_nodes
                        WHERE diagram_nodes.memory_uid = diagram_node_links.memory_uid)""")
        links = cur.rowcount
    return {"ok": True, "relations_removed": rels, "vectors_removed": vecs,
            "suggestions_removed": sugs, "node_links_removed": links}


def vacuum(request, payload) -> dict:
    dbfile = db.default_db_path()
    before = _file_size(dbfile) + _file_size(dbfile.with_name(dbfile.name + "-wal"))
    conn = _raw_connect()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    finally:
        conn.close()
    after = _file_size(dbfile) + _file_size(dbfile.with_name(dbfile.name + "-wal"))
    return {"ok": True, "before": before, "after": after}


def backup(request, payload) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = _backups_dir() / f"memai-{stamp}.db"
    if dest.exists():
        raise ValueError(f"backup already exists: {dest.name}")
    conn = _raw_connect()
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    return {"ok": True, "path": str(dest), "size": _file_size(dest)}


def dedup(request, payload) -> dict:
    threshold = min(max(float(request.query_params.get("threshold", 0.6)), 0.3), 0.99)
    with db.connect() as conn:
        pairs = db.dedup_candidates(
            conn,
            domain=request.query_params.get("domain", ""),
            type=request.query_params.get("type", ""),
            threshold=threshold,
            limit=_int_param(request, "limit", 20, 1, 60))
        result = [{"a": _summary(a, DEDUP_SNIPPET), "b": _summary(b, DEDUP_SNIPPET),
                   "ratio": round(score, 3), "method": method} for a, b, score, method in pairs]
    return {"pairs": result, "threshold": threshold}


def audit(request, payload) -> dict:
    limit = _int_param(request, "limit", 100, 1, 400)
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT e.id, e.memory_uid, e.edited_at, e.note,
                      LENGTH(e.prev_content) AS prev_len, LENGTH(e.new_content) AS new_len,
                      (e.prev_content <> e.new_content) AS content_changed,
                      m.type, m.domain, m.status
               FROM edits e JOIN memories m ON m.uid = e.memory_uid
               ORDER BY e.edited_at DESC, e.id DESC LIMIT ?""", (limit,)).fetchall()
    return {"entries": [dict(r) for r in rows]}


def lookup(request, payload) -> dict:
    """Lightweight finder for the relation-target picker."""
    q = request.query_params.get("q", "").strip()
    exclude = request.query_params.get("exclude", "")
    with db.connect() as conn:
        if not q:
            rows = [dict(r) for r in db.list_recent(conn, limit=10, status="")]
        else:
            exact = db.get_memory(conn, q)
            # pure relevance here: the operator is choosing *any* memory to
            # attach, so lifting diagrams to the top would only be noise
            rows = [dict(exact)] if exact is not None else \
                db.search_hybrid(conn, q, status="", limit=10, diagrams_first=False)
    items = [{
        "uid": r["uid"], "type": r["type"], "domain": r["domain"],
        "status": r["status"], "snippet": _snip(r["content"], 110),
    } for r in rows if r["uid"] != exclude]
    return {"items": items}


# ---------------------------------------------------------------- optimization

def _suggestion_json(conn, row) -> dict:
    """Serialize a staged suggestion for the UI, decorated with target/peer cards."""
    d = {
        "id": row["id"], "run_id": row["run_id"], "kind": row["kind"],
        "target_uid": row["target_uid"], "rationale": row["rationale"],
        "verified": row["verified"], "status": row["status"],
        "decided_at": row["decided_at"], "created_at": row["created_at"],
        "payload": json.loads(row["payload"]) if row["payload"] else {},
    }
    if row["target_uid"]:
        target = _peer_card(conn, row["target_uid"])
        if target is not None:
            trow = db.get_memory(conn, row["target_uid"])
            target["tags"] = trow["tags"]
        d["target"] = target
    peers = {}
    for key in ("from_uid", "to_uid", "keep_uid", "drop_uid"):
        uid = d["payload"].get(key)
        if uid:
            peers[key] = _peer_card(conn, uid)
    if peers:
        d["peers"] = peers
    if row["kind"] == "distill":
        d["sources"] = [
            _peer_card(conn, u) or {"uid": u, "missing": True}
            for u in d["payload"].get("source_uids", [])
        ]
        if row["status"] == "applied" and row["prev_state"]:
            d["new_uid"] = json.loads(row["prev_state"]).get("new_uid")
    return d


def optimization_runs(request, payload) -> dict:
    with db.connect() as conn:
        rows = db.list_optimization_runs(conn)
        kind_rows = db.optimization_run_kind_counts(conn)
    kinds_by_run: dict[int, list[dict]] = {}
    for k in kind_rows:
        kinds_by_run.setdefault(k["run_id"], []).append(
            {"kind": k["kind"], "total": k["total"], "pending": k["pending"]}
        )
    runs = []
    for r in rows:
        d = dict(r)
        d["kinds"] = kinds_by_run.get(r["id"], [])
        runs.append(d)
    return {"runs": runs}


def optimization_suggestions(request, payload) -> dict:
    try:
        run_id = int(request.query_params.get("run", ""))
    except (TypeError, ValueError):
        raise ValueError("run query param (int) required")
    status = request.query_params.get("status", "")
    with db.connect() as conn:
        run = db.get_optimization_run(conn, run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        rows = db.get_optimization_suggestions(conn, run_id, status=status)
        items = [_suggestion_json(conn, r) for r in rows]
    return {"run": dict(run), "suggestions": items}


def _ensure_run_backup(run_id: int) -> str | None:
    """Take a whole-DB backup for a run once, before its first apply.

    VACUUM INTO can't run inside a transaction, so this uses a raw
    autocommit connection (like backup()) between two short db.connect()
    reads/writes. Returns the backup path (existing or freshly created).
    """
    with db.connect() as conn:
        run = db.get_optimization_run(conn, run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        if run["backup_path"]:
            return run["backup_path"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = _backups_dir() / f"optimize-run{run_id}-{stamp}.db"
    conn = _raw_connect()
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    with db.connect() as conn:
        db.set_run_backup(conn, run_id, str(dest))
    return str(dest)


def optimization_apply(request, payload) -> dict:
    sug_id = payload.get("id")
    if not isinstance(sug_id, int):
        raise ValueError("id (int) required")
    with db.connect() as conn:
        row = db.get_suggestion(conn, sug_id)
        if row is None:
            raise ValueError(f"unknown suggestion: {sug_id}")
        run_id = row["run_id"]
    backup = _ensure_run_backup(run_id)
    with db.connect() as conn:
        db.apply_suggestion(conn, sug_id)
    return {"ok": True, "backup": backup}


def optimization_apply_all(request, payload) -> dict:
    run_id = payload.get("run")
    if not isinstance(run_id, int):
        raise ValueError("run (int) required")
    kind = payload.get("kind", "")
    if not isinstance(kind, str):
        raise ValueError("kind must be a string")
    with db.connect() as conn:
        run = db.get_optimization_run(conn, run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        pending = db.get_optimization_suggestions(conn, run_id, status="pending", kind=kind)
    if not pending:
        return {"ok": True, "applied": 0, "failed": [], "backup": run["backup_path"]}
    backup = _ensure_run_backup(run_id)
    applied, failed = 0, []
    for s in pending:
        try:
            with db.connect() as conn:
                db.apply_suggestion(conn, s["id"])
            applied += 1
        except ValueError as e:
            failed.append({"id": s["id"], "error": str(e)})
    return {"ok": True, "applied": applied, "failed": failed, "backup": backup}


def optimization_reject(request, payload) -> dict:
    sug_id = payload.get("id")
    if not isinstance(sug_id, int):
        raise ValueError("id (int) required")
    with db.connect() as conn:
        db.reject_suggestion(conn, sug_id)
    return {"ok": True}


def optimization_revert(request, payload) -> dict:
    sug_id = payload.get("id")
    if not isinstance(sug_id, int):
        raise ValueError("id (int) required")
    with db.connect() as conn:
        db.revert_suggestion(conn, sug_id)
    return {"ok": True}


def optimization_delete_run(request, payload) -> dict:
    run_id = request.path_params["run_id"]
    with db.connect() as conn:
        ok = db.delete_optimization_run(conn, run_id)
    if not ok:
        raise ValueError(f"unknown run: {run_id}")
    return {"ok": True}


# ---------------------------------------------------------------- wiring

async def index(request):
    return FileResponse(WEBUI_DIR / "index.html")


# (family, weight, filename, names an installed copy may go by)
WEBFONTS = (
    ("Roboto", 400, "roboto-400.woff2", ("Roboto", "Roboto Regular")),
    ("Roboto", 500, "roboto-500.woff2", ("Roboto Medium", "Roboto")),
    ("Roboto", 700, "roboto-700.woff2", ("Roboto Bold", "Roboto")),
    ("Roboto Mono", 400, "roboto-mono-400.woff2", ("Roboto Mono", "Roboto Mono Regular")),
    ("Roboto Mono", 500, "roboto-mono-500.woff2", ("Roboto Mono Medium", "Roboto Mono")),
    ("Roboto Mono", 600, "roboto-mono-600.woff2", ("Roboto Mono SemiBold", "Roboto Mono")),
)


async def fonts_css(request):
    """@font-face rules, naming a file only when that file is really there.

    Populating webui/fonts/ is optional (tools/fetch-fonts.py) -- the UI is
    designed to fall back to an installed copy and then to the system stack.
    But a url() in a static stylesheet is a request whether the file exists
    or not, so the optional half was logging 404s in the console for
    something working exactly as intended. Generating the rules means a
    missing face gets a local()-only src: still used if it is installed,
    never fetched, and quiet either way.
    """
    fonts_dir = WEBUI_DIR / "fonts"
    lines = ["/* generated by memai.admin -- populate with tools/fetch-fonts.py */"]
    for family, weight, filename, local_names in WEBFONTS:
        src = [f"local('{name}')" for name in local_names]
        if (fonts_dir / filename).is_file():
            src.append(f"url('/static/fonts/{filename}') format('woff2')")
        lines.append(
            f"@font-face {{ font-family: '{family}'; font-style: normal; "
            f"font-weight: {weight}; font-display: swap; src: {', '.join(src)}; }}")
    return Response("\n".join(lines) + "\n", media_type="text/css")


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Admin UI iterates often and is tiny; never let a browser cache it."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


def _same_origin(origin: str, request) -> bool:
    """Does `origin` name this very server, as the request reached it?"""
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc == request.headers.get("host", "")


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Keep another page in the browser from driving this server.

    There is no login here -- it is a single-user loopback tool -- so the
    browser is the only thing between a random web page you happen to
    visit and POST /api/maintenance/vacuum on your own machine. Two
    checks do that job:

    * Fetch metadata, then Origin. A browser labels every request with
      Sec-Fetch-Site, and any cross-origin one with Origin. A non-browser
      client (curl, the test suite) sends neither and is let through --
      it is not the threat, and it cannot be tricked by a web page.

    * application/json on a written body. A cross-origin POST escapes the
      CORS preflight only while it looks like a form: text/plain,
      multipart/form-data, application/x-www-form-urlencoded. Starlette's
      request.json() does not care about the content type, which is what
      made that a working attack -- so care here instead. Requiring JSON
      forces a preflight, and this server answers none.

    Neither check is a substitute for authentication. Do not put this on
    a network interface; see the warning in main().
    """

    WRITE_METHODS = ("POST", "PUT", "PATCH")

    async def dispatch(self, request, call_next):
        site = request.headers.get("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            return JSONResponse({"error": f"cross-origin request refused ({site})"},
                                status_code=403)
        origin = request.headers.get("origin")
        if origin and not _same_origin(origin, request):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        if request.method in self.WRITE_METHODS:
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse(
                    {"error": f"{request.method} requires Content-Type: application/json"},
                    status_code=415)
        return await call_next(request)


routes = [
    Route("/", index),
    Route("/fonts.css", fonts_css),
    Route("/api/overview", api(overview)),
    Route("/api/memories", api(list_memories), methods=["GET"]),
    Route("/api/memories", api(create_memory), methods=["POST"]),
    Route("/api/memories/{uid}", api(memory_detail), methods=["GET"]),
    Route("/api/memories/{uid}/content", api(edit_content), methods=["POST"]),
    Route("/api/memories/{uid}/meta", api(edit_meta), methods=["POST"]),
    Route("/api/memories/{uid}/confidence", api(edit_confidence), methods=["POST"]),
    Route("/api/memories/{uid}/status", api(edit_status), methods=["POST"]),
    Route("/api/memories/{uid}/purge", api(purge), methods=["POST"]),
    Route("/api/bulk", api(bulk), methods=["POST"]),
    Route("/api/relations", api(create_relation), methods=["POST"]),
    Route("/api/relations/{rel_id:int}", api(delete_relation), methods=["DELETE"]),
    Route("/api/graph", api(graph)),
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
    Route("/api/diagrams/{uid}/mermaid", api(diagram_mermaid), methods=["GET"]),
    Route("/api/config", api(get_config), methods=["GET"]),
    Route("/api/config", api(set_config), methods=["POST"]),
    Route("/api/domains", api(domains)),
    Route("/api/domains/rename", api(rename_domain), methods=["POST"]),
    Route("/api/domains/normalize", api(normalize_domains), methods=["POST"]),
    Route("/api/maintenance/health", api(health)),
    Route("/api/maintenance/fts-rebuild", api(fts_rebuild), methods=["POST"]),
    Route("/api/maintenance/reembed", api(reembed), methods=["POST"]),
    Route("/api/maintenance/clean-orphans", api(clean_orphans), methods=["POST"]),
    Route("/api/maintenance/vacuum", api(vacuum), methods=["POST"]),
    Route("/api/maintenance/backup", api(backup), methods=["POST"]),
    Route("/api/maintenance/dedup", api(dedup)),
    Route("/api/optimization/runs", api(optimization_runs), methods=["GET"]),
    Route("/api/optimization/runs/{run_id:int}", api(optimization_delete_run), methods=["DELETE"]),
    Route("/api/optimization/suggestions", api(optimization_suggestions), methods=["GET"]),
    Route("/api/optimization/apply", api(optimization_apply), methods=["POST"]),
    Route("/api/optimization/apply-all", api(optimization_apply_all), methods=["POST"]),
    Route("/api/optimization/reject", api(optimization_reject), methods=["POST"]),
    Route("/api/optimization/revert", api(optimization_revert), methods=["POST"]),
    Route("/api/audit", api(audit)),
    Route("/api/lookup", api(lookup)),
    Mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static"),
]

app = Starlette(routes=routes, middleware=[
    Middleware(SameOriginMiddleware),
    Middleware(NoCacheMiddleware),
])


def main() -> None:
    parser = argparse.ArgumentParser(description="memai admin dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MEMAI_ADMIN_PORT", "8765")))
    args = parser.parse_args()
    print(f"memai admin · db {db.default_db_path()} · http://{args.host}:{args.port}")
    if args.host not in LOOPBACK_HOSTS:
        print(f"  WARNING: {args.host} is not loopback. This API has NO authentication:"
              f"\n  anyone who can reach {args.host}:{args.port} can read, edit and"
              f"\n  permanently delete every memory in the store.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
