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
"DELETE <uid>" typed by the operator, same guardrail as server.py. A
whole domain reads the same way one memory does -- archiving it is the
reversible option, and deleting it asks for "DELETE <domain>".

Run with `memai-admin` (default http://127.0.0.1:8888); binds to
loopback unless --host says otherwise. Honors MEMAI_HOME.
"""

from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import os
import signal
import socket
import sqlite3
import sys
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

from memai import __version__, autostart, db, embed

# Windows' registry-derived mimetypes map serves .js as text/plain, which
# browsers refuse to execute as an ES module. Force the correct types.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
# Not in the registry map at all, so the bundled faces went out as
# application/octet-stream. Browsers sniff woff2 and render it anyway,
# but there is no reason to describe a file wrongly.
mimetypes.add_type("font/woff2", ".woff2")

WEBUI_DIR = Path(__file__).parent / "webui"
SNIPPET_LIMIT = 280
DEDUP_SNIPPET = 480

KNOWN_TYPES = ("note", "checkpoint", "anti_pattern", "reasoning", "handoff", "diagram")
CONFIDENCES = ("unverified", "confirmed", "contradicted")
STATUSES = ("active", "archived")

# How many uids one /api/bulk call carries. Also the cap on the uid list a
# scope-wide archive echoes back for its Undo -- an Undo that came back longer
# than bulk accepts would be a button that cannot work.
BULK_MAX = 500

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


def _paths(d: dict) -> dict:
    """Swap the `also_domains` mirror for the `also` list a view reads.

    That column exists for the FTS index and the embedder, which cannot
    join (see db); db.parse_domains is its inverse. No payload carries the
    mirror -- a view that filtered on it would be reading the copy instead
    of the rows in memory_domains.
    """
    blob = d.pop("also_domains", "")
    if blob:
        d["also"] = db.parse_domains(blob)
    return d


def _summary(row, limit: int = SNIPPET_LIMIT) -> dict:
    d = _paths(dict(row))
    d["content_len"] = len(d.get("content", ""))
    d["content"] = _snip(d.get("content", ""), limit)
    return d


# What a memory list may be ordered by. 'recalls' is how often an agent was
# handed it (db.memory_usage). Sorting by it is a way to LOOK; it is not a
# verdict. A rarely-read memory is usually about a rarely-needed subject,
# which is not the same as dead weight -- and nothing in retrieval reads
# this column, deliberately (see the schema comment on memory_usage).
_MEMORY_SORTS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "recalls": "recalls",
    "last_recall": "last_recall",
}


def _with_usage(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Attach recall counts to rows that were selected without the join."""
    usage = db.usage_for(conn, [i["uid"] for i in items])
    for i in items:
        u = usage.get(i["uid"])
        i["recalls"] = u["recalls"] if u else 0
        i["last_recall"] = u["last_recall"] if u else None
    return items


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


def _subtree_param(request) -> bool:
    """Whether a domain filter covers its subdomains. On unless told otherwise.

    A domain is a scope, and the useful default for a filter is the whole
    scope -- picking 'acme/x100' and seeing none of its routines reads as
    an empty module. `subtree=0` narrows to the exact path.
    """
    return request.query_params.get("subtree", "1").lower() not in ("0", "false", "no")


def _scope_echo(conn: sqlite3.Connection, domain: str) -> dict:
    """`domain_scope` for a response, but only when it is news.

    A domain filter is allowed to resolve a name that is the deep end of a
    path ('p200' -> 'acme/x100/p200'); a view that showed those rows
    without saying so would be claiming a filter it did not run. Omitted
    when the filter matched literally, which is the ordinary case.
    """
    if not domain:
        return {}
    scopes = db.resolve_domain_scopes(conn, domain)
    return {} if scopes == [db.normalize_domain(domain)] else {"domain_scope": scopes}


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
        domains = db.list_domains(conn)
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
            # written-to paths only: an ancestor that exists because
            # something deeper is filed under it is a level of the tree,
            # not a domain anybody named
            "domains": sum(1 for d in domains if not d["implicit"]),
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
    if sort not in _MEMORY_SORTS:
        sort = "created_at"
    direction = "ASC" if qp.get("dir", "desc").lower() == "asc" else "DESC"
    limit = _int_param(request, "limit", 50, 1, 200)
    offset = _int_param(request, "offset", 0, 0, 1_000_000)
    subtree = _subtree_param(request)

    with db.connect() as conn:
        scope = _scope_echo(conn, domain)
        if q:
            hits = db.search_hybrid(conn, q, domain=domain, type=type_, status=status,
                                    limit=200, subtree=subtree)
            if confidence:
                hits = [h for h in hits if h["confidence"] == confidence]
            if session:
                hits = [h for h in hits if h["session"] == session]
            total = len(hits)
            items = _with_usage(conn, [_summary(h) for h in hits[offset:offset + limit]])
            return {"total": total, "items": items, "searched": True, **scope}

        where, params = ["1=1"], []
        if domain:
            clause, values, _ = db.domain_scope_clause(conn, domain, alias="", subtree=subtree)
            where.append(clause)
            params.extend(values)
        for field, value in (("type", type_), ("status", status),
                             ("confidence", confidence), ("session", session)):
            if value:
                where.append(f"AND {field} = ?")
                params.append(value)
        clause = " ".join(where)
        total = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {clause}", params).fetchone()[0]
        # The join is what makes 'recalls' sortable; the filters above name
        # bare columns, which stay unambiguous because memory_usage shares
        # none of them. NULLs sort as never-recalled, which is the truth.
        rows = conn.execute(
            f"""SELECT m.*, COALESCE(u.recall_count, 0) AS recalls,
                       u.last_recalled_at AS last_recall
                FROM memories m LEFT JOIN memory_usage u ON u.memory_uid = m.uid
                WHERE {clause} ORDER BY {_MEMORY_SORTS[sort]} {direction} LIMIT ? OFFSET ?""",
            [*params, limit, offset]).fetchall()
    return {"total": total, "items": [_summary(r) for r in rows], "searched": False, **scope}


def memory_detail(request, payload) -> dict:
    uid = request.path_params["uid"]
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            raise ValueError(f"unknown memory: {uid}")
        result = _paths(dict(row))
        usage = db.usage_for(conn, [uid]).get(uid)
        result["recalls"] = usage["recalls"] if usage else 0
        result["last_recall"] = usage["last_recall"] if usage else None
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
            also=payload.get("also") or "",
            session=(payload.get("session") or "").strip(),
            tags=(payload.get("tags") or "").strip(),
            confidence=confidence,
        )
        return {"uid": uid, "also": db.get_domain_links(conn, uid)}


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
    """Update domain/also/tags/session/type. Domain, also or tags changes
    re-embed the row (the vector is computed over content+tags+domains) and
    every change leaves an audit entry in edits, so curation stays traceable.

    `also` is the set of extra domains the memory belongs to, replaced whole
    -- a list, or one string of comma-separated paths. It is applied AFTER a
    domain change in the same request, because the policy that drops a
    redundant cross-listing reads the domain the memory ends up with."""
    uid = request.path_params["uid"]
    allowed = ("domain", "tags", "session", "type", "review_after", "source_ref")
    updates = {k: str(payload[k]).strip() for k in allowed if k in payload}
    if not updates and "also" not in payload:
        raise ValueError(f"nothing to update (fields: {(*allowed, 'also')})")
    if "review_after" in updates:
        # normalized here so the stored value is a date whatever was typed,
        # and rejected loudly rather than silently kept as free text -- an
        # unparseable date would just never come due
        updates["review_after"] = db.normalize_review_after(updates["review_after"])
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
            updates["domain"] = db.apply_domain_policy(conn, updates["domain"])
        changed = {k: v for k, v in updates.items() if v != row[k]}
        if changed:
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
                    changed.get("tags", row["tags"]), changed.get("domain", row["domain"]),
                    row["also_domains"])
        # a domain change with no `also` in the request still re-runs the
        # link policy: the new path may already cover a membership the old
        # one needed (db.apply_link_policy)
        before = db.get_domain_links(conn, uid)
        if "also" in payload or ("domain" in changed and before):
            want = payload["also"] if "also" in payload else before
            if db.set_domain_links(conn, uid, want) != before:
                changed["also"] = True
        result = {"ok": True, "changed": list(changed)}
        if "also" in changed:
            result["also"] = db.get_domain_links(conn, uid)
        return result


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
    if len(uids) > BULK_MAX:
        raise ValueError(f"at most {BULK_MAX} uids per operation")
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
    # Every rule this endpoint used to spell out now lives in db.add_relation,
    # so the MCP tool refuses the same edges with the same words.
    with db.connect() as conn:
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
    limit = _int_param(request, "limit", GRAPH_NODE_CAP, 1, 2000)
    with db.connect() as conn:
        scope = _scope_echo(conn, domain)
        where, params = ["1=1"], []
        if domain:
            clause, values, _ = db.domain_scope_clause(
                conn, domain, alias="", subtree=_subtree_param(request))
            where.append(clause)
            params.extend(values)
        for field, value in (("status", status), ("type", type_)):
            if value:
                where.append(f"AND {field} = ?")
                params.append(value)
        total = conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {' '.join(where)}", params).fetchone()[0]
        # `deg` orders the cut, not the payload: the degree reported per
        # node below counts only edges between nodes that made it in, so
        # what the legend says matches what is drawn.
        rows = conn.execute(
            f"SELECT uid, type, domain, also_domains, status, confidence, content, tags, "
            f"       created_at, (SELECT COUNT(*) FROM relations r "
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
    # `tags` and `also` are here for the graph's spotlight filter, which
    # matches on what a human would type to find a node again: its opening
    # line, a domain it belongs to, or a tag. Everything else is already drawn.
    nodes = [_paths({
        "uid": r["uid"], "type": r["type"], "domain": r["domain"],
        "also_domains": r["also_domains"],
        "status": r["status"], "confidence": r["confidence"],
        "tags": r["tags"],
        "label": _snip(r["content"].split("\n", 1)[0], 90),
        "degree": degree.get(r["uid"], 0),
        "created_at": r["created_at"],
    }) for r in rows]
    # A cap that says nothing reads as "this is everything".
    return {"nodes": nodes, "edges": edges,
            "total": total, "truncated": total > len(nodes), **scope}


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
        items = db.diagram_overview(conn, domain=domain, status=status,
                                    subtree=_subtree_param(request))
        scope = _scope_echo(conn, domain)
    return {
        "total": len(items),
        "with_issues": sum(1 for d in items if d["issues"]),
        "items": items,
        **scope,
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
            also=payload.get("also") or "",
            session=(payload.get("session") or "").strip(),
            tags=(payload.get("tags") or "").strip(),
        ))
        return {"uid": uid, "also": db.get_domain_links(conn, uid)}


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


