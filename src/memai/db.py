"""SQLite-backed store for memai.

Single WAL-mode file holds memory rows, an FTS5 index, a sqlite-vec
vector table, edit history, and a relations graph together under one
set of ACID transactions -- vectors live INSIDE the transactional
store, not beside it, so there is nothing that can desync from the
metadata on a hard-kill.

Retrieval is hybrid: FTS5 BM25 keyword search plus brute-force KNN over
model2vec embeddings, merged by reciprocal rank fusion. Both sides only
widen the candidate set -- semantic judgment is still left to the
calling agent, which reads the candidates back and decides relevance
itself. If the embedding model or the sqlite-vec extension is
unavailable, everything degrades to FTS-only and vectors are backfilled
on a later connect.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from memai import embed

# Eager import for the same reason as in embed.py: sqlite_vec pulls in
# numpy, and importing that DLL lazily from inside a tool call deadlocks
# on Windows once the MCP stdio server is running.
try:
    import sqlite_vec
except Exception:  # pragma: no cover - extension unavailable
    sqlite_vec = None

# Domain-casing policy. Stored in the `meta` table under DOMAIN_CASE_KEY and
# enforced at every domain write path. 'preserve' keeps free-text casing (the
# historical behaviour); 'lower'/'upper' coerce every stored domain.
DOMAIN_CASE_KEY = "domain_case"
DOMAIN_CASE_MODES = ("preserve", "lower", "upper")
DOMAIN_CASE_DEFAULT = "preserve"

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    rowid_pk        INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,
    domain          TEXT NOT NULL DEFAULT '',
    session         TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    confidence      TEXT NOT NULL DEFAULT 'unverified',
    superseded_by   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, domain,
    content='memories', content_rowid='rowid_pk',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, domain)
    VALUES (new.rowid_pk, new.content, new.tags, new.domain);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, domain)
    VALUES ('delete', old.rowid_pk, old.content, old.tags, old.domain);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, domain)
    VALUES ('delete', old.rowid_pk, old.content, old.tags, old.domain);
    INSERT INTO memories_fts(rowid, content, tags, domain)
    VALUES (new.rowid_pk, new.content, new.tags, new.domain);
END;

CREATE TABLE IF NOT EXISTS edits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_uid    TEXT NOT NULL REFERENCES memories(uid),
    edited_at     TEXT NOT NULL,
    prev_content  TEXT NOT NULL,
    new_content   TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS relations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    from_uid       TEXT NOT NULL REFERENCES memories(uid),
    to_uid         TEXT NOT NULL REFERENCES memories(uid),
    relation_type  TEXT NOT NULL,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_uid);
CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_uid);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimization_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open',
    backup_path  TEXT
);

CREATE TABLE IF NOT EXISTS optimization_suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES optimization_runs(id),
    kind        TEXT NOT NULL,
    target_uid  TEXT,
    payload     TEXT NOT NULL,
    rationale   TEXT NOT NULL DEFAULT '',
    verified    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    prev_state  TEXT,
    decided_at  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_optsug_run ON optimization_suggestions(run_id);
CREATE INDEX IF NOT EXISTS idx_optsug_status ON optimization_suggestions(status);
"""


def default_db_path() -> Path:
    home = Path(os.environ.get("MEMAI_HOME", Path.home() / ".memai"))
    home.mkdir(parents=True, exist_ok=True)
    return home / "memai.db"


def new_uid() -> str:
    return secrets.token_hex(8)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_vec_extension(conn: sqlite3.Connection) -> bool:
    if sqlite_vec is None:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


def _vec_ready(conn: sqlite3.Connection) -> bool:
    """True when the memories_vec table exists (extension loaded + model seen at least once)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories_vec'"
    ).fetchone()
    if row is None:
        return False
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        return False


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_domain_case(conn: sqlite3.Connection) -> str:
    """The active domain-casing policy (one of DOMAIN_CASE_MODES)."""
    mode = _get_meta(conn, DOMAIN_CASE_KEY)
    return mode if mode in DOMAIN_CASE_MODES else DOMAIN_CASE_DEFAULT


def set_domain_case(conn: sqlite3.Connection, mode: str) -> str:
    """Persist the domain-casing policy. Returns the normalized value stored."""
    mode = (mode or "").strip().lower()
    if mode not in DOMAIN_CASE_MODES:
        raise ValueError(f"domain_case must be one of {', '.join(DOMAIN_CASE_MODES)}")
    _set_meta(conn, DOMAIN_CASE_KEY, mode)
    return mode


def case_domain(mode: str, domain: str) -> str:
    """Apply a casing policy to one domain string. Idempotent; empty stays empty."""
    if not domain:
        return domain
    if mode == "lower":
        return domain.lower()
    if mode == "upper":
        return domain.upper()
    return domain


def coerce_domain(conn: sqlite3.Connection, domain: str) -> tuple[str, str]:
    """Coerce a domain to the store's policy. Returns (coerced_domain, active_mode)."""
    mode = get_domain_case(conn)
    return case_domain(mode, domain), mode


def apply_domain_case(conn: sqlite3.Connection, domain: str) -> str:
    """Coerce a domain to the store's configured casing policy."""
    return coerce_domain(conn, domain)[0]


def _embed_source(content: str, tags: str, domain: str) -> str:
    """The text a memory's vector is computed from -- same fields FTS indexes."""
    return "\n".join(p for p in (content, tags, domain) if p)


