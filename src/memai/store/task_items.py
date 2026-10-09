"""A task's item keys, held equal to the items' positions, and the content the items generate."""

from __future__ import annotations

import sqlite3

from memai.sections import (
    BRIEF_SPEC,
    DependsEntry,
    parse_depends,
    read_spec,
    render_depends,
    render_spec,
)
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


def _follow(entries: list[DependsEntry], moved: dict[str, str],
            gone: tuple[str, str] | None) -> list[DependsEntry]:
    """The entries after the keys moved and the `gone` item went; one pass, so no rename feeds another."""
    out = []
    for e in entries:
        if gone and e.item == gone[0]:
            out.append(DependsEntry("", gone[1], e.reason))
        elif e.item in moved:
            out.append(DependsEntry(moved[e.item], "", e.reason))
        else:
            out.append(e)
    return out


def _follow_depends(conn: sqlite3.Connection, uid: str, moved: dict[str, str],
                    gone: tuple[str, str] | None) -> None:
    """Rewrite the DEPENDS ON of the task's briefs; a body that does not read as a brief is left as written."""
    for note in conn.execute("SELECT id, body FROM task_notes WHERE memory_uid = ?", (uid,)).fetchall():
        reading = read_spec(BRIEF_SPEC, note["body"])
        if not reading.conforms:
            continue
        entries, problems = parse_depends(reading.sections["depends_on"])
        followed = _follow(entries, moved, gone)
        if problems or followed == entries:
            continue
        body = render_spec(BRIEF_SPEC, {**reading.sections, "depends_on": render_depends(followed)})
        conn.execute("UPDATE task_notes SET body = ? WHERE id = ?", (body, note["id"]))


def compact(conn: sqlite3.Connection, uid: str, gone: tuple[str, str] | None = None) -> dict[str, str]:
    """Number the task's items i1..iN in order, carrying every row that names them.

    `gone` is the key and text of an item deleted just before: a brief that
    depended on it comes to name the text instead. Returns {old key: new key}
    for the keys that moved, in item order.
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
    if moved or gone:
        _follow_depends(conn, uid, moved, gone)
    return moved


def regenerate(conn: sqlite3.Connection, uid: str, *, touch: bool = True) -> None:
    """Rewrite the task's content from its goal and items, recording no edit.

    touch=False leaves the task's updated_at where it was.
    """
    goal = conn.execute("SELECT goal FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["goal"]
    items = [{"key": r["item_key"], "state": r["state"], "text": r["text"]} for r in conn.execute(
        "SELECT item_key, state, text FROM task_items WHERE memory_uid = ? ORDER BY seq, id", (uid,))]
    set_generated_content(conn, uid, render(goal, items), touch=touch)
