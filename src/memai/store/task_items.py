"""The content a task's goal and items generate."""

from __future__ import annotations

import sqlite3

from memai.store.memories import set_generated_content

MARKS = {"todo": "[ ]", "doing": "[~]", "done": "[x]", "dropped": "[-]"}


def render(goal: str, items: list[dict]) -> str:
    """The generated content: a GOAL line, then one line per item: its mark, its id and its text."""
    lines = [f"GOAL: {goal}"]
    lines += [f"{MARKS[i['state']]} {i['id']} {i['text']}" for i in items]
    return "\n".join(lines)


def regenerate(conn: sqlite3.Connection, uid: str, *, touch: bool = True) -> None:
    """Rewrite the task's content from its goal and items, recording no edit.

    touch=False leaves the task's updated_at where it was.
    """
    goal = conn.execute("SELECT goal FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["goal"]
    items = [dict(r) for r in conn.execute(
        "SELECT id, state, text FROM task_items WHERE memory_uid = ? ORDER BY seq, id", (uid,))]
    set_generated_content(conn, uid, render(goal, items), touch=touch)
