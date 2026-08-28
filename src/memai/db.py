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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from memai import embed, sections

# Eager import for the same reason as in embed.py: sqlite_vec pulls in
# numpy, and importing that DLL lazily from inside a tool call deadlocks
# on Windows once the MCP stdio server is running.
try:
    import sqlite_vec
except Exception:  # pragma: no cover - extension unavailable
    sqlite_vec = None

# Domain-casing policy. Stored in the `meta` table under DOMAIN_CASE_KEY and
# enforced at every domain write path. 'preserve' keeps free-text casing;
# 'lower'/'upper' coerce every stored domain.
DOMAIN_CASE_KEY = "domain_case"
DOMAIN_CASE_MODES = ("preserve", "lower", "upper")
DOMAIN_CASE_DEFAULT = "preserve"

# A domain is one string that reads as a PATH: the segments between
# DOMAIN_SEP nest, outermost first, so 'acme/x100/p200' files a memory
# under a routine that belongs to a module that belongs to a product.
# Asking about the module includes its routines.
#
# The nesting lives in the string: no domains table, no id to resolve. A
# store with no separator anywhere is a tree of depth 1, and FTS tokenizes
# the ancestors into searchable words.
DOMAIN_SEP = "/"

# A memory is FILED at one path and can additionally BELONG to others. The
# path says where it lives -- one direct parent, the thing a re-home
# renames. A cross-listing says it is also part of a subject that cuts
# ACROSS the tree: the same routine belongs to the module it runs in and to
# the end-to-end flow it is a step of, and neither of those is the other's
# ancestor. `memory_domains` holds those extra memberships, one row per
# path, and every domain filter reads it (see domain_clause).
#
# `memories.also_domains` carries the same paths as one text field, for
# exactly the two readers that cannot join: the FTS index and the embedder.
# Nothing filters on it -- see _write_domain_links, its only writer.
ALSO_SEP = "\n"

# How much a memory is trusted, on its own axis: `status` says whether a row
# is in play at all, this says whether what it claims still holds. Up here
# rather than beside the curation code because retrieval reads it too --
# a contradicted memory sorts behind everything that still holds, and a
# warm-up leaves it out (see search_hybrid, _sound_clause).
CONFIDENCE_CONTRADICTED = "contradicted"

# The FTS index and its triggers, kept separate because they are also what a
# store built before a new indexed column has to be rebuilt from (_ensure_fts).
_FTS_COLUMNS = ("content", "tags", "domain", "also_domains")

