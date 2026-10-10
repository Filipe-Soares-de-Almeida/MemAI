"""What is still open in a scope: counts per category, and paged headers.

A listing carries headers only (no bodies), so a session can see what is
pending for the price of a title each and open what it will touch.
"""

import sqlite3

from . import budget, lite, tasks
from .store import connection, memories, search
from .store import domains as store_domains

CATEGORIES = ("task", "anti_pattern", "handoff", "note", "diagram")
# what a pin can be counted under: the categories, then the types outside them
PINNED_TYPES = CATEGORIES + ("checkpoint", "reasoning")

LIMIT_MAX = 50
_SOUND = {"anti_pattern", "handoff", "note", "checkpoint", "reasoning"}

# A path `col` that is the asked scope or one of its ancestors, compared folded
# because a path's casing is not part of its name.
_ANCHOR = ("({col} <> '' AND (lower(?) = lower({col}) "
           "OR substr(lower(?), 1, length({col}) + 1) = lower({col}) || '/'))")


def _scope(conn: sqlite3.Connection, domain: str) -> tuple[str, list]:
    """The domain clause on memories `m`, or nothing for the whole project.

    A domain that normalizes to nothing ("/") names no path, so it is the
    whole project too.
    """
    if not lite.normalize_domain(domain):
        return "", []
    clause, params, _ = store_domains.domain_scope_clause(conn, domain, alias="m")
    return clause, params


def _pin_scope(conn: sqlite3.Connection, domain: str) -> tuple[str, list]:
    """The pins in scope for `domain`: every global one, and each domain pin
    whose domain or also path is the asked domain or above it."""
    scopes = store_domains.resolve_domain_scopes(conn, domain) if lite.normalize_domain(domain) else []
    if not scopes:
        return "AND m.pin = 'global'", []
    arms, params = [], []
    for scope in scopes:
        arms.append(_ANCHOR.format(col="m.domain"))
        arms.append("EXISTS (SELECT 1 FROM memory_domains pd WHERE pd.memory_uid = m.uid "
                    f"AND {_ANCHOR.format(col='pd.domain')})")
        params.extend([scope, scope, scope, scope])
    return f"AND (m.pin = 'global' OR (m.pin = 'domain' AND ({' OR '.join(arms)})))", params


def _from_where(conn: sqlite3.Connection, domain: str, type_: str,
                pinned: bool = False) -> tuple[str, list]:
    scope, params = _pin_scope(conn, domain) if pinned else _scope(conn, domain)
    if type_ == memories.TASK_TYPE:
        return (f"FROM memories m JOIN tasks t ON t.memory_uid = m.uid "
                f"WHERE m.type = ? AND m.status = 'active' AND t.state = 'open' {scope}", [type_, *params])
    sound = search._sound_clause(type_ in _SOUND)
    return (f"FROM memories m WHERE m.type = ? AND m.status = 'active'{sound} {scope}",
            [type_, *params])


def _count(conn: sqlite3.Connection, domain: str, type_: str, pinned: bool = False) -> int:
    where, params = _from_where(conn, domain, type_, pinned)
    return conn.execute(f"SELECT COUNT(*) {where}", params).fetchone()[0]


def counts(conn: sqlite3.Connection, domain: str = "") -> list[dict]:
    """[{"type", "count"}] in CATEGORIES order, leaving out empty categories."""
    found = [{"type": t, "count": _count(conn, domain, t)} for t in CATEGORIES]
    return [c for c in found if c["count"] > 0]


def pinned_counts(conn: sqlite3.Connection, domain: str = "") -> list[dict]:
    """[{"type", "count"}] of the pins in scope, PINNED_TYPES order, empty ones left out."""
    found = [{"type": t, "count": _count(conn, domain, t, True)} for t in PINNED_TYPES]
    return [c for c in found if c["count"] > 0]


def open_task_uids(conn: sqlite3.Connection, domains: list[str]) -> list[str]:
    """The distinct uids of the open tasks inside any of `domains`.

    Each domain is resolved the way `headers` resolves it, subdomains and
    cross-listings included, and one that normalizes to nothing ("", "/") is
    skipped: the whole project is not a domain.
    """
    found: dict[str, None] = {}
    for domain in domains:
        if not lite.normalize_domain(str(domain)):
            continue
        where, params = _from_where(conn, str(domain).strip(), memories.TASK_TYPE)
        for row in conn.execute(f"SELECT m.uid {where}", params):
            found[row["uid"]] = None
    return list(found)


def _doing(conn: sqlite3.Connection, uid: str) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM task_items WHERE memory_uid = ? AND state = 'doing' "
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
    item = {"uid": row["uid"], "title": row["title"], "domain": row["domain"], "tags": row["tags"],
            "est_tokens": connection.est_tokens(len(row["content"]))}
    if row["type"] == memories.TASK_TYPE:
        item["progress"] = task_progress(conn, row["uid"])
        item["doing"] = _doing(conn, row["uid"])
    return item


def headers(conn: sqlite3.Connection, domain: str, type_: str,
            limit: int = 10, offset: int = 0, *, pinned: bool = False) -> dict:
    """One page of pending headers of a category, newest first.

    At most `limit` headers, fewer when they reach budget.PAGE_MAX_CHARS first;
    `next_offset` is present only when another page follows. A task sorts by
    its newest item update. `pinned` narrows the page to the pins in scope
    (see _pin_scope).
    """
    limit = max(1, min(int(limit), LIMIT_MAX))
    offset = max(0, int(offset))
    where, params = _from_where(conn, domain, type_, pinned)
    total = _count(conn, domain, type_, pinned)
    if type_ == memories.TASK_TYPE:
        order = ("ORDER BY COALESCE((SELECT MAX(i.updated_at) FROM task_items i "
                 "WHERE i.memory_uid = m.uid), m.created_at) DESC, m.rowid_pk DESC")
    else:
        order = "ORDER BY m.created_at DESC, m.rowid_pk DESC"
    rows = conn.execute(f"SELECT m.* {where} {order} LIMIT ? OFFSET ?",
                        [*params, limit, offset]).fetchall()
    shown, _ = budget.page([_header(conn, r) for r in rows], 0)
    result = {"type": type_, "total": total, "items": shown}
    if offset + len(shown) < total:
        result["next_offset"] = offset + len(shown)
    return result
