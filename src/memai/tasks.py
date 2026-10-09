"""Task checklists: a `task` memory that owns items, links and comments.

A task is a memories row of type 'task' whose content is generated from its
goal and items, the way a diagram's content is generated from its graph.
"""

import re
import sqlite3

from . import budget, lite, sections
from .contract import GOAL_MAX, ITEM_MAX, ITEM_STATES, ITEMS_MAX, NOTE_MAX, TASK_STATES
from .store import connection, memories, task_items
from .store import item_ids as store_item_ids
from .store import sections as store_sections

COMMENT_MAX = 2000

_CITED_KEY_RE = re.compile(r"\bi([1-9][0-9]*)\b", re.I)

CITE_RULE = ("an item is cited by its key only in DEPENDS ON: keys renumber when an item is "
             "deleted, and free text is never rewritten. Name the item by what it does, or "
             "put the dependency in DEPENDS ON")


def _item_length_error(text: str) -> str | None:
    if len(text) <= ITEM_MAX:
        return None
    return (f"an item is {len(text)} characters; the limit is {ITEM_MAX}. An item is a short "
            "label of a few words; its detail goes in a brief (task_note)")


def cited_key_error(fields: dict[str, str], count: int) -> str | None:
    """Why `fields` (free text by field name) may not be written, or None.

    A field fails when it names one of the task's `count` item keys; a
    number past the last item is not a key.
    """
    problems = []
    for name, text in fields.items():
        cited = dict.fromkeys(f"i{int(m[1])}" for m in _CITED_KEY_RE.finditer(str(text))
                              if int(m[1]) <= count)
        if cited:
            problems.append(f"{name} cites {', '.join(cited)}")
    return f"{'; '.join(problems)}: {CITE_RULE}" if problems else None


def _require_no_cited_key(fields: dict[str, str], count: int) -> None:
    error = cited_key_error(fields, count)
    if error:
        raise ValueError(error)


_BRIEF_LABELS = {s.key: s.label for s in sections.BRIEF_SPEC}


def free_text(body: str) -> dict[str, str]:
    """A note body's free text by field: a brief's fields but DEPENDS ON, or the whole body."""
    fields = brief_fields(body)
    if fields is None:
        return {"body": body}
    return {_BRIEF_LABELS[k]: v for k, v in fields.items() if k != "depends_on"}


def _given_free_text(title: str, body: str, brief: dict[str, str] | None) -> dict[str, str]:
    """The free text a note edit writes: only the title, body or brief fields it gives."""
    given = {"title": str(title)} if str(title).strip() else {}
    if _has_fields(brief):
        return {**given, **{_BRIEF_LABELS.get(k, k): str(v) for k, v in (brief or {}).items()
                            if str(v).strip() and k != "depends_on"}}
    return {**given, **free_text(str(body))} if str(body).strip() else given


def split_items(text: str) -> list[str]:
    """The non-blank lines of `text`, each trimmed."""
    return [line.strip() for line in text.splitlines() if line.strip()]


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
        if error := _item_length_error(text):
            raise ValueError(error)
    _require_no_cited_key({"title": title, "goal": goal,
                           **{f"item {n}": t for n, t in enumerate(items, start=1)}}, len(items))
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
    uid = memories.insert_memory(
        conn, type=memories.TASK_TYPE, content=task_items.render(goal, []), title=title,
        domain=domain, also=also, session=session, tags=tags,
    )
    conn.execute("INSERT INTO tasks (memory_uid, goal) VALUES (?, ?)", (uid, goal))
    stamp = lite.now_iso()
    conn.executemany(
        """INSERT INTO task_items (memory_uid, seq, text, state, updated_at, updated_session)
           VALUES (?, ?, ?, 'todo', ?, ?)""",
        [(uid, n, text, stamp, session) for n, text in enumerate(items, start=1)],
    )
    task_items.regenerate(conn, uid, touch=False)
    return uid


def is_task(conn: sqlite3.Connection, uid: str) -> bool:
    """True when `uid` is a task memory."""
    row = memories.get_memory(conn, uid)
    return row is not None and row["type"] == memories.TASK_TYPE


