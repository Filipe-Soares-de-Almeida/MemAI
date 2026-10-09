"""Task checklists: a `task` memory that owns items, links and comments.

A task is a memories row of type 'task' whose content is generated from its
goal and items, the way a diagram's content is generated from its graph.
"""

import re
import sqlite3

from . import budget, lite, sections
from .contract import GOAL_MAX, ITEM_MAX, ITEM_STATES, ITEMS_MAX, NOTE_MAX, TASK_STATES
from .store import connection, memories, task_items
from .store import sections as store_sections

COMMENT_MAX = 2000

_KEY_RE = re.compile(r"^i?([1-9][0-9]*)$")
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


def item_key(value: str) -> str:
    """Normalize "3", "i3" or " I3 " to "i3"; anything else is a ValueError."""
    match = _KEY_RE.match(str(value).strip().lower())
    if match is None:
        raise ValueError(f"{value!r} is not an item key; use a number such as 3 or i3")
    return f"i{match.group(1)}"


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
    rows = [
        {"key": f"i{n}", "seq": n, "text": text, "state": "todo"}
        for n, text in enumerate(items, start=1)
    ]
    uid = memories.insert_memory(
        conn, type=memories.TASK_TYPE, content=task_items.render(goal, rows), title=title,
        domain=domain, also=also, session=session, tags=tags,
    )
    conn.execute("INSERT INTO tasks (memory_uid, goal) VALUES (?, ?)", (uid, goal))
    stamp = lite.now_iso()
    conn.executemany(
        """INSERT INTO task_items
           (memory_uid, item_key, seq, text, state, updated_at, updated_session)
           VALUES (?, ?, ?, ?, 'todo', ?, ?)""",
        [(uid, r["key"], r["seq"], r["text"], stamp, session) for r in rows],
    )
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
    links: dict[str, list[dict]] = {}
    for r in conn.execute(
        """SELECT l.item_key, l.target_uid, m.title, m.type
           FROM task_item_links l JOIN memories m ON m.uid = l.target_uid
           WHERE l.memory_uid = ? ORDER BY l.created_at, l.target_uid""",
        (uid,),
    ):
        links.setdefault(r["item_key"], []).append(
            {"uid": r["target_uid"], "title": r["title"], "type": r["type"]}
        )
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
    keys = _note_item_map(conn, uid)
    notes = [_note_dict(r, keys) for r in conn.execute(
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
            (state, lite.now_iso(), session, uid, key),
        )
        _regenerate(conn, uid, f"item {key}: {old} -> {state}", record_edit=False)
        _settle(conn, uid)
    return {"uid": uid, "item": key, "state": state, "changed": changed, **_outcome(conn, uid)}


def add_items(conn: sqlite3.Connection, uid: str, items: list[str], *, session: str = "") -> dict:
    """Append items under the next positions; a closed task reopens."""
    items = [str(i).strip() for i in items]
    if not items or not all(items):
        raise ValueError("add at least one item, and no item may be empty")
    for text in items:
        if error := _item_length_error(text):
            raise ValueError(error)
    _lock(conn, uid)
    last = len(_items(conn, uid))
    if last + len(items) > ITEMS_MAX:
        raise ValueError(f"a task holds at most {ITEMS_MAX} items; it has {last}")
    _require_no_cited_key({f"item {last + n}": t for n, t in enumerate(items, start=1)},
                          last + len(items))
    stamp = lite.now_iso()
    keys = [f"i{n}" for n in range(last + 1, last + len(items) + 1)]
    conn.executemany(
        """INSERT INTO task_items
           (memory_uid, item_key, seq, text, state, updated_at, updated_session)
           VALUES (?, ?, ?, ?, 'todo', ?, ?)""",
        [(uid, k, last + n, t, stamp, session) for n, (k, t) in enumerate(zip(keys, items, strict=True), start=1)],
    )
    note = f"item {keys[0]} added" if len(keys) == 1 else f"items {', '.join(keys)} added"
    _regenerate(conn, uid, note, record_edit=False)
    _settle(conn, uid)
    return {"uid": uid, "keys": keys, **_outcome(conn, uid)}


def delete_item(
    conn: sqlite3.Connection, uid: str, item: str, *, expect: str | None = None, session: str = "",
) -> dict:
    """Remove one item with its comments and links, renumber the items after it, then settle an open task.

    Every item after it moves up one key, and its links, comments and note
    attachments move with it; `renumbered` maps each old key to its new one.
    The deletion enters the edit history. A closed task keeps its state.
    Raises ValueError for a non-task, an unknown item, the task's only item,
    or an item whose text differs from `expect` (the text the caller saw,
    since a key names whichever item holds that position now), before
    anything is written. The deletion adds no text, so it is not held to
    the leak check.

    `session` is accepted for signature parity with the other writers; the
    item it would stamp is the one removed, so nothing records it.
    """
    _lock(conn, uid)
    key = _require_item(conn, uid, item)
    rows = _items(conn, uid)
    if len(rows) == 1:
        raise ValueError("a task keeps at least one item")
    gone = next(r for r in rows if r["key"] == key)
    if expect is not None and gone["text"] != expect:
        raise ValueError(f"item {key} is no longer {expect!r}; reload the task before deleting")
    conn.execute(
        "DELETE FROM task_note_items WHERE item_key = ? AND note_id IN "
        "(SELECT id FROM task_notes WHERE memory_uid = ?)", (key, uid))
    for table in ("task_comments", "task_item_links"):
        conn.execute(f"DELETE FROM {table} WHERE memory_uid = ? AND item_key = ?", (uid, key))
    conn.execute("DELETE FROM task_items WHERE memory_uid = ? AND item_key = ?", (uid, key))
    moved = task_items.compact(conn, uid, gone=(key, gone["text"]))
    note = f"item {key} deleted: {gone['text']}"
    if moved:
        note += f"; {_span(list(moved))} renumbered to {_span(list(moved.values()))}"
    _regenerate(conn, uid, note, record_edit=True, leaked_ok=True)
    state = conn.execute("SELECT state FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()["state"]
    if state == "open":
        _settle(conn, uid)
    return {"uid": uid, "item": key, "renumbered": moved, **_outcome(conn, uid)}


def _span(keys: list[str]) -> str:
    return keys[0] if len(keys) == 1 else f"{keys[0]}..{keys[-1]}"


def set_goal(conn: sqlite3.Connection, uid: str, goal: str) -> None:
    """Replace the goal; the content is regenerated when the goal changed."""
    goal = str(goal).strip()
    if not goal:
        raise ValueError("a task needs a goal")
    if len(goal) > GOAL_MAX:
        raise ValueError(f"goal is {len(goal)} characters; the limit is {GOAL_MAX}")
    _lock(conn, uid)
    _require_no_cited_key({"goal": goal}, len(_items(conn, uid)))
    conn.execute("UPDATE tasks SET goal = ? WHERE memory_uid = ?", (goal, uid))
    _regenerate(conn, uid, "goal edited", record_edit=True)


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
    store_sections._refuse_leak(memories.TASK_TYPE, body)
    if author not in ("agent", "person"):
        raise ValueError(f"{author!r} is not a comment author; use agent or person")
    _lock(conn, uid)
    key = _require_item(conn, uid, item) if str(item).strip() else ""
    _require_no_cited_key({"comment": body}, len(_items(conn, uid)))
    cur = conn.execute(
        """INSERT INTO task_comments (memory_uid, item_key, body, author, session, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, key, body, author, session, lite.now_iso()),
    )
    return cur.lastrowid or 0


def link_item(conn: sqlite3.Connection, uid: str, item: str, targets: list[str]) -> list[str]:
    """Link memories to an item; returns the ones newly linked.

    An unknown target raises ValueError and links nothing.
    """
    _lock(conn, uid)
    key = _require_item(conn, uid, item)
    wanted = list(dict.fromkeys(str(t).strip() for t in targets))
    for target in wanted:
        if memories.get_memory(conn, target) is None:
            raise ValueError(f"no memory {target}")
    linked = []
    for target in wanted:
        cur = conn.execute(
            """INSERT OR IGNORE INTO task_item_links (memory_uid, item_key, target_uid, created_at)
               VALUES (?, ?, ?, ?)""",
            (uid, key, target, lite.now_iso()),
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


DEPENDS_FORMAT = "DEPENDS ON is none, or item keys with an optional reason in parentheses: i3 (why), i9"


def depends_error(conn: sqlite3.Connection, uid: str, body: str, applies_to: list[str], *,
                  overlap_only: bool = False) -> str | None:
    """The problems with the brief's DEPENDS ON, or None when it reads, names only items of the task, and none the note applies to.

    overlap_only checks just the last of those, and only for a field that reads.
    """
    entries, problems = sections.parse_depends((brief_fields(body) or {}).get("depends_on", "none"))
    if overlap_only:
        problems = []
    known = {i["key"] for i in _items(conn, uid)}
    for e in entries:
        if e.item and e.item not in known and not overlap_only:
            problems.append(f"{e.item} is not an item of this task")
        elif e.item in applies_to:
            problems.append(f"{e.item} is an item this note applies to")
    return "; ".join(problems) or None


def _require_depends(conn: sqlite3.Connection, uid: str, body: str, applies_to: list[str], *,
                     overlap_only: bool = False) -> None:
    error = depends_error(conn, uid, body, applies_to, overlap_only=overlap_only)
    if error:
        raise ValueError(f"{DEPENDS_FORMAT} — {error}")


def brief_depends(body: str) -> list[sections.DependsEntry] | None:
    """The entries of a brief's DEPENDS ON, or None when `body` is no brief or the field does not read."""
    fields = brief_fields(body)
    if fields is None:
        return None
    entries, problems = sections.parse_depends(fields["depends_on"])
    return None if problems else entries


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


def _note_keys(conn: sqlite3.Connection, uid: str, items: list[str]) -> list[str]:
    return list(dict.fromkeys(_require_item(conn, uid, i) for i in items if str(i).strip()))


def _require_note(conn: sqlite3.Connection, uid: str, note_id: int) -> int:
    found = conn.execute(
        "SELECT id FROM task_notes WHERE id = ? AND memory_uid = ?", (int(note_id), uid)
    ).fetchone()
    if found is None:
        raise ValueError(f"task {uid} has no note {note_id}")
    return found["id"]


def _set_note_items(conn: sqlite3.Connection, note_id: int, keys: list[str]) -> None:
    conn.execute("DELETE FROM task_note_items WHERE note_id = ?", (note_id,))
    conn.executemany("INSERT INTO task_note_items (note_id, item_key) VALUES (?, ?)",
                     [(note_id, k) for k in keys])


def _note_item_map(conn: sqlite3.Connection, uid: str) -> dict[int, list[str]]:
    """Each note's item keys, in item order."""
    keys: dict[int, list[str]] = {}
    for r in conn.execute(
        """SELECT i.note_id, i.item_key FROM task_note_items i
           JOIN task_notes n ON n.id = i.note_id
           JOIN task_items t ON t.memory_uid = n.memory_uid AND t.item_key = i.item_key
           WHERE n.memory_uid = ? ORDER BY t.seq""", (uid,)):
        keys.setdefault(r["note_id"], []).append(r["item_key"])
    return keys


def _note_dict(row: sqlite3.Row, keys: dict[int, list[str]]) -> dict:
    return {"id": row["id"], "title": row["title"], "body": row["body"],
            "items": keys.get(row["id"], []), "updated_at": row["updated_at"]}


def add_note(conn: sqlite3.Connection, uid: str, *, title: str, body: str = "",
             items: list[str], brief: dict[str, str] | None = None, session: str = "") -> int:
    """A note owned by the task, on `items` or, with none, on the task as a whole; returns its id.

    A note on items is a brief, given as `body` or built from `brief` fields.
    """
    on_items = any(str(i).strip() for i in items)
    title, body = _note_fields(title, _compose(body, brief, "", on_items=on_items))
    _lock(conn, uid)
    _require_no_cited_key({"title": title, **free_text(body)}, len(_items(conn, uid)))
    keys = _note_keys(conn, uid, items)
    if keys:
        _require_brief(body)
    if keys or _has_fields(brief):
        _require_depends(conn, uid, body, keys)
    stamp = lite.now_iso()
    cur = conn.execute(
        """INSERT INTO task_notes (memory_uid, title, body, session, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""", (uid, title, body, session, stamp, stamp))
    note_id = cur.lastrowid or 0
    _set_note_items(conn, note_id, keys)
    return note_id


def edit_note(conn: sqlite3.Connection, uid: str, note_id: int, *, title: str = "",
              body: str = "", items: list[str] | None = None,
              brief: dict[str, str] | None = None) -> None:
    """Overwrite a note; an empty title or body keeps the stored one, items=None keeps the set.

    `brief` fields replace only their own in a stored brief. A note that ends
    up on items must be a brief when the write gives it a body or moves it
    there from the whole task; a title or scope edit of a stored note on
    items leaves its body as it is.
    """
    _lock(conn, uid)
    note_id = _require_note(conn, uid, note_id)
    _require_no_cited_key(_given_free_text(title, body, brief), len(_items(conn, uid)))
    row = conn.execute("SELECT title, body FROM task_notes WHERE id = ?", (note_id,)).fetchone()
    had = conn.execute("SELECT 1 FROM task_note_items WHERE note_id = ?", (note_id,)).fetchone() is not None
    keys = None if items is None else _note_keys(conn, uid, items)
    on_items = bool(keys) if keys is not None else had
    given = _compose(body, brief, row["body"], on_items=on_items)
    title, body = _note_fields(title or row["title"], given or row["body"])
    if on_items and (given or not had):
        _require_brief(body)
    if (on_items and (given or not had)) or _has_fields(brief):
        applies_to = keys if keys is not None else [r["item_key"] for r in conn.execute(
            "SELECT item_key FROM task_note_items WHERE note_id = ?", (note_id,))]
        _require_depends(conn, uid, body, applies_to)
    elif keys and had:
        _require_depends(conn, uid, body, keys, overlap_only=True)
    conn.execute("UPDATE task_notes SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                 (title, body, lite.now_iso(), note_id))
    if keys is not None:
        _set_note_items(conn, note_id, keys)


def delete_note(conn: sqlite3.Connection, uid: str, note_id: int) -> None:
    _lock(conn, uid)
    note_id = _require_note(conn, uid, note_id)
    conn.execute("DELETE FROM task_note_items WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM task_notes WHERE id = ?", (note_id,))


def note(conn: sqlite3.Connection, uid: str, note_id: int) -> dict:
    """One note of the task, shaped like a notes() entry."""
    note_id = _require_note(conn, uid, note_id)
    row = conn.execute("SELECT * FROM task_notes WHERE id = ?", (note_id,)).fetchone()
    return _note_dict(row, _note_item_map(conn, uid))


def notes(conn: sqlite3.Connection, uid: str, item: str = "") -> list[dict]:
    """The task-level notes, or with `item` the notes on that item, oldest first."""
    if str(item).strip():
        key = _require_item(conn, uid, item)
        rows = conn.execute(
            """SELECT n.* FROM task_notes n JOIN task_note_items i ON i.note_id = n.id
               WHERE n.memory_uid = ? AND i.item_key = ? ORDER BY n.created_at, n.id""",
            (uid, key)).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM task_notes n WHERE memory_uid = ? AND NOT EXISTS
               (SELECT 1 FROM task_note_items i WHERE i.note_id = n.id)
               ORDER BY created_at, id""", (uid,)).fetchall()
    keys = _note_item_map(conn, uid)
    return [_note_dict(r, keys) for r in rows]


def restore_task(conn: sqlite3.Connection, record: dict) -> None:
    """Write a task's rows from an export record, under an already-restored memory.

    Skips a link whose target is not in the store and writes no edit. A state
    outside TASK_STATES or ITEM_STATES, or an item_seq that is not a
    non-negative integer, is a ValueError before any row is written. Items whose
    keys are not their positions are renumbered after the rows are written.
    """
    uid = str(record["uid"])
    state = record.get("state", "open")
    if state not in TASK_STATES:
        raise ValueError(f"{state!r} is not a task state; use {', '.join(TASK_STATES)}")
    for i in record.get("items") or []:
        if i.get("state", "todo") not in ITEM_STATES:
            raise ValueError(
                f"{i.get('state')!r} is not an item state; use {', '.join(ITEM_STATES)}")
    mark = record.get("item_seq", 0)
    if isinstance(mark, bool) or not isinstance(mark, int) or mark < 0:
        raise ValueError(f"{mark!r} is not an item_seq; use a non-negative integer")
    conn.execute(
        "INSERT INTO tasks (memory_uid, goal, state, completed_at, item_seq) VALUES (?, ?, ?, ?, ?)",
        (uid, record.get("goal", ""), state, record.get("completed_at", ""), mark),
    )
    conn.executemany(
        """INSERT INTO task_items
           (memory_uid, item_key, seq, text, state, updated_at, updated_session)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (uid, i["key"], i["seq"], i["text"], i.get("state", "todo"),
             i.get("updated_at") or lite.now_iso(), i.get("updated_session", ""))
            for i in record.get("items") or []
        ],
    )
    conn.executemany(
        """INSERT OR IGNORE INTO task_item_links (memory_uid, item_key, target_uid, created_at)
           VALUES (?, ?, ?, ?)""",
        [
            (uid, link["item_key"], link["target_uid"], link.get("created_at") or lite.now_iso())
            for link in record.get("links") or []
            if memories.get_memory(conn, link["target_uid"]) is not None
        ],
    )
    conn.executemany(
        """INSERT INTO task_comments (memory_uid, item_key, body, author, session, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (uid, c.get("item_key", ""), c["body"], c.get("author", "agent"),
             c.get("session", ""), c.get("created_at") or lite.now_iso())
            for c in record.get("comments") or []
        ],
    )
    known = {i["key"] for i in record.get("items") or []}
    for n in record.get("notes") or []:
        cur = conn.execute(
            """INSERT INTO task_notes (memory_uid, title, body, session, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, n["title"], n["body"], n.get("session", ""),
             n.get("created_at") or lite.now_iso(), n.get("updated_at") or lite.now_iso()))
        _set_note_items(conn, cur.lastrowid or 0, [k for k in n.get("items") or [] if k in known])
    if task_items.compact(conn, uid):
        task_items.regenerate(conn, uid, touch=False)


PARTS = ("items", "notes", "comments", "links")


def _require_task(conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()
    if row is None:
        raise ValueError(f"no task {uid}")
    return row


def _count(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def _item_counts(conn: sqlite3.Connection, uid: str, key: str) -> dict:
    return {
        "notes": _count(conn, "SELECT COUNT(*) FROM task_note_items i JOIN task_notes n "
                              "ON n.id = i.note_id WHERE n.memory_uid = ? AND i.item_key = ?",
                        uid, key),
        "comments": _count(conn, "SELECT COUNT(*) FROM task_comments "
                                 "WHERE memory_uid = ? AND item_key = ?", uid, key),
        "links": _count(conn, "SELECT COUNT(*) FROM task_item_links "
                              "WHERE memory_uid = ? AND item_key = ?", uid, key),
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
                                     "WHERE memory_uid = ? AND item_key = ''", uid),
        },
    }


def _records(conn: sqlite3.Connection, uid: str, part: str, key: str) -> list[dict]:
    if part == "items":
        return [{"key": i["key"], "state": i["state"], "text": i["text"],
                 "counts": _item_counts(conn, uid, i["key"])} for i in _items(conn, uid)]
    if part == "notes":
        return notes(conn, uid, key)
    if part == "comments":
        return [{"id": r["id"], "item": r["item_key"], "body": r["body"], "author": r["author"],
                 "created_at": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM task_comments WHERE memory_uid = ? AND item_key = ? "
                    "ORDER BY created_at, id", (uid, key))]
    return [{"uid": r["target_uid"], "type": r["type"], "title": r["title"],
             "est_tokens": connection.est_tokens(r["n"])}
            for r in conn.execute(
                """SELECT l.target_uid, m.type, m.title, LENGTH(m.content) AS n
                   FROM task_item_links l JOIN memories m ON m.uid = l.target_uid
                   WHERE l.memory_uid = ? AND l.item_key = ?
                   ORDER BY l.created_at, l.target_uid""", (uid, key))]


def read_part(conn: sqlite3.Connection, uid: str, part: str, item: str = "",
              offset: int = 0) -> dict:
    """One page of one collection of the task; `next_offset` is absent on the last page."""
    _require_task(conn, uid)
    if part not in PARTS:
        raise ValueError(f"{part!r} is not a part; use one of {', '.join(PARTS)}")
    key = _require_item(conn, uid, item) if str(item).strip() else ""
    if part == "items" and key:
        raise ValueError("part='items' lists every item; leave item empty")
    if part == "links" and not key:
        raise ValueError("part='links' needs an item")
    records = _records(conn, uid, part, key)
    rows, nxt = budget.page(records, offset)
    out = {"uid": uid, "part": part, "item": key, "total": len(records),
           "offset": offset, "records": rows}
    if nxt is not None:
        out["next_offset"] = nxt
    return out