# BM25 weights per indexed column, in _FTS_COLUMNS order. Unweighted, a
# domain match scored like a claim: every row filed under 'acme/cache'
# ranked for the word "cache" whether or not it said anything about one,
# and in a store organised by domain that is most of the store. The paths
# stay indexed -- a scope name should be findable -- they just stop
# outranking the memory that actually discusses the subject. Keyed by name
# so adding an indexed column without weighting it fails at import.
_FTS_WEIGHTS = {"content": 1.0, "tags": 0.8, "domain": 0.3, "also_domains": 0.3}
_BM25 = f"bm25(memories_fts, {', '.join(str(_FTS_WEIGHTS[c]) for c in _FTS_COLUMNS)})"

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, domain, also_domains,
    content='memories', content_rowid='rowid_pk',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, domain, also_domains)
    VALUES (new.rowid_pk, new.content, new.tags, new.domain, new.also_domains);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, domain, also_domains)
    VALUES ('delete', old.rowid_pk, old.content, old.tags, old.domain, old.also_domains);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, domain, also_domains)
    VALUES ('delete', old.rowid_pk, old.content, old.tags, old.domain, old.also_domains);
    INSERT INTO memories_fts(rowid, content, tags, domain, also_domains)
    VALUES (new.rowid_pk, new.content, new.tags, new.domain, new.also_domains);
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
-- via_* attribute a read to the retriever that surfaced it, so the store can
-- say whether the vector arm earns its keep without anyone parsing session
-- transcripts. A read with no search behind it (pulse, a list, get_memory)
-- counts in recall_count and in none of them.
CREATE TABLE IF NOT EXISTS memory_usage (
    memory_uid        TEXT PRIMARY KEY REFERENCES memories(uid),
    recall_count      INTEGER NOT NULL DEFAULT 0,
    last_recalled_at  TEXT NOT NULL,
    via_fts           INTEGER NOT NULL DEFAULT 0,
    via_vec           INTEGER NOT NULL DEFAULT 0,
    via_both          INTEGER NOT NULL DEFAULT 0
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


def renders_dir() -> Path:
    """Where generated SVG files go: a subdirectory, never the home root.

    The home root holds the database, its WAL and the backups, and a
    housekeeping sweep that deletes by age has no business running in a
    directory containing those. Keeping the renders in their own folder is
    what lets prune_renders() be a simple rule instead of a careful one.
    """
    out = default_db_path().parent / "renders"
    out.mkdir(parents=True, exist_ok=True)
    return out


# How long a generated SVG is kept. A render is a cache -- the diagram it
# came from is the real record -- so the only question is how much disk the
# user wants it to occupy.
SVG_RETENTION_KEY = "svg_retention"
SVG_RETENTION_MODES = ("1d", "7d", "30d", "never")
SVG_RETENTION_DEFAULT = "7d"
_RETENTION_DAYS = {"1d": 1, "7d": 7, "30d": 30}
# the two things this folder ever holds: a bare SVG, and the same drawing
# wrapped in a pan/zoom shell
RENDER_SUFFIXES = (".svg", ".html")


def get_svg_retention(conn: sqlite3.Connection) -> str:
    mode = _get_meta(conn, SVG_RETENTION_KEY)
    return mode if mode in SVG_RETENTION_MODES else SVG_RETENTION_DEFAULT


def set_svg_retention(conn: sqlite3.Connection, mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in SVG_RETENTION_MODES:
        raise ValueError(
            f"svg_retention must be one of {', '.join(SVG_RETENTION_MODES)}")
    _set_meta(conn, SVG_RETENTION_KEY, mode)
    return mode


WARDEN_ENABLED_KEY = "warden_enabled"
WARDEN_ENABLED_DEFAULT = True
WARDEN_MINUTES_KEY = "warden_minutes"
WARDEN_MINUTES_DEFAULT = 20
# A session is one conversation, so an interval longer than a working day
# would only ever fire once; below a minute the ask lands on every turn.
WARDEN_MINUTES_RANGE = (1, 480)


def get_warden_enabled(conn: sqlite3.Connection) -> bool:
    """Whether the Stop hook may ask a session to launch the warden.

    This setting is store-wide, and one store serves every project on the
    machine: turning it off here turns it off everywhere. That is the scope
    the question deserves -- it is a standing preference about what an agent
    costs, not a fact about one repository.
    """
    value = _get_meta(conn, WARDEN_ENABLED_KEY)
    return WARDEN_ENABLED_DEFAULT if value is None else value == "1"


def set_warden_enabled(conn: sqlite3.Connection, enabled: object) -> bool:
    """Persist the warden switch. Accepts a bool or the strings a form sends."""
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("", "0", "false", "off", "no")
    _set_meta(conn, WARDEN_ENABLED_KEY, "1" if enabled else "0")
    return bool(enabled)


def get_warden_minutes(conn: sqlite3.Connection) -> int:
    """How long a session goes before the warden is asked for again.

    A floor on the cost, not a schedule: the warden reads whole turns and
    costs a subagent run, and the ask lands on the first Stop after the
    interval, never between turns.
    """
    try:
        value = int(_get_meta(conn, WARDEN_MINUTES_KEY) or "")
    except ValueError:
        return WARDEN_MINUTES_DEFAULT
    low, high = WARDEN_MINUTES_RANGE
    return value if low <= value <= high else WARDEN_MINUTES_DEFAULT


def set_warden_minutes(conn: sqlite3.Connection, minutes: object) -> int:
    """Persist the warden interval, in minutes."""
    low, high = WARDEN_MINUTES_RANGE
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError):
        raise ValueError(f"warden_minutes must be a whole number of minutes "
                         f"between {low} and {high}")
    if not low <= value <= high:
        raise ValueError(f"warden_minutes must be between {low} and {high}")
    _set_meta(conn, WARDEN_MINUTES_KEY, str(value))
    return value


def renders_usage() -> dict:
    """What the render folder currently costs, for the maintenance view."""
    files = [p for p in renders_dir().iterdir()
             if p.is_file() and p.suffix in RENDER_SUFFIXES]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def prune_renders_all() -> dict:
    """Empty the render folder regardless of age.

    Separate from prune_renders rather than a magic mode, because "keep
    nothing" and "keep for N days" are different intentions and folding
    them together is how a retention setting of 1 day ends up meaning
    'delete everything' by accident.
    """
    return {**_sweep_renders(lambda _stat: True, keep=None), "mode": "all"}


def _sweep_renders(should_delete, *, keep: Path | None) -> dict:
    """The one place that deletes a render, so the guards live once.

    Narrow deliberately, because this removes files and MEMAI_HOME is
    whatever an environment variable says it is: only inside renders/, only
    a suffix in RENDER_SUFFIXES, only a real file, never a symlink, never
    recursive, and never `keep` -- the render being written right now.
    """
    pruned = 0
    freed = 0
    for path in renders_dir().iterdir():
        if path.suffix not in RENDER_SUFFIXES:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if keep is not None and path.resolve() == keep.resolve():
            continue
        try:
            stat = path.stat()
            if not should_delete(stat):
                continue
            path.unlink()
        except OSError:
            # a file another process is holding is not this sweep's problem
            continue
        pruned += 1
        freed += stat.st_size
    return {"pruned": pruned, "bytes": freed}


def prune_renders(mode: str, *, keep: Path | None = None) -> dict:
    """Delete generated renders older than the retention window.

    'never' removes nothing. Returns what it did rather than staying quiet:
    a sweep that silently deletes is one the user stops trusting.
    """
    days = _RETENTION_DAYS.get(mode)
    if days is None:
        return {"pruned": 0, "bytes": 0, "mode": mode}
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    swept = _sweep_renders(lambda stat: stat.st_mtime < cutoff, keep=keep)
    return {**swept, "mode": mode}


# ------------------------------------------------------------- going stale

# A memory about code is true until the code changes, and the store has no
# way to notice that on its own. `review_after` is the writer's own estimate
# of when the claim stops being safe to trust unchecked -- a date, so the
# question "what in here is overdue" is a comparison rather than a judgement,
# and a warm-up can ask it without reading anything.
_REVIEW_RELATIVE = re.compile(r"^(\d{1,4})\s*d$", re.I)


def today_iso() -> str:
    return now_iso()[:10]


def normalize_review_after(value: str, *, today: str | None = None) -> str:
    """A review date as 'YYYY-MM-DD', from a date or from '90d'.

    The relative form is there because that is how the answer arrives: a
    writer knows "this is worth rechecking in a quarter" and does not know
    today's date without asking. Empty means never -- most memories are not
    about anything that goes stale, and a store that made everyone pick a
    date would get dates nobody meant.
    """
    v = (value or "").strip()
    if not v:
        return ""
    rel = _REVIEW_RELATIVE.match(v)
    if rel:
        return (date.fromisoformat(today or today_iso())
                + timedelta(days=int(rel.group(1)))).isoformat()
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise ValueError(
            f"review_after must be a date ('2026-11-01') or a span ('90d'); got {value!r}")


def _due_clause(at: str | None = None) -> tuple[str, list]:
    """"this memory is overdue for a recheck", as SQL."""
    return "review_after <> '' AND review_after <= ?", [at or today_iso()]


def due_for_review(
    conn: sqlite3.Connection, *, domain: str = "", limit: int = 20,
    at: str | None = None, status: str = "active",
) -> list[sqlite3.Row]:
    """Active memories whose own review date has passed, oldest date first."""
    clause, params = _due_clause(at)
    sql = [f"SELECT * FROM memories WHERE {clause}"]
    if domain:
        scope, values, _ = domain_scope_clause(conn, domain, alias="", subtree=True)
        sql.append(scope)
        params.extend(values)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY review_after ASC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def new_uid() -> str:
    return secrets.token_hex(8)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Characters a token is worth in est_tokens. An ESTIMATE, not a tokenizer:
# no host's tokenizer is reachable from here, so the count is a fixed ratio
# over the character length.
CHARS_PER_TOKEN = 4


def est_tokens(chars: int) -> int:
    """Estimated token count for a body of `chars` characters.

    chars / CHARS_PER_TOKEN, rounded up; 0 characters is 0 tokens. Good for
    budgeting a fetch, not for predicting a host's own accounting.
    """
    return (max(chars, 0) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


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


def split_domain(domain: str) -> list[str]:
    """A domain path's segments, outermost first. Blank segments drop out."""
    return [s for s in (p.strip() for p in (domain or "").split(DOMAIN_SEP)) if s]


def normalize_domain(domain: str) -> str:
    """Canonical form of a domain path: trimmed segments, single separators.

    Every write path runs this, so 'acme / x100//' and 'acme/x100' are one
    domain and no caller can coin an empty segment -- a path with one
    would sit in the tree at a level nothing can name.
    """
    return DOMAIN_SEP.join(split_domain(domain))


def domain_parent(domain: str) -> str:
    """The path one level up. '' for a root, and for no domain at all."""
    return DOMAIN_SEP.join(split_domain(domain)[:-1])


def domain_ancestors(domain: str, *, include_self: bool = False) -> list[str]:
    """Every enclosing path, outermost first: acme, acme/x100, acme/x100/p200."""
    segs = split_domain(domain)
    end = len(segs) if include_self else len(segs) - 1
    return [DOMAIN_SEP.join(segs[: i + 1]) for i in range(max(end, 0))]


def domain_depth(domain: str) -> int:
    """How deep a path sits. 0 for no domain, 1 for a root."""
    return len(split_domain(domain))


def in_domain(domain: str, scope: str) -> bool:
    """True when `domain` IS `scope` or sits under it. An empty scope holds all.

    Segment-wise, not string-wise: 'acme/x1000' is not inside 'acme/x100'
    however similar the two read.
    """
    segs, want = split_domain(domain), split_domain(scope)
    return segs[: len(want)] == want


def parse_domains(value) -> list[str]:
    """Several domain paths out of one field, normalized and deduped.

    Splits on commas, semicolons and newlines. A path's own separator is
    '/', so those three are free to mean "next path" -- which is what a
    form field and a string-typed MCP argument both hand over. A list or
    tuple is taken as already split.
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;\n]", str(value))
    out: list[str] = []
    for raw in items:
        path = normalize_domain(str(raw))
        if path and path not in out:
            out.append(path)
    return out


def coerce_domain(conn: sqlite3.Connection, domain: str) -> tuple[str, str]:
    """Coerce a domain to the store's policy. Returns (coerced_domain, active_mode).

    Two rules, one call: the casing policy, and the path shape every
    reader assumes (see normalize_domain).
    """
    mode = get_domain_case(conn)
    return normalize_domain(case_domain(mode, domain)), mode


def apply_domain_policy(conn: sqlite3.Connection, domain: str) -> str:
    """Coerce a domain to the store's casing policy and canonical path shape."""
    return coerce_domain(conn, domain)[0]


def apply_link_policy(
    conn: sqlite3.Connection, also, primary: str, *, coerce: bool = True
) -> list[str]:
    """The cross-listings worth storing for a memory filed at `primary`.

    Casing and path shape come from the store's policy, same as the primary
    domain. A path the primary already satisfies is dropped: with the memory
    filed at 'acme/x100/p200', a cross-listing at 'acme' -- or at the
    primary itself -- matches nothing the prefix arm of a domain filter did
    not already match, and storing it would count the memory twice in its
    own branch. A path BELOW the primary is kept: it is a narrower scope,
    which is a real thing to say.

    coerce=False keeps the casing as given, for a caller rewriting exact
    stored strings rather than accepting new ones -- move_domain leaves the
    primary domain's casing alone for the same reason, and the pass that
    repairs casing (admin.normalize_domains) decides which strings it
    touches and reports them.

    Sorted, because this is a set and the order it arrives in means nothing.
    Sorting HERE rather than at each read is what keeps the `memory_domains`
    rows (read back ORDER BY domain) and the `also_domains` mirror (read back
    in stored order) telling one story: the same memory's memberships came
    back in two different orders while the mirror kept write order.
    """
    primary = normalize_domain(primary)
    out: list[str] = []
    for path in parse_domains(also):
        if coerce:
            path = apply_domain_policy(conn, path)
        if path and not in_domain(primary, path) and path not in out:
            out.append(path)
    return sorted(out)


def _embed_source(content: str, tags: str, domain: str, also: str = "") -> str:
    """The text a memory's vector is computed from -- same fields FTS indexes."""
    return "\n".join(p for p in (content, tags, domain, also) if p)


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
    ("memories", "also_domains", "TEXT NOT NULL DEFAULT ''"),
    ("memories", "review_after", "TEXT NOT NULL DEFAULT ''"),
    ("memories", "source_ref", "TEXT NOT NULL DEFAULT ''"),
    ("memory_usage", "via_fts", "INTEGER NOT NULL DEFAULT 0"),
    ("memory_usage", "via_vec", "INTEGER NOT NULL DEFAULT 0"),
    ("memory_usage", "via_both", "INTEGER NOT NULL DEFAULT 0"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add any column in _ADDED_COLUMNS the store does not have yet."""
    for table, column, decl in _ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # table itself is new; the schema above already has it
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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


# Whether every body in this store has been read into memory_sections. Set
def unread_sections(conn: sqlite3.Connection) -> int:
    """How many bodies of a sectioned type nobody has read yet.

    A body that has been read leaves something behind either way: the
    fields it was read into, or a queue row saying what stopped it. One
    with neither predates the spec its type now has.

    This is DERIVED rather than recorded, and that is the point. A stored
    "the migration ran" flag says nothing about which spec it ran under, so
    the day a type joins SECTION_SPEC the flag is a false claim nobody
    notices: the panel reports a clean store, the strict refusal stays on,
    and the bodies of the new type are frozen -- unreadable and unwritable
    at once. Counting them instead makes a store unread again the moment
    its spec grows, which is the state it is actually in.
    """
    types = tuple(sections.SECTION_SPEC)
    if not types:
        return 0
    marks = ",".join("?" * len(types))
    return conn.execute(
        f"""SELECT COUNT(*) FROM memories m
             WHERE m.type IN ({marks})
               AND NOT EXISTS (SELECT 1 FROM memory_sections s WHERE s.memory_uid = m.uid)
               AND NOT EXISTS (SELECT 1 FROM section_migration q WHERE q.memory_uid = m.uid)""",
        types,
    ).fetchone()[0]


def sections_read(conn: sqlite3.Connection) -> bool:
    """Whether every body whose type has fields has been read into them."""
    return unread_sections(conn) == 0


def section_error(conn: sqlite3.Connection, type: str, content: str) -> str | None:
    """Why this body cannot be written as this type, or None.

    Silent while any body of a sectioned type is still unread: refusing
    then would lock the very rows a human has to work through, and a store
    whose spec just grew is in exactly that state. Reading the store is
    what turns the refusal on.
    """
    if not sections.is_sectioned(type) or not sections_read(conn):
        return None
    problems = sections.read(type, content).problems
    if not problems:
        return None
    return (f"a {type} body is made of {', '.join(s.label for s in sections.spec_for(type))} "
            f"and this one does not read that way: {'; '.join(problems)}")


def _refuse_unreadable(conn: sqlite3.Connection, type: str, content: str) -> None:
    error = section_error(conn, type, content)
    if error:
        raise ValueError(error)


def _write_sections(conn: sqlite3.Connection, uid: str, type: str, content: str) -> None:
    """Read a body into its fields, replacing whatever was stored for it.

    The only writer of memory_sections and section_migration. Every writer
    of `memories.content` calls it with the body it just wrote, so the rows
    describe the text that is there now.

    A type with no spec keeps no rows in either table. A body that does not
    conform leaves a queue row saying what stops it, and whatever fields
    could still be read.
    """
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
    spec = sections.spec_for(type)
    if not spec:
        return
    reading = sections.read(type, content)
    seq = {s.key: i for i, s in enumerate(spec)}
    if reading.sections:
        conn.executemany(
            "INSERT INTO memory_sections (memory_uid, seq, key, text) VALUES (?, ?, ?, ?)",
            [(uid, seq[k], k, v) for k, v in reading.sections.items()],
        )
    if reading.problems:
        conn.execute(
            "INSERT INTO section_migration (memory_uid, verdict, detail, decided_at) "
            "VALUES (?, 'needs_review', ?, ?)",
            (uid, "; ".join(reading.problems), now_iso()),
        )


def migrate_sections(conn: sqlite3.Connection) -> dict:
    """Read every sectioned body in the store into memory_sections, once.

    Returns how many bodies already conformed, how many were rewritten to
    reach the canonical shape, and how many are left in the queue for a
    human. Re-running it rewrites nothing: what conformed on the first pass
    conforms on the second.

    A body whose only fault is text above its first label is rewritten from
    the fields that text hides -- the preamble goes, the fields are
    re-emitted in spec order. That rewrite goes through
    update_memory_content, so it lands in `edits` next to the body it
    replaced and the row is re-embedded.

    A body left in the queue can only be written through set_sections,
    which builds the body from its fields and so cannot produce another one
    that does not conform.
    """
    types = tuple(sections.SECTION_SPEC)
    rows = conn.execute(
        f"SELECT uid, type, content FROM memories WHERE type IN ({','.join('?' * len(types))})",
        types,
    ).fetchall()
    conformed = rewritten = 0
    for row in rows:
        uid, type_, content = row["uid"], row["type"], row["content"]
        if sections.read(type_, content).conforms:
            _write_sections(conn, uid, type_, content)
            conformed += 1
            continue
        salvaged = sections.salvage(type_, content)
        if salvaged.conforms:
            update_memory_content(conn, uid, sections.render(type_, salvaged.sections),
                                  note="sections: rewritten to the canonical body")
            rewritten += 1
            continue
        _write_sections(conn, uid, type_, content)
    pending = conn.execute("SELECT COUNT(*) AS n FROM section_migration").fetchone()["n"]
    return {"total": len(rows), "conformed": conformed,
            "rewritten": rewritten, "needs_review": pending}


_BODY_LINK = re.compile(r"\[\[([0-9a-f]{16})\]\]")


def body_links(conn: sqlite3.Connection, uid: str, content: str) -> dict[str, dict]:
    """What each [[uid]] written in a body points at, keyed by that uid.

    A wikilink is written INSIDE the text, so it says nothing about the
    relations table: `linked` reports whether an edge exists either way
    between the two, which is what turns a reference somebody typed into a
    reference the graph can be queried for. A uid nothing resolves comes
    back as {"missing": True} rather than being left out, so a reader is
    told the target is gone instead of being handed a link that fails.
    """
    targets = {m.group(1) for m in _BODY_LINK.finditer(content or "")} - {uid}
    if not targets:
        return {}
    edges = {
        r["other"] for r in conn.execute(
            "SELECT to_uid AS other FROM relations WHERE from_uid = ? "
            "UNION SELECT from_uid FROM relations WHERE to_uid = ?", (uid, uid))
    }
    found = {}
    marks = ",".join("?" * len(targets))
    for row in conn.execute(
        f"SELECT uid, type, domain, status, content FROM memories WHERE uid IN ({marks})",
        tuple(targets),
    ):
        found[row["uid"]] = {
            "uid": row["uid"], "type": row["type"], "domain": row["domain"],
            "status": row["status"], "snippet": (row["content"] or "")[:120],
            "linked": row["uid"] in edges,
        }
    for target in targets - set(found):
        found[target] = {"uid": target, "missing": True}
    return found


def section_problem(conn: sqlite3.Connection, uid: str) -> str:
    """What stops this memory's body conforming, or "" when nothing does."""
    row = conn.execute(
        "SELECT detail FROM section_migration WHERE memory_uid = ?", (uid,)
    ).fetchone()
    return row["detail"] if row else ""


def section_queue(conn: sqlite3.Connection) -> list[dict]:
    """The bodies that do not conform, newest first, with what stops each."""
    return [
        {"uid": r["uid"], "type": r["type"], "domain": r["domain"],
         "status": r["status"], "detail": r["detail"],
         "snippet": (r["content"] or "")[:160],
         "created_at": r["created_at"]}
        for r in conn.execute(
            """SELECT m.uid, m.type, m.domain, m.status, m.content, m.created_at, s.detail
                 FROM section_migration s JOIN memories m ON m.uid = s.memory_uid
                ORDER BY m.created_at DESC"""
        )
    ]


def set_sections(conn: sqlite3.Connection, uid: str, values: dict, note: str = "") -> bool:
    """Replace a memory's body with one rendered from its fields.

    The way out of the queue: the body is built from the fields rather than
    typed, so what it writes conforms as long as no field was left empty.
    Raises ValueError for a memory whose type has no spec, and for a field
    the spec does not name.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    spec = sections.spec_for(row["type"])
    if not spec:
        raise ValueError(f"a {row['type']} has no sections to set")
    unknown = sorted(set(values) - {s.key for s in spec})
    if unknown:
        raise ValueError(f"not a section of a {row['type']}: {', '.join(unknown)}")
    empty = [s.label for s in spec if not str(values.get(s.key, "")).strip()]
    if empty:
        raise ValueError(f"nothing under {', '.join(empty)}")
    return update_memory_content(conn, uid, sections.render(row["type"], values),
                                 note=note or "sections: set by hand")


def get_sections(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """A memory's fields in spec order; empty for a type with no spec."""
    return [
        {"key": r["key"], "text": r["text"]}
        for r in conn.execute(
            "SELECT key, text FROM memory_sections WHERE memory_uid = ? ORDER BY seq",
            (uid,),
        )
    ]


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
        """SELECT rowid_pk, content, tags, domain, also_domains FROM memories
           WHERE rowid_pk NOT IN (SELECT rowid FROM memories_vec)"""
    ).fetchall()
    if missing:
        blobs = embed.embed_texts([
            _embed_source(r["content"], r["tags"], r["domain"], r["also_domains"])
            for r in missing])
        if blobs:
            conn.executemany(
                "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                [(r["rowid_pk"], b) for r, b in zip(missing, blobs)],
            )


def _upsert_vector(
    conn: sqlite3.Connection, rowid_pk: int, content: str, tags: str, domain: str,
    also: str = "",
) -> None:
    if not _vec_ready(conn):
        return
    blobs = embed.embed_texts([_embed_source(content, tags, domain, also)])
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
    _ensure_fts(conn)
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
    also: str = "",
    session: str = "",
    tags: str = "",
    confidence: str = "unverified",
    created_at: str | None = None,
    review_after: str = "",
    source_ref: str = "",
) -> str:
    _refuse_unreadable(conn, type, content)
    uid = new_uid()
    ts = created_at or now_iso()
    domain = apply_domain_policy(conn, domain)
    links = apply_link_policy(conn, also, domain)
    blob = ALSO_SEP.join(links)
    review_after = normalize_review_after(review_after, today=(created_at or ts)[:10])
    cur = conn.execute(
        """INSERT INTO memories
           (uid, type, domain, also_domains, session, tags, content, status,
            confidence, created_at, updated_at, review_after, source_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
        (uid, type, domain, blob, session, tags, content, confidence, ts, ts,
         review_after, source_ref.strip()),
    )
    if links:
        conn.executemany(
            "INSERT INTO memory_domains (memory_uid, domain, created_at) VALUES (?, ?, ?)",
            [(uid, path, ts) for path in links],
        )
    _upsert_vector(conn, cur.lastrowid, content, tags, domain, blob)
    _write_sections(conn, uid, type, content)
    return uid


def restore_memory(conn: sqlite3.Connection, record: dict) -> str:
    """Write a memory back exactly as it was, uid and timestamps included.

    insert_memory() coins a uid and stamps `now`, which is right for a
    memory being made and wrong for one being restored: an import that
    renumbered every row would break every relation, node link and jump
    pointing at it, and would date a two-year-old decision to today.

    Domain policy is NOT applied. A restore reproduces a store; coercing
    the paths on the way in would mean an export and its import disagree
    about where things are filed, which is the one thing a round trip has
    to get right.
    """
    uid = str(record["uid"])
    ts = record.get("created_at") or now_iso()
    domain = normalize_domain(record.get("domain", ""))
    links = [normalize_domain(p) for p in parse_domains(record.get("also") or [])]
    links = [p for p in links if p and not in_domain(domain, p)]
    cur = conn.execute(
        """INSERT INTO memories
           (uid, type, domain, also_domains, session, tags, content, status,
            confidence, superseded_by, created_at, updated_at, review_after, source_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, record["type"], domain, ALSO_SEP.join(links),
         record.get("session", ""), record.get("tags", ""), record.get("content", ""),
         record.get("status", "active"), record.get("confidence", "unverified"),
         record.get("superseded_by") or None, ts, record.get("updated_at") or ts,
         record.get("review_after", ""), record.get("source_ref", "")),
    )
    if links:
        conn.executemany(
            "INSERT INTO memory_domains (memory_uid, domain, created_at) VALUES (?, ?, ?)",
            [(uid, path, ts) for path in links])
    if record.get("recalls"):
        conn.execute(
            "INSERT INTO memory_usage (memory_uid, recall_count, last_recalled_at) "
            "VALUES (?, ?, ?)",
            (uid, int(record["recalls"]), record.get("last_recall") or ts))
    _upsert_vector(conn, cur.lastrowid, record.get("content", ""),
                   record.get("tags", ""), domain, ALSO_SEP.join(links))
    _write_sections(conn, uid, record["type"], record.get("content", ""))
    return uid


def restore_diagram(conn: sqlite3.Connection, record: dict) -> None:
    """Put a diagram's graph back under an already-restored memory row.

    Positions come from the export rather than the layout engine: an
    arrangement somebody made by hand is part of the record, and
    recomputing it on import would quietly redraw every flow in the store.
    """
    uid = str(record["uid"])
    conn.execute(
        "INSERT INTO diagrams (memory_uid, kind, title, summary, font_scale) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, record.get("diagram_kind", "flowchart"), record.get("title", ""),
         record.get("summary", ""), record.get("font_scale", 1)))
    conn.executemany(
        "INSERT INTO diagram_nodes (memory_uid, node_key, shape, label, note, seq, x, y, w, h) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(uid, n["key"], n.get("shape", "step"), n.get("label", ""), n.get("note", ""),
          i, n.get("x") or 0.0, n.get("y") or 0.0, n.get("w"), n.get("h"))
         for i, n in enumerate(record.get("nodes") or [])])
    conn.executemany(
        "INSERT INTO diagram_edges (memory_uid, from_key, to_key, label, seq) "
        "VALUES (?, ?, ?, ?, ?)",
        [(uid, e["from"], e["to"], e.get("label", ""), i)
         for i, e in enumerate(record.get("edges") or [])])