def _ensure_vec(conn: sqlite3.Connection) -> None:
    """Create/migrate the vector table and backfill missing vectors.

    Runs inside the connection's transaction, so a hard-kill mid-backfill
    rolls back cleanly. A model swap (name or dim change vs. the meta
    table) drops and rebuilds every vector -- stored vectors from one
    model are meaningless in another model's space.
    """
    dim = embed.embedding_dim()
    if dim is None:
        return  # model unavailable; stay FTS-only, backfill next time
    stored_model = _get_meta(conn, "embed_model")
    stored_dim = _get_meta(conn, "embed_dim")
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories_vec'"
    ).fetchone() is not None
    if table_exists and (stored_model != embed.model_name() or stored_dim != str(dim)):
        conn.execute("DROP TABLE memories_vec")
        table_exists = False
    if not table_exists:
        conn.execute(
            f"CREATE VIRTUAL TABLE memories_vec USING vec0(embedding float[{dim}] distance_metric=cosine)"
        )
        _set_meta(conn, "embed_model", embed.model_name())
        _set_meta(conn, "embed_dim", str(dim))
    missing = conn.execute(
        """SELECT rowid_pk, content, tags, domain FROM memories
           WHERE rowid_pk NOT IN (SELECT rowid FROM memories_vec)"""
    ).fetchall()
    if missing:
        blobs = embed.embed_texts([_embed_source(r["content"], r["tags"], r["domain"]) for r in missing])
        if blobs:
            conn.executemany(
                "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                [(r["rowid_pk"], b) for r, b in zip(missing, blobs)],
            )


def _upsert_vector(conn: sqlite3.Connection, rowid_pk: int, content: str, tags: str, domain: str) -> None:
    if not _vec_ready(conn):
        return
    blobs = embed.embed_texts([_embed_source(content, tags, domain)])
    if not blobs:
        return
    conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (rowid_pk,))
    conn.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)", (rowid_pk, blobs[0]))


