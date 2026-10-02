"""Task checklists: a `task` memory that owns items, links and comments.

A task is a memories row of type 'task' whose content is generated from its
goal and items, the way a diagram's content is generated from its graph.
"""

import re
import sqlite3

from . import db

ITEM_STATES = ("todo", "doing", "done", "dropped")
TASK_STATES = ("open", "completed", "cancelled")

GOAL_MAX = 2000
ITEM_MAX = 300
ITEMS_MAX = 50
COMMENT_MAX = 2000

MARKS = {"todo": "[ ]", "doing": "[~]", "done": "[x]", "dropped": "[-]"}

_KEY_RE = re.compile(r"^i?([1-9][0-9]*)$")


def split_items(text: str) -> list[str]:
    """The non-blank lines of `text`, each trimmed."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def item_key(value: str) -> str:
    """Normalize "3", "i3" or " I3 " to "i3"; anything else is a ValueError."""
    match = _KEY_RE.match(str(value).strip().lower())
    if match is None:
        raise ValueError(f"{value!r} is not an item key; use a number such as 3 or i3")
    return f"i{match.group(1)}"


def render(goal: str, items: list[dict]) -> str:
    """The generated content: a GOAL line, then one mark line per item."""
    lines = [f"GOAL: {goal}"]
    lines += [f"{MARKS[i['state']]} {i['key']} {i['text']}" for i in items]
    return "\n".join(lines)


def _validated(title: str, goal: str, items: list[str]) -> tuple[str, str, list[str]]:
    title = str(title).strip()
    goal = str(goal).strip()
    items = [str(i).strip() for i in items]
    if not title:
        raise ValueError("a task needs a title")
    if not goal:
        raise ValueError("a task needs a goal")
    if len(goal) > GOAL_MAX:
        raise ValueError(f"goal is {len(goal)} characters; the limit is {GOAL_MAX}")
    if not items or not all(items):
        raise ValueError("a task needs at least one item, and no item may be empty")
    if len(items) > ITEMS_MAX:
        raise ValueError(f"{len(items)} items; a task holds at most {ITEMS_MAX}")
    for text in items:
        if len(text) > ITEM_MAX:
            raise ValueError(f"an item is {len(text)} characters; the limit is {ITEM_MAX}")
    return title, goal, items


def create_task(
    conn: sqlite3.Connection,
    *,
    title: str,
    goal: str,
    items: list[str],
    domain: str = "",
    also: str = "",
    tags: str = "",
    session: str = "",
) -> str:
    """Create a task and its items in the caller's transaction; returns the uid.

    Raises ValueError on an empty or over-limit input, before anything is written.
    """
    title, goal, items = _validated(title, goal, items)
    rows = [
        {"key": f"i{n}", "seq": n, "text": text, "state": "todo"}
        for n, text in enumerate(items, start=1)
    ]
    uid = db.insert_memory(
        conn, type=db.TASK_TYPE, content=render(goal, rows), title=title,
        domain=domain, also=also, session=session, tags=tags,
    )
    conn.execute("INSERT INTO tasks (memory_uid, goal) VALUES (?, ?)", (uid, goal))
    stamp = db.now_iso()
    conn.executemany(
        """INSERT INTO task_items
           (memory_uid, item_key, seq, text, state, updated_at, updated_session)
           VALUES (?, ?, ?, ?, 'todo', ?, ?)""",
        [(uid, r["key"], r["seq"], r["text"], stamp, session) for r in rows],
    )
    return uid


def is_task(conn: sqlite3.Connection, uid: str) -> bool:
    """True when `uid` is a task memory."""
    row = db.get_memory(conn, uid)
    return row is not None and row["type"] == db.TASK_TYPE


def get_task(conn: sqlite3.Connection, uid: str) -> dict | None:
    """The task's goal, state, items (with linked memories) and comments, or None."""
    head = conn.execute("SELECT * FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()
    if head is None:
        return None
    links: dict[str, list[dict]] = {}
    for r in conn.execute(
        """SELECT l.item_key, l.target_uid, m.title
           FROM task_item_links l JOIN memories m ON m.uid = l.target_uid
           WHERE l.memory_uid = ? ORDER BY l.created_at, l.target_uid""",
        (uid,),
    ):
        links.setdefault(r["item_key"], []).append({"uid": r["target_uid"], "title": r["title"]})
    items = [
        {
            "key": r["item_key"], "seq": r["seq"], "text": r["text"], "state": r["state"],
            "updated_at": r["updated_at"], "updated_session": r["updated_session"],
            "links": links.get(r["item_key"], []),
        }
        for r in conn.execute(
            "SELECT * FROM task_items WHERE memory_uid = ? ORDER BY seq, id", (uid,)
        )
    ]
    comments = [
        {
            "id": r["id"], "item": r["item_key"], "body": r["body"], "author": r["author"],
            "session": r["session"], "created_at": r["created_at"],
        }
        for r in conn.execute(
            "SELECT * FROM task_comments WHERE memory_uid = ? ORDER BY created_at, id", (uid,)
        )
    ]
    return {
        "goal": head["goal"], "state": head["state"], "completed_at": head["completed_at"],
        "items": items, "comments": comments,
    }
