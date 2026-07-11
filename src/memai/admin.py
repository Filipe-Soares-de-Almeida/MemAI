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

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse
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

KNOWN_TYPES = ("note", "checkpoint", "anti_pattern", "reasoning", "handoff")
CONFIDENCES = ("unverified", "confirmed", "contradicted")
STATUSES = ("active", "archived")


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
    return result


def create_memory(request, payload) -> dict:
    type_ = (payload.get("type") or "").strip()
    content = (payload.get("content") or "").strip()
    confidence = payload.get("confidence") or "unverified"
    if not type_:
        raise ValueError("type is required")
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
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            raise ValueError(f"unknown memory: {uid}")
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
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT uid, type, domain, status, confidence, content, created_at "
            f"FROM memories WHERE {' '.join(where)}", params).fetchall()
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
    return {"nodes": nodes, "edges": edges}


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
        rows = conn.execute(
            "SELECT rowid_pk, uid, content, tags FROM memories WHERE domain = ?", (src,)).fetchall()
        if not rows:
            raise ValueError(f"no memories in domain '{src}'")
        merged = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE domain = ?", (dst,)).fetchone()[0] > 0
        now = db.now_iso()
        conn.execute(
            "UPDATE memories SET domain = ?, updated_at = ? WHERE domain = ?", (dst, now, src))
        for r in rows:
            conn.execute(
                "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
                (r["uid"], now, r["content"], r["content"], f"meta: domain '{src}' → '{dst}'"))
            db._upsert_vector(conn, r["rowid_pk"], r["content"], r["tags"], dst)
    return {"ok": True, "affected": len(rows), "merged": merged}


# ------------------------------------------------------------- maintenance

def _fts_check(conn: sqlite3.Connection) -> tuple[bool, str]:
    """FTS5 integrity-check; the 2-arg form also verifies the index against
    the external content table where supported."""
    try:
        try:
            conn.execute("INSERT INTO memories_fts(memories_fts, rank) VALUES ('integrity-check', 1)")
        except sqlite3.OperationalError:
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
        return True, "index consistent with the table"
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
        "integrity": {"ok": integrity_ok, "detail": "; ".join(quick)[:400]},
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
    return {"ok": True, "relations_removed": rels, "vectors_removed": vecs,
            "suggestions_removed": sugs}


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
            rows = [dict(exact)] if exact is not None else \
                db.search_hybrid(conn, q, status="", limit=10)
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


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Admin UI iterates often and is tiny; never let a browser cache it."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


routes = [
    Route("/", index),
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
    Route("/api/domains", api(domains)),
    Route("/api/domains/rename", api(rename_domain), methods=["POST"]),
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

app = Starlette(routes=routes, middleware=[Middleware(NoCacheMiddleware)])


def main() -> None:
    parser = argparse.ArgumentParser(description="memai admin dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MEMAI_ADMIN_PORT", "8765")))
    args = parser.parse_args()
    print(f"memai admin · db {db.default_db_path()} · http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
