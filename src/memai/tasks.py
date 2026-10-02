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


def _lock(conn: sqlite3.Connection, uid: str) -> None:
    """Take the write lock on the task's row, so what follows reads settled state.

    Raises ValueError when `uid` is not a task.
    """
    cur = conn.execute("UPDATE tasks SET memory_uid = memory_uid WHERE memory_uid = ?", (uid,))
    if cur.rowcount == 0:
        raise ValueError(f"no task {uid}")


def _items(conn: sqlite3.Connection, uid: str) -> list[dict]:
    return [
        {"key": r["item_key"], "seq": r["seq"], "text": r["text"], "state": r["state"]}
        for r in conn.execute(
            "SELECT item_key, seq, text, state FROM task_items WHERE memory_uid = ? ORDER BY seq, id",
            (uid,),
        )
    ]


def progress(conn: sqlite3.Connection, uid: str) -> dict:
    """How many items are done, dropped and in all."""
    if not is_task(conn, uid):
        raise ValueError(f"no task {uid}")
    states = [i["state"] for i in _items(conn, uid)]
    return {"done": states.count("done"), "dropped": states.count("dropped"), "total": len(states)}


def _require_item(conn: sqlite3.Connection, uid: str, item: str) -> str:
    key = item_key(item)
    found = conn.execute(
        "SELECT 1 FROM task_items WHERE memory_uid = ? AND item_key = ?", (uid, key)
    ).fetchone()
    if found is None:
        raise ValueError(f"task {uid} has no item {key}")
    return key


