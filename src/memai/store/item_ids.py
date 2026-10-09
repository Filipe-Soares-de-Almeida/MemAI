"""The task tables keyed by item id, the rows that hold a brief's dependencies, and the move of a store keyed by position onto them."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from memai import sections
from memai.lite import home
from memai.sections import Dependency
from memai.store import backups, paths

# Each task table with {table} for its name: SCHEMA creates it under its own name, and the
# migration builds it under a temporary one beside the table it replaces.
TABLES: dict[str, str] = {
    "task_items": """
CREATE TABLE {table} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_uid      TEXT NOT NULL REFERENCES memories(uid),
    seq             INTEGER NOT NULL,
    text            TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'todo', -- todo | doing | done | dropped
    updated_at      TEXT NOT NULL,
    updated_session TEXT NOT NULL DEFAULT ''
);
""",
    "task_item_links": """
CREATE TABLE {table} (
    memory_uid TEXT NOT NULL REFERENCES memories(uid),
    item_id    INTEGER NOT NULL REFERENCES task_items(id),
    target_uid TEXT NOT NULL REFERENCES memories(uid),
    created_at TEXT NOT NULL,
    PRIMARY KEY (item_id, target_uid)
);
""",
    "task_comments": """
CREATE TABLE {table} (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_uid TEXT NOT NULL REFERENCES memories(uid),
    item_id    INTEGER REFERENCES task_items(id),   -- NULL: on the task as a whole
    body       TEXT NOT NULL,
    author     TEXT NOT NULL DEFAULT 'agent',      -- agent | person
    session    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
""",
    "task_note_items": """
CREATE TABLE {table} (
    note_id INTEGER NOT NULL REFERENCES task_notes(id),
    item_id INTEGER NOT NULL REFERENCES task_items(id),
    PRIMARY KEY (note_id, item_id)
);
""",
}

_DEPENDS = """
CREATE TABLE IF NOT EXISTS task_note_depends (
    note_id INTEGER NOT NULL REFERENCES task_notes(id),
    ord     INTEGER NOT NULL,
    item_id INTEGER REFERENCES task_items(id),   -- NULL: the item was deleted, and text names it
    text    TEXT NOT NULL DEFAULT '',
    reason  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (note_id, ord)
);

CREATE INDEX IF NOT EXISTS idx_task_items_mem ON task_items(memory_uid);
CREATE INDEX IF NOT EXISTS idx_task_comments_mem ON task_comments(memory_uid);
"""

# On columns a store keyed by position does not have, so they are made once it has migrated.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_task_comments_item ON task_comments(item_id);
CREATE INDEX IF NOT EXISTS idx_task_note_items_item ON task_note_items(item_id);
CREATE INDEX IF NOT EXISTS idx_task_note_depends_item ON task_note_depends(item_id);
"""


def schema() -> str:
    """The task tables as a new store gets them."""
    return "".join(t.format(table=f"IF NOT EXISTS {name}") for name, t in TABLES.items()) + _DEPENDS


def dependencies(conn: sqlite3.Connection, note_id: int) -> list[Dependency]:
    """A note's DEPENDS ON entries in order."""
    return [Dependency(r["item_id"], "", "" if r["item_id"] is not None else r["text"], r["reason"])
            for r in conn.execute(
                "SELECT item_id, text, reason FROM task_note_depends WHERE note_id = ? ORDER BY ord",
                (note_id,))]


def set_dependencies(conn: sqlite3.Connection, note_id: int, entries: list[Dependency]) -> None:
    """Replace a note's DEPENDS ON entries."""
    conn.execute("DELETE FROM task_note_depends WHERE note_id = ?", (note_id,))
    conn.executemany(
        "INSERT INTO task_note_depends (note_id, ord, item_id, text, reason) VALUES (?, ?, ?, ?, ?)",
        [(note_id, n, e.item, "" if e.item is not None else e.text, e.reason)
         for n, e in enumerate(entries, start=1)])


KIND = "item-ids"


def needs_migration(conn: sqlite3.Connection) -> bool:
    """True while the task tables still name items by position key."""
    return "item_key" in {r[1] for r in conn.execute("PRAGMA table_info(task_items)")}


def _shelf(db_path: Path) -> tuple[str, Path]:
    """The project a store file belongs to and the folder its backups go in: the project's backup
    shelf for a file in the store home, beside the file for any other."""
    db_path = Path(db_path).resolve()
    root = home().resolve()
    if db_path == root / paths.GENERAL_FILE:
        return paths.GENERAL_PROJECT, backups.backups_dir(paths.GENERAL_PROJECT)
    if db_path.parent == root / paths.PROJECTS_DIRNAME:
        return db_path.stem, backups.backups_dir(db_path.stem)
    return db_path.stem, db_path.parent


def taken_backup(db_path: Path) -> Path | None:
    """A copy an earlier attempt took before migrating this store, if one is on its shelf."""
    project, folder = _shelf(db_path)
    found = sorted(folder.glob(f"{project}-{KIND}-*.db"))
    return found[0] if found else None


def backup_path(db_path: Path) -> Path:
    """Where the copy taken before the migration goes, a name no file holds yet."""
    project, folder = _shelf(db_path)
    name = Path(backups.backup_name(project, KIND))
    dest, n = folder / name, 1
    while dest.exists():
        n += 1
        dest = folder / f"{name.stem}-{n}{name.suffix}"
    return dest


def split_legacy(body: str, ids_by_key: dict[str, int]) -> tuple[str, list[Dependency]] | None:
    """A brief written with position keys: its body without DEPENDS ON, and its entries with each
    key turned into the id it named. None when the body is no brief, its DEPENDS ON does not
    read, or a key names no item."""
    reading = sections.read_spec(sections.BRIEF_SPEC, body)
    if not reading.conforms:
        return None
    entries, problems = sections.read_depends(reading.sections["depends_on"])
    if problems:
        return None
    out = []
    for e in entries:
        if e.item is not None or (e.key and e.key not in ids_by_key):
            return None
        out.append(Dependency(ids_by_key[e.key], "", "", e.reason) if e.key else e)
    return sections.without_depends(body) or body, out


_COPY = {
    "task_items": """
        INSERT INTO task_items_new (id, memory_uid, seq, text, state, updated_at, updated_session)
        SELECT i.id, i.memory_uid, i.seq, i.text, i.state, i.updated_at, i.updated_session
        FROM task_items i
        WHERE i.memory_uid IN (SELECT t.memory_uid FROM tasks t JOIN memories m ON m.uid = t.memory_uid)""",
    "task_item_links": """
        INSERT OR IGNORE INTO task_item_links_new (memory_uid, item_id, target_uid, created_at)
        SELECT l.memory_uid, i.id, l.target_uid, l.created_at
        FROM task_item_links l JOIN task_items i ON i.memory_uid = l.memory_uid AND i.item_key = l.item_key
        WHERE i.id IN (SELECT id FROM task_items_new) AND l.target_uid IN (SELECT uid FROM memories)""",
    "task_comments": """
        INSERT INTO task_comments_new (id, memory_uid, item_id, body, author, session, created_at)
        SELECT c.id, c.memory_uid, i.id, c.body, c.author, c.session, c.created_at
        FROM task_comments c
        LEFT JOIN task_items i ON i.memory_uid = c.memory_uid AND i.item_key = c.item_key
        WHERE c.memory_uid IN (SELECT t.memory_uid FROM tasks t JOIN memories m ON m.uid = t.memory_uid)
          AND (c.item_key = '' OR i.id IN (SELECT id FROM task_items_new))""",
    "task_note_items": """
        INSERT OR IGNORE INTO task_note_items_new (note_id, item_id)
        SELECT ni.note_id, i.id
        FROM task_note_items ni JOIN task_notes n ON n.id = ni.note_id
        JOIN task_items i ON i.memory_uid = n.memory_uid AND i.item_key = ni.item_key
        WHERE i.id IN (SELECT id FROM task_items_new)""",
}


def _sequence(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
    return row[0] if row else 0


def _keep_sequence(conn: sqlite3.Connection, table: str, seq: int) -> None:
    conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (seq, table))
    conn.execute("INSERT INTO sqlite_sequence (name, seq) SELECT ?, ? "
                 "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = ?)", (table, seq, table))


def _split_briefs(conn: sqlite3.Connection) -> None:
    """Move every brief's DEPENDS ON into rows, reading keys through the tables not yet dropped."""
    keys: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT memory_uid, item_key, id FROM task_items "
                          "WHERE id IN (SELECT id FROM task_items_new)"):
        keys.setdefault(r["memory_uid"], {})[r["item_key"]] = r["id"]
    for n in conn.execute("SELECT id, memory_uid, body FROM task_notes WHERE split_depends = 0").fetchall():
        split = split_legacy(n["body"], keys.get(n["memory_uid"], {}))
        if split is None:
            continue
        body, entries = split
        conn.execute("UPDATE task_notes SET body = ?, split_depends = 1 WHERE id = ?", (body, n["id"]))
        set_dependencies(conn, n["id"], entries)


