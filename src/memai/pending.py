"""What is still open in a scope: counts per category, and paged headers.

A listing carries headers only (no bodies), so a session can see what is
pending for the price of a title each and open what it will touch.
"""

import sqlite3

from . import db, tasks

CATEGORIES = ("task", "anti_pattern", "handoff", "note", "diagram")

LIMIT_MAX = 50
_SOUND = {"anti_pattern", "handoff", "note"}


def _scope(conn: sqlite3.Connection, domain: str) -> tuple[str, list]:
    """The domain clause on memories `m`, or nothing for the whole project.

    A domain that normalizes to nothing ("/") names no path, so it is the
    whole project too.
    """
    if not db.normalize_domain(domain):
        return "", []
    clause, params, _ = db.domain_scope_clause(conn, domain, alias="m")
    return clause, params


def _from_where(conn: sqlite3.Connection, domain: str, type_: str) -> tuple[str, list]:
    scope, params = _scope(conn, domain)
    if type_ == db.TASK_TYPE:
        return (f"FROM memories m JOIN tasks t ON t.memory_uid = m.uid "
                f"WHERE m.type = ? AND m.status = 'active' AND t.state = 'open' {scope}", [type_, *params])
    sound = db._sound_clause(type_ in _SOUND)
    return (f"FROM memories m WHERE m.type = ? AND m.status = 'active'{sound} {scope}",
            [type_, *params])


def _count(conn: sqlite3.Connection, domain: str, type_: str) -> int:
    where, params = _from_where(conn, domain, type_)
    return conn.execute(f"SELECT COUNT(*) {where}", params).fetchone()[0]


def counts(conn: sqlite3.Connection, domain: str = "") -> list[dict]:
    """[{"type", "count"}] in CATEGORIES order, leaving out empty categories."""
    found = [{"type": t, "count": _count(conn, domain, t)} for t in CATEGORIES]
    return [c for c in found if c["count"] > 0]


def open_task_uids(conn: sqlite3.Connection, domains: list[str]) -> list[str]:
    """The distinct uids of the open tasks inside any of `domains`.

    Each domain is resolved the way `headers` resolves it, subdomains and
    cross-listings included, and one that normalizes to nothing ("", "/") is
    skipped: the whole project is not a domain.
    """
    found: dict[str, None] = {}
    for domain in domains:
        if not db.normalize_domain(str(domain)):
            continue
        where, params = _from_where(conn, str(domain).strip(), db.TASK_TYPE)
        for row in conn.execute(f"SELECT m.uid {where}", params):
            found[row["uid"]] = None
    return list(found)


def _doing(conn: sqlite3.Connection, uid: str) -> list[str]:
    return [r["item_key"] for r in conn.execute(
        "SELECT item_key FROM task_items WHERE memory_uid = ? AND state = 'doing' "
        "ORDER BY seq, id", (uid,))]


def task_progress(conn: sqlite3.Connection, uid: str) -> dict | None:
    """{"done", "total"} for a task: the progress shape listings carry.

    None for a task memory that has no `tasks` row.
    """
    if task_state(conn, uid) is None:
        return None
    progress = tasks.progress(conn, uid)
    return {"done": progress["done"], "total": progress["total"]}


def task_state(conn: sqlite3.Connection, uid: str) -> str | None:
    """The task's state (open, completed or cancelled), or None without a `tasks` row."""
    row = conn.execute("SELECT state FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()
    return None if row is None else row["state"]


def _header(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = {"uid": row["uid"], "title": row["title"], "domain": row["domain"],
            "est_tokens": db.est_tokens(len(row["content"]))}
    if row["type"] == db.TASK_TYPE:
        item["progress"] = task_progress(conn, row["uid"])
        item["doing"] = _doing(conn, row["uid"])
    return item


def headers(conn: sqlite3.Connection, domain: str, type_: str,
            limit: int = 10, offset: int = 0) -> dict:
    """One page of pending headers of a category, newest first.

    `next_offset` is present only when another page follows. A task sorts by
    its newest item update.
    """
    limit = max(1, min(int(limit), LIMIT_MAX))
    offset = max(0, int(offset))
    where, params = _from_where(conn, domain, type_)
    total = _count(conn, domain, type_)
    if type_ == db.TASK_TYPE:
        order = ("ORDER BY COALESCE((SELECT MAX(i.updated_at) FROM task_items i "
                 "WHERE i.memory_uid = m.uid), m.created_at) DESC, m.rowid_pk DESC")
    else:
        order = "ORDER BY m.created_at DESC, m.rowid_pk DESC"
    rows = conn.execute(f"SELECT m.* {where} {order} LIMIT ? OFFSET ?",
                        [*params, limit, offset]).fetchall()
    result = {"type": type_, "total": total,
              "items": [_header(conn, r) for r in rows]}
    if offset + limit < total:
        result["next_offset"] = offset + limit
    return result
