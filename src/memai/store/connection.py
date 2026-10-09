"""Opening a store: the schema, the migrations every connect() runs, and uid and token helpers."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from memai.store.item_ids import INDEXES as ITEM_INDEXES
from memai.store.item_ids import schema as item_schema
from memai.store.paths import default_db_path, project_path
from memai.store.settings import _get_meta, _set_meta

# The FTS index and its triggers, kept separate because they are also what a
# store built before a new indexed column has to be rebuilt from (_ensure_fts).
_FTS_COLUMNS = ("title", "content", "tags", "domain", "also_domains")

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, content, tags, domain, also_domains,
    content='memories', content_rowid='rowid_pk',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, tags, domain, also_domains)
    VALUES (new.rowid_pk, new.title, new.content, new.tags, new.domain, new.also_domains);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, domain, also_domains)
    VALUES ('delete', old.rowid_pk, old.title, old.content, old.tags, old.domain, old.also_domains);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, domain, also_domains)
    VALUES ('delete', old.rowid_pk, old.title, old.content, old.tags, old.domain, old.also_domains);
    INSERT INTO memories_fts(rowid, title, content, tags, domain, also_domains)
    VALUES (new.rowid_pk, new.title, new.content, new.tags, new.domain, new.also_domains);
END;
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    rowid_pk        INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,
    domain          TEXT NOT NULL DEFAULT '',
    also_domains    TEXT NOT NULL DEFAULT '',   -- indexing mirror of memory_domains
    session         TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '',
    -- One line naming what the memory is about. Every writing tool requires
    -- one; a row holding none is listed by the opening line of its body.
    title           TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    confidence      TEXT NOT NULL DEFAULT 'unverified',
    -- '' not pinned, 'global' every scope, 'domain' its domain and also paths
    pin             TEXT NOT NULL DEFAULT '',
    superseded_by   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);

-- One row per extra domain a memory belongs to, beside the one it is filed
-- at. No row here is ever a memory's own path or an ancestor of it: the
-- prefix arm of a domain filter already covers those, and recording one
-- would count the memory twice in its own branch (see apply_link_policy).
CREATE TABLE IF NOT EXISTS memory_domains (
    memory_uid  TEXT NOT NULL REFERENCES memories(uid),
    domain      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (memory_uid, domain)
);

CREATE INDEX IF NOT EXISTS idx_memory_domains_domain ON memory_domains(domain);

-- The named fields a body is made of, for the types memai.sections gives a
-- spec (see SECTION_SPEC). One row per field, `seq` in spec order.
--
-- Read out of `memories.content`, which stays the record: the only writer
-- here is _write_sections, and every writer of a body calls it with the
-- body it just wrote. A query that edits these rows on their own moves the
-- fields away from the text they were read from.
CREATE TABLE IF NOT EXISTS memory_sections (
    memory_uid  TEXT NOT NULL REFERENCES memories(uid),
    seq         INTEGER NOT NULL,
    key         TEXT NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (memory_uid, key)
);

-- One row per body that has a spec and does not meet it. `detail` is what
-- memai.sections.read said stops it; the dashboard lists these for a human.
-- A body that conforms has no row, so this table is the queue and its
-- emptiness is the store being clean.
CREATE TABLE IF NOT EXISTS section_migration (
    memory_uid  TEXT PRIMARY KEY REFERENCES memories(uid),
    verdict     TEXT NOT NULL,          -- 'needs_review'
    detail      TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_section_migration_verdict
    ON section_migration(verdict);

-- How often a memory was actually READ BACK, which is the only evidence
-- that writing it was worth anything. A curation pass without this judges
-- text: it can see that a memory is old, duplicated or vague, and cannot
-- see that one nobody has needed in six months is the store's dead weight
-- while the vague-looking one is answered with three times a week.
--
-- Its own table, not two columns on `memories`, for one concrete reason:
-- the FTS trigger fires on ANY update of that table, so counting a recall
-- there would delete and reinsert the row's index entry on every search.
-- It also keeps usage droppable without touching a memory, and keeps
-- `updated_at` meaning "the content changed".
--
-- NOTHING HERE MAY EVER REACH A RANKING. It is tempting -- boost what gets
-- read, obviously -- and it is wrong: a memory read twice a year is not
-- worse than one read weekly, it is about a rarer subject. Some of what a
-- store exists FOR is the thing nobody remembers to look up, and ranking by
-- popularity buries exactly that, then buries it deeper every time it loses.
-- Usage answers "was this ever worth anything", for a human curating. It
-- does not answer "is this the answer", which is the query's job.
-- test_usage.py holds that line.
--
-- via_fts counts the reads a search produced, so "was this found, or only
-- listed" stays answerable without parsing session transcripts. A read with
-- no search behind it (pulse, a list, get_memory) counts in recall_count and
-- not here.
CREATE TABLE IF NOT EXISTS memory_usage (
    memory_uid        TEXT PRIMARY KEY REFERENCES memories(uid),
    recall_count      INTEGER NOT NULL DEFAULT 0,
    last_recalled_at  TEXT NOT NULL,
    via_fts           INTEGER NOT NULL DEFAULT 0
);
""" + _FTS_SCHEMA + """
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
-- of the same graph, which is what FTS sees.
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

-- A step of one flow continuing into ANOTHER flow. Deliberately not a
-- diagram_node_links row: that table attaches PROSE to a step ("here is
-- why this step is the way it is"), this one is a way THROUGH ("the rest
-- of this branch is documented over there"). `to_node` is optional -- ''
-- means the target diagram as a whole -- and one row is read from BOTH
-- ends, so the trip back needs no second row (see get_diagram_jumps).
CREATE TABLE IF NOT EXISTS diagram_jumps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_uid    TEXT NOT NULL REFERENCES memories(uid),  -- diagram jumped from
    from_node   TEXT NOT NULL,
    to_uid      TEXT NOT NULL REFERENCES memories(uid),  -- diagram jumped to
    to_node     TEXT NOT NULL DEFAULT '',               -- '' = the whole diagram
    label       TEXT NOT NULL DEFAULT '',               -- why it continues there
    created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_diagram_jumps_pair
    ON diagram_jumps(from_uid, from_node, to_uid, to_node);
CREATE INDEX IF NOT EXISTS idx_diagram_jumps_to ON diagram_jumps(to_uid);

CREATE TABLE IF NOT EXISTS tasks (
    memory_uid   TEXT PRIMARY KEY REFERENCES memories(uid),
    goal         TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'open',    -- open | completed | cancelled
    completed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_uid    TEXT NOT NULL REFERENCES memories(uid),
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    split_depends INTEGER NOT NULL DEFAULT 0, -- 1: a brief stored without DEPENDS ON; task_note_depends holds it
    session       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_notes_mem ON task_notes(memory_uid);

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

-- One row per day the dashboard was opened, holding that day's health index
-- and its four axes. The store keeps no history of a confidence or a
-- relation, so "is this getting better" cannot be reconstructed after the
-- fact -- it has to be written down as it happens. The writer is
-- health_snapshot(); a day already written is left alone, so the number is
-- the first reading of the day and not the last.
CREATE TABLE IF NOT EXISTS health_daily (
    day            TEXT PRIMARY KEY,          -- YYYY-MM-DD, UTC
    score          INTEGER NOT NULL,
    curation       INTEGER NOT NULL,
    connectivity   INTEGER NOT NULL,
    freshness      INTEGER NOT NULL,
    organization   INTEGER NOT NULL
);
"""