@contextmanager
def connect(db_path: Path | None = None):
    path = db_path or default_db_path()
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    vec_loaded = _load_vec_extension(conn)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    if vec_loaded:
        _ensure_vec(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_memory(
    conn: sqlite3.Connection,
    *,
    type: str,
    content: str,
    domain: str = "",
    session: str = "",
    tags: str = "",
    confidence: str = "unverified",
    created_at: str | None = None,
) -> str:
    uid = new_uid()
    ts = created_at or now_iso()
    domain = apply_domain_case(conn, domain)
    cur = conn.execute(
        """INSERT INTO memories
           (uid, type, domain, session, tags, content, status, confidence, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
        (uid, type, domain, session, tags, content, confidence, ts, ts),
    )
    _upsert_vector(conn, cur.lastrowid, content, tags, domain)
    return uid


def get_memory(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM memories WHERE uid = ?", (uid,)).fetchone()


def update_memory_content(conn: sqlite3.Connection, uid: str, new_content: str, note: str = "") -> bool:
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], new_content, note),
    )
    conn.execute(
        "UPDATE memories SET content = ?, updated_at = ? WHERE uid = ?",
        (new_content, now_iso(), uid),
    )
    _upsert_vector(conn, row["rowid_pk"], new_content, row["tags"], row["domain"])
    return True


def get_edit_history(conn: sqlite3.Connection, uid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM edits WHERE memory_uid = ? ORDER BY edited_at ASC", (uid,)
    ).fetchall()


def set_status(
    conn: sqlite3.Connection,
    uid: str,
    status: str,
    superseded_by: str | None = None,
    note: str = "",
) -> bool:
    """Change a memory's status; optionally record why in the audit log.

    When `note` is given it is stored as a status-change audit entry in
    `edits` (prev_content == new_content, since the content itself is not
    touched) -- deliberately without recomputing the embedding, which
    archiving does not affect. This replaces the old forget-with-reason
    path that round-tripped through update_memory_content and needlessly
    re-embedded unchanged content.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute(
        "UPDATE memories SET status = ?, superseded_by = ?, updated_at = ? WHERE uid = ?",
        (status, superseded_by, now_iso(), uid),
    )
    if note:
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
            (uid, now_iso(), row["content"], row["content"], note),
        )
    return True


def set_confidence(conn: sqlite3.Connection, uid: str, confidence: str) -> bool:
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute(
        "UPDATE memories SET confidence = ?, updated_at = ? WHERE uid = ?",
        (confidence, now_iso(), uid),
    )
    return True


def purge_memory(conn: sqlite3.Connection, uid: str) -> bool:
    """Irreversibly delete a memory row plus its edit history and relations.

    The memories_ad trigger removes the matching FTS row as part of the
    DELETE. Callers must gate this behind explicit user confirmation --
    forget() (soft-delete/archive) is the default and should be used
    unless the user specifically asked for permanent removal.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute("DELETE FROM edits WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM relations WHERE from_uid = ? OR to_uid = ?", (uid, uid))
    conn.execute("DELETE FROM optimization_suggestions WHERE target_uid = ?", (uid,))
    if _vec_ready(conn):
        conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (row["rowid_pk"],))
    conn.execute("DELETE FROM memories WHERE uid = ?", (uid,))
    return True


def add_relation(
    conn: sqlite3.Connection, from_uid: str, to_uid: str, relation_type: str, note: str = ""
) -> int:
    cur = conn.execute(
        "INSERT INTO relations (from_uid, to_uid, relation_type, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (from_uid, to_uid, relation_type, note, now_iso()),
    )
    return cur.lastrowid


def get_relations(conn: sqlite3.Connection, uid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM relations WHERE from_uid = ? OR to_uid = ? ORDER BY created_at ASC",
        (uid, uid),
    ).fetchall()


def _fts_query(raw: str) -> str:
    """Turn free-text/multi-term input into an FTS5 OR query across terms.

    Lets the calling agent pass several paraphrases in one call
    ("reranking teacher model" or "best of n dpo critic") and get the
    union of matches back, instead of one narrow AND match.
    """
    terms = [t.strip() for t in raw.replace(" OR ", " ").split() if t.strip()]
    if not terms:
        return raw
    escaped = [f'"{t}"' if not t.replace("_", "").isalnum() else t for t in terms]
    return " OR ".join(escaped)


def search_memories(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    status: str = "active",
    limit: int = 30,
) -> list[sqlite3.Row]:
    sql = [
        """SELECT m.*, bm25(memories_fts) AS rank
           FROM memories_fts
           JOIN memories m ON m.rowid_pk = memories_fts.rowid
           WHERE memories_fts MATCH ?"""
    ]
    params: list = [_fts_query(query)]
    if domain:
        sql.append("AND m.domain = ?")
        params.append(domain)
    if type:
        sql.append("AND m.type = ?")
        params.append(type)
    if status:
        sql.append("AND m.status = ?")
        params.append(status)
    sql.append("ORDER BY rank LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


_KNN_MAX_K = 10000  # upper bound on the "fetch (nearly) all, then filter" KNN path


def search_semantic(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    status: str = "active",
    limit: int = 30,
) -> list[sqlite3.Row]:
    """Brute-force KNN over the vector table, filtered post-KNN.

    Returns [] when vectors are unavailable, so callers can always call
    this unconditionally. domain/type/status filters apply *after* the
    nearest-neighbor pass, so a fixed limit*4 over-fetch can starve a
    small, selective domain: if every one of the limit*4 global nearest
    neighbors belongs to another domain, the filter leaves nothing even
    when relevant in-domain vectors exist just outside that window. When a
    domain/type filter narrows the result we therefore widen k to the whole
    vector set (capped at _KNN_MAX_K) so the post-KNN filter keeps the right
    rows in correct distance order; unfiltered searches keep the cheap
    fixed over-fetch.
    """
    if not _vec_ready(conn):
        return []
    blobs = embed.embed_texts([query])
    if not blobs:
        return []
    if domain or type:
        total = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
        k = min(max(total, 1), _KNN_MAX_K)
    else:
        k = max(limit * 4, 50)
    sql = [
        """SELECT m.*, v.distance AS vec_distance
           FROM (SELECT rowid, distance FROM memories_vec
                 WHERE embedding MATCH ? AND k = ?) v
           JOIN memories m ON m.rowid_pk = v.rowid
           WHERE 1=1"""
    ]
    params: list = [blobs[0], k]
    if domain:
        sql.append("AND m.domain = ?")
        params.append(domain)
    if type:
        sql.append("AND m.type = ?")
        params.append(type)
    if status:
        sql.append("AND m.status = ?")
        params.append(status)
    sql.append("ORDER BY v.distance LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    status: str = "active",
    limit: int = 30,
) -> list[dict]:
    """FTS BM25 + vector KNN, merged by reciprocal rank fusion.

    Each result dict carries `match_source` ("fts" | "vec" | "both"),
    plus `fts_rank` (bm25, lower = better) and/or `vec_distance`
    (cosine, lower = closer) so the agent can judge each candidate.
    Ordering is RRF, but it's a candidate ordering, not a verdict --
    the agent decides relevance, same as FTS-only did.
    """
    fts_rows = search_memories(conn, query, domain=domain, type=type, status=status, limit=limit)
    vec_rows = search_semantic(conn, query, domain=domain, type=type, status=status, limit=limit)

    K = 60  # standard RRF damping constant
    merged: dict[str, dict] = {}
    for i, row in enumerate(fts_rows):
        d = dict(row)
        d["fts_rank"] = d.pop("rank")
        d["match_source"] = "fts"
        d["_rrf"] = 1.0 / (K + i + 1)
        merged[d["uid"]] = d
    for i, row in enumerate(vec_rows):
        uid = row["uid"]
        if uid in merged:
            merged[uid]["vec_distance"] = row["vec_distance"]
            merged[uid]["match_source"] = "both"
            merged[uid]["_rrf"] += 1.0 / (K + i + 1)
        else:
            d = dict(row)
            d["match_source"] = "vec"
            d["_rrf"] = 1.0 / (K + i + 1)
            merged[uid] = d
    results = sorted(merged.values(), key=lambda d: d["_rrf"], reverse=True)[:limit]
    for d in results:
        del d["_rrf"]
    return results


def list_by_domain(
    conn: sqlite3.Connection, domain: str, *, type: str = "", status: str = "active", limit: int = 50
) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM memories WHERE domain = ?"]
    params: list = [domain]
    if type:
        sql.append("AND type = ?")
        params.append(type)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY created_at DESC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def list_recent(
    conn: sqlite3.Connection, *, type: str = "", domain: str = "", status: str = "active", limit: int = 20
) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM memories WHERE 1=1"]
    params: list = []
    if type:
        sql.append("AND type = ?")
        params.append(type)
    if domain:
        sql.append("AND domain = ?")
        params.append(domain)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY created_at DESC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def list_domains(
    conn: sqlite3.Connection, *, status: str = "active"
) -> list[sqlite3.Row]:
    """Distinct non-empty domains with their memory count + latest activity.

    Warm-up discovery. domain is free text and drifts over time (e.g.
    'PROJ-1042' vs 'PROJ-1042 invoice rounding'), and pulse/
    list_by_domain match it exactly -- listing the real strings lets the
    caller target the right one instead of guessing.
    """
    sql = [
        "SELECT domain, COUNT(*) AS count, MAX(created_at) AS latest_at",
        "FROM memories WHERE domain <> ''",
    ]
    params: list = []
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("GROUP BY domain ORDER BY latest_at DESC")
    return conn.execute(" ".join(sql), params).fetchall()


def latest_by_type(
    conn: sqlite3.Connection, type: str, *, domain: str = "", status: str = "active"
) -> sqlite3.Row | None:
    rows = list_recent(conn, type=type, domain=domain, status=status, limit=1)
    return rows[0] if rows else None


def _timeline_pair(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    """Checkpoint x checkpoint inside the same effort is a timeline, not a dup.

    Consecutive checkpoints of one ticket/session share the same skeleton
    (intent/established/next-steps) and score high on any similarity
    measure while narrating different moments -- the dominant source of
    dedup false positives in the field.
    """
    if a["type"] != "checkpoint" or b["type"] != "checkpoint":
        return False
    same_domain = bool(a["domain"]) and a["domain"] == b["domain"]
    same_session = bool(a["session"]) and a["session"] == b["session"]
    return same_domain or same_session


def dedup_candidates(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    threshold: float = 0.6, limit: int = 20, since: str = "",
) -> list[tuple[sqlite3.Row, sqlite3.Row, float, str]]:
    """Surface likely-duplicate/contradictory pairs for the agent to review.

    Semantic-first: when the vector table is available, candidate pairs
    come from cosine similarity over the embedded store (method
    'vector'); without vectors it falls back to lexical difflib overlap
    (method 'lexical'). `threshold` applies to the returned score in both
    modes (score = 1 - cosine distance on the vector path). Not a merge,
    just a candidate list -- the agent judges whether pairs are actually
    duplicates, same "agent as embedder" split used for search.

    `since` makes the hints directional for incremental runs: at least
    one side of every pair is new (created/updated at/after `since`),
    but the OTHER side may be anywhere in the store -- a new memory
    colliding with an old one outside the scan window still surfaces.
    Old x old pairs are skipped; they belong to a full pass, not this
    run's delta.

    Checkpoint handling: checkpoint x checkpoint pairs within the same
    domain or session are dropped entirely (see _timeline_pair), and
    pairs involving checkpoints rank below note/reasoning pairs of equal
    score -- real merges live in durable types. The returned score is
    never altered, only the ordering.
    """
    sql = ["SELECT * FROM memories WHERE status = 'active'"]
    params: list = []
    if domain:
        sql.append("AND domain = ?")
        params.append(domain)
    if type:
        sql.append("AND type = ?")
        params.append(type)
    rows = conn.execute(" ".join(sql), params).fetchall()
    is_new = (lambda r: r["updated_at"] >= since) if since else (lambda r: True)

    pairs: list[tuple[sqlite3.Row, sqlite3.Row, float, str]] = []
    if _vec_ready(conn):
        by_rowid = {r["rowid_pk"]: r for r in rows}
        # widen k when a filter narrows the candidate set, same starvation
        # logic as search_semantic: the neighbors we need may be far down
        # the global distance order
        total = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
        k = min(max(total, 1), _KNN_MAX_K) if (domain or type) else min(max(total, 1), 8)
        seen: set[tuple[int, int]] = set()
        for r in rows:
            if not is_new(r):
                continue  # probe from new memories only; matches may be old
            emb = conn.execute(
                "SELECT embedding FROM memories_vec WHERE rowid = ?", (r["rowid_pk"],)
            ).fetchone()
            if emb is None:
                continue
            neighbors = conn.execute(
                "SELECT rowid, distance FROM memories_vec WHERE embedding MATCH ? AND k = ?",
                (emb["embedding"], k),
            ).fetchall()
            for n in neighbors:
                other = by_rowid.get(n["rowid"])
                if other is None or n["rowid"] == r["rowid_pk"]:
                    continue
                key = (min(r["rowid_pk"], n["rowid"]), max(r["rowid_pk"], n["rowid"]))
                if key in seen:
                    continue
                seen.add(key)
                score = 1.0 - n["distance"]
                if score >= threshold:
                    pairs.append((r, other, score, "vector"))
    else:
        import difflib

        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if not (is_new(a) or is_new(b)):
                    continue
                ratio = difflib.SequenceMatcher(None, a["content"], b["content"]).quick_ratio()
                if ratio >= threshold:
                    pairs.append((a, b, ratio, "lexical"))

    pairs = [p for p in pairs if not _timeline_pair(p[0], p[1])]

    def rank(p) -> float:
        penalty = 0.05 * ((p[0]["type"] == "checkpoint") + (p[1]["type"] == "checkpoint"))
        return p[2] - penalty

    pairs.sort(key=rank, reverse=True)
    return pairs[:limit]


# ------------------------------------------------------------------ optimization

CONFIDENCE_VALUES = ("unverified", "confirmed", "contradicted")
SUGGESTION_KINDS = (
    "compact", "reword", "retag", "redomain",
    "set_confidence", "archive", "link", "merge", "distill",
)
# distill targets must be durable knowledge types -- distilling INTO a
# checkpoint/handoff would just recreate the ephemera it exists to retire
DISTILL_TYPES = ("note", "reasoning", "anti_pattern")


CORPUS_SNIPPET_LEN = 120
CORPUS_TAGS_LEN = 100
CORPUS_ANCHORS_CAP = 5
# Per-page ceiling on the serialized memory listing (compact-JSON chars).
# MCP hosts cap tool output around 25k tokens, and dense JSON (hex uids,
# timestamps, punctuation) tokenizes at roughly 3 chars/token -- a 76k-char
# response was observed to overflow the cap. 28k of listing keeps the full
# response (pretty-printing + stats/hints/relations on top) near ~12k
# tokens, no matter how fat individual memories are. Callers page with
# offset.
CORPUS_CHAR_BUDGET = 28_000

# Verifiable anchors an agent can go check against live facts: URLs,
# file paths, table/field-style identifiers and SNAKE_CASE constants.
_ANCHOR_PATTERNS = (
    re.compile(r"""https?://[^\s)>\]"']+"""),
    re.compile(
        r"[\w./\\~-]*\w\.(?:pas|py|js|ts|tsx|sql|json|ya?ml|toml|md|css|html|ini|cfg|bat|ps1|sh)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z][A-Z0-9]{0,4}\d{3,}\b"),          # X100, AB1234 …
    re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b"),      # F100_TOTAL, SOME_FLAG …
)


