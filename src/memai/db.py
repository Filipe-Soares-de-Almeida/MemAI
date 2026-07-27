"""SQLite-backed store for memai.

Single WAL-mode file holds memory rows, an FTS5 index, a sqlite-vec
vector table, edit history, a relations graph, and the node/edge tables
behind type='diagram' memories together under one set of ACID
transactions -- vectors live INSIDE the transactional store, not beside
it, so there is nothing that can desync from the metadata on a
hard-kill.

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

-- A type='diagram' memory keeps its structure here instead of in
-- `content`: one row per step, so a step can carry its own note and its
-- own links. `memories.content` still holds a generated prose rendering
-- of the same graph, which is what FTS and the embedder see.
CREATE TABLE IF NOT EXISTS diagrams (
    memory_uid  TEXT PRIMARY KEY REFERENCES memories(uid),
    kind        TEXT NOT NULL DEFAULT 'flowchart',
    title       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    font_scale  REAL NOT NULL DEFAULT 1          -- how big the text is drawn
);

CREATE TABLE IF NOT EXISTS diagram_nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_uid  TEXT NOT NULL REFERENCES memories(uid),
    node_key    TEXT NOT NULL,                  -- stable id the edges refer to
    shape       TEXT NOT NULL DEFAULT 'step',   -- start|step|decision|io|end
    label       TEXT NOT NULL,                  -- objective: what happens here
    note        TEXT NOT NULL DEFAULT '',       -- optional long explanation
    seq         INTEGER NOT NULL DEFAULT 0,     -- authoring order
    x           REAL NOT NULL,                  -- always set: server-computed
    y           REAL NOT NULL,                  -- layout, overwritten by drags
    w           REAL,                           -- NULL = the shape's default
    h           REAL                            -- NULL = the shape's default
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_diagram_nodes_key
    ON diagram_nodes(memory_uid, node_key);

CREATE TABLE IF NOT EXISTS diagram_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_uid  TEXT NOT NULL REFERENCES memories(uid),
    from_key    TEXT NOT NULL,
    to_key      TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',       -- branch condition
    seq         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_diagram_edges_mem ON diagram_edges(memory_uid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_diagram_edges_pair
    ON diagram_edges(memory_uid, from_key, to_key);

CREATE TABLE IF NOT EXISTS diagram_node_links (
    memory_uid     TEXT NOT NULL REFERENCES memories(uid),  -- the diagram
    node_key       TEXT NOT NULL,
    target_uid     TEXT NOT NULL REFERENCES memories(uid),  -- linked memory
    relation_type  TEXT NOT NULL DEFAULT 'explains',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (memory_uid, node_key, target_uid)
);

CREATE INDEX IF NOT EXISTS idx_diagram_links_target
    ON diagram_node_links(target_uid);

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


# Columns added to a table that already exists in someone's store.
# `CREATE TABLE IF NOT EXISTS` -- how everything else here migrates -- is
# free for a new TABLE and does nothing at all for a new COLUMN, so a
# store created before the column would keep failing on every query that
# names it. Each entry must be nullable or carry a default: ADD COLUMN
# fills existing rows with it.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("diagrams", "font_scale", "REAL NOT NULL DEFAULT 1"),
    ("diagram_nodes", "w", "REAL"),
    ("diagram_nodes", "h", "REAL"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add any column in _ADDED_COLUMNS the store does not have yet."""
    for table, column, decl in _ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # table itself is new; the schema above already has it
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
    _ensure_columns(conn)
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

    Diagram tables cascade both ways: the graph of a purged diagram goes,
    and so does any OTHER diagram's node link that pointed at this memory
    -- otherwise a purged note leaves a node link dangling at a uid that
    no longer resolves.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute("DELETE FROM edits WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM relations WHERE from_uid = ? OR to_uid = ?", (uid, uid))
    conn.execute("DELETE FROM optimization_suggestions WHERE target_uid = ?", (uid,))
    conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? OR target_uid = ?", (uid, uid)
    )
    conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM diagram_edges WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM diagrams WHERE memory_uid = ?", (uid,))
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


# -------------------------------------------------------------------- diagrams
#
# A diagram documents what a routine does, start to end, as a graph: one
# row per step so a step can carry its own note and its own links to
# other memories. The graph is the source of truth; memories.content
# holds a generated prose rendering of it (see _render_text), which is
# what FTS indexes and the embedder vectorizes. Nothing hand-writes that
# content -- the free-text editors refuse a diagram for exactly that
# reason (see is_diagram).

DIAGRAM_TYPE = "diagram"
DIAGRAM_KINDS = ("flowchart",)
NODE_SHAPES = ("start", "step", "decision", "io", "end")

# Cap on a rendered body handed back to an agent, so one big flow cannot
# eat a whole context window. Same intent as CORPUS_SNIPPET_LEN; the
# stored content is never truncated, only what a tool returns.
DIAGRAM_BODY_BUDGET = 12_000

# Abstract canvas units. Renderers pan and zoom these; they never
# re-arrange them. Layout is computed HERE so every consumer -- admin
# canvas, a chat-side renderer, a future exporter -- draws the same
# picture, including a diagram nobody has ever opened in the editor.
#
# The default box, which a renderer MUST draw a node at when the node
# carries no size of its own. Duplicated in the canvas (diagram.js) for
# the same reason the shapes are: the two have to agree or a stored
# arrangement stops matching what is drawn on it.
NODE_DEFAULT_W = 170.0
NODE_DEFAULT_H = 48.0
DECISION_DEFAULT_H = 66.0        # a diamond needs the extra height to read
# What a resize is allowed to reach. Wide enough for a long routine name,
# bounded so one card cannot swallow the canvas.
NODE_MIN_W, NODE_MAX_W = 110.0, 560.0
NODE_MIN_H, NODE_MAX_H = 34.0, 340.0
FONT_SCALE_MIN, FONT_SCALE_MAX = 0.7, 2.5