def new_uid() -> str:
    return secrets.token_hex(8)


# Characters per token in est_tokens: a fixed-ratio estimate, since no host tokenizer is reachable.
CHARS_PER_TOKEN = 4


def est_tokens(chars: int) -> int:
    """Estimated token count for a body of `chars` characters.

    chars / CHARS_PER_TOKEN, rounded up; 0 characters is 0 tokens. Good for
    budgeting a fetch, not for predicting a host's own accounting.
    """
    return (max(chars, 0) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# Columns added to existing tables, since CREATE TABLE IF NOT EXISTS never adds a column. Each must
# be nullable or carry a default: ADD COLUMN fills existing rows with it.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("diagrams", "font_scale", "REAL NOT NULL DEFAULT 1"),
    ("task_notes", "split_depends", "INTEGER NOT NULL DEFAULT 0"),
    ("diagram_nodes", "w", "REAL"),
    ("diagram_nodes", "h", "REAL"),
    ("memories", "also_domains", "TEXT NOT NULL DEFAULT ''"),
    ("memories", "review_after", "TEXT NOT NULL DEFAULT ''"),
    ("memories", "title", "TEXT NOT NULL DEFAULT ''"),
    ("memories", "source_ref", "TEXT NOT NULL DEFAULT ''"),
    ("memory_usage", "via_fts", "INTEGER NOT NULL DEFAULT 0"),
    ("memories", "pin", "TEXT NOT NULL DEFAULT ''"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add any column in _ADDED_COLUMNS the store does not have yet."""
    for table, column, decl in _ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # table itself is new; the schema above already has it
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _repair_task_states(conn: sqlite3.Connection) -> None:
    """Cancel an open task whose memory is not active.

    A writer that archives a memory without knowing tasks leaves its state
    open; nothing else would close it.
    """
    conn.execute(
        "UPDATE tasks SET state = 'cancelled' WHERE state = 'open' AND memory_uid IN "
        "(SELECT uid FROM memories WHERE status <> 'active')")


def _ensure_diagram_titles(conn: sqlite3.Connection) -> None:
    """Give a diagram memory the name its graph already carries.

    A diagram's title is one name stored twice: `diagrams.title` and
    `memories.title` (see update_diagram). A store whose diagrams predate
    the `memories.title` column has the name on the graph only, and every
    listing falls back to the opening line of the generated body.

    Copies, never invents: the length cap that title_error applies to a new
    title is not applied here, or a name already in the store would be
    unrecoverable.

    Runs after _ensure_fts: this UPDATE fires the index triggers, and on a
    store whose index predates the column they name a field memories_fts
    does not have yet.
    """
    conn.execute(
        "UPDATE memories SET title = ("
        "  SELECT TRIM(d.title) FROM diagrams d WHERE d.memory_uid = memories.uid)"
        " WHERE TRIM(title) = ''"
        "   AND EXISTS (SELECT 1 FROM diagrams d"
        "               WHERE d.memory_uid = memories.uid AND TRIM(d.title) <> '')")


def _ensure_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index when its columns are behind _FTS_SCHEMA.

    fts5 has no ALTER, and `CREATE VIRTUAL TABLE IF NOT EXISTS` does
    nothing at all for a store whose index predates a column -- so a newly
    indexed field means dropping the index and rebuilding it from the
    content table. Runs after _ensure_columns, which is what puts the new
    column on `memories` for the rebuild to read.

    The triggers go with it, and not as tidying: on an external-content
    index, a `delete` command has to hand fts5 the OLD value of EVERY
    column, so a trigger still naming three of four would corrupt the
    index on the next edit rather than fail visibly.
    """
    have = tuple(r["name"] for r in conn.execute("PRAGMA table_info(memories_fts)"))
    if have == _FTS_COLUMNS:
        return
    for trigger in ("memories_ai", "memories_ad", "memories_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute("DROP TABLE IF EXISTS memories_fts")
    conn.executescript(_FTS_SCHEMA)
    conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")


# Why the file holds free pages: removals free pages without shrinking the file, so the
# dashboard's disk row names the space and its VACUUM clears it.
COMPACT_REASON_KEY = "compact_reason"
COMPACT_REASON_VECTORS = "vector_store"


def get_compact_reason(conn: sqlite3.Connection) -> str:
    """What freed the pages VACUUM would give back, empty when nothing did."""
    return _get_meta(conn, COMPACT_REASON_KEY) or ""


def clear_compact_reason(conn: sqlite3.Connection) -> None:
    """Called after a VACUUM: the pages are back, so the reason is spent."""
    conn.execute("DELETE FROM meta WHERE key = ?", (COMPACT_REASON_KEY,))


# What a store can carry that nothing here reads: the sqlite-vec table and its vec0 shadow tables,
# the embedding meta keys, and two usage counters beside via_fts.
_VEC_TABLE = "memories_vec"
_VEC_META_KEYS = ("embed_model", "embed_dim")
_VEC_USAGE_COLUMNS = ("via_vec", "via_both")


def _drop_vector_store(conn: sqlite3.Connection) -> bool:
    """Remove the sqlite-vec table, its shadow tables and its counters.

    Returns True when it removed something. Runs on every connect, so a
    store carrying them is sanitized by being opened -- there is no separate
    restore path anyone has to remember.

    `DROP TABLE memories_vec` needs the vec0 module registered and nothing
    registers it, so the virtual table's declaration is deleted from the
    schema directly and the schema cookie bumped, which is what tells every
    other connection to re-read it. The shadow tables are ordinary tables
    and go the ordinary way once the declaration is gone.

    ALTER TABLE ... DROP COLUMN is SQLite 3.35 and later.
    """
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")]
    tables = [n for n in names if n == _VEC_TABLE or n.startswith(_VEC_TABLE + "_")]
    usage = {r["name"] for r in conn.execute("PRAGMA table_info(memory_usage)")}
    columns = [c for c in _VEC_USAGE_COLUMNS if c in usage]
    keys = [k for k in _VEC_META_KEYS if _get_meta(conn, k) is not None]
    if not tables and not columns and not keys:
        return False

    if _VEC_TABLE in tables:
        version = conn.execute("PRAGMA schema_version").fetchone()[0]
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute("DELETE FROM sqlite_master WHERE type = 'table' AND name = ?",
                     (_VEC_TABLE,))
        conn.execute(f"PRAGMA schema_version = {version + 1}")
        conn.execute("PRAGMA writable_schema = OFF")
        conn.commit()
    for name in tables:
        if name != _VEC_TABLE:
            conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    for column in columns:
        conn.execute(f"ALTER TABLE memory_usage DROP COLUMN {column}")
    if keys:
        conn.executemany("DELETE FROM meta WHERE key = ?", [(k,) for k in keys])
    if _VEC_TABLE in tables:
        # the columns and the meta keys free almost nothing; the table is
        # what leaves a file full of pages nobody has claimed back
        _set_meta(conn, COMPACT_REASON_KEY, COMPACT_REASON_VECTORS)
    return True


@contextmanager
def connect(db_path: Path | None = None, *, project: str | None = None):
    """A connection to one project's file, committed when the block exits cleanly.

    `db_path` opens that file, `project` opens the project of that name (in
    any casing), and neither opens the active project (see active_project).
    The schema and the migrations run on every open.
    """
    if db_path is not None and project is not None:
        raise ValueError("pass db_path or project, not both")
    path = db_path or (project_path(project) if project else default_db_path())
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    conn.executescript(item_schema())
    _ensure_columns(conn)
    _drop_vector_store(conn)
    _ensure_fts(conn)
    _ensure_diagram_titles(conn)
    _repair_task_states(conn)
    conn.executescript(ITEM_INDEXES)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