def _extract_anchors(content: str, cap: int = 8) -> list[str]:
    """Pull the verifiable anchors out of a memory's full content."""
    seen: list[str] = []
    for pat in _ANCHOR_PATTERNS:
        for m in pat.findall(content):
            if m not in seen:
                seen.append(m)
            if len(seen) >= cap:
                return seen
    return seen


def _norm_domain(d: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-")


def _domain_hints(domain_counts: dict[str, int]) -> list[dict]:
    """Cluster domain-string variants that likely mean the same thing.

    Groups by normalized form (lowercase, separators collapsed) and, when
    the domain embeds a ticket-style id (e.g. proj-1042), by that id --
    so 'PROJ-1042', 'proj_1042' and 'proj-1042-fix' all cluster.
    Returns only clusters with 2+ distinct raw strings, canonical first.
    """
    groups: dict[str, list[str]] = {}
    for raw in domain_counts:
        if not raw:
            continue
        norm = _norm_domain(raw)
        m = re.search(r"[a-z]{2,}-\d{3,}", norm)
        key = m.group(0) if m else norm
        groups.setdefault(key, []).append(raw)
    hints = []
    for variants in groups.values():
        if len(variants) < 2:
            continue
        variants.sort(key=lambda v: (-domain_counts[v], len(v)))
        hints.append({
            "canonical": variants[0],
            "variants": [{"domain": v, "count": domain_counts[v]} for v in variants],
            "total": sum(domain_counts[v] for v in variants),
        })
    hints.sort(key=lambda h: -h["total"])
    return hints


def optimization_corpus(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    since: str = "", include_archived: bool = False, limit: int = 500,
    offset: int = 0, full: bool = False,
) -> dict:
    """Compact whole-corpus dump for an agent to reason over in one call.

    Returns every memory's curation-relevant fields plus the relation edges
    touching them, so the agent can spot missing links, duplicates, stale or
    mis-scoped rows without hundreds of individual reads.

    The listing is aggressively slimmed so a few-hundred-memory store fits
    one MCP response (the full-body version of a real 200-memory store was
    ~450KB; even snippet-only it overflowed on metadata alone):
      - content is a snippet with content_len alongside (full=True keeps
        whole bodies; get_memory fetches one on demand)
      - tags longer than CORPUS_TAGS_LEN are cut, with tags_len alongside
      - empty/default fields are omitted (blank domain/session/tags, null
        superseded_by, status matching the filter default, confidence
        'unverified' -- stats.by_confidence keeps the aggregate view)
      - created_at drops sub-second precision; updated_at is not listed
        at all (get_memory has it)
      - anchors come as one space-joined string, capped at
        CORPUS_ANCHORS_CAP
    Beyond `limit`, a page also ends early when the serialized listing
    reaches CORPUS_CHAR_BUDGET -- the guarantee is that ONE response
    always fits an MCP host's output cap, whatever the store looks like.
    A `stats` block aggregates the filtered corpus regardless of limit,
    `domain_hints` clusters likely-variant domain strings, and
    `truncated` flags when the listing stopped before the corpus ended --
    page onward with offset (offset + count is the next page's offset).

    `since` makes curation incremental: only memories created OR updated
    at/after the given ISO timestamp (a date like '2026-07-01' works --
    string comparison over ISO values). Stats then describe that delta,
    but domain_hints stay cross-window: clusters are computed over the
    WHOLE store and reported when they touch the delta, so a new
    domain-string variant still pairs with an old spelling that sits
    outside the scan window.
    """
    status = "" if include_archived else "active"
    where = ["1=1"]
    params: list = []
    if domain:
        where.append("AND domain = ?")
        params.append(domain)
    if type:
        where.append("AND type = ?")
        params.append(type)
    if status:
        where.append("AND status = ?")
        params.append(status)
    base_where_sql, base_params = " ".join(where), list(params)
    if since:
        where.append("AND updated_at >= ?")
        params.append(since)
    where_sql = " ".join(where)

    rows = conn.execute(
        f"""SELECT uid, type, domain, session, tags, content, status,
                   confidence, superseded_by, created_at, updated_at
            FROM memories WHERE {where_sql}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    mems = []
    budget_used = 0
    for r in rows:
        m = {"uid": r["uid"], "type": r["type"]}
        if r["confidence"] != "unverified":
            m["confidence"] = r["confidence"]
        if r["domain"]:
            m["domain"] = r["domain"]
        if r["session"]:
            m["session"] = r["session"]
        if r["tags"]:
            tags = r["tags"]
            if len(tags) > CORPUS_TAGS_LEN:
                m["tags_len"] = len(tags)
                tags = tags[: CORPUS_TAGS_LEN - 1] + "…"
            m["tags"] = tags
        if include_archived and r["status"] != "active":
            m["status"] = r["status"]
        if r["superseded_by"]:
            m["superseded_by"] = r["superseded_by"]
        m["created_at"] = r["created_at"][:19]
        content = r["content"]
        m["content_len"] = len(content)
        m["content"] = content if full or len(content) <= CORPUS_SNIPPET_LEN \
            else content[: CORPUS_SNIPPET_LEN - 1] + "…"
        anchors = _extract_anchors(content, cap=CORPUS_ANCHORS_CAP)
        if anchors:
            m["anchors"] = " ".join(anchors)
        mems.append(m)
        budget_used += len(json.dumps(m, ensure_ascii=False))
        if budget_used >= CORPUS_CHAR_BUDGET:
            break
    uids = {m["uid"] for m in mems}
    rels = conn.execute(
        "SELECT id, from_uid, to_uid, relation_type FROM relations"
    ).fetchall()
    edges = [dict(r) for r in rels if r["from_uid"] in uids or r["to_uid"] in uids]

    # stats over the WHOLE filtered corpus (not just the LIMIT window)
    total = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params).fetchone()[0]
    def agg(col: str) -> dict:
        return dict(conn.execute(
            f"SELECT {col}, COUNT(*) FROM memories WHERE {where_sql} GROUP BY {col} ORDER BY COUNT(*) DESC",
            params).fetchall())
    by_domain = agg("domain")
    stats = {
        "total": total,
        "by_type": agg("type"),
        "by_confidence": agg("confidence"),
        "by_domain": by_domain,
        "empty_domain": by_domain.get("", 0),
    }

    # domain hints cluster over the WHOLE store; with `since`, keep only
    # clusters that touch the delta (counts stay store-wide)
    if since:
        by_domain_global = dict(conn.execute(
            f"SELECT domain, COUNT(*) FROM memories WHERE {base_where_sql} "
            "GROUP BY domain ORDER BY COUNT(*) DESC", base_params).fetchall())
        hints = [h for h in _domain_hints(by_domain_global)
                 if any(v["domain"] in by_domain for v in h["variants"])]
    else:
        hints = _domain_hints(by_domain)

    return {
        "memories": mems,
        "relations": edges,
        "count": len(mems),
        "offset": offset,
        "truncated": offset + len(mems) < total,
        "stats": stats,
        "domain_hints": hints,
    }


def _memory_exists(conn: sqlite3.Connection, uid: str | None) -> bool:
    return bool(uid) and get_memory(conn, uid) is not None


def _validate_suggestion(conn: sqlite3.Connection, s: object) -> tuple[dict | None, str | None]:
    """Return (normalized_row, error). error is a human-readable string or None."""
    if not isinstance(s, dict):
        return None, "suggestion must be an object"
    kind = str(s.get("kind", "")).strip()
    if kind not in SUGGESTION_KINDS:
        return None, f"unknown kind {kind!r} (allowed: {', '.join(SUGGESTION_KINDS)})"
    payload = s.get("payload") or {}
    if not isinstance(payload, dict):
        return None, "payload must be an object"
    target_uid = (str(s.get("target_uid", "")) or "").strip() or None
    rationale = str(s.get("rationale", "")).strip()
    verified = str(s.get("verified", "")).strip()

    def target_err() -> str | None:
        return None if _memory_exists(conn, target_uid) else f"target_uid not found: {target_uid!r}"

    if kind in ("compact", "reword"):
        err = target_err()
        if err:
            return None, err
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
    elif kind == "retag":
        err = target_err()
        if err:
            return None, err
        if "tags" not in payload:
            return None, "payload.tags required"
    elif kind == "redomain":
        err = target_err()
        if err:
            return None, err
        if "domain" not in payload:
            return None, "payload.domain required"
    elif kind == "set_confidence":
        err = target_err()
        if err:
            return None, err
        if payload.get("confidence") not in CONFIDENCE_VALUES:
            return None, f"payload.confidence must be one of {CONFIDENCE_VALUES}"
        if payload["confidence"] == "contradicted" and not verified:
            return None, "verified required: describe the live-facts check that contradicts this memory"
    elif kind == "archive":
        err = target_err()
        if err:
            return None, err
        if not verified:
            return None, "verified required: describe the live-facts check that makes this memory archivable"
    elif kind == "link":
        f = (str(payload.get("from_uid", "")) or "").strip()
        t = (str(payload.get("to_uid", "")) or "").strip()
        if not _memory_exists(conn, f):
            return None, f"payload.from_uid not found: {f!r}"
        if not _memory_exists(conn, t):
            return None, f"payload.to_uid not found: {t!r}"
        if f == t:
            return None, "cannot link a memory to itself"
        if not str(payload.get("relation_type", "")).strip():
            return None, "payload.relation_type required"
        if target_uid and target_uid != f:
            return None, "link derives target_uid from payload.from_uid; omit target_uid or make them match"
        target_uid = f
    elif kind == "merge":
        keep = (str(payload.get("keep_uid", "")) or "").strip()
        drop = (str(payload.get("drop_uid", "")) or "").strip()
        if not _memory_exists(conn, keep):
            return None, f"payload.keep_uid not found: {keep!r}"
        if not _memory_exists(conn, drop):
            return None, f"payload.drop_uid not found: {drop!r}"
        if keep == drop:
            return None, "cannot merge a memory with itself"
        if target_uid and target_uid != drop:
            return None, "merge derives target_uid from payload.drop_uid; omit target_uid or make them match"
        target_uid = drop
    elif kind == "distill":
        if target_uid:
            return None, "distill creates a new memory; omit target_uid"
        sources = payload.get("source_uids")
        if not isinstance(sources, list) or not sources:
            return None, "payload.source_uids must be a non-empty list"
        sources = [str(u).strip() for u in sources]
        if len(set(sources)) != len(sources):
            return None, "payload.source_uids contains duplicates"
        for u in sources:
            if not _memory_exists(conn, u):
                return None, f"payload.source_uids not found: {u!r}"
        if payload.get("new_type") not in DISTILL_TYPES:
            return None, f"payload.new_type must be one of {DISTILL_TYPES}"
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        if not verified:
            return None, "verified required: distill archives its sources -- describe the live-facts check"
        payload = {**payload, "source_uids": sources}

    return {
        "kind": kind, "target_uid": target_uid, "payload": payload,
        "rationale": rationale, "verified": verified,
    }, None


def stage_optimization(conn: sqlite3.Connection, note: str, suggestions: list) -> dict:
    """Validate a batch of suggestions and write them to a new run.

    Invalid suggestions are skipped and reported in `errors`; only valid
    ones are staged. Returns {run_id, staged, errors}. No run is created
    when nothing validates.
    """
    if not isinstance(suggestions, list) or not suggestions:
        raise ValueError("suggestions must be a non-empty list")
    valid, errors = [], []
    for i, s in enumerate(suggestions):
        norm, err = _validate_suggestion(conn, s)
        if err:
            errors.append({"index": i, "error": err})
        else:
            valid.append(norm)
    if not valid:
        return {"run_id": None, "staged": 0, "errors": errors}
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO optimization_runs (created_at, note, status) VALUES (?, ?, 'open')",
        (ts, note or ""),
    )
    run_id = cur.lastrowid
    for v in valid:
        conn.execute(
            """INSERT INTO optimization_suggestions
               (run_id, kind, target_uid, payload, rationale, verified, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (run_id, v["kind"], v["target_uid"], json.dumps(v["payload"]),
             v["rationale"], v["verified"], ts),
        )
    return {"run_id": run_id, "staged": len(valid), "errors": errors}


def list_optimization_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id) AS total,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'pending') AS pending,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'applied') AS applied,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'rejected') AS rejected
           FROM optimization_runs r ORDER BY r.created_at DESC, r.id DESC"""
    ).fetchall()


def optimization_run_kind_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per-run, per-kind suggestion counts (total / pending) across all runs."""
    return conn.execute(
        """SELECT run_id, kind,
                  COUNT(*) AS total,
                  SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending
           FROM optimization_suggestions
           GROUP BY run_id, kind
           ORDER BY run_id, kind"""
    ).fetchall()


