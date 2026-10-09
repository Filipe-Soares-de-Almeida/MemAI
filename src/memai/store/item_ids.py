"""The task tables keyed by item id, and the rows that hold a brief's dependencies."""

from __future__ import annotations

import sqlite3

from memai.sections import Dependency

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
