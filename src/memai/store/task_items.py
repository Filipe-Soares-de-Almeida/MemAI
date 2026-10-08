"""A task's item keys, held equal to the items' positions, and the content the items generate."""

from __future__ import annotations

import sqlite3

from memai.store.memories import set_generated_content

MARKS = {"todo": "[ ]", "doing": "[~]", "done": "[x]", "dropped": "[-]"}


def render(goal: str, items: list[dict]) -> str:
    """The generated content: a GOAL line, then one mark line per item."""
    lines = [f"GOAL: {goal}"]
    lines += [f"{MARKS[i['state']]} {i['key']} {i['text']}" for i in items]
    return "\n".join(lines)


def _rename(conn: sqlite3.Connection, uid: str, old: str, new: str) -> None:
    for table in ("task_items", "task_item_links", "task_comments"):
        conn.execute(f"UPDATE {table} SET item_key = ? WHERE memory_uid = ? AND item_key = ?",
                     (new, uid, old))
    conn.execute("UPDATE task_note_items SET item_key = ? WHERE item_key = ? AND note_id IN "
                 "(SELECT id FROM task_notes WHERE memory_uid = ?)", (new, old, uid))


def compact(conn: sqlite3.Connection, uid: str) -> dict[str, str]:
    """Number the task's items i1..iN in order, carrying every row that names them.

    Returns {old key: new key} for the keys that moved, in item order.
    """
    rows = conn.execute("SELECT id, item_key FROM task_items WHERE memory_uid = ? ORDER BY seq, id",
                        (uid,)).fetchall()
    moved = {r["item_key"]: f"i{n}" for n, r in enumerate(rows, start=1) if r["item_key"] != f"i{n}"}
    # '~' never starts a real key, so no rename lands on a key another item still holds
    for old in moved:
        _rename(conn, uid, old, f"~{old}")
    for old, new in moved.items():
        _rename(conn, uid, f"~{old}", new)
    conn.executemany("UPDATE task_items SET seq = ? WHERE id = ?",
                     [(n, r["id"]) for n, r in enumerate(rows, start=1)])
    return moved


def regenerate(conn: sqlite3.Connection, uid: str) -> None:
    """Rewrite the task's content from its goal and items, recording no edit."""
    goal = conn.execute("SELECT goal FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["goal"]
    items = [{"key": r["item_key"], "state": r["state"], "text": r["text"]} for r in conn.execute(
        "SELECT item_key, state, text FROM task_items WHERE memory_uid = ? ORDER BY seq, id", (uid,))]
    set_generated_content(conn, uid, render(goal, items))