def diagram_jump(request, payload) -> dict:
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
    # An empty node_key on THIS side means the whole diagram, which only
    # happens on the receiving end of a jump -- and that end has to be able
    # to cut it. Creating one still names the step it leaves from.
    if not (node_key or delete):
        raise ValueError("node_key is required")
    with db.connect() as conn:
        if delete:
            if not db.delete_diagram_jump(conn, uid, node_key, peer_uid, peer_node):
                raise ValueError(f"no jump between '{node_key}' and {peer_uid}")
        else:
            _require(db.add_diagram_jump(
                conn, uid, node_key, peer_uid, peer_node,
                label=(payload.get("label") or "").strip()))
    return {"ok": True}


def diagram_mermaid(request, payload) -> dict:
    uid = request.path_params["uid"]
    with db.connect() as conn:
        _diagram_or_400(conn, uid)
        return {"uid": uid, "mermaid": db.render_diagram_mermaid(conn, uid)}


# ---------------------------------------------------------------- domains

def domains(request, payload) -> dict:
    """The domain tree, one entry per path, for the Domains view.

    Every field the table draws: both status counts, the type mix, the
    spelling-variant warning, and the tree position (parent/depth/
    children) with the subtree rollups the parent rows are drawn from.

    A level nobody wrote to directly still gets an entry, flagged
    `implicit`: 'acme/x100/p200' means the tree HAS an 'acme/x100', and a
    view that skipped it could not draw the branch its children hang from.

    `also` and `subtree_also` count the memories CROSS-LISTED at a path
    rather than filed there -- kept out of the status counts, because the
    tree would otherwise total more than the store. A path with no counts of
    its own and an `also` above zero is a purely cross-cutting subject, and
    the view says so instead of drawing it as empty.
    """
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT domain, status, type, COUNT(*) AS n, MAX(created_at) AS latest
               FROM memories WHERE domain <> ''
               GROUP BY domain, status, type""").fetchall()
        link_rows = conn.execute(
            """SELECT dl.domain AS domain, COUNT(*) AS n, MAX(m.created_at) AS latest
               FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid
               WHERE dl.domain <> '' GROUP BY dl.domain""").fetchall()
    agg: dict[str, dict] = {}

    def node(path: str) -> dict:
        return agg.setdefault(path, {
            "domain": path, "active": 0, "archived": 0, "types": {},
            "latest_at": "", "parent": db.domain_parent(path),
            "depth": db.domain_depth(path), "children": 0,
            "subtree_active": 0, "subtree_archived": 0,
            "also": 0, "subtree_also": 0,
            "subtree_latest_at": "", "implicit": True,
        })

    for r in rows:
        d = node(db.normalize_domain(r["domain"]))
        d["implicit"] = False
        if r["status"] == "active":
            d["active"] += r["n"]
        else:
            d["archived"] += r["n"]
        d["types"][r["type"]] = d["types"].get(r["type"], 0) + r["n"]
        d["latest_at"] = max(d["latest_at"], r["latest"])
        for ancestor in db.domain_ancestors(d["domain"]):
            node(ancestor)

    # being cross-listed at a path names it as surely as being filed there,
    # so it clears `implicit` and counts as activity for the ordering
    for r in link_rows:
        d = node(db.normalize_domain(r["domain"]))
        d["implicit"] = False
        d["also"] += r["n"]
        d["latest_at"] = max(d["latest_at"], r["latest"])
        for ancestor in db.domain_ancestors(d["domain"]):
            node(ancestor)

    for d in list(agg.values()):
        for scope in db.domain_ancestors(d["domain"], include_self=True):
            holder = agg[scope]
            holder["subtree_active"] += d["active"]
            holder["subtree_archived"] += d["archived"]
            holder["subtree_also"] += d["also"]
            holder["subtree_latest_at"] = max(holder["subtree_latest_at"], d["latest_at"])
        if d["parent"]:
            agg[d["parent"]]["children"] += 1

    # Spelling variants are compared per level, not per whole path: two
    # siblings called 'Cache' and 'cache' are the drift worth merging,
    # while the same word at two different depths is two different scopes.
    by_sibling: dict[tuple[str, str], list[str]] = {}
    for path, d in agg.items():
        if d["implicit"]:
            continue
        by_sibling.setdefault(
            (d["parent"], db.split_domain(path)[-1].lower()), []).append(path)
    for names in by_sibling.values():
        if len(names) > 1:
            for n in names:
                agg[n]["collides_with"] = [x for x in names if x != n]

    result = sorted(agg.values(), key=lambda d: d["domain"])
    result.sort(key=lambda d: d["subtree_latest_at"], reverse=True)
    return {"domains": result}


def rename_domain(request, payload) -> dict:
    """Rename, re-home or merge a domain, subdomains included.

    'to' is a full path, so this is also how a domain is nested: renaming
    'x100' to 'acme/x100' moves the bucket (and its subtree) under 'acme'.
    Every affected row is re-embedded (domain is part of the embedding
    source) and audited in edits -- see db.move_domain.

    Cross-listings into the renamed scope follow it (`also_affected`), so a
    memory that merely belongs to the subject is not left pointing at a path
    that no longer exists. It is not moved: where it is filed is untouched.
    """
    src = (payload.get("from") or "").strip()
    dst = (payload.get("to") or "").strip()
    if not src:
        raise ValueError("'from' is required")
    if not dst:
        raise ValueError("'to' is required")
    with db.connect() as conn:
        moved = db.move_domain(conn, src, dst)
    return {"ok": True, "affected": moved["moved"],
            "also_affected": moved["also_moved"],
            "domains": moved["domains"], "merged": moved["merged"]}


def domain_status(request, payload) -> dict:
    """Archive or restore a whole domain, subdomains included.

    A domain has no status column -- it is named by the memories filed under
    it -- so this is the scope-wide reading of the per-memory archive, and
    the tree draws a level with archived memories and no active ones as an
    archived branch.

    `uids` is what actually changed, so the UI can offer an exact Undo
    instead of restoring everything in the scope (see db.set_domain_status).
    Withheld past BULK_MAX, which is the most /api/bulk would take back.
    """
    domain = (payload.get("domain") or "").strip()
    status = payload.get("status", "")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    reason = (payload.get("reason") or "").strip()
    verb = "archived" if status == "archived" else "restored"
    note = f"{verb} with domain '{domain}'" + (f": {reason}" if reason else "")
    with db.connect() as conn:
        moved = db.set_domain_status(conn, domain, status, note=note)
    uids = moved["uids"]
    return {"ok": True, "affected": len(uids), "domains": moved["domains"],
            "uids": uids if len(uids) <= BULK_MAX else []}


def delete_domain(request, payload) -> dict:
    """Permanently delete a domain and every memory filed in it.

    Same guardrail as the MCP purge_memory tool and the per-memory purge
    above, for the same reason and at a much larger blast radius: the
    operator must type the literal phrase 'DELETE <domain>', and the UI
    never pre-fills it. Archiving the domain is the reversible option and
    is what the view offers first.
    """
    domain = (payload.get("domain") or "").strip()
    if not domain:
        raise ValueError("'domain' is required")
    expected = f"DELETE {domain}"
    if payload.get("confirm", "") != expected:
        raise ValueError(f"confirm phrase must exactly equal '{expected}'")
    with db.connect() as conn:
        gone = db.purge_domain(conn, domain)
    return {"ok": True, **gone}


def _normalize_plan(mode: str, counts: dict[str, int]) -> list[dict]:
    """Compute the per-domain moves that bring `counts` in line with policy.

    Policy is the casing `mode` plus the canonical path shape, the same
    pair every write path applies -- so this also repairs a domain that
    reached the table with a blank or padded segment ('acme//x100',
    'acme / x100'), which no prefix query could match as written.

    Each entry: {from, to, count, action}. action is 'merge' when the
    target already exists or more than one source collapses into it,
    otherwise 'rename'. Domains that already conform are omitted.
    """
    existing = set(counts)
    targets: dict[str, list[str]] = {}
    for d in counts:
        targets.setdefault(db.normalize_domain(db.case_domain(mode, d)), []).append(d)
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
    """Bring already-stored domains in line with the casing + path policy.

    dry_run (default true) returns the plan for preview -- what renames
    and what merges -- without touching data. dry_run=false applies it,
    reusing the rename/merge path (UPDATE + audit + re-embed). Each entry
    names one exact stored domain, so the moves are exact-path (a
    descendant appears as its own entry, or does not need moving at all).
    No-op when everything already conforms.

    A path that exists only as a cross-listing is in the plan too: it is a
    stored domain string like any other, and a repair pass that skipped it
    would leave the one spelling no prefix query can match.
    """
    dry_run = bool(payload.get("dry_run", True))
    with db.connect() as conn:
        mode = db.get_domain_case(conn)
        counts: dict[str, int] = {}
        for sql in (
            "SELECT domain, COUNT(*) AS n FROM memories WHERE domain <> '' GROUP BY domain",
            "SELECT domain, COUNT(*) AS n FROM memory_domains WHERE domain <> '' GROUP BY domain",
        ):
            for r in conn.execute(sql):
                counts[r["domain"]] = counts.get(r["domain"], 0) + r["n"]
        plan = _normalize_plan(mode, counts)
        if dry_run:
            return {"mode": mode, "dry_run": True, "plan": plan,
                    "renames": sum(1 for e in plan if e["action"] == "rename"),
                    "merges": sum(1 for e in plan if e["action"] == "merge")}
        moved = [db.move_domain(conn, e["from"], e["to"], subtree=False) for e in plan]
    return {"ok": True, "mode": mode, "moved": len(plan),
            "affected": sum(m["moved"] for m in moved),
            "also_affected": sum(m["also_moved"] for m in moved)}


# ------------------------------------------------------------------ config

def get_config(request, payload) -> dict:
    with db.connect() as conn:
        return {"domain_case": db.get_domain_case(conn),
                "svg_retention": db.get_svg_retention(conn)}


def set_config(request, payload) -> dict:
    """Write whichever settings the payload names.

    Partial on purpose: this used to demand domain_case, so a second
    setting could not be saved without also restating the first -- and a
    caller that only knew about one of them would clear nothing but would
    have to send a value it had no business choosing.
    """
    writers = {"domain_case": db.set_domain_case,
               "svg_retention": db.set_svg_retention}
    given = {k: payload[k] for k in writers if payload.get(k) is not None}
    if not given:
        raise ValueError(f"expected one of {', '.join(writers)}")
    with db.connect() as conn:
        for key, value in given.items():
            writers[key](conn, value)
        return {"domain_case": db.get_domain_case(conn),
                "svg_retention": db.get_svg_retention(conn)}


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
        render_retention = db.get_svg_retention(conn)
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
        # generated SVGs are a cache, so what matters is what they cost and
        # whether the retention rule is actually clearing them
        "renders": {**db.renders_usage(), "retention": render_retention,
                    "path": str(db.renders_dir())},
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
        # a jump has four things that can rot -- both diagrams and both node
        # keys -- and `to_node` is legitimately empty for a whole-diagram jump
        cur = conn.execute(
            """DELETE FROM diagram_jumps
               WHERE from_uid NOT IN (SELECT memory_uid FROM diagrams)
                  OR to_uid NOT IN (SELECT memory_uid FROM diagrams)
                  OR from_node NOT IN (
                        SELECT node_key FROM diagram_nodes
                        WHERE diagram_nodes.memory_uid = diagram_jumps.from_uid)
                  OR (to_node <> '' AND to_node NOT IN (
                        SELECT node_key FROM diagram_nodes
                        WHERE diagram_nodes.memory_uid = diagram_jumps.to_uid))""")
        jumps = cur.rowcount
    return {"ok": True, "relations_removed": rels, "vectors_removed": vecs,
            "suggestions_removed": sugs, "node_links_removed": links,
            "jumps_removed": jumps}


def prune_renders(request, payload) -> dict:
    """Clear generated SVGs now, rather than waiting for the next render.

    `all=true` empties the folder regardless of age -- the retention rule
    answers "how long to keep them", this answers "get rid of them". The
    diagrams themselves are untouched either way: a render is a cache.
    """
    before = db.renders_usage()
    if payload.get("all"):
        swept = db.prune_renders_all()
    else:
        with db.connect() as conn:
            swept = db.prune_renders(db.get_svg_retention(conn))
    return {"ok": True, **swept, "before": before, "after": db.renders_usage()}


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
    domain = request.query_params.get("domain", "")
    with db.connect() as conn:
        scope = _scope_echo(conn, domain)
        pairs = db.dedup_candidates(
            conn,
            domain=domain,
            type=request.query_params.get("type", ""),
            threshold=threshold,
            subtree=_subtree_param(request),
            limit=_int_param(request, "limit", 20, 1, 60))
        result = [{"a": _summary(a, DEDUP_SNIPPET), "b": _summary(b, DEDUP_SNIPPET),
                   "ratio": round(score, 3), "method": method} for a, b, score, method in pairs]
    return {"pairs": result, "threshold": threshold, **scope}


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
    """Finder for the memory-link picker in a record and on a diagram step.

    Every field returned is one the picker renders. The operator is
    choosing which memory to point at, and a uid is not something a human
    recognizes -- so domain, status and the retrieval provenance travel
    with the snippet, and the UI is free to show why a row is in the list
    rather than asking the reader to trust the ranking.

    Defaults to active memories. Archived ones are still reachable (the
    picker has a toggle), but they are the exception: linking to something
    already retired is a deliberate act, not the resting state.
    """
    q = request.query_params.get("q", "").strip()
    exclude = request.query_params.get("exclude", "")
    type_ = request.query_params.get("type", "")
    domain = request.query_params.get("domain", "")
    tag = request.query_params.get("tag", "").strip()
    status = request.query_params.get("status", "active")
    limit = _int_param(request, "limit", 20, 1, 50)
    # One row past the cap answers "is there more?" without a second COUNT
    # over the same predicate, and the excluded row costs one more on top.
    fetch = limit + 1 + (1 if exclude else 0)
    with db.connect() as conn:
        if not q:
            rows = [dict(r) for r in db.list_recent(
                conn, type=type_, domain=domain, tag=tag, status=status, limit=fetch)]
        else:
            # A pasted uid is an explicit request for one memory, so it
            # answers past every filter including status -- the operator
            # named the row, there is nothing left to narrow.
            exact = db.get_memory(conn, q)
            rows = [dict(exact)] if exact is not None else \
                db.search_hybrid(conn, q, type=type_, domain=domain, tag=tag,
                                 status=status, limit=fetch)
    rows = [r for r in rows if r["uid"] != exclude]
    items = [{
        "uid": r["uid"], "type": r["type"], "domain": r["domain"],
        "status": r["status"], "snippet": _snip(r["content"], 110),
        "match_source": r.get("match_source", ""),
        "fts_rank": r.get("fts_rank"), "vec_distance": r.get("vec_distance"),
    } for r in rows[:limit]]
    return {"items": items, "has_more": len(rows) > limit}


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
            # a crosslist suggestion replaces the whole set, so the Before
            # pane needs the whole set, not only the filed path
            target["also"] = db.get_domain_links(conn, row["target_uid"])
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


async def ping(request):
    """Say who is answering, so a probe can tell this apart from anything else.

    autostart.py needs to know whether the thing on a port is a MemAI
    dashboard before it decides not to start one -- on the machine this
    was written for, the admin's own default port was held by an
    unrelated MCP server. An open TCP port proves nothing; this reply
    does. Unauthenticated on purpose: it says only what a connection
    attempt already reveals.
    """
    return JSONResponse({
        "app": "memai",
        "version": __version__,
        "pid": os.getpid(),
        "db": str(db.default_db_path()),
    })


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

    webui/fonts/ ships with the repo, so the normal case is that every face
    is present. The existence check stays because a url() in a static
    stylesheet is a request whether the file is there or not: a checkout
    someone pruned, or a face a future release renames, would log 404s in
    the console for something otherwise working. Generating the rules means
    an absent face degrades to a local()-only src -- still used if the
    system has it, never fetched, and quiet either way.

    The BUNDLED file comes FIRST, and that order is load-bearing. "Roboto"
    names several releases whose advance widths differ -- an installed copy
    measured x at 1030 units where the bundled face measures 1016 -- so
    preferring local() meant the canvas broke a node label in a different
    place on a machine that happened to have Roboto installed.
    diagram_svg.measure() reads a width table extracted from the file in
    webui/fonts/ (tools/gen-roboto-metrics.py), so the canvas has to be
    looking at that same file for the two renderers to wrap alike. local()
    stays as the fallback for a checkout missing the file, where nothing
    can be guaranteed anyway.
    """
    fonts_dir = WEBUI_DIR / "fonts"
    lines = ["/* generated by memai.admin -- populate with tools/fetch-fonts.py */"]
    for family, weight, filename, local_names in WEBFONTS:
        src = []
        if (fonts_dir / filename).is_file():
            src.append(f"url('/static/fonts/{filename}') format('woff2')")
        src += [f"local('{name}')" for name in local_names]
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
    Route("/api/ping", ping),
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
    Route("/api/diagrams/{uid}/jump", api(diagram_jump), methods=["POST"]),
    Route("/api/diagrams/{uid}/mermaid", api(diagram_mermaid), methods=["GET"]),
    Route("/api/config", api(get_config), methods=["GET"]),
    Route("/api/config", api(set_config), methods=["POST"]),
    Route("/api/domains", api(domains)),
    Route("/api/domains/rename", api(rename_domain), methods=["POST"]),
    Route("/api/domains/normalize", api(normalize_domains), methods=["POST"]),
    Route("/api/domains/status", api(domain_status), methods=["POST"]),
    Route("/api/domains/delete", api(delete_domain), methods=["POST"]),
    Route("/api/maintenance/health", api(health)),
    Route("/api/maintenance/fts-rebuild", api(fts_rebuild), methods=["POST"]),
    Route("/api/maintenance/reembed", api(reembed), methods=["POST"]),
    Route("/api/maintenance/clean-orphans", api(clean_orphans), methods=["POST"]),
    Route("/api/maintenance/prune-renders", api(prune_renders), methods=["POST"]),
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