def get_optimization_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM optimization_runs WHERE id = ?", (run_id,)).fetchone()


def get_optimization_suggestions(
    conn: sqlite3.Connection, run_id: int, status: str = "", kind: str = ""
) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM optimization_suggestions WHERE run_id = ?"]
    params: list = [run_id]
    if status:
        sql.append("AND status = ?")
        params.append(status)
    if kind:
        sql.append("AND kind = ?")
        params.append(kind)
    sql.append("ORDER BY id ASC")
    return conn.execute(" ".join(sql), params).fetchall()


def get_suggestion(conn: sqlite3.Connection, sug_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM optimization_suggestions WHERE id = ?", (sug_id,)
    ).fetchone()


def set_run_backup(conn: sqlite3.Connection, run_id: int, backup_path: str) -> None:
    conn.execute(
        "UPDATE optimization_runs SET backup_path = ? WHERE id = ?", (backup_path, run_id)
    )


def _update_meta_field(conn: sqlite3.Connection, uid: str, field: str, value: str) -> None:
    """Mirror admin.edit_meta for one tag/domain field: UPDATE + audit + re-embed.

    `field` is only ever 'tags' or 'domain' (caller-controlled), so the
    f-string interpolation is not an injection surface.
    """
    if field == "domain":
        value = apply_domain_case(conn, value)
    row = get_memory(conn, uid)
    conn.execute(
        f"UPDATE memories SET {field} = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid),
    )
    note = f"meta: {field} '{row[field]}' → '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"], note),
    )
    _upsert_vector(
        conn, row["rowid_pk"], row["content"],
        value if field == "tags" else row["tags"],
        value if field == "domain" else row["domain"],
    )