def restore_diagram_refs(conn: sqlite3.Connection, record: dict) -> None:
    """A diagram's links and jumps, once every memory they name exists.

    Separate from restore_diagram because both point at OTHER memories: a
    flow can link a step to a note filed later in the file, and a jump
    reaches a diagram that has not been read yet. Skips a reference whose
    other end is not in the import -- a partial export is a legitimate
    thing to restore, and a dangling row is not.
    """
    uid = str(record["uid"])
    ts = record.get("created_at") or now_iso()

    def known(other: str) -> bool:
        return get_memory(conn, other) is not None

    conn.executemany(
        "INSERT OR IGNORE INTO diagram_node_links "
        "(memory_uid, node_key, target_uid, relation_type, created_at) VALUES (?, ?, ?, ?, ?)",
        [(uid, l["node_key"], l["target_uid"], l.get("relation_type", "explains"),
          l.get("created_at") or ts)
         for l in (record.get("links") or []) if known(l["target_uid"])])
    # only the outgoing side: a jump is stored once and read from both ends,
    # so restoring both would write the same row twice
    conn.executemany(
        "INSERT OR IGNORE INTO diagram_jumps "
        "(from_uid, from_node, to_uid, to_node, label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [(uid, j["node_key"], j["peer_uid"], j.get("peer_node", ""), j.get("label", ""),
          j.get("created_at") or ts)
         for j in (record.get("jumps") or [])
         if j.get("direction") == "out" and known(j["peer_uid"])])


def get_memory(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM memories WHERE uid = ?", (uid,)).fetchone()


def update_memory_content(
    conn: sqlite3.Connection, uid: str, new_content: str, note: str = "",
    *, append: bool = False,
) -> bool:
    """Replace a memory's content, or add to the end of it.

    append exists because the alternative is a caller reading the whole
    body, restating it, and sending it back to add one line -- which costs
    the body twice and stakes the existing text on it being copied
    faithfully. The edit history records the same thing either way: what it
    said before, and what it says now.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    if append:
        new_content = f"{row['content']}\n{new_content}" if row["content"] else new_content
    _refuse_unreadable(conn, row["type"], new_content)
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], new_content, note),
    )
    conn.execute(
        "UPDATE memories SET content = ?, updated_at = ? WHERE uid = ?",
        (new_content, now_iso(), uid),
    )
    _upsert_vector(conn, row["rowid_pk"], new_content, row["tags"], row["domain"],
                   row["also_domains"])
    _write_sections(conn, uid, row["type"], new_content)
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


def set_review_after(conn: sqlite3.Connection, uid: str, value: str) -> bool:
    """Move (or clear) a memory's recheck date, and audit the move.

    No re-embedding: the vector is computed over content, tags and domains,
    and a date is none of those. Audited, because "this was rechecked and
    pushed out six months" is exactly the kind of decision a later pass
    needs to be able to see it did not invent.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    value = normalize_review_after(value)
    if value == row["review_after"]:
        return True
    conn.execute(
        "UPDATE memories SET review_after = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid))
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"meta: review_after '{row['review_after']}' -> '{value}'"))
    return True


def set_source_ref(conn: sqlite3.Connection, uid: str, value: str, note: str = "") -> bool:
    """Point (or repoint) a memory at what it came from, and audit the move.

    No re-embedding, for the same reason as the date: the vector is computed
    over content, tags and domains. Audited, because the reference is what a
    later pass checks the claim against, and "this was pointed at that file
    on purpose" is not something it should have to infer.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    value = value.strip()
    if value == row["source_ref"]:
        return True
    conn.execute(
        "UPDATE memories SET source_ref = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid))
    audit = f"meta: source_ref '{row['source_ref']}' -> '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"{audit} ({note})" if note else audit))
    return True


def purge_memory(conn: sqlite3.Connection, uid: str) -> bool:
    """Irreversibly delete a memory row plus its edit history and relations.

    The memories_ad trigger removes the matching FTS row as part of the
    DELETE. Callers must gate this behind explicit user confirmation --
    forget() (soft-delete/archive) is the default and should be used
    unless the user specifically asked for permanent removal.

    Diagram tables cascade both ways: the graph of a purged diagram goes,
    and so does any OTHER diagram's node link or jump that pointed at this
    memory -- otherwise a purged note leaves a node link dangling at a uid
    that no longer resolves.

    `memory_domains` goes with it for the same reason, and the FK on that
    table means it HAS to: the DELETE below is refused outright while a
    cross-listing still names this uid. No mirror to rewrite -- the row
    itself is on its way out. `memory_sections` and `section_migration`
    carry the same FK and go the same way.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute("DELETE FROM memory_domains WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM memory_usage WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM edits WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM relations WHERE from_uid = ? OR to_uid = ?", (uid, uid))
    conn.execute("DELETE FROM optimization_suggestions WHERE target_uid = ?", (uid,))
    conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? OR target_uid = ?", (uid, uid)
    )
    conn.execute("DELETE FROM diagram_jumps WHERE from_uid = ? OR to_uid = ?", (uid, uid))
    conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM diagram_edges WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM diagrams WHERE memory_uid = ?", (uid,))
    if _vec_ready(conn):
        conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (row["rowid_pk"],))
    conn.execute("DELETE FROM memories WHERE uid = ?", (uid,))
    return True


# ----------------------------------------------------------------- usage

MATCH_SOURCES = ("fts", "vec", "both")


def record_recall(
    conn: sqlite3.Connection, uids, *, at: str | None = None,
    sources: dict[str, str] | None = None,
) -> int:
    """Count one read of each of these memories. Returns how many it touched.

    Called from the MCP tools and from nowhere else, deliberately. What
    this measures is "an agent was handed this memory in answer to
    something", and a person scrolling the dashboard is not that -- letting
    the admin surface inflate the counters would turn the one signal the
    curation pass has into a record of who browsed what.

    `sources` maps uid -> match_source for reads that came out of a search,
    so the store can later say which retriever is worth its keep. A read
    with no search behind it passes none, and counts only in recall_count.

    None of this may ever reach a ranking -- see the schema comment on
    memory_usage for why, and test_usage.py for the test that says so.

    Best-effort: a uid that no longer exists is skipped rather than failing
    the read that produced it.
    """
    seen = [u for u in dict.fromkeys(uids) if u]
    if not seen:
        return 0
    ts = at or now_iso()
    live = {r["uid"] for r in conn.execute(
        f"SELECT uid FROM memories WHERE uid IN ({', '.join('?' * len(seen))})", seen)}
    rows = []
    for u in seen:
        if u not in live:
            continue
        arm = (sources or {}).get(u)
        rows.append((u, ts, int(arm == "fts"), int(arm == "vec"), int(arm == "both")))
    conn.executemany(
        "INSERT INTO memory_usage "
        "(memory_uid, recall_count, last_recalled_at, via_fts, via_vec, via_both) "
        "VALUES (?, 1, ?, ?, ?, ?) ON CONFLICT(memory_uid) DO UPDATE SET "
        "recall_count = recall_count + 1, last_recalled_at = excluded.last_recalled_at, "
        "via_fts = via_fts + excluded.via_fts, via_vec = via_vec + excluded.via_vec, "
        "via_both = via_both + excluded.via_both",
        rows,
    )
    return len(rows)


def usage_for(conn: sqlite3.Connection, uids) -> dict[str, dict]:
    """{uid: {"recalls": n, "last_recall": iso}} for the ones ever read."""
    seen = [u for u in dict.fromkeys(uids) if u]
    if not seen:
        return {}
    rows = conn.execute(
        f"SELECT memory_uid, recall_count, last_recalled_at FROM memory_usage "
        f"WHERE memory_uid IN ({', '.join('?' * len(seen))})", seen).fetchall()
    return {r["memory_uid"]: {"recalls": r["recall_count"],
                              "last_recall": r["last_recalled_at"]} for r in rows}