# Air between boxes, on top of whatever the boxes measure. Generous on
# purpose: the gaps end up wider than a default box. A thirty-step routine
# packed shoulder to shoulder is a wall of text -- the edges are what carry
# the sequence, so the boxes can afford to sit apart. Changing these only
# affects diagrams created or re-arranged afterwards; stored coordinates
# are never rewritten behind the user's back (see relayout_diagram).
LAYOUT_GAP_X = 130.0
LAYOUT_GAP_Y = 152.0
LAYOUT_COL_W = NODE_DEFAULT_W + LAYOUT_GAP_X   # 300, the pitch for default boxes
LAYOUT_ROW_H = NODE_DEFAULT_H + LAYOUT_GAP_Y   # 200


def node_box(node: dict, font_scale: float = 1.0) -> tuple[float, float]:
    """The size a node is drawn at: its own, or its shape's default.

    A default box grows with the diagram's font scale, because otherwise
    asking for bigger text just truncates the label -- the card it has to
    fit in never changed. A box the user sized by hand is left alone: they
    chose that size while looking at that text.
    """
    default_h = DECISION_DEFAULT_H if node.get("shape") == "decision" else NODE_DEFAULT_H
    scale = max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, float(font_scale or 1)))
    w = node.get("w") or NODE_DEFAULT_W * scale
    h = node.get("h") or default_h * scale
    return float(w), float(h)

# Node keys double as mermaid node ids, so keep them to characters that
# need no escaping on either side.
_NODE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _as_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _norm_nodes(nodes: object) -> list[dict]:
    """Coerce whatever came over MCP/HTTP into the one node shape used below."""
    out: list[dict] = []
    for i, n in enumerate(nodes or []):  # type: ignore[arg-type]
        if not isinstance(n, dict):
            continue
        out.append({
            "key": str(n.get("key", "")).strip(),
            "label": str(n.get("label", "")).strip(),
            "shape": str(n.get("shape", "")).strip() or "step",
            "note": str(n.get("note", "")).strip(),
            "seq": _as_int(n.get("seq"), i),
        })
    return out


def _norm_edges(edges: object) -> list[dict]:
    """Same for edges; accepts from/to or the from_key/to_key column names."""
    out: list[dict] = []
    for i, e in enumerate(edges or []):  # type: ignore[arg-type]
        if not isinstance(e, dict):
            continue
        out.append({
            "from": str(e.get("from", e.get("from_key", ""))).strip(),
            "to": str(e.get("to", e.get("to_key", ""))).strip(),
            "label": str(e.get("label", "")).strip(),
            "seq": _as_int(e.get("seq"), i),
        })
    return out


def _node_field_errors(n: dict) -> list[str]:
    """Rules that hold for a single node in isolation."""
    errors: list[str] = []
    key = n["key"] or "?"
    if not n["key"]:
        errors.append("node is missing a key")
    elif not _NODE_KEY_RE.match(n["key"]):
        errors.append(f"invalid node key {n['key']!r}: use letters, digits, '_' or '-'")
    if not n["label"]:
        errors.append(f"node {key!r} has an empty label")
    if n["shape"] not in NODE_SHAPES:
        errors.append(
            f"node {key!r} has unknown shape {n['shape']!r}; use one of: {', '.join(NODE_SHAPES)}"
        )
    return errors


def _adjacency(edges: list[dict]) -> dict[str, list[str]]:
    """Successors per node in edge order -- what keeps the layout deterministic."""
    adj: dict[str, list[str]] = {}
    for e in sorted(edges, key=lambda x: x["seq"]):
        adj.setdefault(e["from"], []).append(e["to"])
    return adj