def _regenerate(conn: sqlite3.Connection, uid: str, note: str) -> None:
    """Rewrite the memory's content from the goal and items when it changed."""
    goal = conn.execute("SELECT goal FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["goal"]
    content = render(goal, _items(conn, uid))
    if content != db.get_memory(conn, uid)["content"]:
        db.update_memory_content(conn, uid, content, note=note)


def _settle(conn: sqlite3.Connection, uid: str) -> None:
    """Close, cancel or reopen the task to match its items, as the lifecycle says."""
    states = {i["state"] for i in _items(conn, uid)}
    if states & {"todo", "doing"}:
        wanted = "open"
    elif "done" in states:
        wanted = "completed"
    else:
        wanted = "cancelled"
    current = conn.execute("SELECT state FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["state"]
    if wanted == current:
        return
    if wanted == "open":
        conn.execute("UPDATE tasks SET state = 'open', completed_at = '' WHERE memory_uid = ?", (uid,))
        db.set_status(conn, uid, "active", note="reopened")
        return
    stamp = db.now_iso() if wanted == "completed" else ""
    conn.execute(
        "UPDATE tasks SET state = ?, completed_at = ? WHERE memory_uid = ?", (wanted, stamp, uid)
    )
    db.set_status(conn, uid, "archived", note=wanted)


def _outcome(conn: sqlite3.Connection, uid: str) -> dict:
    state = conn.execute("SELECT state FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["state"]
    return {
        "progress": progress(conn, uid),
        "task_state": state,
        "archived": db.get_memory(conn, uid)["status"] == "archived",
    }


def set_item_state(
    conn: sqlite3.Connection, uid: str, item: str, state: str, *, session: str = ""
) -> dict:
    """Move one item to `state`, then close or reopen the task to match.

    A move to the state the item already has writes nothing and reports
    changed=False. Raises ValueError for a non-task, an unknown item or state.
    """
    if state not in ITEM_STATES:
        raise ValueError(f"{state!r} is not an item state; use one of {', '.join(ITEM_STATES)}")
    _lock(conn, uid)
    key = _require_item(conn, uid, item)
    old = conn.execute(
        "SELECT state FROM task_items WHERE memory_uid = ? AND item_key = ?", (uid, key)
    ).fetchone()["state"]
    changed = old != state
    if changed:
        conn.execute(
            """UPDATE task_items SET state = ?, updated_at = ?, updated_session = ?
               WHERE memory_uid = ? AND item_key = ?""",
            (state, db.now_iso(), session, uid, key),
        )
        _regenerate(conn, uid, f"item {key}: {old} -> {state}")
        _settle(conn, uid)
    return {"uid": uid, "item": key, "state": state, "changed": changed, **_outcome(conn, uid)}


def add_items(conn: sqlite3.Connection, uid: str, items: list[str], *, session: str = "") -> dict:
    """Append items under the next unused keys; a closed task reopens."""
    items = [str(i).strip() for i in items]
    if not items or not all(items):
        raise ValueError("add at least one item, and no item may be empty")
    for text in items:
        if len(text) > ITEM_MAX:
            raise ValueError(f"an item is {len(text)} characters; the limit is {ITEM_MAX}")
    _lock(conn, uid)
    last = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM task_items WHERE memory_uid = ?", (uid,)
    ).fetchone()[0]
    if last + len(items) > ITEMS_MAX:
        raise ValueError(f"a task holds at most {ITEMS_MAX} items; it has {last}")
    stamp = db.now_iso()
    keys = [f"i{n}" for n in range(last + 1, last + len(items) + 1)]
    conn.executemany(
        """INSERT INTO task_items
           (memory_uid, item_key, seq, text, state, updated_at, updated_session)
           VALUES (?, ?, ?, ?, 'todo', ?, ?)""",
        [(uid, k, last + n, t, stamp, session) for n, (k, t) in enumerate(zip(keys, items), start=1)],
    )
    note = f"item {keys[0]} added" if len(keys) == 1 else f"items {', '.join(keys)} added"
    _regenerate(conn, uid, note)
    _settle(conn, uid)
    return {"uid": uid, "keys": keys, **_outcome(conn, uid)}


def set_goal(conn: sqlite3.Connection, uid: str, goal: str) -> None:
    """Replace the goal; the content is regenerated when the goal changed."""
    goal = str(goal).strip()
    if not goal:
        raise ValueError("a task needs a goal")
    if len(goal) > GOAL_MAX:
        raise ValueError(f"goal is {len(goal)} characters; the limit is {GOAL_MAX}")
    _lock(conn, uid)
    conn.execute("UPDATE tasks SET goal = ? WHERE memory_uid = ?", (goal, uid))
    _regenerate(conn, uid, "goal edited")


def add_comment(
    conn: sqlite3.Connection, uid: str, body: str, *,
    item: str = "", author: str = "agent", session: str = "",
) -> int:
    """Store a comment on the task, or on one item; returns its id.

    Comments stay out of the content, so they never become an edit.
    """
    body = str(body).strip()
    if not body:
        raise ValueError("a comment needs a body")
    if len(body) > COMMENT_MAX:
        raise ValueError(f"comment is {len(body)} characters; the limit is {COMMENT_MAX}")
    if author not in ("agent", "person"):
        raise ValueError(f"{author!r} is not a comment author; use agent or person")
    _lock(conn, uid)
    key = _require_item(conn, uid, item) if str(item).strip() else ""
    cur = conn.execute(
        """INSERT INTO task_comments (memory_uid, item_key, body, author, session, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, key, body, author, session, db.now_iso()),
    )
    return cur.lastrowid


def link_item(conn: sqlite3.Connection, uid: str, item: str, targets: list[str]) -> list[str]:
    """Link memories to an item; returns the ones newly linked.

    An unknown target raises ValueError and links nothing.
    """
    _lock(conn, uid)
    key = _require_item(conn, uid, item)
    wanted = list(dict.fromkeys(str(t).strip() for t in targets))
    for target in wanted:
        if db.get_memory(conn, target) is None:
            raise ValueError(f"no memory {target}")
    linked = []
    for target in wanted:
        cur = conn.execute(
            """INSERT OR IGNORE INTO task_item_links (memory_uid, item_key, target_uid, created_at)
               VALUES (?, ?, ?, ?)""",
            (uid, key, target, db.now_iso()),
        )
        if cur.rowcount:
            linked.append(target)
    return linked


def unlink_item(conn: sqlite3.Connection, uid: str, item: str, target: str) -> bool:
    """Remove one link from an item; True when there was one."""
    _lock(conn, uid)
    key = _require_item(conn, uid, item)
    cur = conn.execute(
        "DELETE FROM task_item_links WHERE memory_uid = ? AND item_key = ? AND target_uid = ?",
        (uid, key, str(target).strip()),
    )
    return cur.rowcount > 0