def arm_effectiveness(conn: sqlite3.Connection) -> dict:
    """How much of what got read came from each retriever.

    The question "does the vector arm earn its keep" answered by the store
    itself, over real use, instead of by a benchmark's guess at what a query
    looks like. Read it as a ratio between the arms and not as an absolute:
    a memory can be acted on from its snippet without ever being opened, so
    every arm is undercounted by the same unknown amount.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(via_fts), 0) AS fts, COALESCE(SUM(via_vec), 0) AS vec, "
        "COALESCE(SUM(via_both), 0) AS both, COALESCE(SUM(recall_count), 0) AS reads "
        "FROM memory_usage").fetchone()
    out = {arm: row[arm] for arm in MATCH_SOURCES}
    out["reads"] = row["reads"]
    out["from_search"] = sum(out[arm] for arm in MATCH_SOURCES)
    return out


def add_relation(
    conn: sqlite3.Connection, from_uid: str, to_uid: str, relation_type: str, note: str = ""
) -> int:
    """Create a typed edge, or raise ValueError saying which rule it broke.

    The checks live here rather than in each caller because the two
    surfaces have to agree: the dashboard refused a self-edge, an unknown
    uid and a duplicate, and the MCP tool refused nothing -- a typo'd uid
    reached the INSERT, where the foreign key turned it into a raw
    IntegrityError rather than something an agent could act on.
    """
    if not (from_uid and to_uid and relation_type):
        raise ValueError("from_uid, to_uid and relation_type are required")
    if from_uid == to_uid:
        raise ValueError("a memory cannot relate to itself")
    for uid in (from_uid, to_uid):
        if get_memory(conn, uid) is None:
            raise ValueError(f"unknown memory: {uid}")
    dup = conn.execute(
        "SELECT id FROM relations WHERE from_uid = ? AND to_uid = ? AND relation_type = ?",
        (from_uid, to_uid, relation_type)).fetchone()
    if dup:
        raise ValueError(f"identical relation already exists (id {dup['id']})")
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

    # Within a row, discovery order. BFS already groups a node's children
    # next to each other, and the crossings that remain come from
    # many-to-many fan-in, which no ordering within a row can remove.
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
    also: str = "",
    session: str = "",
    tags: str = "",
    review_after: str = "",
    source_ref: str = "",
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
        domain=domain, also=also, session=session, tags=tags or DIAGRAM_TYPE,
        review_after=review_after, source_ref=source_ref,
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
    relayout_diagram() for a clean arrangement. Node links and jumps
    pointing at keys the rewrite dropped go with them -- including a jump
    ANOTHER diagram aimed at one of those keys, which this diagram is the
    only one able to notice.
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
    holes = ",".join("?" * len(live))
    conn.execute(
        f"DELETE FROM diagram_node_links WHERE memory_uid = ? AND node_key NOT IN ({holes})",
        (uid, *live),
    )
    conn.execute(
        f"DELETE FROM diagram_jumps WHERE from_uid = ? AND from_node NOT IN ({holes})",
        (uid, *live),
    )
    # to_node = '' is the diagram as a whole and survives any rewrite
    conn.execute(
        "DELETE FROM diagram_jumps WHERE to_uid = ? AND to_node <> '' "
        f"AND to_node NOT IN ({holes})",
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
    """Remove a node together with its edges, its memory links and its jumps.

    Both ends of a jump, because the step is gone from the picture either
    way: a jump leaving it has nothing to leave from, and a jump another
    diagram aimed AT it has nowhere to land.
    """
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
    conn.execute(
        """DELETE FROM diagram_jumps
           WHERE (from_uid = ? AND from_node = ?) OR (to_uid = ? AND to_node = ?)""",
        (uid, node_key, uid, node_key),
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


def add_diagram_jump(
    conn: sqlite3.Connection, uid: str, from_node: str, to_uid: str,
    to_node: str = "", label: str = "",
) -> tuple[bool, list[str]]:
    """Point one step of a flow at another FLOW -- optionally at one of its steps.

    A routine documented as one diagram usually is not one routine: a
    branch hands off to a second flow, which hands back. That handoff is
    not prose to read beside the step (add_node_link) but a place to go,
    and it is the same statement from either side -- so it is stored once
    and read from both ends.

    An empty `to_node` means the target diagram as a whole. Jumping inside
    one diagram is refused: that is an edge, and drawing it is the honest
    way to say it.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    if to_uid == uid:
        return False, ["a jump goes to another diagram; inside one, draw an edge"]
    if get_diagram_row(conn, to_uid) is None:
        return False, [f"jump target {to_uid!r} is not a diagram"]
    from_node = str(from_node).strip()
    to_node = str(to_node or "").strip()
    if conn.execute(
        "SELECT 1 FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (uid, from_node)
    ).fetchone() is None:
        return False, [f"no node {from_node!r} in {uid}"]
    if to_node and conn.execute(
        "SELECT 1 FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (to_uid, to_node)
    ).fetchone() is None:
        return False, [f"no node {to_node!r} in {to_uid}"]
    conn.execute(
        """INSERT INTO diagram_jumps (from_uid, from_node, to_uid, to_node, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(from_uid, from_node, to_uid, to_node)
           DO UPDATE SET label = excluded.label""",
        (uid, from_node, to_uid, to_node, str(label or "").strip(), now_iso()),
    )
    return True, []


def delete_diagram_jump(
    conn: sqlite3.Connection, uid: str, node_key: str, peer_uid: str, peer_node: str = ""
) -> bool:
    """Drop one jump, named from EITHER end.

    `uid`/`node_key` is the caller's own side and `peer_uid`/`peer_node`
    the other one, whichever way the arrow points. The row is a single
    statement shared by two diagrams, so the diagram on the receiving end
    has to be able to cut it too -- otherwise the only way out of an
    unwanted incoming jump is to go and open the diagram that made it.
    """
    cur = conn.execute(
        """DELETE FROM diagram_jumps
           WHERE (from_uid = ? AND from_node = ? AND to_uid = ? AND to_node = ?)
              OR (from_uid = ? AND from_node = ? AND to_uid = ? AND to_node = ?)""",
        (uid, node_key, peer_uid, peer_node, peer_uid, peer_node, uid, node_key),
    )
    return cur.rowcount > 0


# One query per direction. Which column carries the peer is the only
# difference between them, and folding the two into a single UNION hid
# exactly that.
_JUMP_SQL = """SELECT j.*, d.title AS peer_title, m.status AS peer_status,
                      n.label AS peer_node_label
               FROM diagram_jumps j
               JOIN diagrams d ON d.memory_uid = j.{far}_uid
               JOIN memories m ON m.uid = j.{far}_uid
               LEFT JOIN diagram_nodes n
                      ON n.memory_uid = j.{far}_uid AND n.node_key = j.{far}_node
               WHERE j.{near}_uid = ? ORDER BY j.{near}_node, j.created_at"""