def _apply_kind(conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict) -> dict:
    """Execute one suggestion and return the prev_state dict for undo."""
    if kind in ("compact", "reword"):
        row = get_memory(conn, target_uid)
        prev = {"content": row["content"]}
        update_memory_content(conn, target_uid, payload["new_content"], note=f"optimize:{kind}")
        return prev
    if kind == "retag":
        row = get_memory(conn, target_uid)
        prev = {"tags": row["tags"]}
        _update_meta_field(conn, target_uid, "tags", str(payload["tags"]).strip())
        return prev
    if kind == "redomain":
        row = get_memory(conn, target_uid)
        prev = {"domain": row["domain"]}
        _update_meta_field(conn, target_uid, "domain", str(payload["domain"]).strip())
        return prev
    if kind == "set_confidence":
        row = get_memory(conn, target_uid)
        prev = {"confidence": row["confidence"]}
        set_confidence(conn, target_uid, payload["confidence"])
        return prev
    if kind == "archive":
        row = get_memory(conn, target_uid)
        prev = {"status": row["status"], "superseded_by": row["superseded_by"]}
        reason = str(payload.get("reason", "")).strip() or "optimize: archived"
        set_status(conn, target_uid, "archived", note=reason)
        return prev
    if kind == "link":
        rid = add_relation(
            conn, payload["from_uid"].strip(), payload["to_uid"].strip(),
            str(payload["relation_type"]).strip(), str(payload.get("note", "")).strip(),
        )
        return {"relation_id": rid}
    if kind == "merge":
        keep, drop = payload["keep_uid"].strip(), payload["drop_uid"].strip()
        drow = get_memory(conn, drop)
        prev = {"drop_status": drow["status"], "drop_superseded_by": drow["superseded_by"]}
        rid = add_relation(conn, keep, drop, "supersedes", str(payload.get("note", "")).strip())
        prev["relation_id"] = rid
        set_status(conn, drop, "archived", superseded_by=keep, note="optimize: merged")
        return prev
    if kind == "distill":
        new_uid = insert_memory(
            conn, type=payload["new_type"], content=payload["new_content"],
            tags=str(payload.get("tags", "")).strip(),
            domain=str(payload.get("domain", "")).strip(),
        )
        prev = {"new_uid": new_uid, "relation_ids": [], "sources": []}
        for u in payload["source_uids"]:
            row = get_memory(conn, u)
            prev["sources"].append(
                {"uid": u, "status": row["status"], "superseded_by": row["superseded_by"]})
            prev["relation_ids"].append(
                add_relation(conn, new_uid, u, "supersedes", "optimize: distilled"))
            set_status(conn, u, "archived", superseded_by=new_uid,
                       note=f"optimize: distilled into {new_uid}")
        return prev
    raise ValueError(f"unknown kind: {kind}")