def get_task(conn: sqlite3.Connection, uid: str) -> dict | None:
    """The task's goal, state, items (with linked memories), comments and notes, or None."""
    head = conn.execute("SELECT * FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()
    if head is None:
        return None
    links: dict[int, list[dict]] = {}
    for r in conn.execute(
        """SELECT l.item_id, l.target_uid, m.title, m.type
           FROM task_item_links l JOIN memories m ON m.uid = l.target_uid
           WHERE l.memory_uid = ? ORDER BY l.created_at, l.target_uid""",
        (uid,),
    ):
        links.setdefault(r["item_id"], []).append(
            {"uid": r["target_uid"], "title": r["title"], "type": r["type"]}
        )
    items = [
        {
            "id": r["id"], "n": n, "seq": r["seq"], "text": r["text"], "state": r["state"],
            "updated_at": r["updated_at"], "updated_session": r["updated_session"],
            "links": links.get(r["id"], []),
        }
        for n, r in enumerate(conn.execute(
            "SELECT * FROM task_items WHERE memory_uid = ? ORDER BY seq, id", (uid,)), start=1)
    ]
    comments = [
        {
            "id": r["id"], "item": r["item_id"], "body": r["body"], "author": r["author"],
            "session": r["session"], "created_at": r["created_at"],
        }
        for r in conn.execute(
            "SELECT * FROM task_comments WHERE memory_uid = ? ORDER BY created_at, id", (uid,)
        )
    ]
    ids = _note_item_map(conn, uid)
    notes = [_note_dict(conn, r, ids) for r in conn.execute(
        "SELECT * FROM task_notes WHERE memory_uid = ? ORDER BY created_at, id", (uid,))]
    return {
        "goal": head["goal"], "state": head["state"], "completed_at": head["completed_at"],
        "items": items, "comments": comments, "notes": notes,
    }


def _lock(conn: sqlite3.Connection, uid: str) -> None:
    """Take the write lock on the task's row, so what follows reads settled state.

    Raises ValueError when `uid` is not a task.
    """
    cur = conn.execute("UPDATE tasks SET memory_uid = memory_uid WHERE memory_uid = ?", (uid,))
    if cur.rowcount == 0:
        raise ValueError(f"no task {uid}")


def _items(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """The task's items in list order, each with its id and its position `n`."""
    return [
        {"id": r["id"], "n": n, "seq": r["seq"], "text": r["text"], "state": r["state"]}
        for n, r in enumerate(conn.execute(
            "SELECT id, seq, text, state FROM task_items WHERE memory_uid = ? ORDER BY seq, id",
            (uid,)), start=1)
    ]


def item_ids(conn: sqlite3.Connection, uid: str) -> list[int]:
    """The task's item ids in list order."""
    return [i["id"] for i in _items(conn, uid)]


def progress(conn: sqlite3.Connection, uid: str) -> dict:
    """How many items are done, dropped and in all."""
    if not is_task(conn, uid):
        raise ValueError(f"no task {uid}")
    states = [i["state"] for i in _items(conn, uid)]
    return {"done": states.count("done"), "dropped": states.count("dropped"), "total": len(states)}


def require_item(conn: sqlite3.Connection, uid: str, item) -> int:
    """The id `item` names when it is an item of the task.

    Raises ValueError otherwise; when `item` is a position on the list, the
    message names the id at that position.
    """
    try:
        if isinstance(item, bool):
            raise ValueError
        value = int(item)
    except (TypeError, ValueError):
        raise ValueError(f"{item!r} is not an item id; task_read(part='items') lists them") from None
    ids = item_ids(conn, uid)
    if value in ids:
        return value
    hint = f"; item {value} on the list is {ids[value - 1]}" if 1 <= value <= len(ids) else ""
    raise ValueError(f"task {uid} has no item {value}; task_read(part='items') lists its ids{hint}")


def _regenerate(
    conn: sqlite3.Connection, uid: str, note: str, *, record_edit: bool, leaked_ok: bool = False,
) -> None:
    """Rewrite the memory's content from the goal and items when it changed.

    leaked_ok skips the leak check on a recorded edit, for a write that adds no new text.
    """
    goal = conn.execute("SELECT goal FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["goal"]
    content = task_items.render(goal, _items(conn, uid))
    if content == memories.memory_row(conn, uid)["content"]:
        return
    if record_edit:
        memories.update_memory_content(conn, uid, content, note=note, leaked_ok=leaked_ok)
    else:
        memories.set_generated_content(conn, uid, content)


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
        memories.set_status(conn, uid, "active", note="reopened")
        return
    stamp = lite.now_iso() if wanted == "completed" else ""
    conn.execute(
        "UPDATE tasks SET state = ?, completed_at = ? WHERE memory_uid = ?", (wanted, stamp, uid)
    )
    memories.set_status(conn, uid, "archived", note=wanted)


def _outcome(conn: sqlite3.Connection, uid: str) -> dict:
    state = conn.execute("SELECT state FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["state"]
    return {
        "progress": progress(conn, uid),
        "task_state": state,
        "archived": memories.memory_row(conn, uid)["status"] == "archived",
    }


def set_item_state(
    conn: sqlite3.Connection, uid: str, item: int, state: str, *, session: str = ""
) -> dict:
    """Move one item to `state`, then close or reopen the task to match.

    A move to the state the item already has writes nothing and reports
    changed=False. Raises ValueError for a non-task, an unknown item or state.
    """
    if state not in ITEM_STATES:
        raise ValueError(f"{state!r} is not an item state; use one of {', '.join(ITEM_STATES)}")
    _lock(conn, uid)
    item_id = require_item(conn, uid, item)
    old = conn.execute("SELECT state FROM task_items WHERE id = ?", (item_id,)).fetchone()["state"]
    changed = old != state
    if changed:
        conn.execute(
            "UPDATE task_items SET state = ?, updated_at = ?, updated_session = ? WHERE id = ?",
            (state, lite.now_iso(), session, item_id),
        )
        _regenerate(conn, uid, f"item {item_id}: {old} -> {state}", record_edit=False)
        _settle(conn, uid)
    return {"uid": uid, "item": item_id, "state": state, "changed": changed, **_outcome(conn, uid)}


def rename_item(conn: sqlite3.Connection, uid: str, item: int, text: str, *, session: str = "") -> dict:
    """Give one item a new text; its id, state, notes, comments and links stay.

    The previous text stays in the edit history. Raises ValueError for an
    empty, over-long or key-citing text or an unknown item, before anything
    is written. The same text again writes nothing and reports changed=False.
    """
    text = str(text).strip()
    if not text:
        raise ValueError("an item's text may not be empty")
    if error := _item_length_error(text):
        raise ValueError(error)
    _lock(conn, uid)
    item_id = require_item(conn, uid, item)
    rows = _items(conn, uid)
    row = next(r for r in rows if r["id"] == item_id)
    _require_no_cited_key({f"item {row['n']}": text}, len(rows))
    changed = row["text"] != text
    if changed:
        conn.execute(
            "UPDATE task_items SET text = ?, updated_at = ?, updated_session = ? WHERE id = ?",
            (text, lite.now_iso(), session, item_id),
        )
        _regenerate(conn, uid, f"item {item_id} renamed: {row['text']} -> {text}", record_edit=True)
    return {"uid": uid, "item": item_id, "text": text, "changed": changed, **_outcome(conn, uid)}


def add_items(conn: sqlite3.Connection, uid: str, items: list[str], *, session: str = "") -> dict:
    """Append items at the end of the list; a closed task reopens."""
    items = [str(i).strip() for i in items]
    if not items or not all(items):
        raise ValueError("add at least one item, and no item may be empty")
    for text in items:
        if error := _item_length_error(text):
            raise ValueError(error)
    _lock(conn, uid)
    count = len(_items(conn, uid))
    if count + len(items) > ITEMS_MAX:
        raise ValueError(f"a task holds at most {ITEMS_MAX} items; it has {count}")
    _require_no_cited_key({f"item {count + n}": t for n, t in enumerate(items, start=1)},
                          count + len(items))
    last = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM task_items WHERE memory_uid = ?",
                        (uid,)).fetchone()[0]
    stamp = lite.now_iso()
    ids = []
    for n, text in enumerate(items, start=1):
        cur = conn.execute(
            """INSERT INTO task_items (memory_uid, seq, text, state, updated_at, updated_session)
               VALUES (?, ?, ?, 'todo', ?, ?)""", (uid, last + n, text, stamp, session))
        ids.append(cur.lastrowid or 0)
    note = f"item {ids[0]} added" if len(ids) == 1 else f"items {', '.join(map(str, ids))} added"
    _regenerate(conn, uid, note, record_edit=False)
    _settle(conn, uid)
    return {"uid": uid, "ids": ids, **_outcome(conn, uid)}


def delete_item(conn: sqlite3.Connection, uid: str, item: int, *, session: str = "") -> dict:
    """Remove one item with its comments, links and note attachments, then settle an open task.

    No other item moves. A DEPENDS ON entry that named it keeps its text,
    marked deleted. The deletion enters the edit history and adds no text,
    so it is not held to the leak check. A closed task keeps its state.
    Raises ValueError for a non-task, an unknown item or the task's only
    item, before anything is written.

    `session` is accepted for signature parity with the other writers; the
    item it would stamp is the one removed, so nothing records it.
    """
    _lock(conn, uid)
    item_id = require_item(conn, uid, item)
    rows = _items(conn, uid)
    if len(rows) == 1:
        raise ValueError("a task keeps at least one item")
    gone = next(r for r in rows if r["id"] == item_id)
    conn.execute("UPDATE task_note_depends SET item_id = NULL, text = ? WHERE item_id = ?",
                 (gone["text"], item_id))
    for table in ("task_note_items", "task_comments", "task_item_links"):
        conn.execute(f"DELETE FROM {table} WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM task_items WHERE id = ?", (item_id,))
    _regenerate(conn, uid, f"item {item_id} deleted: {gone['text']}", record_edit=True, leaked_ok=True)
    state = conn.execute("SELECT state FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["state"]
    if state == "open":
        _settle(conn, uid)
    return {"uid": uid, "item": item_id, **_outcome(conn, uid)}


def set_goal(conn: sqlite3.Connection, uid: str, goal: str, *, note: str = "") -> None:
    """Replace the goal; the content is regenerated, and `note` names the edit in the history."""
    goal = str(goal).strip()
    if not goal:
        raise ValueError("a task needs a goal")
    if len(goal) > GOAL_MAX:
        raise ValueError(f"goal is {len(goal)} characters; the limit is {GOAL_MAX}")
    _lock(conn, uid)
    _require_no_cited_key({"goal": goal}, len(_items(conn, uid)))
    conn.execute("UPDATE tasks SET goal = ? WHERE memory_uid = ?", (goal, uid))
    _regenerate(conn, uid, note or "goal edited", record_edit=True)


def add_comment(
    conn: sqlite3.Connection, uid: str, body: str, *,
    item: int = 0, author: str = "agent", session: str = "",
) -> int:
    """Store a comment on the task, or on one item when `item` is an id; returns its id.

    Comments stay out of the content, so they never become an edit.
    """
    body = str(body).strip()
    if not body:
        raise ValueError("a comment needs a body")
    if len(body) > COMMENT_MAX:
        raise ValueError(f"comment is {len(body)} characters; the limit is {COMMENT_MAX}")
    store_sections._refuse_leak(memories.TASK_TYPE, body)
    if author not in ("agent", "person"):
        raise ValueError(f"{author!r} is not a comment author; use agent or person")
    _lock(conn, uid)
    item_id = require_item(conn, uid, item) if item else None
    _require_no_cited_key({"comment": body}, len(_items(conn, uid)))
    cur = conn.execute(
        """INSERT INTO task_comments (memory_uid, item_id, body, author, session, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, item_id, body, author, session, lite.now_iso()),
    )
    return cur.lastrowid or 0


def link_item(conn: sqlite3.Connection, uid: str, item: int, targets: list[str]) -> list[str]:
    """Link memories to an item; returns the ones newly linked.

    An unknown target raises ValueError and links nothing.
    """
    _lock(conn, uid)
    item_id = require_item(conn, uid, item)
    wanted = list(dict.fromkeys(str(t).strip() for t in targets))
    for target in wanted:
        if memories.get_memory(conn, target) is None:
            raise ValueError(f"no memory {target}")
    linked = []
    for target in wanted:
        cur = conn.execute(
            """INSERT OR IGNORE INTO task_item_links (memory_uid, item_id, target_uid, created_at)
               VALUES (?, ?, ?, ?)""",
            (uid, item_id, target, lite.now_iso()),
        )
        if cur.rowcount:
            linked.append(target)
    return linked


def unlink_item(conn: sqlite3.Connection, uid: str, item: int, target: str) -> bool:
    """Remove one link from an item; True when there was one."""
    _lock(conn, uid)
    item_id = require_item(conn, uid, item)
    cur = conn.execute(
        "DELETE FROM task_item_links WHERE item_id = ? AND target_uid = ?",
        (item_id, str(target).strip()),
    )
    return cur.rowcount > 0


BRIEF_KEYS: tuple[str, ...] = tuple(s.key for s in sections.BRIEF_SPEC)


def brief_error(body: str, *, on_items: bool = True) -> str | None:
    """Why `body` is not a brief, or None when it is one.

    on_items says whether the note lands on items; a note on the whole task
    is only held to a brief when it is built from brief fields.
    """
    problems = sections.read_spec(sections.BRIEF_SPEC, body).problems
    if not problems:
        return None
    required = ", ".join(s.label for s in sections.BRIEF_SPEC if not s.optional)
    optional = ", ".join(s.label for s in sections.BRIEF_SPEC if s.optional)
    shape = f"{required}, then optionally {optional}"
    if on_items:
        return f"a note on items is a brief: {shape}; this one does not read that way: {'; '.join(problems)}"
    return f"brief fields must form a whole brief: {shape}; these do not: {'; '.join(problems)}"


def brief_fields(body: str) -> dict[str, str] | None:
    """The brief's fields by key, or None when `body` is not a brief."""
    reading = sections.read_spec(sections.BRIEF_SPEC, body)
    return reading.sections if reading.conforms else None


def brief_body(fields: dict[str, str], stored: str = "", *, on_items: bool = True) -> str:
    """A brief built from `fields`; over a `stored` brief, each field given replaces only its own."""
    unknown = sorted(set(fields) - set(BRIEF_KEYS))
    if unknown:
        raise ValueError(f"{', '.join(unknown)} is not a brief field; use {', '.join(BRIEF_KEYS)}")
    given = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
    body = sections.render_spec(sections.BRIEF_SPEC, {**(brief_fields(stored) or {}), **given})
    _require_brief(body, on_items=on_items)
    return body


def _require_brief(body: str, *, on_items: bool = True) -> None:
    error = brief_error(body, on_items=on_items)
    if error:
        raise ValueError(error)


DEPENDS_FORMAT = "DEPENDS ON is none, or item ids with an optional reason in parentheses: 4812 (why), 4815"


def depends_error(conn: sqlite3.Connection, uid: str, field: str, applies_to: list[int]) -> str | None:
    """The problems with a DEPENDS ON field, or None when it reads and names only other live items of the task."""
    entries, problems = sections.read_depends(field)
    live = set(item_ids(conn, uid))
    for e in entries:
        if e.key:
            problems.append(f"{e.key} is a position; name the item by its id")
        elif e.item is not None and e.item not in live:
            problems.append(f"{e.item} is not an item of this task")
        elif e.item is not None and e.item in applies_to:
            problems.append(f"{e.item} is an item this note applies to")
    return "; ".join(problems) or None


def _split(conn: sqlite3.Connection, uid: str, body: str, applies_to: list[int], *,
           check: bool) -> tuple[str, list[sections.Dependency] | None]:
    """The body to store and its dependencies: a brief whose DEPENDS ON reads is stored without
    the field, which comes back as entries; any other body is stored as written, with None.

    check makes a DEPENDS ON that does not read, or names an item it may not, a ValueError.
    """
    fields = brief_fields(body)
    if fields is None:
        return body, None
    error = depends_error(conn, uid, fields["depends_on"], applies_to)
    if error:
        if check:
            raise ValueError(f"{DEPENDS_FORMAT} — {error}")
        return body, None
    entries, _ = sections.read_depends(fields["depends_on"])
    return sections.without_depends(body) or body, entries


def _require_no_overlap(body: str, applies_to: list[int]) -> None:
    """A scope edit may not put a brief on an item its DEPENDS ON names; a field that does not read stays as it is."""
    entries, problems = sections.read_depends((brief_fields(body) or {}).get("depends_on", "none"))
    hit = [e.item for e in entries if e.item is not None and e.item in applies_to]
    if hit and not problems:
        raise ValueError(f"{DEPENDS_FORMAT} — {hit[0]} is an item this note applies to")


def _read_body(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """A note's body as read: a split brief gets its DEPENDS ON back from its rows."""
    if not row["split_depends"]:
        return row["body"]
    field = sections.write_depends(store_item_ids.dependencies(conn, row["id"]))
    return sections.with_depends(row["body"], field)


def dependencies(conn: sqlite3.Connection, note_id: int) -> list[dict] | None:
    """A split brief's dependencies as {item, text, reason}, or None for any other note."""
    row = conn.execute("SELECT split_depends FROM task_notes WHERE id = ?", (note_id,)).fetchone()
    if row is None or not row["split_depends"]:
        return None
    return [{"item": e.item, "text": e.text, "reason": e.reason}
            for e in store_item_ids.dependencies(conn, note_id)]


def _has_fields(brief: dict[str, str] | None) -> bool:
    return any(str(v).strip() for v in (brief or {}).values())


def _compose(body: str, brief: dict[str, str] | None, stored: str, *, on_items: bool) -> str:
    """The body a write supplies: `body` as given, or one built from brief fields."""
    if not _has_fields(brief):
        return str(body)
    if str(body).strip():
        raise ValueError("give a note's body or its brief fields, not both")
    return brief_body(brief or {}, stored, on_items=on_items)


def _note_fields(title: str, body: str) -> tuple[str, str]:
    title, body = str(title).strip(), str(body).strip()
    if not title or not body:
        raise ValueError("a task note needs a title and a body")
    if len(title) > store_sections.TITLE_MAX:
        raise ValueError(f"title is {len(title)} characters; the limit is {store_sections.TITLE_MAX}")
    if len(body) > NOTE_MAX:
        raise ValueError(f"body is {len(body)} characters; the limit is {NOTE_MAX}")
    store_sections._refuse_leak("task_note", f"{title}\n{body}")
    return title, body


def _note_ids(conn: sqlite3.Connection, uid: str, items: list) -> list[int]:
    return list(dict.fromkeys(require_item(conn, uid, i) for i in items if str(i).strip()))


def _require_note(conn: sqlite3.Connection, uid: str, note_id: int) -> int:
    found = conn.execute(
        "SELECT id FROM task_notes WHERE id = ? AND memory_uid = ?", (int(note_id), uid)
    ).fetchone()
    if found is None:
        raise ValueError(f"task {uid} has no note {note_id}")
    return found["id"]


def _set_note_items(conn: sqlite3.Connection, note_id: int, ids: list[int]) -> None:
    conn.execute("DELETE FROM task_note_items WHERE note_id = ?", (note_id,))
    conn.executemany("INSERT INTO task_note_items (note_id, item_id) VALUES (?, ?)",
                     [(note_id, i) for i in ids])


def _note_item_map(conn: sqlite3.Connection, uid: str) -> dict[int, list[int]]:
    """Each note's item ids, in item order."""
    ids: dict[int, list[int]] = {}
    for r in conn.execute(
        """SELECT i.note_id, i.item_id FROM task_note_items i
           JOIN task_notes n ON n.id = i.note_id
           JOIN task_items t ON t.id = i.item_id
           WHERE n.memory_uid = ? ORDER BY t.seq, t.id""", (uid,)):
        ids.setdefault(r["note_id"], []).append(r["item_id"])
    return ids


def _note_dict(conn: sqlite3.Connection, row: sqlite3.Row, ids: dict[int, list[int]]) -> dict:
    return {"id": row["id"], "title": row["title"], "body": _read_body(conn, row),
            "items": ids.get(row["id"], []), "updated_at": row["updated_at"]}


def add_note(conn: sqlite3.Connection, uid: str, *, title: str, body: str = "",
             items: list, brief: dict[str, str] | None = None, session: str = "") -> int:
    """A note owned by the task, on `items` (ids) or, with none, on the task as a whole; returns its id.

    A note on items is a brief, given as `body` or built from `brief` fields.
    """
    on_items = any(str(i).strip() for i in items)
    title, body = _note_fields(title, _compose(body, brief, "", on_items=on_items))
    _lock(conn, uid)
    _require_no_cited_key({"title": title, **free_text(body)}, len(_items(conn, uid)))
    ids = _note_ids(conn, uid, items)
    if ids:
        _require_brief(body)
    stored, entries = _split(conn, uid, body, ids, check=bool(ids) or _has_fields(brief))
    stamp = lite.now_iso()
    cur = conn.execute(
        """INSERT INTO task_notes (memory_uid, title, body, split_depends, session, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (uid, title, stored, int(entries is not None), session, stamp, stamp))
    note_id = cur.lastrowid or 0
    _set_note_items(conn, note_id, ids)
    if entries is not None:
        store_item_ids.set_dependencies(conn, note_id, entries)
    return note_id


def edit_note(conn: sqlite3.Connection, uid: str, note_id: int, *, title: str = "",
              body: str = "", items: list | None = None,
              brief: dict[str, str] | None = None) -> None:
    """Overwrite a note; an empty title or body keeps the stored one, items=None keeps the set.

    `brief` fields replace only their own in a stored brief, and its
    dependencies stay unless `depends_on` is one of them. A note that ends up
    on items must be a brief when the write gives it a body or moves it there
    from the whole task; a title or scope edit of a stored note on items
    leaves its body as it is.
    """
    _lock(conn, uid)
    note_id = _require_note(conn, uid, note_id)
    _require_no_cited_key(_given_free_text(title, body, brief), len(_items(conn, uid)))
    row = conn.execute("SELECT * FROM task_notes WHERE id = ?", (note_id,)).fetchone()
    current = _read_body(conn, row)
    had = conn.execute("SELECT 1 FROM task_note_items WHERE note_id = ?", (note_id,)).fetchone() is not None
    ids = None if items is None else _note_ids(conn, uid, items)
    on_items = bool(ids) if ids is not None else had
    given = _compose(body, brief, current, on_items=on_items)
    title, body = _note_fields(title or row["title"], given or current)
    if on_items and (given or not had):
        _require_brief(body)
    applies_to = ids if ids is not None else [r["item_id"] for r in conn.execute(
        "SELECT item_id FROM task_note_items WHERE note_id = ?", (note_id,))]
    check = bool(on_items and (given or not had)) or _has_fields(brief)
    if not check and ids and had:
        _require_no_overlap(body, ids)
    stored, entries = _split(conn, uid, body, applies_to, check=check)
    conn.execute("UPDATE task_notes SET title = ?, body = ?, split_depends = ?, updated_at = ? WHERE id = ?",
                 (title, stored, int(entries is not None), lite.now_iso(), note_id))
    if ids is not None:
        _set_note_items(conn, note_id, ids)
    store_item_ids.set_dependencies(conn, note_id, entries or [])


def delete_note(conn: sqlite3.Connection, uid: str, note_id: int) -> None:
    _lock(conn, uid)
    note_id = _require_note(conn, uid, note_id)
    conn.execute("DELETE FROM task_note_depends WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM task_note_items WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM task_notes WHERE id = ?", (note_id,))


def note(conn: sqlite3.Connection, uid: str, note_id: int) -> dict:
    """One note of the task, shaped like a notes() entry."""
    note_id = _require_note(conn, uid, note_id)
    row = conn.execute("SELECT * FROM task_notes WHERE id = ?", (note_id,)).fetchone()
    return _note_dict(conn, row, _note_item_map(conn, uid))


def notes(conn: sqlite3.Connection, uid: str, item: int = 0) -> list[dict]:
    """The task-level notes, or with `item` the notes on that item, oldest first."""
    if item:
        item_id = require_item(conn, uid, item)
        rows = conn.execute(
            """SELECT n.* FROM task_notes n JOIN task_note_items i ON i.note_id = n.id
               WHERE n.memory_uid = ? AND i.item_id = ? ORDER BY n.created_at, n.id""",
            (uid, item_id)).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM task_notes n WHERE memory_uid = ? AND NOT EXISTS
               (SELECT 1 FROM task_note_items i WHERE i.note_id = n.id)
               ORDER BY created_at, id""", (uid,)).fetchall()
    ids = _note_item_map(conn, uid)
    return [_note_dict(conn, r, ids) for r in rows]


def _restored_dependencies(depends: list[dict], ids: dict) -> list[sections.Dependency]:
    """A record's dependency rows moved through `ids`; one naming an item the record lacks keeps its text, or goes."""
    out = []
    for d in depends:
        if d.get("item") in ids:
            out.append(sections.Dependency(ids[d["item"]], "", "", d.get("reason", "")))
        elif d.get("text"):
            out.append(sections.Dependency(None, "", d["text"], d.get("reason", "")))
    return out


def restore_task(conn: sqlite3.Connection, record: dict) -> None:
    """Write a task's rows from an export record, under an already-restored memory.

    Items get this store's ids, and every row naming one follows the map
    from the record's ids. Skips a link whose target is not in the store and
    writes no edit. A state outside TASK_STATES or ITEM_STATES is a
    ValueError before any row is written.
    """
    uid = str(record["uid"])
    state = record.get("state", "open")
    if state not in TASK_STATES:
        raise ValueError(f"{state!r} is not a task state; use {', '.join(TASK_STATES)}")
    for i in record.get("items") or []:
        if i.get("state", "todo") not in ITEM_STATES:
            raise ValueError(
                f"{i.get('state')!r} is not an item state; use {', '.join(ITEM_STATES)}")
    conn.execute(
        "INSERT INTO tasks (memory_uid, goal, state, completed_at) VALUES (?, ?, ?, ?)",
        (uid, record.get("goal", ""), state, record.get("completed_at", "")),
    )
    ids: dict = {}
    for i in record.get("items") or []:
        cur = conn.execute(
            """INSERT INTO task_items (memory_uid, seq, text, state, updated_at, updated_session)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, i["seq"], i["text"], i.get("state", "todo"),
             i.get("updated_at") or lite.now_iso(), i.get("updated_session", "")))
        ids[i["id"]] = cur.lastrowid
    conn.executemany(
        """INSERT OR IGNORE INTO task_item_links (memory_uid, item_id, target_uid, created_at)
           VALUES (?, ?, ?, ?)""",
        [
            (uid, ids[link["item_id"]], link["target_uid"], link.get("created_at") or lite.now_iso())
            for link in record.get("links") or []
            if link.get("item_id") in ids and memories.get_memory(conn, link["target_uid"]) is not None
        ],
    )
    conn.executemany(
        """INSERT INTO task_comments (memory_uid, item_id, body, author, session, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (uid, ids.get(c.get("item_id")), c["body"], c.get("author", "agent"),
             c.get("session", ""), c.get("created_at") or lite.now_iso())
            for c in record.get("comments") or []
            if c.get("item_id") is None or c.get("item_id") in ids
        ],
    )
    for n in record.get("notes") or []:
        depends = n.get("depends")
        entries = None if depends is None else _restored_dependencies(depends, ids)
        cur = conn.execute(
            """INSERT INTO task_notes (memory_uid, title, body, split_depends, session, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uid, n["title"], n["body"], int(entries is not None), n.get("session", ""),
             n.get("created_at") or lite.now_iso(), n.get("updated_at") or lite.now_iso()))
        note_id = cur.lastrowid or 0
        _set_note_items(conn, note_id, [ids[k] for k in n.get("items") or [] if k in ids])
        if entries is not None:
            store_item_ids.set_dependencies(conn, note_id, entries)
    task_items.regenerate(conn, uid, touch=False)


PARTS = ("items", "notes", "comments", "links")


def _require_task(conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()
    if row is None:
        raise ValueError(f"no task {uid}")
    return row


def _count(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def _item_counts(conn: sqlite3.Connection, item_id: int) -> dict:
    return {
        "notes": _count(conn, "SELECT COUNT(*) FROM task_note_items WHERE item_id = ?", item_id),
        "comments": _count(conn, "SELECT COUNT(*) FROM task_comments WHERE item_id = ?", item_id),
        "links": _count(conn, "SELECT COUNT(*) FROM task_item_links WHERE item_id = ?", item_id),
    }


def head(conn: sqlite3.Connection, uid: str) -> dict:
    """Goal, state, progress and the size of each task-level collection; no records."""
    row = _require_task(conn, uid)
    return {
        "goal": row["goal"], "state": row["state"], "progress": progress(conn, uid),
        "counts": {
            "items": _count(conn, "SELECT COUNT(*) FROM task_items WHERE memory_uid = ?", uid),
            "notes": _count(conn, "SELECT COUNT(*) FROM task_notes n WHERE memory_uid = ? AND "
                                  "NOT EXISTS (SELECT 1 FROM task_note_items i "
                                  "WHERE i.note_id = n.id)", uid),
            "comments": _count(conn, "SELECT COUNT(*) FROM task_comments "
                                     "WHERE memory_uid = ? AND item_id IS NULL", uid),
        },
    }


def _records(conn: sqlite3.Connection, uid: str, part: str, item_id: int | None) -> list[dict]:
    if part == "items":
        return [{"id": i["id"], "n": i["n"], "state": i["state"], "text": i["text"],
                 "counts": _item_counts(conn, i["id"])} for i in _items(conn, uid)]
    if part == "notes":
        return notes(conn, uid, item_id or 0)
    if part == "comments":
        return [{"id": r["id"], "item": r["item_id"], "body": r["body"], "author": r["author"],
                 "created_at": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM task_comments WHERE memory_uid = ? AND item_id IS ? "
                    "ORDER BY created_at, id", (uid, item_id))]
    return [{"uid": r["target_uid"], "type": r["type"], "title": r["title"],
             "est_tokens": connection.est_tokens(r["n"])}
            for r in conn.execute(
                """SELECT l.target_uid, m.type, m.title, LENGTH(m.content) AS n
                   FROM task_item_links l JOIN memories m ON m.uid = l.target_uid
                   WHERE l.item_id = ? ORDER BY l.created_at, l.target_uid""", (item_id,))]


def read_part(conn: sqlite3.Connection, uid: str, part: str, item: int = 0,
              offset: int = 0) -> dict:
    """One page of one collection of the task; `next_offset` is absent on the last page."""
    _require_task(conn, uid)
    if part not in PARTS:
        raise ValueError(f"{part!r} is not a part; use one of {', '.join(PARTS)}")
    item_id = require_item(conn, uid, item) if item else None
    if part == "items" and item_id:
        raise ValueError("part='items' lists every item; leave item at 0")
    if part == "links" and not item_id:
        raise ValueError("part='links' needs an item")
    records = _records(conn, uid, part, item_id)
    rows, nxt = budget.page(records, offset)
    out = {"uid": uid, "part": part, "item": item_id or 0, "total": len(records),
           "offset": offset, "records": rows}
    if nxt is not None:
        out["next_offset"] = nxt
    return out