def get_diagram_jumps(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """Every jump touching this diagram, both directions, as one list.

    `node_key` is always the step in THIS diagram, so the editor groups
    them per step without caring which way a jump points -- '' for an
    incoming jump aimed at the diagram as a whole. `peer_node` is where to
    land at the other end, which is what makes the trip back arrive on the
    step it left from rather than on a diagram and a hunt.
    """
    return [
        {
            "direction": direction,
            "node_key": r[f"{near}_node"],
            "peer_uid": r[f"{far}_uid"],
            "peer_node": r[f"{far}_node"],
            "peer_title": r["peer_title"],
            "peer_node_label": r["peer_node_label"] or "",
            "peer_status": r["peer_status"],
            "label": r["label"],
            "created_at": r["created_at"],
        }
        for direction, near, far in (("out", "from", "to"), ("in", "to", "from"))
        for r in conn.execute(_JUMP_SQL.format(near=near, far=far), (uid,)).fetchall()
    ]


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
        "jumps": get_diagram_jumps(conn, uid),
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
    conn: sqlite3.Connection, *, domain: str = "", status: str = "active",
    subtree: bool = True,
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
        clause, values, _ = domain_scope_clause(conn, domain, subtree=subtree)
        where.append(clause.removeprefix("AND "))
        params.extend(values)
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
    # counted from both ends: a diagram nothing leaves but three flows arrive
    # into is just as tied into the set as the one that made those jumps
    jumps_by: dict[str, int] = {}
    for r in conn.execute(
        """SELECT uid, COUNT(*) AS n FROM (
               SELECT from_uid AS uid FROM diagram_jumps
               UNION ALL SELECT to_uid AS uid FROM diagram_jumps
           ) GROUP BY uid"""
    ):
        jumps_by[r["uid"]] = r["n"]

    # the flows cross-listed into other subjects: the Diagrams view groups by
    # branch, and a flow that is a step of an end-to-end process belongs
    # under that process's branch as well as its own
    also_by = domain_links_for(conn, [r["uid"] for r in rows])

    out = []
    for r in rows:
        nodes = nodes_by.get(r["uid"], [])
        edges = edges_by.get(r["uid"], [])
        issues = _graph_issues(nodes, edges)
        out.append({
            **dict(r),
            "also": also_by.get(r["uid"], []),
            "nodes": len(nodes),
            "edges": len(edges),
            "links": links_by.get(r["uid"], 0),
            "jumps": jumps_by.get(r["uid"], 0),
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
    ("reranking teacher model" or "best of n dpo critic") and get the union
    of matches back, instead of one narrow AND match. BM25 scores a row
    matching every term far above a row matching one, so the union does not
    cost the precise query its rank.

    Recall rises with the number of terms and barely moves with their
    phrasing, which is why search() tells callers to spend terms rather than
    to word the query carefully. tools/bench-retrieval.py measures both.
    """
    terms = [t.strip() for t in raw.replace(" OR ", " ").split() if t.strip()]
    if not terms:
        return raw
    # EVERY term is quoted, not only the ones with punctuation in them. A bare
    # alphanumeric term looked safe and is not: 'AND', 'NOT' and 'NEAR' are
    # fts5 operators, so a query containing one reached the engine as syntax
    # and took the whole search down with an OperationalError -- out of a tool
    # call, that is a crash rather than a bad result. Quoting is free: a
    # quoted single token matches exactly what the bare one did.
    escaped = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(escaped)


def _like_needle(value: str) -> str:
    """Escape a literal so it matches itself inside a LIKE ... ESCAPE '\\'.

    `_` is a single-character wildcard, and real identifiers carry one
    ('anti_pattern', 'F100_TOTAL'), so an unescaped needle silently
    matches rows nobody asked for.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _path_predicate(col: str, path: str, subtree: bool) -> tuple[str, list]:
    """"this column holds that path" -- the scope arm, or just the bucket."""
    if not subtree:
        return f"{col} = ?", [path]
    return (
        f"({col} = ? OR {col} LIKE ? ESCAPE '\\')",
        [path, _like_needle(path) + DOMAIN_SEP + "%"],
    )


def domain_clause(
    domain: str, *, alias: str = "m", subtree: bool = True, also: bool = True
) -> tuple[str, list]:
    """SQL fragment + bound values that scope a query to one domain.

    Subtree by default, because a domain names a scope and the reason the
    scope nests is that asking about 'acme/x100' must not hide what is
    filed under 'acme/x100/p200'. In a store with no nesting the prefix
    arm matches nothing extra, so this returns exactly the rows plain
    equality used to.

    subtree=False is for the questions about the bucket itself rather than
    the scope: what is filed at THIS path, and which rows a rename of this
    exact string has to rewrite.

    A scope also holds what is CROSS-LISTED into it -- the same predicate,
    over the extra memberships in `memory_domains` -- because a memory that
    belongs to two subjects belongs to both when either one is asked about.
    also=False drops that arm, for the operations that mean the filed path
    and nothing else: a re-home rewrites where a memory LIVES, and matching
    a cross-listing there would move a memory that only passes through.
    """
    col = f"{alias}.domain" if alias else "domain"
    path = normalize_domain(domain)
    own, values = _path_predicate(col, path, subtree)
    if not also:
        return f"AND {own}", values
    linked, link_values = _path_predicate("dl.domain", path, subtree)
    uid = f"{alias}.uid" if alias else "uid"
    return (
        f"AND ({own} OR EXISTS (SELECT 1 FROM memory_domains dl "
        f"WHERE dl.memory_uid = {uid} AND {linked}))",
        [*values, *link_values],
    )


def all_domains_sql() -> str:
    """Every distinct path in use, filed or cross-listed, in a `domain` column.

    One statement, because "which domains exist" has two sources now and a
    caller that reads only `memories` would miss a subject that exists
    purely as a cross-listing -- which is a legitimate way for one to exist.
    """
    return (
        "SELECT DISTINCT domain FROM ("
        "SELECT domain FROM memories WHERE domain <> '' "
        "UNION SELECT domain FROM memory_domains WHERE domain <> '')"
    )


def _fold(segments: list[str]) -> list[str]:
    """Segments as a filter compares them: case is not part of the name.

    A domain is a name a caller types from memory, and the two casings of
    one subject are the same subject -- which is why the dashboard reports
    'acme/Cache' next to 'acme/cache' as drift to merge rather than as two
    scopes. Matching folds; the STORED spelling is what comes back, so a
    resolved scope is always a real path and its equality arm hits.
    """
    return [s.lower() for s in segments]


def _inner_scope(stored: list[str], want: list[str]) -> str | None:
    """Where `want` sits inside a stored path, as a path down to its end.

    'p200' inside 'acme/x100/p200/warmup' is the scope 'acme/x100/p200'
    -- the level asked for, with whatever it contains. The OUTERMOST
    occurrence wins: a repeated segment name inside one path is a level and
    a sublevel of itself, and the level is the one that was asked for.

    Compared folded, returned as stored (see _fold). Position 0 is not a
    special case here: a path matching at the front is the literal reading,
    and resolve_domain_scopes takes that pass before this one.
    """
    folded_stored, folded_want = _fold(stored), _fold(want)
    for i in range(len(stored) - len(want) + 1):
        if folded_stored[i:i + len(want)] == folded_want:
            return DOMAIN_SEP.join(stored[: i + len(want)])
    return None


def resolve_domain_scopes(conn: sqlite3.Connection, domain: str) -> list[str]:
    """The paths a domain filter should cover, given what the caller wrote.

    A path taken from the tree matches as a prefix, which is the normal
    case and settles here immediately. But the name a caller has in hand is
    usually the DEEP end of the path -- a routine code, not the product it
    belongs to -- and 'p200' matches no prefix once that routine lives at
    'acme/x100/p200'. So when nothing in the store starts with the string,
    it is tried as a run of segments anywhere inside a path, and every
    branch it names becomes a scope.

    Two deliberate properties. The literal reading always wins: if 'p200'
    also exists as a top-level domain, that is what a filter on 'p200'
    means, and the routine buried elsewhere is not silently mixed in. And
    an ambiguous name broadens instead of guessing -- a code filed under
    two modules resolves to both scopes, and callers are told which
    (`scope.paths` in pulse(), `domain_scope` in the admin responses),
    because picking one silently is the failure this exists to avoid.

    Returns the requested path unchanged when nothing matches, so an
    unknown domain still means "no rows" rather than "everything".

    Cross-listings count as paths in use on both readings: a subject that
    exists only because memories were cross-listed into it is a real scope,
    and it resolves literally like any other.

    Casing is not part of either reading. The store's policy is applied
    first, so a store that coerces to one case resolves a filter written in
    the other; and both passes then match folded (see _fold), because a
    'preserve' store keeps whatever spelling each write happened to use and
    a caller has no way to know which one that was. Every scope returned is
    a path as STORED. `LIKE` ignores case and `=` does not, so a scope
    resolved to the caller's spelling hands the equality arm a string no row
    carries: the descendants match and the path itself is missed.
    """
    path = normalize_domain(case_domain(get_domain_case(conn), domain))
    if not path:
        return []
    # Fast path: something is filed at exactly this string. The literal
    # reading is then settled and no scan is needed -- which is the common
    # call, a path taken from list_domains() and passed straight back.
    if conn.execute(
        "SELECT 1 FROM (SELECT domain FROM memories UNION "
        "SELECT domain FROM memory_domains) WHERE domain = ? LIMIT 1",
        (path,),
    ).fetchone():
        return [path]

    want = split_domain(path)
    stored = [split_domain(r["domain"]) for r in conn.execute(all_domains_sql())]
    # The literal reading, folded: every stored path this one is the front
    # of, cut back to the depth that was asked for. Covers the same-path
    # spelling and the level that exists only because something deeper is
    # filed under it -- both are "this path", not a name found inside one.
    literal = {
        DOMAIN_SEP.join(segments[:len(want)])
        for segments in stored
        if _fold(segments[:len(want)]) == _fold(want)
    }
    if literal:
        return sorted(literal)

    scopes: set[str] = set()
    for segments in stored:
        found = _inner_scope(segments, want)
        if found:
            scopes.add(found)
    # No scope in here can contain another, so there is nothing to collapse:
    # a scope that ended deeper than an ancestor scope would mean the query
    # also occurred at the ancestor's depth in that same path, and
    # _inner_scope would have stopped there. Which is why it takes the
    # outermost occurrence -- the alternative needs this set pruned.
    return sorted(scopes) or [path]


def domain_scope_clause(
    conn: sqlite3.Connection, domain: str, *, alias: str = "m", subtree: bool = True,
    also: bool = True,
) -> tuple[str, list, list[str]]:
    """domain_clause over every scope the filter resolves to.

    Returns (sql, params, scopes) -- `scopes` is what the query actually
    covers, which is the string the caller passed unless resolution had to
    reach for it (see resolve_domain_scopes).
    """
    scopes = resolve_domain_scopes(conn, domain)
    parts: list[str] = []
    params: list = []
    for scope in scopes:
        clause, values = domain_clause(scope, alias=alias, subtree=subtree, also=also)
        parts.append(clause.removeprefix("AND "))
        params.extend(values)
    return f"AND ({' OR '.join(parts)})", params, scopes


def _tag_clause(tag: str, alias: str = "m") -> tuple[str, str]:
    """SQL fragment + bound value for "this row carries this tag".

    `tags` is one comma-separated string, so a bare LIKE '%flag%' also
    matches 'flagged' and 'feature-flag'. Padding both the column and the
    needle with commas makes the boundaries explicit.

    Two details the shape forces. Spaces are stripped from both sides
    because the column is hand-written and 'a, b' is as common as 'a,b';
    the cost is that a tag with an interior space matches without it,
    which is a trade the alternative (a tags table) is not worth here.
    And % _ \\ are escaped, because a tag like 'anti_pattern' would
    otherwise be a LIKE pattern matching 'anti-pattern' too.
    """
    col = f"{alias}.tags" if alias else "tags"
    needle = _like_needle(tag.replace(" ", ""))
    return (
        f"AND (',' || REPLACE({col}, ' ', '') || ',') LIKE ('%,' || ? || ',%') ESCAPE '\\'",
        needle,
    )


def search_memories(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    tag: str = "",
    status: str = "active",
    limit: int = 30,
    subtree: bool = True,
) -> list[sqlite3.Row]:
    sql = [
        f"""SELECT m.*, {_BM25} AS rank
           FROM memories_fts
           JOIN memories m ON m.rowid_pk = memories_fts.rowid
           WHERE memories_fts MATCH ?"""
    ]
    params: list = [_fts_query(query)]
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, subtree=subtree)
        sql.append(clause)
        params.extend(values)
    if type:
        sql.append("AND m.type = ?")
        params.append(type)
    if tag:
        clause, needle = _tag_clause(tag)
        sql.append(clause)
        params.append(needle)
    if status:
        sql.append("AND m.status = ?")
        params.append(status)
    sql.append("ORDER BY rank LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


_KNN_MAX_K = 10000  # upper bound on the "fetch (nearly) all, then filter" KNN path

# How far a neighbor may be and still count as one. A KNN has no notion of
# "nothing here is close": ask it for 200 rows and it returns the 200 nearest
# however far away they are, so without a cut a query for a word the store
# does not hold sweeps in the whole store, every row past the keyword matches
# labelled `match_source: vec`.
#
# Calibrated against ONE corpus and ONE embedding model. A different model has
# a different distance scale; re-measure with tools/bench-retrieval.py before
# trusting the number.
VEC_MAX_DISTANCE = 0.60

# A query that is nothing but an opaque token -- a uid, a sha, a hex blob --
# has no semantic neighborhood to find. The tokenizer still splits it into
# subword pieces, those pieces DO have vectors, and the KNN dutifully answers
# with whatever they land near.
#
# Measured on a 386-vector store: a random 16-char hex reaches a nearest
# neighbor at cosine +0.644, against +0.656 for a real question. The arm
# cannot tell one from the other, and VEC_MAX_DISTANCE cannot be placed
# between them. Twelve characters is the floor because a uid is sixteen and
# a shortened sha is seven to twelve; below that the pattern starts matching
# words like 'facade' and 'decade'.
_OPAQUE_QUERY = re.compile(r"[0-9a-f]{12,}", re.IGNORECASE)


def is_opaque_query(raw: str) -> bool:
    """True when the whole query is one opaque identifier, nothing else.

    Only the whole query counts: a uid inside a sentence leaves the rest of
    the words to search on, and that search is worth running.
    """
    q = raw.strip()
    return bool(q) and _OPAQUE_QUERY.fullmatch(q) is not None


def search_semantic(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    tag: str = "",
    status: str = "active",
    limit: int = 30,
    subtree: bool = True,
    max_distance: float | None = None,
) -> list[sqlite3.Row]:
    """Brute-force KNN over the vector table, filtered post-KNN.

    Bounded by distance as well as by count (see VEC_MAX_DISTANCE): a KNN
    asked for more rows than it has close neighbors returns far ones, and a
    far one is not a match. Pass max_distance=2 to see the raw ordering.

    None rather than VEC_MAX_DISTANCE as the default: a default argument is
    evaluated once at import, so naming the constant here would freeze the
    cut at its import-time value and put a runtime change out of reach.

    Returns [] when vectors are unavailable, and for a query that is one
    opaque identifier (see is_opaque_query) -- so callers can always call
    this unconditionally. domain/type/tag/status filters apply *after* the
    nearest-neighbor pass, so a fixed limit*4 over-fetch can starve a
    small, selective domain: if every one of the limit*4 global nearest
    neighbors belongs to another domain, the filter leaves nothing even
    when relevant in-domain vectors exist just outside that window. When a
    domain/type/tag filter narrows the result we therefore widen k to the
    whole vector set (capped at _KNN_MAX_K) so the post-KNN filter keeps the
    right rows in correct distance order; unfiltered searches keep the cheap
    fixed over-fetch.
    """
    if max_distance is None:
        max_distance = VEC_MAX_DISTANCE
    if is_opaque_query(query):
        return []
    if not _vec_ready(conn):
        return []
    blobs = embed.embed_texts([query])
    if not blobs:
        return []
    if domain or type or tag:
        total = conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
        k = min(max(total, 1), _KNN_MAX_K)
    else:
        k = max(limit * 4, 50)
    sql = [
        """SELECT m.*, v.distance AS vec_distance
           FROM (SELECT rowid, distance FROM memories_vec
                 WHERE embedding MATCH ? AND k = ?) v
           JOIN memories m ON m.rowid_pk = v.rowid
           WHERE v.distance <= ?"""
    ]
    params: list = [blobs[0], k, max_distance]
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, subtree=subtree)
        sql.append(clause)
        params.extend(values)
    if type:
        sql.append("AND m.type = ?")
        params.append(type)
    if tag:
        clause, needle = _tag_clause(tag)
        sql.append(clause)
        params.append(needle)
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
    tag: str = "",
    status: str = "active",
    limit: int = 30,
    subtree: bool = True,
    collapse: bool = False,
) -> list[dict]:
    """FTS BM25 + vector KNN, merged by reciprocal rank fusion.

    Each result dict carries `match_source` ("fts" | "vec" | "both"),
    plus `fts_rank` (bm25, lower = better) and/or `vec_distance`
    (cosine, lower = closer) so the agent can judge each candidate.
    Ordering is RRF, but it's a candidate ordering, not a verdict --
    the agent decides relevance, same as FTS-only did.

    Type is not part of the ordering. A diagram earns its place the way
    every other memory does: it comes back when it matches, where its
    scores put it.

    collapse=True folds near-identical results into the best-ranked one of
    them (see _collapse_near_copies). Off by default: it is a concession to
    a caller paying for every row in a context window, and the dashboard is
    the opposite case -- a human curating the store needs to SEE that the
    same fact was written five times, which is what the dedup queue is for.
    """
    # A query that IS a uid names one row, and the hybrid index cannot match
    # on it: a uid occurs in OTHER bodies as [[uid]], so the search answers
    # "what points at this" and never "this". The named row is pinned above
    # its referrers -- the same treatment admin.py gives a pasted uid, here so
    # every caller inherits it rather than the dashboard alone.
    pinned: dict | None = None
    if is_opaque_query(query):
        row = get_memory(conn, query.strip())
        if row is not None and (not status or row["status"] == status):
            pinned = dict(row)
            pinned["match_source"] = "uid"

    K = 60  # standard RRF damping constant
    merged: dict[str, dict] = {}

    def fold(rows, source: str, weight: float = 1.0) -> None:
        for i, row in enumerate(rows):
            uid = row["uid"]
            contribution = weight / (K + i + 1)
            seen = merged.get(uid)
            if seen is None:
                d = dict(row)
                if source == "fts":
                    d["fts_rank"] = d.pop("rank")
                d["match_source"] = source
                d["_rrf"] = contribution
                merged[uid] = d
            else:
                if source == "fts":
                    seen["fts_rank"] = row["rank"]
                else:
                    seen["vec_distance"] = row["vec_distance"]
                seen["match_source"] = "both"
                seen["_rrf"] += contribution

    # `_FUSION_FETCH` rows per unit of `limit` from each arm, taken as one
    # wider LIMIT rather than a second query.
    deep = limit * _FUSION_FETCH
    fold(search_memories(conn, query, domain=domain, type=type, tag=tag,
                         status=status, limit=deep, subtree=subtree), "fts")
    fold(search_semantic(conn, query, domain=domain, type=type, tag=tag,
                         status=status, limit=deep, subtree=subtree), "vec", VEC_WEIGHT)

    # Contradicted last, and only then by score: a memory marked known-wrong
    # still comes back, never ahead of one that still holds.
    ranked = sorted(
        merged.values(),
        key=lambda d: (d.get("confidence") == CONFIDENCE_CONTRADICTED, -d["_rrf"]),
    )
    results = (_collapse_near_copies(ranked) if collapse else ranked)[:limit]
    for d in results:
        del d["_rrf"]
    if pinned is not None:
        results = [pinned] + [r for r in results if r["uid"] != pinned["uid"]]
        results = results[:limit]
    _attach_succession(conn, results)
    return results


# What a vector hit is worth next to a keyword hit, in the fusion. Below the
# equal vote plain RRF would give it: at an equal vote the vector arm displaces
# keyword hits at the same rank.
#
# Calibrated against ONE corpus and ONE embedding model, like
# VEC_MAX_DISTANCE; re-measure with tools/bench-retrieval.py before moving it.
VEC_WEIGHT = 0.5

# How many rows each retriever fetches per unit of `limit` before fusion. The
# deeper the fetch, the more of the weaker arm's ranking gets a vote, so
# raising this is a claim that both retrievers are informative -- measure it
# against the store with tools/bench-retrieval.py first.
_FUSION_FETCH = 1

# Above this difflib ratio two results are the same text, not two takes on
# one subject. Near-identity on purpose: this drops copies of one fact, it
# does not judge whether two related memories are each worth a slot.
_COPY_RATIO = 0.92


def _collapse_near_copies(ranked: list[dict]) -> list[dict]:
    """Drop a result that repeats one already kept, and say so on the keeper.

    Lexical, the same measure dedup_candidates falls back to: it matches
    near-identical text, not paraphrases.
    """
    import difflib

    kept: list[dict] = []
    for d in ranked:
        content = d.get("content") or ""
        for k in kept:
            if difflib.SequenceMatcher(None, content, k.get("content") or "").quick_ratio() >= _COPY_RATIO:
                k.setdefault("collapsed", []).append(d["uid"])
                break
        else:
            kept.append(d)
    return kept


def _attach_succession(conn: sqlite3.Connection, results: list[dict]) -> None:
    """Mark a result that something in the store already supersedes.

    The relations graph knew the answer and retrieval did not read it, so a
    superseded memory came back looking current with its replacement sitting
    one edge away. `succeeded_by` is the uid(s) to read instead -- separate
    from the `superseded_by` COLUMN, which set_status writes when archiving
    and which says nothing about an edge drawn between two active rows.
    """
    if not results:
        return
    uids = [d["uid"] for d in results]
    rows = conn.execute(
        f"SELECT from_uid, to_uid FROM relations WHERE relation_type = 'supersedes' "
        f"AND to_uid IN ({', '.join('?' * len(uids))})",
        uids,
    ).fetchall()
    after: dict[str, list[str]] = {}
    for r in rows:
        after.setdefault(r["to_uid"], []).append(r["from_uid"])
    for d in results:
        if d["uid"] in after:
            d["succeeded_by"] = after[d["uid"]]


def _sound_clause(exclude_contradicted: bool) -> str:
    """The arm that keeps known-wrong memories out of a list.

    Off by default: list_by_domain/list_recent are the fallback for a search
    that came back thin, and a caller asking for everything in a scope means
    everything. pulse() opts in, because a warm-up presents what it returns
    as the current state -- a contradicted anti-pattern read there is a
    pitfall to avoid, not one that turned out not to be.
    """
    return f" AND confidence <> '{CONFIDENCE_CONTRADICTED}'" if exclude_contradicted else ""


def list_by_domain(
    conn: sqlite3.Connection, domain: str, *, type: str = "", status: str = "active",
    limit: int = 50, subtree: bool = True, exclude_contradicted: bool = False,
) -> list[sqlite3.Row]:
    """Recency-ordered rows of one domain, its subdomains included.

    subtree=False narrows to what is filed at exactly this path.
    """
    clause, params, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
    sql = [f"SELECT * FROM memories WHERE 1=1 {clause}{_sound_clause(exclude_contradicted)}"]
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
    conn: sqlite3.Connection, *, type: str = "", domain: str = "", tag: str = "",
    status: str = "active", limit: int = 20, subtree: bool = True,
    exclude_contradicted: bool = False,
) -> list[sqlite3.Row]:
    sql = [f"SELECT * FROM memories WHERE 1=1{_sound_clause(exclude_contradicted)}"]
    params: list = []
    if type:
        sql.append("AND type = ?")
        params.append(type)
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        sql.append(clause)
        params.extend(values)
    if tag:
        clause, needle = _tag_clause(tag, alias="")
        sql.append(clause)
        params.append(needle)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY created_at DESC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def timeline_neighbours(
    conn: sqlite3.Connection, anchor: sqlite3.Row, *, before: int = 3, after: int = 3,
    domain: str = "", type: str = "", status: str = "active", subtree: bool = True,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """The rows either side of `anchor` in creation order.

    Returns (older, newer): the `before` rows created immediately before the
    anchor and the `after` rows created immediately after it, both
    oldest-first. The anchor is in neither list. `domain`/`type`/`status`
    narrow the neighbourhood only -- the anchor is the row the caller passes
    in, wherever it is filed and whatever its status.

    Ordering is (created_at, rowid_pk). created_at is not unique: two rows
    written in the same instant compare equal, and the insertion order is
    what separates them -- without it a row can land on both sides.
    """
    where = ["1=1"]
    params: list = []
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        where.append(clause)
        params.extend(values)
    if type:
        where.append("AND type = ?")
        params.append(type)
    if status:
        where.append("AND status = ?")
        params.append(status)
    where_sql = " ".join(where)
    at = [anchor["created_at"], anchor["created_at"], anchor["rowid_pk"]]

    older = conn.execute(
        f"""SELECT * FROM memories WHERE {where_sql}
              AND (created_at < ? OR (created_at = ? AND rowid_pk < ?))
            ORDER BY created_at DESC, rowid_pk DESC LIMIT ?""",
        [*params, *at, max(before, 0)],
    ).fetchall()
    newer = conn.execute(
        f"""SELECT * FROM memories WHERE {where_sql}
              AND (created_at > ? OR (created_at = ? AND rowid_pk > ?))
            ORDER BY created_at ASC, rowid_pk ASC LIMIT ?""",
        [*params, *at, max(after, 0)],
    ).fetchall()
    return list(reversed(older)), list(newer)


def list_domains(
    conn: sqlite3.Connection, *, status: str = "active"
) -> list[dict]:
    """Every domain in the store, as the nodes of the domain tree.

    Warm-up discovery. domain is free text and drifts over time (e.g.
    'proj-1042' vs 'proj-1042-cache-warmup'), so listing the real
    strings lets the caller target the right one instead of guessing.

    Both counts are reported, because they answer different questions:
    `count` is what is filed at exactly this path, `subtree` is that plus
    everything nested under it. A domain holding nothing of its own can
    still be the right thing to warm up -- that is what a parent IS.

    `also` and `subtree_also` are the same two questions for the memories
    CROSS-LISTED here rather than filed here -- counted apart, because a
    scope's own size and how much of another subject passes through it are
    different facts and adding them would make the tree deeper than the
    store. A node with `count` 0 and `also` above it exists purely as a
    cross-cutting subject, which is a legitimate way for one to exist.

    Ancestors nobody wrote to directly still get a node, flagged
    `implicit`: one 'acme/x100/p200' means the tree has an 'acme' and an
    'acme/x100', whether or not a memory was ever filed at either. Being
    cross-listed at a path names it as surely as being filed there, so that
    clears `implicit` too.
    Ordering stays recency-first (by subtree activity, so a parent sorts
    with its liveliest child), alphabetical within a tie. `latest_at` counts
    both kinds of activity, or a subject that only ever gets cross-listed
    into would sink to the bottom of the tree it organizes.
    """
    where = "AND m.status = ?" if status else ""
    params: list = [status] if status else []
    rows = conn.execute(
        "SELECT m.domain AS domain, COUNT(*) AS count, MAX(m.created_at) AS latest_at "
        f"FROM memories m WHERE m.domain <> '' {where} GROUP BY m.domain", params).fetchall()
    link_rows = conn.execute(
        "SELECT dl.domain AS domain, COUNT(*) AS count, MAX(m.created_at) AS latest_at "
        "FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid "
        f"WHERE dl.domain <> '' {where} GROUP BY dl.domain", params).fetchall()

    nodes: dict[str, dict] = {}

    def node(path: str) -> dict:
        return nodes.setdefault(path, {
            "domain": path, "count": 0, "latest_at": "",
            "parent": domain_parent(path), "depth": domain_depth(path),
            "subtree": 0, "subtree_latest_at": "", "children": 0,
            "also": 0, "subtree_also": 0,
            "implicit": True,
        })

    for source, key in ((rows, "count"), (link_rows, "also")):
        for r in source:
            # normalized on every write path; normalizing again keeps a store
            # written to directly from splitting one path across two nodes
            n = node(normalize_domain(r["domain"]))
            n[key] += r["count"]
            n["latest_at"] = max(n["latest_at"], r["latest_at"] or "")
            n["implicit"] = False
            for ancestor in domain_ancestors(n["domain"]):
                node(ancestor)

    for n in list(nodes.values()):
        for scope in domain_ancestors(n["domain"], include_self=True):
            holder = nodes[scope]
            holder["subtree"] += n["count"]
            holder["subtree_also"] += n["also"]
            holder["subtree_latest_at"] = max(holder["subtree_latest_at"], n["latest_at"])
        if n["parent"]:
            nodes[n["parent"]]["children"] += 1

    out = sorted(nodes.values(), key=lambda n: n["domain"])
    out.sort(key=lambda n: n["subtree_latest_at"], reverse=True)
    return out


def domain_census(
    conn: sqlite3.Connection, domain: str = "", *, status: str = "active"
) -> dict:
    """What a domain scope holds, and how it splits one level down.

    pulse() returns the newest few of each type; this is how it can say what
    it did NOT return. `by_type` counts the whole scope, so a caller can see
    that 5 of 13 notes came back and reach for search()/list_by_domain()
    with the domain it already has.

    `children` stops at the NEXT level rather than walking the subtree: "what
    else is in here" is answered a level at a time, and the child's own
    census is one call away when the work goes there. Each child reports
    `own` (filed at that path) and `subtree` (that plus everything under it),
    because a child that holds nothing itself can still be where the branch
    lives.

    An empty `domain` censuses the whole store, and then `children` are its
    roots.

    A memory CROSS-LISTED into the scope is part of the scope -- it counts
    in `total` and `by_type` like any other. But its filed path is somewhere
    else entirely, so it cannot be placed by that path: the level it sits at
    HERE is the one its cross-listing names. `also` reports how much of the
    scope arrives that way, and each child carries its own `also`/
    `subtree_also`, so a drill-down plan built from this never points at a
    child that turns out to hold nothing. All three are omitted when zero --
    a store that never cross-lists should not pay for the field on every
    child of every pulse.
    """
    scopes = resolve_domain_scopes(conn, domain)
    where, params = ["1=1"], []
    if scopes:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=True)
        where.append(clause)
        params.extend(values)
    if status:
        where.append("AND status = ?")
        params.append(status)
    where_sql = " ".join(where)
    rows = conn.execute(
        f"SELECT domain, type, COUNT(*) AS n FROM memories WHERE {where_sql} "
        "GROUP BY domain, type", params).fetchall()
    # the cross-listings of the in-scope memories, by the path they name --
    # one row per (link path, type), so a child is placed by the membership
    # that put the memory in this scope rather than by where it lives
    link_rows = conn.execute(
        "SELECT dl.domain AS domain, m.type AS type, COUNT(*) AS n "
        "FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid "
        f"WHERE m.rowid_pk IN (SELECT rowid_pk FROM memories WHERE {where_sql}) "
        "GROUP BY dl.domain, m.type", params).fetchall()

    by_type: dict[str, int] = {}
    kids: dict[str, dict] = {}

    def kid(child: str) -> dict:
        return kids.setdefault(child, {
            "domain": child, "own": 0, "subtree": 0, "also": 0, "subtree_also": 0})

    def trim(k: dict) -> dict:
        return {key: v for key, v in k.items() if v or key not in ("also", "subtree_also")}

    def place(path: str, n: int, own_key: str, subtree_key: str) -> None:
        for scope in (scopes or [""]):
            if path == scope or not in_domain(path, scope):
                continue
            child = DOMAIN_SEP.join(split_domain(path)[: domain_depth(scope) + 1])
            k = kid(child)
            k[subtree_key] += n
            if path == child:
                k[own_key] += n

    also_total = 0
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + r["n"]
        # in scope only because of a cross-listing: nothing about its filed
        # path belongs to this census beyond the count
        if scopes and not any(in_domain(r["domain"], s) for s in scopes):
            also_total += r["n"]
            continue
        place(r["domain"], r["n"], "own", "subtree")
    for r in link_rows:
        place(r["domain"], r["n"], "also", "subtree_also")
    out = {
        "paths": scopes,
        "total": sum(by_type.values()),
        "by_type": by_type,
        "children": [trim(k) for k in sorted(
            kids.values(),
            key=lambda k: (-(k["subtree"] + k["subtree_also"]), k["domain"]))],
    }
    if also_total:
        out["also"] = also_total
    # Overdue for a recheck. One count, omitted when zero, because it is the
    # one thing a warm-up can say about DECAY -- everything else it reports
    # is what the scope holds, not whether it still holds.
    due_clause, due_params = _due_clause()
    stale = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND {due_clause}",
        [*params, *due_params]).fetchone()[0]
    if stale:
        out["stale"] = stale
    return out