def _bind(host: str, port: int) -> socket.socket | None:
    """Take the port, or report that somebody else has it.

    uvicorn.run() binds inside its own event loop and, on a taken port,
    logs an error and exits 3. That is the right behaviour for a person
    who typed a command and can read the message, and the wrong one for
    an autostarted process racing its own siblings -- so bind here first
    and hand the socket over.

    Two error numbers mean "taken", and only one of them is obvious.
    Plain bind() on Windows gives EADDRINUSE, but a socket that set
    SO_REUSEADDR gets EACCES instead -- errno 13, not errno.WSAEACCES,
    which is 10013 and would never match. On POSIX, EACCES means a
    privileged port and is a real error, so the second case is gated to
    Windows. (A Windows reserved exclusion range also lands on EACCES
    with nothing listening; the caller says "in use" either way, which is
    imprecise but points at the same fix: pick another port.)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != "win32":
        # What asyncio would have done for us. Not on Windows, where
        # SO_REUSEADDR means "steal a live socket" rather than "reuse a
        # dead one" -- a different and unwanted thing.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            return None
        if sys.platform == "win32" and exc.errno == errno.EACCES:
            return None
        raise
    sock.listen(2048)
    return sock


def _write_registry(host: str, port: int) -> None:
    """Record where this dashboard is, for autostart to find later.

    Written by the server itself because only it knows its own pid: a
    venv's python.exe is a redirector that runs the real interpreter as a
    child, so whoever spawned us saw a different process.

    Advisory, never a lock. autostart re-confirms it with /api/ping
    before believing it, which is what keeps a record left behind by an
    abrupt kill from disabling the dashboard rather than merely being
    ignored.
    """
    try:
        autostart.registry_path().write_text(
            json.dumps({"host": host, "port": port, "pid": os.getpid(),
                        "version": __version__}) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"  note: could not write {autostart.registry_path()} ({exc})")


def _clear_registry() -> None:
    try:
        autostart.registry_path().unlink(missing_ok=True)
    except OSError:
        pass


def _cmd_status() -> int:
    found = autostart.find_running()
    if not found:
        print("memai admin: not running")
        return 1
    host, port = found
    info = autostart.ping(host, port) or {}
    print(f"memai admin: http://{host}:{port} · pid {info.get('pid', '?')} "
          f"· version {info.get('version', '?')}")
    return 0


def _cmd_stop() -> int:
    """Stop a dashboard started detached.

    Needed because DETACHED_PROCESS means no console, so there is no
    console control event to send and uvicorn's own signal handling is
    out of reach. The store survives a hard stop -- SQLite in WAL mode is
    the whole reason this project keeps its state in a database rather
    than in the server's memory.
    """
    found = autostart.find_running()
    if not found:
        print("memai admin: not running")
        return 1
    host, port = found
    info = autostart.ping(host, port) or {}
    pid = info.get("pid")
    if not isinstance(pid, int):
        print(f"memai admin at http://{host}:{port} did not report a pid")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"could not stop pid {pid}: {exc}")
        return 1
    _clear_registry()
    print(f"memai admin: stopped pid {pid} (was http://{host}:{port})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Separate from main() so a test can ask what the defaults resolved to.

    The port default comes from autostart rather than being read here, so
    that the guard looking for a dashboard and the dashboard itself
    cannot end up with different ideas of where it is -- which is exactly
    what happened while run-admin.bat set the variable and the code
    defaulted elsewhere.
    """
    parser = argparse.ArgumentParser(description="memai admin dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=autostart.configured_port())
    parser.add_argument("--autostarted", action="store_true",
                        help="started by the MCP server: lose a port race quietly")
    parser.add_argument("--status", action="store_true",
                        help="report where a running dashboard is, and exit")
    parser.add_argument("--stop", action="store_true",
                        help="stop a running dashboard, and exit")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.status:
        raise SystemExit(_cmd_status())
    if args.stop:
        raise SystemExit(_cmd_stop())

    sock = _bind(args.host, args.port)
    if sock is None:
        # Losing this race is the normal way a duplicate autostart ends:
        # several MCP servers start at once, all of them see no dashboard,
        # and the kernel picks one. Only the operator gets told.
        if args.autostarted:
            raise SystemExit(0)
        print(f"memai admin: port {args.port} is already in use "
              f"(memai-admin --status says what is there)")
        raise SystemExit(1)

    print(f"memai admin · db {db.default_db_path()} · http://{args.host}:{args.port}")
    if args.host not in LOOPBACK_HOSTS:
        print(f"  WARNING: {args.host} is not loopback. This API has NO authentication:"
              f"\n  anyone who can reach {args.host}:{args.port} can read, edit and"
              f"\n  permanently delete every memory in the store.")

    _write_registry(args.host, args.port)
    try:
        config = uvicorn.Config(app, log_level="warning")
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        _clear_registry()


if __name__ == "__main__":
    main()