def _reachable(start: str, edges: list[dict]) -> set[str]:
    adj = _adjacency(edges)
    seen = {start}
    queue = [start]
    while queue:
        for nxt in adj.get(queue.pop(0), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _validate_graph(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Reject a graph that cannot be a flow, before anything is written.

    One gatekeeper for both the MCP tool and the HTTP API. The structural
    rules here (exactly one start, everything reachable) only make sense
    for a WHOLE graph -- the incremental single-node/single-edge writers
    deliberately skip them, because "add a node, then wire it up" has to
    be expressible in two calls.

    Cycles are legal: a retry loop is a real flow, not a mistake.
    """
    if not nodes:
        return ["graph has no nodes"]
    errors: list[str] = []
    keys: set[str] = set()
    for n in nodes:
        errors.extend(_node_field_errors(n))
        if n["key"] in keys:
            errors.append(f"duplicate node key: {n['key']!r}")
        keys.add(n["key"])

    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    if not starts:
        errors.append("graph has no 'start' node")
    elif len(starts) > 1:
        errors.append(
            f"graph has {len(starts)} 'start' nodes, expected exactly one: {', '.join(starts)}"
        )

    pairs: set[tuple[str, str]] = set()
    for e in edges:
        if not e["from"] or not e["to"]:
            errors.append("edge is missing an endpoint")
            continue
        for endpoint in (e["from"], e["to"]):
            if endpoint not in keys:
                errors.append(f"edge endpoint {endpoint!r} is not a node key")
        if e["from"] == e["to"]:
            errors.append(
                f"self-loop on {e['from']!r}: model a retry with an explicit decision node"
            )
        if (e["from"], e["to"]) in pairs:
            errors.append(f"duplicate edge {e['from']!r} -> {e['to']!r}")
        pairs.add((e["from"], e["to"]))

    # only worth reporting once the basics hold, else the list is noise
    if not errors:
        orphans = sorted(keys - _reachable(starts[0], edges))
        if orphans:
            errors.append("unreachable from start: " + ", ".join(orphans))
    return errors


def _back_edges(adj: dict[str, list[str]], roots: list[str]) -> set[tuple[str, str]]:
    """The loop-closing edges: those pointing back into the path being walked.

    A flow may legitimately cycle (a retry), but layering needs a DAG.
    Removing exactly the back-edges leaves the forward skeleton to layer;
    the loop closer is still drawn, just pointing back up the canvas.
    """
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished
    for root in roots:
        if state.get(root):
            continue
        state[root] = 1
        stack = [(root, iter(adj.get(root, ())))]
        while stack:
            node, successors = stack[-1]
            descended = False
            for nxt in successors:
                if state.get(nxt) == 1:
                    back.add((node, nxt))
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(adj.get(nxt, ()))))
                    descended = True
                    break
            if not descended:
                state[node] = 2
                stack.pop()
    return back


def loop_edges(nodes: list[dict], edges: list[dict]) -> set[tuple[str, str]]:
    """Which edges close a cycle -- a retry, a return to a menu.

    A property of the GRAPH, computed the same way the layout computes it,
    so a renderer can mark a loop closer without guessing. The canvas used
    to guess from the coordinates (does this edge point up the page?),
    which made a normal edge look like a loop as soon as someone dragged
    its target above its source.
    """
    keys = [n["key"] for n in nodes]
    known = set(keys)
    adj = {k: [t for t in v if t in known] for k, v in _adjacency(edges).items()}
    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    roots = [r for r in (starts or keys[:1]) if r in known]
    return _back_edges(adj, roots)


def _layout_graph(
    nodes: list[dict], edges: list[dict], font_scale: float = 1.0,
) -> dict[str, tuple[float, float]]:
    """Deterministic layered layout: the row is a node's longest path from start.

    A pure function of the graph, so the same flow always arranges the
    same way and the result is testable without a browser.

    Longest path, not first-visit depth: a step sits one row below its
    DEEPEST predecessor, so a terminal step lands under every branch that
    reaches it rather than level with whichever branch was walked first.
    Cycles cannot make this hang -- back-edges are dropped before
    layering (see _back_edges) and drawn as edges that point back up.

    Deliberately simple otherwise: dense graphs will still produce
    crossing edges. The user drags, and the drag persists, so
    crossing-minimisation can stay a later refinement of this one
    function.
    """
    if not nodes:
        return {}
    keys = [n["key"] for n in nodes]
    known = set(keys)
    adj = {k: [t for t in v if t in known] for k, v in _adjacency(edges).items()}
    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    roots = [r for r in (starts or keys[:1]) if r in known]
    back = _back_edges(adj, roots)

    def forward(node: str) -> list[str]:
        return [t for t in adj.get(node, []) if (node, t) not in back]

    # BFS over the forward skeleton: reachability, plus a stable
    # discovery order that decides left-to-right placement within a row
    order: list[str] = []
    seen = set(roots)
    queue = list(roots)
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in forward(cur):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    # Kahn over the same skeleton, relaxing depth upward as we go: one
    # pass is enough for longest-path layering because a node is only
    # released once every predecessor has been placed
    indeg = {k: 0 for k in order}
    for k in order:
        for nxt in forward(k):
            if nxt in indeg:
                indeg[nxt] += 1
    ready = [k for k in order if indeg[k] == 0]
    depth = {k: 0 for k in ready}
    while ready:
        cur = ready.pop(0)
        for nxt in forward(cur):
            if nxt not in indeg:
                continue
            depth[nxt] = max(depth.get(nxt, 0), depth[cur] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)

    # Anything with no depth yet parks in a row of its own: nodes the flow
    # cannot reach (possible via the incremental writers, which skip the
    # reachability rule) and, defensively, any node a residual cycle kept
    # from being released above.
    floor = max(depth.values()) + 1 if depth else 0
    stragglers = [k for k in keys if k not in depth]
    for k in stragglers:
        depth[k] = floor

    # Within a row, discovery order. A barycentre pass over these rows --
    # the textbook crossing-reduction step -- was tried and measured on a
    # 33-step branchy flow and on a deliberately reversed one: 37 crossings
    # became 35 on the first and 0 stayed 0 on the second. BFS already
    # groups a node's children next to each other, and what crossings
    # remain come from many-to-many fan-in, which no ordering can remove.
    # It was removed rather than kept on the strength of the textbook.
    rows: dict[int, list[str]] = {}
    for k in order + [k for k in stragglers if k not in seen]:
        rows.setdefault(depth[k], []).append(k)

    # The pitch follows the biggest box in the diagram, so resizing a card
    # -- or scaling the text every default box is sized from -- does not
    # make the next auto-arrange overlap it with its neighbours.
    boxes = [node_box(n, font_scale) for n in nodes]
    col_w = max(LAYOUT_COL_W, max(w for w, _ in boxes) + LAYOUT_GAP_X)
    row_h = max(LAYOUT_ROW_H, max(h for _, h in boxes) + LAYOUT_GAP_Y)

    pos: dict[str, tuple[float, float]] = {}
    for d, row in rows.items():
        span = (len(row) - 1) / 2.0
        for i, k in enumerate(row):
            pos[k] = ((i - span) * col_w, d * row_h)
    return pos


def _flow_order(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Node keys in reading order: down the rows, left to right within one."""
    pos = _layout_graph(nodes, edges)
    return sorted((n["key"] for n in nodes), key=lambda k: (pos[k][1], pos[k][0], k))


def _render_text(title: str, summary: str, kind: str, nodes: list[dict], edges: list[dict]) -> str:
    """The prose projection stored in memories.content.

    Generated, never hand-written: this is what FTS indexes and the
    embedder vectorizes, so a diagram is findable by what the routine
    actually does rather than by its title alone. One line per node in
    flow order keeps the edit-history diff readable.
    """
    by_key = {n["key"]: n for n in nodes}
    out = [f"DIAGRAM: {title}", f"KIND: {kind}"]
    if summary:
        out.append(f"SUMMARY: {summary}")
    out += ["", "FLOW:"]
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for e in sorted(edges, key=lambda x: x["seq"]):
        outgoing.setdefault(e["from"], []).append((e["to"], e["label"]))
    ordered = _flow_order(nodes, edges)
    for k in ordered:
        n = by_key[k]
        out.append(f"{k} [{n['shape']}]: {n['label']}")
        for to, label in outgoing.get(k, []):
            out.append(f"  -> {to}" + (f" [{label}]" if label else ""))
    notes = [(k, by_key[k]["note"]) for k in ordered if by_key[k]["note"]]
    if notes:
        out += ["", "NOTES:"]
        out += [f"{k}: {note}" for k, note in notes]
    return "\n".join(out)


def _mermaid_escape(text: str) -> str:
    """Quotes and newlines would break out of a mermaid node label."""
    return text.replace('"', "#quot;").replace("\n", " ")


# Mermaid keywords that cannot appear bare as a node id. `end` is the
# dangerous one: it closes a subgraph, so a node keyed 'end' -- the
# obvious key for a terminal step -- silently breaks the whole diagram.
_MERMAID_RESERVED = frozenset({
    "end", "graph", "subgraph", "flowchart", "class", "classdef",
    "click", "style", "linkstyle", "direction",
})


def _mermaid_id(key: str) -> str:
    return f"n_{key}" if key.lower() in _MERMAID_RESERVED else key


def _mermaid_node(key: str, shape: str, label: str) -> str:
    node_id = _mermaid_id(key)
    text = _mermaid_escape(label)
    if shape in ("start", "end"):
        return f'{node_id}(["{text}"])'
    if shape == "decision":
        return f'{node_id}{{"{text}"}}'
    if shape == "io":
        return f'{node_id}[/"{text}"/]'
    return f'{node_id}["{text}"]'


def _render_mermaid(title: str, nodes: list[dict], edges: list[dict]) -> str:
    """Mermaid source, for a host that can render a fenced diagram inline.

    Mermaid always applies its own layout, so this is the one renderer
    that ignores the stored coordinates -- the price of a one-line render
    in a chat client. Consumers that want the exact admin arrangement read
    the coordinates from get_diagram() instead.
    """
    by_key = {n["key"]: n for n in nodes}
    lines = []
    if title:
        lines += ["---", f"title: {_mermaid_escape(title)}", "---"]
    lines.append("flowchart TD")
    for k in _flow_order(nodes, edges):
        lines.append("    " + _mermaid_node(k, by_key[k]["shape"], by_key[k]["label"]))
    for e in sorted(edges, key=lambda x: x["seq"]):
        label = f'|"{_mermaid_escape(e["label"])}"|' if e["label"] else ""
        lines.append(f'    {_mermaid_id(e["from"])} -->{label} {_mermaid_id(e["to"])}')
    return "\n".join(lines)


def get_diagram_row(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM diagrams WHERE memory_uid = ?", (uid,)).fetchone()


def _font_scale(conn: sqlite3.Connection, uid: str) -> float:
    """The diagram's text scale, which every default box is sized from."""
    row = get_diagram_row(conn, uid)
    return float((row["font_scale"] if row is not None else 1) or 1)


def is_diagram(conn: sqlite3.Connection, uid: str) -> bool:
    """True for a diagram memory.

    The guard the free-text content editors use: hand-editing a diagram's
    content would desync it from the graph that generates it, so they
    refuse and point at the diagram writers instead.
    """
    row = get_memory(conn, uid)
    return row is not None and row["type"] == DIAGRAM_TYPE


def _load_graph(conn: sqlite3.Connection, uid: str) -> tuple[list[dict], list[dict]]:
    """A stored diagram's nodes/edges as the same dicts the writers accept."""
    nodes = [
        {"key": r["node_key"], "label": r["label"], "shape": r["shape"],
         "note": r["note"], "seq": r["seq"], "x": r["x"], "y": r["y"],
         "w": r["w"], "h": r["h"]}
        for r in conn.execute(
            "SELECT * FROM diagram_nodes WHERE memory_uid = ? ORDER BY seq, id", (uid,)
        )
    ]
    edges = [
        {"from": r["from_key"], "to": r["to_key"], "label": r["label"], "seq": r["seq"]}
        for r in conn.execute(
            "SELECT * FROM diagram_edges WHERE memory_uid = ? ORDER BY seq, id", (uid,)
        )
    ]
    return nodes, edges


def _write_nodes(
    conn: sqlite3.Connection, uid: str, nodes: list[dict],
    positions: dict[str, tuple[float, float]],
) -> None:
    conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ?", (uid,))
    conn.executemany(
        """INSERT INTO diagram_nodes (memory_uid, node_key, shape, label, note, seq, x, y)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(uid, n["key"], n["shape"], n["label"], n["note"], i,
          positions[n["key"]][0], positions[n["key"]][1])
         for i, n in enumerate(nodes)],
    )


def _write_edges(conn: sqlite3.Connection, uid: str, edges: list[dict]) -> None:
    conn.execute("DELETE FROM diagram_edges WHERE memory_uid = ?", (uid,))
    conn.executemany(
        "INSERT INTO diagram_edges (memory_uid, from_key, to_key, label, seq) VALUES (?, ?, ?, ?, ?)",
        [(uid, e["from"], e["to"], e["label"], i) for i, e in enumerate(edges)],
    )


def _refresh_diagram_content(conn: sqlite3.Connection, uid: str, note: str = "") -> None:
    """Re-generate memories.content after a structural change.

    Routed through update_memory_content so the change lands in the audit
    log and the vector is refreshed -- the same path a hand edit of any
    other type takes. A change that leaves the projection identical (an
    edge label rewritten to itself, say) writes nothing.
    """
    d = get_diagram_row(conn, uid)
    row = get_memory(conn, uid)
    if d is None or row is None:
        return
    nodes, edges = _load_graph(conn, uid)
    content = _render_text(d["title"], d["summary"], d["kind"], nodes, edges)
    if content == row["content"]:
        return
    update_memory_content(conn, uid, content, note=note)


def insert_diagram(
    conn: sqlite3.Connection,
    *,
    title: str,
    nodes: object,
    edges: object,
    summary: str = "",
    kind: str = "flowchart",
    domain: str = "",
    session: str = "",
    tags: str = "",
) -> tuple[str | None, list[str]]:
    """Create a type='diagram' memory from a whole graph.

    Returns (uid, errors). On any validation error nothing at all is
    written and uid is None -- a half-written flow is worse than no flow.
    """
    if kind not in DIAGRAM_KINDS:
        return None, [f"unknown diagram kind {kind!r}; use one of: {', '.join(DIAGRAM_KINDS)}"]
    title = str(title).strip()
    if not title:
        return None, ["diagram needs a title"]
    n = _norm_nodes(nodes)
    e = _norm_edges(edges)
    errors = _validate_graph(n, e)
    if errors:
        return None, errors
    summary = str(summary).strip()
    uid = insert_memory(
        conn, type=DIAGRAM_TYPE, content=_render_text(title, summary, kind, n, e),
        domain=domain, session=session, tags=tags or DIAGRAM_TYPE,
    )
    conn.execute(
        "INSERT INTO diagrams (memory_uid, kind, title, summary) VALUES (?, ?, ?, ?)",
        (uid, kind, title, summary),
    )
    _write_nodes(conn, uid, n, _layout_graph(n, e))
    _write_edges(conn, uid, e)
    return uid, []


def replace_diagram_graph(
    conn: sqlite3.Connection, uid: str, nodes: object, edges: object
) -> tuple[bool, list[str]]:
    """Swap a diagram's whole graph, keeping the positions of surviving nodes.

    A node the user dragged keeps its coordinates across a rewrite; only
    keys that are new to the graph get layout coordinates. Call
    relayout_diagram() for a clean arrangement. Node links pointing at
    keys the rewrite dropped go with them.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    n = _norm_nodes(nodes)
    e = _norm_edges(edges)
    errors = _validate_graph(n, e)
    if errors:
        return False, errors
    old_nodes, _ = _load_graph(conn, uid)
    kept = {o["key"]: (o["x"], o["y"]) for o in old_nodes}
    fresh = _layout_graph(n, e, _font_scale(conn, uid))
    positions = {node["key"]: kept.get(node["key"], fresh[node["key"]]) for node in n}
    _write_nodes(conn, uid, n, positions)
    _write_edges(conn, uid, e)
    live = [node["key"] for node in n]
    conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? "
        f"AND node_key NOT IN ({','.join('?' * len(live))})",
        (uid, *live),
    )
    _refresh_diagram_content(conn, uid)
    return True, []


def upsert_diagram_node(
    conn: sqlite3.Connection,
    uid: str,
    node_key: str,
    *,
    label: str | None = None,
    shape: str | None = None,
    note: str | None = None,
) -> tuple[bool, list[str]]:
    """Create or patch one node; only the fields passed are touched.

    Structural rules are NOT enforced here -- see _validate_graph. A new
    node gets its coordinates from a fresh layout of the resulting graph,
    but only its OWN coordinate is applied: every existing node keeps
    wherever the user dragged it.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    nodes, edges = _load_graph(conn, uid)
    existing = next((n for n in nodes if n["key"] == node_key), None)
    prev = existing or {}
    candidate = {
        "key": str(node_key).strip(),
        "label": str(prev.get("label", "") if label is None else label).strip(),
        "shape": str(prev.get("shape", "step") if shape is None else shape).strip() or "step",
        "note": str(prev.get("note", "") if note is None else note).strip(),
        "seq": prev.get("seq", len(nodes)),
    }
    errors = _node_field_errors(candidate)
    if errors:
        return False, errors
    if existing is not None:
        conn.execute(
            """UPDATE diagram_nodes SET label = ?, shape = ?, note = ?
               WHERE memory_uid = ? AND node_key = ?""",
            (candidate["label"], candidate["shape"], candidate["note"], uid, node_key),
        )
    else:
        x, y = _layout_graph(
            nodes + [candidate], edges, _font_scale(conn, uid))[candidate["key"]]
        conn.execute(
            """INSERT INTO diagram_nodes (memory_uid, node_key, shape, label, note, seq, x, y)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, candidate["key"], candidate["shape"], candidate["label"],
             candidate["note"], candidate["seq"], x, y),
        )
    _refresh_diagram_content(conn, uid)
    return True, []


def delete_diagram_node(
    conn: sqlite3.Connection, uid: str, node_key: str
) -> tuple[bool, list[str]]:
    """Remove a node together with its edges and its memory links."""
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    cur = conn.execute(
        "DELETE FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (uid, node_key)
    )
    if cur.rowcount == 0:
        return False, [f"no node {node_key!r} in {uid}"]
    conn.execute(
        "DELETE FROM diagram_edges WHERE memory_uid = ? AND (from_key = ? OR to_key = ?)",
        (uid, node_key, node_key),
    )
    conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? AND node_key = ?", (uid, node_key)
    )
    _refresh_diagram_content(conn, uid)
    return True, []