def move_domain(
    conn: sqlite3.Connection, src: str, dst: str, *, subtree: bool = True
) -> dict:
    """Re-home a domain and, by default, everything nested under it.

    Moving 'acme/x100' to 'acme/legacy' takes 'acme/x100/p200' along as
    'acme/legacy/p200': the descendants are part of what the operator
    pointed at, and leaving them behind would silently split a subject in
    two. A merge is not a separate operation -- renaming onto a path that
    already holds memories means those two sets are one domain now.

    Every moved row is re-embedded (domain is part of the embedding
    source) and audited in `edits`, so a re-home is reconstructible.

    Cross-listings pointing INTO the moved scope follow it: renaming a
    subject renames it for the memories that merely belong to it too, or
    they would be left pointing at a path that no longer exists. What does
    NOT follow is the memory itself -- the rows to re-home are matched on
    the filed path alone (also=False), because a cross-listing says a memory
    belongs to a subject, not that it lives there.

    Refuses to move a domain into its own subtree: 'acme' -> 'acme/x100'
    would make the path its own ancestor.

    `src` is matched as given AND in canonical form. Every writer normalizes,
    so the two are the same string in practice -- but a row written straight
    into the table can hold a shape no writer would produce ('acme//x100 '),
    and repairing exactly that is what the normalize pass names it for.
    """
    src_given, src, dst = src or "", normalize_domain(src), normalize_domain(dst)
    if not src:
        raise ValueError("source domain is required")
    if not dst:
        raise ValueError("target domain is required")
    if src == dst and src_given == src:
        raise ValueError("source and target are the same")
    if subtree and in_domain(dst, src):
        raise ValueError(f"cannot move '{src}' into its own subtree ('{dst}')")

    # `memory_domains` names its path `domain` too, so one clause selects
    # the rows filed in the moved scope and the cross-listings into it.
    clause, params = domain_clause(src, alias="", subtree=subtree, also=False)
    if src_given != src:
        clause = f"AND ({clause.removeprefix('AND ')} OR domain = ?)"
        params = [*params, src_given]
    rows = conn.execute(
        f"SELECT rowid_pk, uid, content, tags, domain, also_domains FROM memories "
        f"WHERE 1=1 {clause}", params).fetchall()
    link_rows = conn.execute(
        f"SELECT memory_uid, domain FROM memory_domains WHERE 1=1 {clause}",
        params).fetchall()
    if not rows and not link_rows:
        raise ValueError(f"no memories in domain '{src}'")

    # "merge" means rows that were NOT part of this move already live at
    # the target scope -- asked before the UPDATE, or every move would
    # look like one
    moving = {r["rowid_pk"] for r in rows}
    dst_clause, dst_params = domain_clause(dst, alias="", subtree=True, also=False)
    merged = any(
        r["rowid_pk"] not in moving for r in conn.execute(
            f"SELECT rowid_pk FROM memories WHERE 1=1 {dst_clause}", dst_params))

    def retarget(old: str) -> str:
        return dst if old in (src, src_given) else dst + DOMAIN_SEP + old[len(src) + 1:]

    now = now_iso()
    touched: set[str] = set()
    for r in rows:
        old = r["domain"]
        target = retarget(old)
        conn.execute(
            "UPDATE memories SET domain = ?, updated_at = ? WHERE rowid_pk = ?",
            (target, now, r["rowid_pk"]))
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["uid"], now, r["content"], r["content"],
             f"meta: domain '{old}' → '{target}'"))
        _upsert_vector(conn, r["rowid_pk"], r["content"], r["tags"], target,
                       r["also_domains"])
        touched.add(old)

    # Retargeting a cross-listing can land it on the memory's own path -- a
    # re-home that makes the two subjects one -- and apply_link_policy then
    # drops it. Same on the other side: a memory whose FILED path just moved
    # under a subject it was cross-listed into no longer needs the
    # cross-listing. Both go through set_domain_links, which is where that
    # policy lives, rather than an UPDATE per row.
    #
    # Only the paths this move SELECTED are retargeted. A subtree=False move
    # is one exact string at a time (the normalize pass), and rewriting a
    # descendant membership here would rename it out from under its own entry
    # in that plan, which then finds nothing to move.
    moved_paths = {r["domain"] for r in link_rows}
    relinked = sorted({r["memory_uid"] for r in link_rows})
    for uid in relinked:
        want = [retarget(p) if p in moved_paths else p
                for p in get_domain_links(conn, uid)]
        set_domain_links(conn, uid, want, coerce=False,
                         note=f"meta: also '{src}' → '{dst}'")
    for r in rows:
        if r["also_domains"] and r["uid"] not in relinked:
            set_domain_links(conn, r["uid"], get_domain_links(conn, r["uid"]),
                             coerce=False)
    touched.update(r["domain"] for r in link_rows)
    return {
        "moved": len(rows), "domains": len(touched), "merged": merged,
        "also_moved": len(relinked),
    }


