"""Opening a store that carries a sqlite-vec table.

Such a store holds a `vec0` virtual table whose module nothing registers,
the shadow tables it keeps its data in, meta keys naming an embedding
model, and two usage counters beside via_fts. A `VACUUM INTO` backup is a
byte copy, so restoring one is the same case as opening the store it came
from -- which is why the removal happens on connect and not behind a
command somebody has to remember.
"""

from __future__ import annotations

import sqlite3

import pytest

from memai import db

_SHADOW = ("memories_vec_chunks", "memories_vec_rowids", "memories_vec_vector_chunks00")


def _plant_vector_table(conn) -> None:
    """Declare a vec0 table the way a store carrying one has it.

    The declaration is written straight into the schema because nothing
    registers the module that would accept `CREATE VIRTUAL TABLE ... USING
    vec0` -- which is the whole condition under test.
    """
    for name in _SHADOW:
        conn.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, data BLOB)")
    conn.commit()
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute(
        "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) "
        "VALUES ('table', 'memories_vec', 'memories_vec', 0, ?)",
        ("CREATE VIRTUAL TABLE memories_vec USING vec0("
         "embedding float[256] distance_metric=cosine)",))
    conn.execute(f"PRAGMA schema_version = {version + 1}")
    conn.execute("PRAGMA writable_schema = OFF")
    conn.commit()


def _store_with_vectors(path) -> str:
    """A store carrying all four leftovers. Returns the uid it holds."""
    with db.connect(path) as conn:
        uid = db.insert_memory(conn, type="note", domain="acme/x100", tags="cache",
                               content="cache warmup runs before the first request")
        db.record_recall(conn, [uid], sources={uid: "fts"})
        conn.execute("ALTER TABLE memory_usage ADD COLUMN via_vec INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE memory_usage ADD COLUMN via_both INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE memory_usage SET via_vec = 3, via_both = 2")
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                         [("embed_model", "potion-base-8M"), ("embed_dim", "256")])
        _plant_vector_table(conn)
    return uid


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def carrier(tmp_path):
    path = tmp_path / "carrier.db"
    return path, _store_with_vectors(path)


# --------------------------------------------------------------- the removal

def test_opening_the_store_removes_the_vector_table(carrier):
    path, _ = carrier
    with db.connect(path) as conn:
        tables = _tables(conn)
    assert "memories_vec" not in tables
    assert not [t for t in tables if t.startswith("memories_vec")]


def test_it_removes_the_counters_beside_via_fts(carrier):
    path, _ = carrier
    with db.connect(path) as conn:
        assert _columns(conn, "memory_usage") == {
            "memory_uid", "recall_count", "last_recalled_at", "via_fts"}


def test_it_removes_the_meta_keys_naming_the_model(carrier):
    path, _ = carrier
    with db.connect(path) as conn:
        keys = {r[0] for r in conn.execute("SELECT key FROM meta")}
    assert "embed_model" not in keys and "embed_dim" not in keys


def test_the_memories_survive_it(carrier):
    """The vectors go; nothing a person wrote does."""
    path, uid = carrier
    with db.connect(path) as conn:
        row = db.get_memory(conn, uid)
        assert row["content"] == "cache warmup runs before the first request"
        assert row["domain"] == "acme/x100"
        assert [r["uid"] for r in db.search_ranked(conn, "cache warmup")] == [uid]
        assert db.usage_for(conn, [uid])[uid]["recalls"] == 1
        assert db.search_share(conn)["fts"] == 1


def test_it_says_what_the_free_pages_are(carrier):
    """Dropping the table frees pages without shrinking the file, so the
    dashboard's disk row has something to name."""
    path, _ = carrier
    with db.connect(path) as conn:
        assert db.get_compact_reason(conn) == db.COMPACT_REASON_VECTORS


def test_clearing_the_reason_leaves_nothing_to_name(carrier):
    """What a compaction does to it -- the dashboard's VACUUM calls this,
    and test_admin covers that path end to end."""
    path, _ = carrier
    with db.connect(path) as conn:
        db.clear_compact_reason(conn)
    with db.connect(path) as conn:
        assert db.get_compact_reason(conn) == ""


# ------------------------------------------------------------- running twice

def test_a_second_open_finds_nothing_left_to_do(carrier):
    path, _ = carrier
    with db.connect(path) as conn:
        pass
    with db.connect(path) as conn:
        assert db._drop_vector_store(conn) is False


def test_a_store_without_them_is_left_alone(tmp_path):
    with db.connect(tmp_path / "fresh.db") as conn:
        db.insert_memory(conn, type="note", content="row merge keeps the older id")
        assert db._drop_vector_store(conn) is False


def test_the_columns_alone_are_enough_to_clean(tmp_path):
    """A counter arrives by ALTER TABLE, so a store can carry one with no
    vector table beside it."""
    path = tmp_path / "half.db"
    with db.connect(path) as conn:
        conn.execute("ALTER TABLE memory_usage ADD COLUMN via_vec INTEGER NOT NULL DEFAULT 0")
    with db.connect(path) as conn:
        assert "via_vec" not in _columns(conn, "memory_usage")
        # a column frees no pages worth compacting for
        assert db.get_compact_reason(conn) == ""


# ----------------------------------------------------------- what it leaves

def test_the_schema_is_readable_by_a_fresh_connection(carrier):
    """The declaration is deleted with writable_schema, so the cookie has to
    be bumped -- otherwise another connection keeps a schema naming a module
    it cannot load."""
    path, _ = carrier
    with db.connect(path):
        pass
    raw = sqlite3.connect(str(path))
    try:
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        raw.close()
    assert "memories_vec" not in names