def upsert_diagram_edge(
    conn: sqlite3.Connection, uid: str, from_key: str, to_key: str, label: str = ""
) -> tuple[bool, list[str]]:
    """Wire two nodes, or relabel an existing wire between them."""
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    nodes, edges = _load_graph(conn, uid)
    keys = {n["key"] for n in nodes}
    errors = [f"edge endpoint {k!r} is not a node key" for k in (from_key, to_key) if k not in keys]
    if from_key == to_key:
        errors.append(f"self-loop on {from_key!r}: model a retry with an explicit decision node")
    if errors:
        return False, errors
    conn.execute(
        """INSERT INTO diagram_edges (memory_uid, from_key, to_key, label, seq)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(memory_uid, from_key, to_key) DO UPDATE SET label = excluded.label""",
        (uid, from_key, to_key, str(label).strip(), len(edges)),
    )
    _refresh_diagram_content(conn, uid)
    return True, []


def delete_diagram_edge(
    conn: sqlite3.Connection, uid: str, from_key: str, to_key: str
) -> tuple[bool, list[str]]:
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    cur = conn.execute(
        "DELETE FROM diagram_edges WHERE memory_uid = ? AND from_key = ? AND to_key = ?",
        (uid, from_key, to_key),
    )
    if cur.rowcount == 0:
        return False, [f"no edge {from_key!r} -> {to_key!r} in {uid}"]
    _refresh_diagram_content(conn, uid)
    return True, []