def _revert_kind(
    conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict, prev: dict
) -> None:
    if kind in ("compact", "reword"):
        update_memory_content(conn, target_uid, prev["content"], note=f"optimize:undo {kind}")
    elif kind == "retag":
        _update_meta_field(conn, target_uid, "tags", prev["tags"])
    elif kind == "redomain":
        _update_meta_field(conn, target_uid, "domain", prev["domain"])
    elif kind == "set_confidence":
        set_confidence(conn, target_uid, prev["confidence"])
    elif kind == "archive":
        set_status(conn, target_uid, prev["status"],
                   superseded_by=prev.get("superseded_by"), note="optimize: undo archive")
    elif kind == "link":
        conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
    elif kind == "merge":
        conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
        set_status(conn, payload["drop_uid"].strip(), prev["drop_status"],
                   superseded_by=prev.get("drop_superseded_by"), note="optimize: undo merge")
    elif kind == "distill":
        for s in prev.get("sources", []):
            set_status(conn, s["uid"], s["status"],
                       superseded_by=s.get("superseded_by"), note="optimize: undo distill")
        # purge (not archive) the distilled memory: it was born from this
        # apply, so undo removes it entirely; its relations go with it
        if prev.get("new_uid"):
            purge_memory(conn, prev["new_uid"])
    else:
        raise ValueError(f"unknown kind: {kind}")