def migrate(conn: sqlite3.Connection, db_path: Path) -> Path | None:
    """Move a store whose task rows name items by position key onto item ids.

    Returns the backup taken first, or reused from an attempt that failed, or None when the
    store needed nothing.
    The rebuild is one transaction under the write lock and checks again
    inside it, so a process that lost the race finds nothing to do; any
    failure rolls it all back.
    """
    # imported here: task_items reaches store.memories, which imports store.connection, which imports this module
    from memai.store import task_items

    if not needs_migration(conn):
        return None
    conn.commit()
    dest = taken_backup(db_path)
    if dest is None:
        dest = backup_path(db_path)
        conn.execute("VACUUM INTO ?", (str(dest),))
    # a no-op inside a transaction, so it is switched before BEGIN and back after COMMIT
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not needs_migration(conn):
            conn.rollback()
            return dest
        sequences = {t: _sequence(conn, t) for t in ("task_items", "task_comments")}
        # a store older than task notes gets task_note_items from SCHEMA, already keyed by id
        old = [name for name in TABLES
               if "item_key" in {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}]
        for name in old:
            conn.execute(TABLES[name].format(table=f"{name}_new"))
        for name in old:
            conn.execute(_COPY[name])
        _split_briefs(conn)
        for name in old:
            conn.execute(f"DROP TABLE {name}")
        for name in old:
            conn.execute(f"ALTER TABLE {name}_new RENAME TO {name}")
        for table, seq in sequences.items():
            _keep_sequence(conn, table, seq)
        if "item_seq" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}:
            conn.execute("ALTER TABLE tasks DROP COLUMN item_seq")
        # executescript would commit first, so these run one by one inside the transaction
        for statement in _DEPENDS.split(";"):
            if statement.strip():
                conn.execute(statement)
        for table in (*TABLES, "task_note_depends"):
            if conn.execute(f"PRAGMA foreign_key_check({table})").fetchone():
                raise sqlite3.IntegrityError(f"the item-id migration left a dangling reference in {table}")
        for (uid,) in conn.execute("SELECT t.memory_uid FROM tasks t JOIN memories m ON m.uid = t.memory_uid").fetchall():
            task_items.regenerate(conn, uid, touch=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return dest