def set_domain_status(
    conn: sqlite3.Connection, domain: str, status: str, *, note: str = ""
) -> dict:
    """Archive (or restore) every memory FILED in a domain scope.

    A domain has no status of its own -- it exists because memories name it,
    so "archive this domain" means archiving what is filed under it,
    subdomains included. The view then reads a level with archived memories
    and none active as an archived branch.

    Matched on the filed path alone (also=False), like a re-home: a memory
    cross-listed into the subject lives in another branch, and archiving a
    subject it merely belongs to would reach outside what was pointed at.

    Only rows that actually change are touched, and their uids come back --
    which is what makes an Undo exact. Restoring "everything in the scope"
    would also revive whatever had been archived long before, for reasons
    that have nothing to do with this pass.
    """
    path = normalize_domain(domain)
    if not path:
        raise ValueError("domain is required")
    if status not in ("active", "archived"):
        raise ValueError("status must be 'active' or 'archived'")
    clause, params = domain_clause(path, alias="", subtree=True, also=False)
    rows = conn.execute(
        f"SELECT uid, domain FROM memories WHERE status <> ? {clause}",
        [status, *params]).fetchall()
    for r in rows:
        set_status(conn, r["uid"], status, note=note)
    return {
        "uids": [r["uid"] for r in rows],
        "domains": len({r["domain"] for r in rows}),
    }


def purge_domain(conn: sqlite3.Connection, domain: str) -> dict:
    """Irreversibly delete a domain: every memory filed in it, subtree included.

    The scope-wide counterpart of purge_memory, and it carries the same
    warning N times over -- callers must gate it behind an explicit typed
    confirmation. set_domain_status(status='archived') is the reversible
    reading of "get rid of this domain" and is what the UI offers first.

    Cross-listings pointing INTO the scope go too, because the path they name
    stops existing. The memories holding them do NOT: one is filed in another
    branch and only belonged to this subject, so it loses the membership and
    keeps its own life. Dropped through set_domain_links, which is what keeps
    `memory_domains` and the `also_domains` mirror telling one story.
    """
    path = normalize_domain(domain)
    if not path:
        raise ValueError("domain is required")
    clause, params = domain_clause(path, alias="", subtree=True, also=False)
    rows = conn.execute(
        f"SELECT uid, domain FROM memories WHERE 1=1 {clause}", params).fetchall()
    link_rows = conn.execute(
        f"SELECT memory_uid, domain FROM memory_domains WHERE 1=1 {clause}",
        params).fetchall()
    if not rows and not link_rows:
        raise ValueError(f"no memories in domain '{path}'")

    purged = {r["uid"] for r in rows}
    for uid in sorted(purged):
        purge_memory(conn, uid)
    # a purged memory took its own memberships with it, so what is left here
    # is the memories filed elsewhere that merely belonged to the scope
    unlinked = sorted({r["memory_uid"] for r in link_rows} - purged)
    for uid in unlinked:
        set_domain_links(
            conn, uid,
            [p for p in get_domain_links(conn, uid) if not in_domain(p, path)],
            coerce=False, note=f"meta: also '{path}' dropped (domain deleted)")
    return {
        "purged": len(purged), "unlinked": len(unlinked),
        "domains": len({r["domain"] for r in rows}
                       | {r["domain"] for r in link_rows}),
    }


def get_domain_links(conn: sqlite3.Connection, uid: str) -> list[str]:
    """The extra domains one memory belongs to, beside the one it is filed at."""
    return [r["domain"] for r in conn.execute(
        "SELECT domain FROM memory_domains WHERE memory_uid = ? ORDER BY domain",
        (uid,))]


def domain_links_for(conn: sqlite3.Connection, uids) -> dict[str, list[str]]:
    """get_domain_links for a page of rows, in one query.

    A list view showing rows under a domain filter has to be able to say
    which of them are only cross-listed there, and a per-row lookup would
    make that N queries for a cosmetic truth.
    """
    wanted = set(uids)
    if not wanted:
        return {}
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT memory_uid, domain FROM memory_domains ORDER BY domain"):
        if r["memory_uid"] in wanted:
            out.setdefault(r["memory_uid"], []).append(r["domain"])
    return out


def _write_domain_links(conn: sqlite3.Connection, row: sqlite3.Row, paths: list[str]) -> list[str]:
    """Rewrite one memory's cross-listings: rows, index text, vector.

    `memory_domains` is the truth every domain filter reads.
    `memories.also_domains` is the same paths as one field, and exists for
    the two readers that cannot join it: the FTS index and the embedder.
    Nothing filters on that field -- which is why it is written here, and
    only here, in the same breath as the rows it mirrors.
    """
    uid = row["uid"]
    conn.execute("DELETE FROM memory_domains WHERE memory_uid = ?", (uid,))
    now = now_iso()
    if paths:
        conn.executemany(
            "INSERT INTO memory_domains (memory_uid, domain, created_at) VALUES (?, ?, ?)",
            [(uid, path, now) for path in paths])
    blob = ALSO_SEP.join(paths)
    conn.execute(
        "UPDATE memories SET also_domains = ?, updated_at = ? WHERE uid = ?",
        (blob, now, uid))
    _upsert_vector(conn, row["rowid_pk"], row["content"], row["tags"], row["domain"], blob)
    return paths


def set_domain_links(
    conn: sqlite3.Connection, uid: str, also, *, note: str = "", coerce: bool = True
) -> list[str]:
    """Replace the extra domains a memory belongs to. Returns what was stored.

    Audited in `edits` like any other metadata change, so a membership that
    was added and later dropped is still reconstructible. coerce=False keeps
    the casing as given -- see apply_link_policy.
    """
    row = get_memory(conn, uid)
    if row is None:
        raise ValueError(f"no memory {uid}")
    before = get_domain_links(conn, uid)
    paths = apply_link_policy(conn, also, row["domain"], coerce=coerce)
    if paths == before:
        return paths
    _write_domain_links(conn, row, paths)
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         note or f"meta: also '{', '.join(before)}' → '{', '.join(paths)}'"))
    return paths


def add_domain_link(conn: sqlite3.Connection, uid: str, domain: str) -> list[str]:
    """Cross-list a memory into one more domain. Returns the resulting set."""
    if not normalize_domain(domain):
        raise ValueError("domain is required")
    return set_domain_links(conn, uid, [*get_domain_links(conn, uid), domain])


def remove_domain_link(conn: sqlite3.Connection, uid: str, domain: str) -> list[str]:
    """Drop one cross-listing. Returns the resulting set.

    Matched on the exact path: dropping 'acme' does not drop a separate
    membership in 'acme/x100', which is a scope of its own.
    """
    path = normalize_domain(domain)
    return set_domain_links(
        conn, uid, [p for p in get_domain_links(conn, uid) if p != path])


def latest_by_type(
    conn: sqlite3.Connection, type: str, *, domain: str = "", status: str = "active",
    exclude_contradicted: bool = False,
) -> sqlite3.Row | None:
    rows = list_recent(conn, type=type, domain=domain, status=status, limit=1,
                       exclude_contradicted=exclude_contradicted)
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


# What a just-written memory has to resemble before the writer says so.
# Higher than dedup_candidates' 0.6, which feeds a review queue a human
# reads at leisure: this one interrupts an agent mid-write, so it has to be
# quiet unless the collision is real.
SIMILAR_ON_WRITE = 0.75
SIMILAR_ON_WRITE_MAX = 3
SIMILAR_SNIPPET = 160


def similar_memories(
    conn: sqlite3.Connection, uid: str, *,
    threshold: float = SIMILAR_ON_WRITE, limit: int = SIMILAR_ON_WRITE_MAX,
) -> list[dict]:
    """What the store already held that closely resembles this memory.

    For the moment of writing, which is the only moment the answer is
    free to act on: the agent still has the context that produced the
    text, so it can tell a correction from a duplicate from a second
    unrelated fact. dedup_scan asks the same question later, over the
    whole store, for a human to answer.

    Never blocks a write and never merges anything -- the memory is
    already stored when this runs. Diagrams are out on both sides: their
    content is a projection of a graph, so a resemblance between two of
    them is not a merge anyone could apply. Consecutive checkpoints of one
    effort are out too (see _timeline_pair) -- they share a skeleton by
    design and would fire on every write.
    """
    row = get_memory(conn, uid)
    if row is None or row["type"] == DIAGRAM_TYPE:
        return []

    scored: list[tuple[sqlite3.Row, float, str]] = []
    if _vec_ready(conn):
        emb = conn.execute(
            "SELECT embedding FROM memories_vec WHERE rowid = ?", (row["rowid_pk"],)).fetchone()
        if emb is not None:
            for n in conn.execute(
                "SELECT rowid, distance FROM memories_vec WHERE embedding MATCH ? AND k = ?",
                (emb["embedding"], limit + 8),
            ).fetchall():
                if n["rowid"] == row["rowid_pk"] or 1.0 - n["distance"] < threshold:
                    continue
                other = conn.execute(
                    "SELECT * FROM memories WHERE rowid_pk = ?", (n["rowid"],)).fetchone()
                if other is not None:
                    scored.append((other, 1.0 - n["distance"], "vector"))
    else:
        import difflib

        # Without vectors this is a scan, so it stays inside the scope the
        # memory was filed under -- a write must not get slower as the
        # store grows in branches it has nothing to do with.
        clause, params = domain_clause(row["domain"], alias="") if row["domain"] else ("", [])
        for other in conn.execute(
            f"SELECT * FROM memories WHERE uid <> ? {clause}", [uid, *params]
        ).fetchall():
            ratio = difflib.SequenceMatcher(None, row["content"], other["content"]).quick_ratio()
            if ratio >= threshold:
                scored.append((other, ratio, "lexical"))

    out = []
    for other, score, method in sorted(scored, key=lambda s: -s[1]):
        if other["status"] != "active" or other["type"] == DIAGRAM_TYPE:
            continue
        if _timeline_pair(row, other):
            continue
        out.append({
            "uid": other["uid"], "type": other["type"], "domain": other["domain"],
            "ratio": round(score, 3), "method": method,
            "content": other["content"][:SIMILAR_SNIPPET],
        })
        if len(out) == limit:
            break
    return out