def apply_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] != "pending":
        raise ValueError(f"suggestion already {row['status']}")
    payload = json.loads(row["payload"])
    prev = _apply_kind(conn, row["kind"], row["target_uid"], payload)
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'applied', prev_state = ?, decided_at = ? WHERE id = ?",
        (json.dumps(prev), now_iso(), sug_id),
    )
    return True


def reject_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] == "applied":
        raise ValueError("cannot reject an applied suggestion; revert it first")
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'rejected', decided_at = ? WHERE id = ?",
        (now_iso(), sug_id),
    )
    return True


def revert_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] != "applied":
        raise ValueError("only applied suggestions can be reverted")
    payload = json.loads(row["payload"])
    prev = json.loads(row["prev_state"]) if row["prev_state"] else {}
    _revert_kind(conn, row["kind"], row["target_uid"], payload, prev)
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'pending', prev_state = NULL, decided_at = NULL WHERE id = ?",
        (sug_id,),
    )
    return True


def delete_optimization_run(conn: sqlite3.Connection, run_id: int) -> bool:
    conn.execute("DELETE FROM optimization_suggestions WHERE run_id = ?", (run_id,))
    cur = conn.execute("DELETE FROM optimization_runs WHERE id = ?", (run_id,))
    return cur.rowcount > 0
