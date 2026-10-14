"""The content a task's goal and items generate."""

from __future__ import annotations

import sqlite3

from memai.store.memories import set_generated_content

MARKS = {"todo": "[ ]", "doing": "[~]", "done": "[x]", "dropped": "[-]"}

CONTENT_FORMAT = "numbered"
_FORMAT_KEY = "task_content_format"


def render(goal: str, items: list[dict]) -> str:
    """The generated content: a GOAL line, then one line per item: its mark, number, text and id.

    `items` are in list order; an item without an id yet shows none.
    """
    lines = [f"GOAL: {goal}"]
    lines += [f"{MARKS[i['state']]} {n}. {i['text']}" + (f" (id {i['id']})" if i.get("id") else "")
              for n, i in enumerate(items, start=1)]
    return "\n".join(lines)


def regenerate(conn: sqlite3.Connection, uid: str, *, touch: bool = True) -> None:
    """Rewrite the task's content from its goal and items, recording no edit.

    touch=False leaves the task's updated_at where it was.
    """
    goal = conn.execute("SELECT goal FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["goal"]
    items = [dict(r) for r in conn.execute(
        "SELECT id, state, text FROM task_items WHERE memory_uid = ? ORDER BY seq, id", (uid,))]
    set_generated_content(conn, uid, render(goal, items), touch=touch)


def ensure_format(conn: sqlite3.Connection) -> None:
    """Regenerate every task's content once, when the store's was written in another format."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_FORMAT_KEY,)).fetchone()
    if row is not None and row[0] == CONTENT_FORMAT:
        return
    for (uid,) in conn.execute(
            "SELECT t.memory_uid FROM tasks t JOIN memories m ON m.uid = t.memory_uid").fetchall():
        regenerate(conn, uid, touch=False)
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (_FORMAT_KEY, CONTENT_FORMAT))