def dedup_candidates(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    threshold: float = 0.6, limit: int = 20, since: str = "",
    subtree: bool = True,
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
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        sql.append(clause)
        params.extend(values)
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

CONFIDENCE_VALUES = ("unverified", "confirmed", CONFIDENCE_CONTRADICTED)
SUGGESTION_KINDS = (
    "compact", "reword", "retag", "redomain", "crosslist",
    "set_confidence", "review", "archive", "link", "merge", "distill",
)
# distill targets must be durable knowledge types -- distilling INTO a
# checkpoint/handoff would just recreate the ephemera it exists to retire
DISTILL_TYPES = ("note", "reasoning", "anti_pattern")
# the payload keys distill applies; any other key is a staging error
DISTILL_PAYLOAD_KEYS = ("source_uids", "new_type", "new_content", "tags", "domain")
# the kinds staging refuses without a non-empty `verified`, mapped to what
# each one is being asked to justify: every one of them archives a memory.
# set_confidence belongs here only when its payload says `contradicted`, so
# it is checked where that payload is read.
VERIFIED_REQUIRED = {
    "archive": "verified required: describe the live-facts check that makes this memory archivable",
    "merge": "verified required: merge archives payload.drop_uid -- describe the live-facts check",
    "distill": "verified required: distill archives its sources -- describe the live-facts check",
}


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


# A token that reads as a code rather than as prose: 'x100', 'p200',
# 'x1042'. Two digits minimum, so a word like 'v2' does not pass for one.
_CODE_TOKEN = re.compile(r"^[a-z]{1,4}\d{2,}[a-z0-9]*$")
# How many domains a leading token must head before it counts as a root of
# the tree on its own (a code needs no such evidence).
_ROOT_MIN = 3
NESTING_HINT_CAP = 40


def _nesting_hints(domain_counts: dict[str, int]) -> list[dict]:
    """Propose a nested path for each flat domain that already reads like one.

    'acme-x100-p200-cache-warmup' states a hierarchy in a string
    nothing can group by. This lifts its leading part into path segments
    and keeps the descriptive tail as the leaf --
    'acme/x100/p200/cache-warmup' -- so the diagrams and notes of one
    module can be asked for as one scope.

    Only leading tokens that LOOK structural are lifted: a code
    (_CODE_TOKEN), or a token heading _ROOT_MIN or more domains in the
    store, which is what a root looks like from here whatever it means.
    The first token that is neither ends the path, tail included.

    Advisory, and deliberately so: nothing here can tell a real level from
    a hyphen inside a name, so each proposal is meant for a human -- or for
    an agent staging a `redomain` suggestion the human then approves --
    never for the rows directly.
    """
    heads: dict[str, int] = {}
    for raw in domain_counts:
        segs = split_domain(raw)
        if segs:
            head = segs[0].split("-")[0].lower()
            heads[head] = heads.get(head, 0) + 1

    def structural(token: str) -> bool:
        token = token.lower()
        return bool(_CODE_TOKEN.match(token)) or heads.get(token, 0) >= _ROOT_MIN

    hints: list[dict] = []
    for raw, count in sorted(domain_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if not raw or DOMAIN_SEP in raw:
            continue                      # blank, or already a path
        tokens = raw.split("-")
        cut = 0
        while cut < len(tokens) - 1 and structural(tokens[cut]):
            cut += 1
        if not cut:
            continue
        hints.append({
            "domain": raw,
            "count": count,
            "proposed": DOMAIN_SEP.join([*tokens[:cut], "-".join(tokens[cut:])]),
        })
        if len(hints) >= NESTING_HINT_CAP:
            break
    return hints


def optimization_corpus(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    since: str = "", include_archived: bool = False, limit: int = 500,
    offset: int = 0, full: bool = False, subtree: bool = True,
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
      - empty/default fields are omitted (blank domain/session/tags, no
        cross-listings, null superseded_by, status matching the filter
        default, confidence 'unverified' -- stats.by_confidence keeps the
        aggregate view)
      - `also` lists the domains a memory belongs to besides its own path,
        because a pass proposing a `crosslist` has to be able to tell a new
        membership from one that already holds
      - created_at drops sub-second precision; updated_at is not listed
        at all (get_memory has it)
      - anchors come as one space-joined string, capped at
        CORPUS_ANCHORS_CAP
    Beyond `limit`, a page also ends early when the serialized listing
    reaches CORPUS_CHAR_BUDGET -- the guarantee is that ONE response
    always fits an MCP host's output cap, whatever the store looks like.
    A `stats` block aggregates the filtered corpus regardless of limit,
    `domain_hints` clusters likely-variant domain strings,
    `domain_nesting` proposes a path for each flat domain that already
    spells a hierarchy out (see _nesting_hints -- the raw material for
    `redomain` suggestions), and `truncated` flags when the listing
    stopped before the corpus ended -- page onward with offset (offset +
    count is the next page's offset).

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
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        where.append(clause)
        params.extend(values)
    if type:
        where.append("AND type = ?")
        params.append(type)
    if status:
        where.append("AND status = ?")
        params.append(status)
    today = today_iso()
    base_where_sql, base_params = " ".join(where), list(params)
    if since:
        where.append("AND updated_at >= ?")
        params.append(since)
    where_sql = " ".join(where)

    rows = conn.execute(
        f"""SELECT uid, type, domain, also_domains, session, tags, content, status,
                   confidence, superseded_by, created_at, updated_at,
                   review_after, source_ref
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
        # what it already belongs to besides its own path, or a curation pass
        # proposing a cross-listing cannot tell a new one from one that holds
        if r["also_domains"]:
            m["also"] = parse_domains(r["also_domains"])
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
        # What the writer said would need rechecking, and where to check it.
        # `due` rather than a date comparison the reader has to make: the
        # question a pass asks is "is this one overdue", and answering it
        # here is one character against a paragraph of arithmetic.
        if r["review_after"]:
            m["review_after"] = r["review_after"]
            if r["review_after"] <= today:
                m["due"] = True
        if r["source_ref"]:
            m["source_ref"] = r["source_ref"]
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

    # What each one has been WORTH, next to what it says. Omitted when zero,
    # like every other default here -- and a memory with no `recalls` at all
    # is the interesting case, not a missing field.
    usage = usage_for(conn, uids)
    for m in mems:
        u = usage.get(m["uid"])
        if u:
            m["recalls"], m["last_recall"] = u["recalls"], u["last_recall"][:19]
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
        # Over the whole filtered corpus, not the page. Read it as
        # UNPROVEN, never as useless: a memory nobody has needed yet is
        # indistinguishable from one about a rare subject, and the rare
        # subject is often the reason a store exists at all. The number is
        # worth knowing store-wide -- if almost nothing has ever been read
        # back, the store is a write log -- and worth nothing about any
        # single row.
        "never_recalled": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND uid NOT IN "
            "(SELECT memory_uid FROM memory_usage)", params).fetchone()[0],
        # Whatever a writer dated for a recheck and nobody rechecked. The
        # rows here are not suspect because they are old -- they are suspect
        # because somebody who knew the subject said when to look again.
        "due_for_review": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND {_due_clause(today)[0]}",
            [*params, today]).fetchone()[0],
    }

    # domain hints cluster over the WHOLE store; with `since`, keep only
    # clusters that touch the delta (counts stay store-wide). Nesting
    # proposals follow the same rule: a flat domain is worth re-homing
    # whether or not the memory that revealed it is new, but a proposal
    # about a corner of the store this run never looked at is noise.
    if since:
        by_domain_global = dict(conn.execute(
            f"SELECT domain, COUNT(*) FROM memories WHERE {base_where_sql} "
            "GROUP BY domain ORDER BY COUNT(*) DESC", base_params).fetchall())
        hints = [h for h in _domain_hints(by_domain_global)
                 if any(v["domain"] in by_domain for v in h["variants"])]
        nesting = [n for n in _nesting_hints(by_domain_global) if n["domain"] in by_domain]
    else:
        hints = _domain_hints(by_domain)
        nesting = _nesting_hints(by_domain)

    return {
        "memories": mems,
        "relations": edges,
        "count": len(mems),
        "offset": offset,
        "truncated": offset + len(mems) < total,
        "stats": stats,
        "domain_hints": hints,
        "domain_nesting": nesting,
    }


def _memory_exists(conn: sqlite3.Connection, uid: str | None) -> bool:
    return bool(uid) and get_memory(conn, uid) is not None


def _diagram_content_error(conn: sqlite3.Connection, uid: str | None) -> str | None:
    """The free-text editors' refusal, for the suggestion kinds that rewrite content.

    A diagram's content is the projection of its graph, so a hand-authored
    body applied over it survives only until the next diagram_node/
    diagram_edge edit regenerates it (see is_diagram).
    """
    if not (uid and is_diagram(conn, uid)):
        return None
    return (f"{uid} is a diagram: its content is generated from the graph. "
            "Use diagram_node/diagram_edge to change the flow.")


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
        err = target_err() or _diagram_content_error(conn, target_uid)
        if err:
            return None, err
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        # caught here rather than on apply: a rewrite that would not read
        # back into its type's fields never reaches the queue a human works
        # through, so nothing in that queue is waiting to fail
        row = get_memory(conn, target_uid)
        err = section_error(conn, row["type"], str(payload["new_content"]))
        if err:
            return None, err
    elif kind == "retag":
        err = target_err()
        if err:
            return None, err
        if "tags" not in payload:
            return None, "payload.tags required"
    elif kind == "review":
        err = target_err()
        if err:
            return None, err
        if "review_after" not in payload:
            return None, "payload.review_after required ('' clears the date)"
        # normalized at staging time for the same reason redomain is: the
        # panel shows this payload as what will hold, and '90d' means a
        # different day depending on when it is read
        try:
            payload = {**payload,
                       "review_after": normalize_review_after(str(payload["review_after"]))}
        except ValueError as exc:
            return None, str(exc)
    elif kind == "redomain":
        err = target_err()
        if err:
            return None, err
        if "domain" not in payload:
            return None, "payload.domain required"
        # normalize at staging time, not only on apply: the panel shows
        # this payload as the proposed target, and it has to be the path
        # the memory will actually end up in
        payload = {**payload, "domain": normalize_domain(str(payload["domain"]))}
    elif kind == "crosslist":
        err = target_err()
        if err:
            return None, err
        if "also" not in payload:
            return None, "payload.also required"
        # the whole set is REPLACED, and the panel shows this payload as what
        # will hold -- so the staging pass runs the same policy the apply
        # will (casing, path shape, and dropping a path the memory's own
        # domain already covers) rather than showing paths that then change
        row = get_memory(conn, target_uid)
        given = parse_domains(payload["also"])
        want = apply_link_policy(conn, given, row["domain"])
        # an empty list is a legitimate suggestion ("drop every cross-listing"),
        # but a non-empty one that survives as empty is not the suggestion it
        # looks like: it would apply as a clear, so say what happened instead
        if given and not want:
            return None, (
                f"every path given is already covered by the memory's domain "
                f"{row['domain']!r}: {', '.join(given)}")
        payload = {**payload, "also": want}
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
            return None, VERIFIED_REQUIRED[kind]
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
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
        target_uid = drop
    elif kind == "distill":
        if target_uid:
            return None, "distill creates a new memory; omit target_uid"
        extra = sorted(k for k in payload if k not in DISTILL_PAYLOAD_KEYS)
        if extra:
            return None, (f"payload keys not accepted by distill: {', '.join(extra)} "
                          f"(allowed: {', '.join(DISTILL_PAYLOAD_KEYS)})")
        sources = payload.get("source_uids")
        if not isinstance(sources, list) or not sources:
            return None, "payload.source_uids must be a non-empty list"
        sources = [str(u).strip() for u in sources]
        if len(set(sources)) != len(sources):
            return None, "payload.source_uids contains duplicates"
        for u in sources:
            if not _memory_exists(conn, u):
                return None, f"payload.source_uids not found: {u!r}"
            if is_diagram(conn, u):
                return None, (f"{u} is a diagram: distill archives its sources. "
                              "Use archive to retire a flow on its own.")
        if payload.get("new_type") not in DISTILL_TYPES:
            return None, f"payload.new_type must be one of {DISTILL_TYPES}"
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        # anti_pattern is a distill target and is made of fields, so the body
        # a distill writes has to read back the same way any other one does
        err = section_error(conn, str(payload["new_type"]), str(payload["new_content"]))
        if err:
            return None, err
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
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

    A domain change re-runs the cross-listing policy: the memory's new path
    may already satisfy a membership it used to need (see
    apply_link_policy), and leaving that row would count it twice in its
    own branch.
    """
    if field == "domain":
        value = apply_domain_policy(conn, value)
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
        row["also_domains"],
    )
    if field == "domain" and row["also_domains"]:
        set_domain_links(conn, uid, get_domain_links(conn, uid))


def _apply_kind(conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict) -> dict:
    """Execute one suggestion and return the prev_state dict for undo."""
    if kind in ("compact", "reword"):
        # staging refuses these on a diagram, but a run staged before that
        # guard existed still holds one, and applying it would write over
        # the projection
        err = _diagram_content_error(conn, target_uid)
        if err:
            raise ValueError(err)
        row = get_memory(conn, target_uid)
        prev = {"content": row["content"]}
        update_memory_content(conn, target_uid, payload["new_content"], note=f"optimize:{kind}")
        return prev
    if kind == "retag":
        row = get_memory(conn, target_uid)
        prev = {"tags": row["tags"]}
        _update_meta_field(conn, target_uid, "tags", str(payload["tags"]).strip())
        return prev
    if kind == "review":
        row = get_memory(conn, target_uid)
        prev = {"review_after": row["review_after"]}
        set_review_after(conn, target_uid, str(payload["review_after"]))
        return prev
    if kind == "redomain":
        row = get_memory(conn, target_uid)
        prev = {"domain": row["domain"]}
        _update_meta_field(conn, target_uid, "domain", str(payload["domain"]).strip())
        return prev
    if kind == "crosslist":
        # the whole set, not an addition: undo restores exactly this list
        prev = {"also": get_domain_links(conn, target_uid)}
        set_domain_links(conn, target_uid, payload["also"], note=f"optimize:{kind}")
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
    elif kind == "review":
        set_review_after(conn, target_uid, prev["review_after"])
    elif kind == "redomain":
        _update_meta_field(conn, target_uid, "domain", prev["domain"])
    elif kind == "crosslist":
        set_domain_links(conn, target_uid, prev["also"], coerce=False,
                         note="optimize:undo crosslist")
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