def set_diagram_meta(
    conn: sqlite3.Connection, uid: str, *, title: str | None = None,
    summary: str | None = None, font_scale: object = None,
) -> tuple[bool, list[str]]:
    """Rename a diagram, rewrite its summary, or set how big its text draws.

    Title and summary feed the projection; font_scale does not -- it is how
    the flow is drawn, not what it says. It is stored rather than kept in
    the browser because a card sized to fit its text at one scale is the
    wrong size at another, and the sizes ARE stored.
    """
    d = get_diagram_row(conn, uid)
    if d is None:
        return False, [f"{uid} is not a diagram"]
    new_title = d["title"] if title is None else str(title).strip()
    new_summary = d["summary"] if summary is None else str(summary).strip()
    if not new_title:
        return False, ["diagram needs a title"]
    was = float(d["font_scale"] or 1)
    scale = was
    if font_scale is not None:
        scale = _clamp(font_scale, FONT_SCALE_MIN, FONT_SCALE_MAX)
        if scale is None:
            return False, ["font_scale must be a number"]
    conn.execute(
        "UPDATE diagrams SET title = ?, summary = ?, font_scale = ? WHERE memory_uid = ?",
        (new_title, new_summary, scale, uid),
    )
    if scale != was:
        # Every default box just changed size, so the arrangement has to
        # come with it: scaling the coordinates by the same factor keeps a
        # hand-arranged flow arranged, instead of leaving cards overlapping
        # until someone re-arranges the whole thing. A box someone sized by
        # hand keeps that size -- they picked it looking at that text.
        conn.execute(
            "UPDATE diagram_nodes SET x = x * ?, y = y * ? WHERE memory_uid = ?",
            (scale / was, scale / was, uid),
        )
    _refresh_diagram_content(conn, uid)
    return True, []


def _clamp(value: object, low: float, high: float) -> float | None:
    try:
        return min(high, max(low, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def set_node_positions(conn: sqlite3.Connection, uid: str, positions: object) -> int:
    """Persist a dragged or resized box. Geometry is not content.

    Deliberately touches nothing else: no content re-render, no `edits`
    row, no re-embed, not even memories.updated_at -- moving a box on a
    canvas must not read as an edit or reorder list_recent().

    Accepts {key: (x, y)} or {key: {"x": .., "y": .., "w": .., "h": ..}}.
    x/y are required; w/h are optional and clamped, so a card can be
    resized through the same call that moves it. Unknown keys and
    unparseable numbers are skipped, and the count of rows actually
    written comes back.
    """
    written = 0
    for key, raw in (positions or {}).items():  # type: ignore[union-attr]
        try:
            pair = (raw.get("x"), raw.get("y")) if isinstance(raw, dict) else (raw[0], raw[1])
            x, y = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        sets, params = ["x = ?", "y = ?"], [x, y]
        if isinstance(raw, dict):
            w = _clamp(raw.get("w"), NODE_MIN_W, NODE_MAX_W) if raw.get("w") is not None else None
            h = _clamp(raw.get("h"), NODE_MIN_H, NODE_MAX_H) if raw.get("h") is not None else None
            if w is not None:
                sets.append("w = ?")
                params.append(w)
            if h is not None:
                sets.append("h = ?")
                params.append(h)
        params += [uid, key]
        cur = conn.execute(
            f"UPDATE diagram_nodes SET {', '.join(sets)} WHERE memory_uid = ? AND node_key = ?",
            params,
        )
        written += cur.rowcount
    return written


def reset_node_boxes(conn: sqlite3.Connection, uid: str, keys: object = None) -> int:
    """Drop stored sizes so the shapes' defaults apply again.

    The way back from a resize, per card or for the whole flow -- the same
    role relayout_diagram plays for positions.
    """
    if keys:
        rows = 0
        for key in keys:  # type: ignore[union-attr]
            rows += conn.execute(
                "UPDATE diagram_nodes SET w = NULL, h = NULL "
                "WHERE memory_uid = ? AND node_key = ?", (uid, key),
            ).rowcount
        return rows
    return conn.execute(
        "UPDATE diagram_nodes SET w = NULL, h = NULL WHERE memory_uid = ?", (uid,)
    ).rowcount


def relayout_diagram(conn: sqlite3.Connection, uid: str) -> int:
    """Discard stored coordinates and recompute the whole arrangement.

    The escape hatch for a diagram dragged into a mess. Content is
    untouched: the projection never mentions coordinates.
    """
    if get_diagram_row(conn, uid) is None:
        return 0
    nodes, edges = _load_graph(conn, uid)
    return set_node_positions(
        conn, uid, _layout_graph(nodes, edges, _font_scale(conn, uid)))


def add_node_link(
    conn: sqlite3.Connection, uid: str, node_key: str, target_uid: str,
    relation_type: str = "explains",
) -> tuple[bool, list[str]]:
    """Point one step of a flow at another memory that explains it.

    This is what makes a diagram an index of its domain: the flow says
    what happens, and the linked note/anti_pattern says why that step is
    the way it is.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    node = conn.execute(
        "SELECT 1 FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (uid, node_key)
    ).fetchone()
    if node is None:
        return False, [f"no node {node_key!r} in {uid}"]
    if target_uid == uid:
        return False, ["a diagram cannot link a node back to itself"]
    if get_memory(conn, target_uid) is None:
        return False, [f"unknown target memory {target_uid!r}"]
    conn.execute(
        """INSERT INTO diagram_node_links (memory_uid, node_key, target_uid, relation_type, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(memory_uid, node_key, target_uid)
           DO UPDATE SET relation_type = excluded.relation_type""",
        (uid, node_key, target_uid, str(relation_type).strip() or "explains", now_iso()),
    )
    return True, []


def delete_node_link(
    conn: sqlite3.Connection, uid: str, node_key: str, target_uid: str
) -> bool:
    cur = conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? AND node_key = ? AND target_uid = ?",
        (uid, node_key, target_uid),
    )
    return cur.rowcount > 0


def get_node_links(conn: sqlite3.Connection, uid: str) -> list[sqlite3.Row]:
    """A diagram's node links, joined to the linked memory's own columns."""
    return conn.execute(
        """SELECT l.node_key, l.target_uid, l.relation_type, l.created_at,
                  m.type AS target_type, m.domain AS target_domain,
                  m.status AS target_status, m.confidence AS target_confidence,
                  m.content AS target_content
           FROM diagram_node_links l JOIN memories m ON m.uid = l.target_uid
           WHERE l.memory_uid = ? ORDER BY l.node_key, l.created_at""",
        (uid,),
    ).fetchall()


def diagrams_referencing(conn: sqlite3.Connection, target_uid: str) -> list[sqlite3.Row]:
    """Which diagrams point a node at this memory -- the reverse of a node link."""
    return conn.execute(
        """SELECT l.memory_uid, l.node_key, l.relation_type, d.title, n.label
           FROM diagram_node_links l
           JOIN diagrams d ON d.memory_uid = l.memory_uid
           LEFT JOIN diagram_nodes n
                  ON n.memory_uid = l.memory_uid AND n.node_key = l.node_key
           WHERE l.target_uid = ? ORDER BY d.title, l.node_key""",
        (target_uid,),
    ).fetchall()


def get_diagram(conn: sqlite3.Connection, uid: str) -> dict | None:
    """Everything one diagram is made of, ready to render."""
    d = get_diagram_row(conn, uid)
    if d is None:
        return None
    nodes, edges = _load_graph(conn, uid)
    loops = loop_edges(nodes, edges)
    return {
        "uid": uid,
        "kind": d["kind"],
        "title": d["title"],
        "summary": d["summary"],
        "font_scale": float(d["font_scale"] or 1),
        "nodes": nodes,
        # `loops` is derived, never stored: it says the edge closes a cycle,
        # which is why it is drawn dashed and pointing back
        "edges": [dict(e, loops=(e["from"], e["to"]) in loops) for e in edges],
        "links": [dict(r) for r in get_node_links(conn, uid)],
    }


def _graph_issues(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """The structural defects of one flow, worst first.

    Diagram upkeep is not memory curation: what goes wrong in a flow is
    shape, not confidence, and none of it is visible to the dedup or
    optimization passes built for prose. All of these are reachable
    through the incremental writers, which skip the whole-graph rules on
    purpose so a flow can be built across several calls -- so something
    has to report them afterwards.

    Every rule here has to be worth acting on. "This fork has an arrow
    with no condition on it" was not: on a real routine most forks have an
    obvious fall-through, so it flagged healthy diagrams and taught the
    reader to ignore the whole strip. It was removed rather than tuned.
    """
    if not nodes:
        return [{"kind": "empty", "keys": []}]
    keys = [n["key"] for n in nodes]
    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    ends = [n["key"] for n in nodes if n["shape"] == "end"]
    outgoing: dict[str, list[dict]] = {}
    for e in edges:
        outgoing.setdefault(e["from"], []).append(e)

    issues: list[dict] = []
    if not starts:
        issues.append({"kind": "no_start", "keys": []})
    elif len(starts) > 1:
        issues.append({"kind": "many_starts", "keys": sorted(starts)})
    else:
        orphans = sorted(set(keys) - _reachable(starts[0], edges))
        if orphans:
            issues.append({"kind": "unreachable", "keys": orphans})

    # a step the flow just stops at, without saying it ended
    dead = sorted(n["key"] for n in nodes
                  if n["shape"] != "end" and not outgoing.get(n["key"]))
    if dead:
        issues.append({"kind": "dead_end", "keys": dead})

    if not ends:
        issues.append({"kind": "no_end", "keys": []})
    return issues


def diagram_overview(
    conn: sqlite3.Connection, *, domain: str = "", status: str = "active"
) -> list[dict]:
    """One card per diagram: its size, its links and what is structurally wrong.

    Batched on purpose -- nodes, edges and links come back in one query
    each and are grouped in memory, so N diagrams still cost four queries
    instead of 3N+1.
    """
    sql = [
        """SELECT d.memory_uid AS uid, d.kind, d.title, d.summary,
                  m.domain, m.status, m.confidence, m.tags,
                  m.created_at, m.updated_at
           FROM diagrams d JOIN memories m ON m.uid = d.memory_uid"""
    ]
    params: list = []
    where = []
    if domain:
        where.append("m.domain = ?")
        params.append(domain)
    if status:
        where.append("m.status = ?")
        params.append(status)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY m.updated_at DESC")
    rows = conn.execute(" ".join(sql), params).fetchall()

    nodes_by: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT memory_uid, node_key, shape, label, note FROM diagram_nodes ORDER BY memory_uid, seq, id"
    ):
        nodes_by.setdefault(r["memory_uid"], []).append(
            {"key": r["node_key"], "shape": r["shape"], "label": r["label"], "note": r["note"]})
    edges_by: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT memory_uid, from_key, to_key, label, seq FROM diagram_edges ORDER BY memory_uid, seq, id"
    ):
        edges_by.setdefault(r["memory_uid"], []).append(
            {"from": r["from_key"], "to": r["to_key"], "label": r["label"], "seq": r["seq"]})
    links_by: dict[str, int] = {}
    for r in conn.execute("SELECT memory_uid, COUNT(*) AS n FROM diagram_node_links GROUP BY memory_uid"):
        links_by[r["memory_uid"]] = r["n"]

    out = []
    for r in rows:
        nodes = nodes_by.get(r["uid"], [])
        edges = edges_by.get(r["uid"], [])
        issues = _graph_issues(nodes, edges)
        out.append({
            **dict(r),
            "nodes": len(nodes),
            "edges": len(edges),
            "links": links_by.get(r["uid"], 0),
            "documented": sum(1 for n in nodes if n["note"]),
            "issues": issues,
            "issue_count": sum(max(1, len(i["keys"])) for i in issues),
        })
    return out


def render_diagram_text(conn: sqlite3.Connection, uid: str) -> str:
    d = get_diagram_row(conn, uid)
    if d is None:
        return ""
    nodes, edges = _load_graph(conn, uid)
    return _render_text(d["title"], d["summary"], d["kind"], nodes, edges)


def render_diagram_mermaid(conn: sqlite3.Connection, uid: str) -> str:
    d = get_diagram_row(conn, uid)
    if d is None:
        return ""
    nodes, edges = _load_graph(conn, uid)
    return _render_mermaid(d["title"], nodes, edges)


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


def _diagram_first_tier(row: dict) -> int:
    """0 sorts ahead of everything else, 1 is the normal band.

    A diagram states a whole routine end to end, so inside one candidate
    set it is worth reading before the partial notes scattered around it:
    the flow is the source of truth those notes annotate.

    A contradicted or archived diagram gets no promotion -- it has stopped
    being that source of truth, and pushing it to the top would invert the
    only reason for promoting diagrams in the first place.
    """
    promote = (
        row.get("type") == DIAGRAM_TYPE
        and row.get("confidence") != "contradicted"
        and row.get("status") == "active"
    )
    return 0 if promote else 1


def search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    status: str = "active",
    limit: int = 30,
    diagrams_first: bool = True,
) -> list[dict]:
    """FTS BM25 + vector KNN, merged by reciprocal rank fusion.

    Each result dict carries `match_source` ("fts" | "vec" | "both"),
    plus `fts_rank` (bm25, lower = better) and/or `vec_distance`
    (cosine, lower = closer) so the agent can judge each candidate.
    Ordering is RRF, but it's a candidate ordering, not a verdict --
    the agent decides relevance, same as FTS-only did.

    `diagrams_first` (default on) lifts matching diagrams above the rest
    of the candidate set -- see _diagram_first_tier for which ones and
    why. Three deliberate properties:

    * promoting requires LOOKING for them. Both retrievers apply `limit`
      themselves, so a diagram that lost the global top-N never reaches
      the merge and no amount of re-ordering can rescue it. A second
      retrieval scoped to type='diagram' backfills the pool with the best
      diagrams for this query, whatever else crowded them out.
    * the backfill only adds uids the global passes missed, so nothing
      gets a second RRF contribution for appearing in two passes.
    * RRF scores are never altered, only the ordering, and every promoted
      row is tagged `rank_reason='diagram_first'`. Position 1 can mean "a
      type preference put it there" rather than "it matched best", and a
      reader that cannot tell the difference has been misled -- so the
      row says which it was.

    Pass diagrams_first=False where type preference is noise rather than
    help, e.g. a picker that is choosing any memory to link.
    """
    K = 60  # standard RRF damping constant
    merged: dict[str, dict] = {}

    def fold(rows, source: str, backfill: bool = False) -> None:
        for i, row in enumerate(rows):
            uid = row["uid"]
            contribution = 1.0 / (K + i + 1)
            seen = merged.get(uid)
            if seen is None:
                d = dict(row)
                if source == "fts":
                    d["fts_rank"] = d.pop("rank")
                d["match_source"] = source
                d["_rrf"] = contribution
                merged[uid] = d
            elif not backfill:
                if source == "fts":
                    seen["fts_rank"] = row["rank"]
                else:
                    seen["vec_distance"] = row["vec_distance"]
                seen["match_source"] = "both"
                seen["_rrf"] += contribution

    fold(search_memories(conn, query, domain=domain, type=type, status=status, limit=limit), "fts")
    fold(search_semantic(conn, query, domain=domain, type=type, status=status, limit=limit), "vec")

    # only with no type filter: an explicit type='note' means the caller
    # asked for notes, and backfilling diagrams would break that contract
    # (recall() is exactly that call, and must never surface one)
    if diagrams_first and not type:
        scoped = dict(domain=domain, type=DIAGRAM_TYPE, status=status, limit=limit)
        fold(search_memories(conn, query, **scoped), "fts", backfill=True)
        fold(search_semantic(conn, query, **scoped), "vec", backfill=True)

    tier = _diagram_first_tier if diagrams_first else (lambda d: 1)
    results = sorted(merged.values(), key=lambda d: (tier(d), -d["_rrf"]))[:limit]
    for d in results:
        del d["_rrf"]
        if tier(d) == 0:
            d["rank_reason"] = "diagram_first"
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

    Diagrams never enter the candidate pool: their content is a generated
    projection of a graph, so two similar flows are not a prose merge
    anybody could apply -- proposing one would only produce a suggestion
    that cannot be carried out.
    """
    sql = ["SELECT * FROM memories WHERE status = 'active' AND type != ?"]
    params: list = [DIAGRAM_TYPE]
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
