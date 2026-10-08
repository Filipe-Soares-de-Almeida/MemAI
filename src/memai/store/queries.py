"""The dashboard's reads: the filtered memory list, store counts, the graph, the tree and the log."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from memai import contract
from memai.store.domains import domain_scope_clause
from memai.store.health import STALE_DAYS, today_iso
from memai.store.memories import get_memory
from memai.store.search import search_ranked

# What a memory list may be ordered by. 'recalls' (memory_usage) is a way to look, not a verdict,
# and retrieval never reads it (see the schema comment on memory_usage).
MEMORY_SORTS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "recalls": "recalls",
    "last_recall": "last_recall",
}

# The pin filter: any pin, or one kind.
PIN_FILTERS = {"any": "AND pin <> ''", "global": "AND pin = 'global'",
               "domain": "AND pin = 'domain'"}

# The uids of tasks in one state; "open" leaves out a task whose memory is archived.
_TASK_STATE_UIDS = (
    "SELECT t.memory_uid FROM tasks t JOIN memories m ON m.uid = t.memory_uid "
    "WHERE t.state = ? AND (t.state <> 'open' OR m.status = 'active')")

# The query parameter, and its value, that asks the memory list for each defect.
_DEFECT_PARAMS = {
    "unlinked": ("linked", "no"),
    "due": ("due", "1"),
    "stale": ("stale", "1"),
    "untitled": ("untitled", "1"),
    "untagged": ("untagged", "1"),
}


def _defects(today: str) -> dict[str, tuple[str, list]]:
    """Each defect as an SQL predicate over one memory row, with its params.

    A health symptom counts with these and the memory list filters with
    them, so the number on the overview and the list its button opens are
    the same rows by construction.
    """
    stale = (datetime.fromisoformat(today) - timedelta(days=STALE_DAYS)).isoformat()
    return {
        "stale": ("confidence = 'unverified' AND updated_at < ?", [stale]),
        "due": ("review_after <> '' AND review_after <= ?", [today]),
        "unlinked": ("uid NOT IN (SELECT from_uid FROM relations "
                     "UNION SELECT to_uid FROM relations)", []),
        "untitled": ("TRIM(title) = ''", []),
        # Tags empty or only the type: no synonym, so BM25 reaches the row only by its own wording.
        "untagged": ("TRIM(tags) = '' OR TRIM(tags) = type", []),
    }


def _defect_where(defects: tuple[str, ...]) -> tuple[str, list]:
    preds = _defects(today_iso())
    sql: list[str] = []
    params: list = []
    for key in defects:
        clause, values = preds[key]
        sql.append(f"AND ({clause})")
        params.extend(values)
    return " ".join(sql), params


def _int_param(qp: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    try:
        val = int(qp.get(name, default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


@dataclass(frozen=True)
class MemoryFilter:
    """Which memories a list shows, in what order and which page of them."""
    q: str = ""
    domain: str = ""
    subtree: bool = True
    type: str = ""
    status: str = ""
    confidence: str = ""
    session: str = ""
    defects: tuple[str, ...] = ()
    task_state: str = ""
    pin: str = ""
    sort: str = "created_at"
    descending: bool = True
    limit: int = 50
    offset: int = 0

    @classmethod
    def from_query_params(cls, qp: Mapping[str, str]) -> MemoryFilter:
        """The filter a dashboard query string states. Raises ValueError for a
        task_state or pin it does not know; an unknown sort falls back."""
        task_state = qp.get("task_state", "")
        if task_state and task_state not in contract.TASK_STATES:
            raise ValueError(f"task_state must be one of {', '.join(contract.TASK_STATES)}")
        pin = qp.get("pin", "")
        if pin and pin not in PIN_FILTERS:
            raise ValueError(f"pin must be one of {', '.join(PIN_FILTERS)}")
        sort = qp.get("sort", "created_at")
        return cls(
            q=qp.get("q", "").strip(),
            domain=qp.get("domain", ""),
            # a domain is a scope, and picking 'acme/x100' to see none of its routines would read
            # as an empty module; `subtree=0` narrows to the exact path
            subtree=qp.get("subtree", "1").lower() not in ("0", "false", "no"),
            type=qp.get("type", ""),
            status=qp.get("status", ""),
            confidence=qp.get("confidence", ""),
            session=qp.get("session", ""),
            defects=tuple(k for k, (name, on) in _DEFECT_PARAMS.items() if qp.get(name) == on),
            task_state=task_state,
            pin=pin,
            sort=sort if sort in MEMORY_SORTS else "created_at",
            descending=qp.get("dir", "desc").lower() != "asc",
            # a page can be as long as one bulk call takes, so "every row this filter matches"
            # is a single request
            limit=_int_param(qp, "limit", 50, 1, contract.BULK_MAX),
            offset=_int_param(qp, "offset", 0, 0, 1_000_000),
        )


def _search_page(conn: sqlite3.Connection, f: MemoryFilter) -> tuple[int, list]:
    hits = search_ranked(conn, f.q, domain=f.domain, type=f.type, status=f.status,
                         limit=200, subtree=f.subtree)
    if f.confidence:
        hits = [h for h in hits if h["confidence"] == f.confidence]
    if f.session:
        hits = [h for h in hits if h["session"] == f.session]
    if f.defects:
        sql, params = _defect_where(f.defects)
        keep = {r["uid"] for r in conn.execute(
            "SELECT uid FROM memories WHERE 1=1 " + sql, params)}
        hits = [h for h in hits if h["uid"] in keep]
    if f.task_state:
        keep = {r[0] for r in conn.execute(_TASK_STATE_UIDS, (f.task_state,))}
        hits = [h for h in hits if h["uid"] in keep]
    if f.pin:
        keep = {r[0] for r in conn.execute(
            "SELECT uid FROM memories WHERE 1=1 " + PIN_FILTERS[f.pin])}
        hits = [h for h in hits if h["uid"] in keep]
    # A pasted uid matches only [[uid]] references in other bodies, so the named row is
    # pinned above its referrers, past every filter, as the link picker does.
    exact = get_memory(conn, f.q)
    if exact is not None:
        pinned = dict(exact)
        # the one row in the list that did not match a word
        pinned["match_source"] = "uid"
        hits = [pinned] + [h for h in hits if h["uid"] != f.q]
    return len(hits), hits[f.offset:f.offset + f.limit]


def _filtered_page(conn: sqlite3.Connection, f: MemoryFilter) -> tuple[int, list]:
    where, params = ["1=1"], []
    if f.domain:
        clause, values, _ = domain_scope_clause(conn, f.domain, alias="", subtree=f.subtree)
        where.append(clause)
        params.extend(values)
    for column, value in (("type", f.type), ("status", f.status),
                          ("confidence", f.confidence), ("session", f.session)):
        if value:
            where.append(f"AND {column} = ?")
            params.append(value)
    if f.task_state:
        where.append(f"AND uid IN ({_TASK_STATE_UIDS})")
        params.append(f.task_state)
    if f.pin:
        where.append(PIN_FILTERS[f.pin])
    defects, defect_params = _defect_where(f.defects)
    if defects:
        where.append(defects)
    params.extend(defect_params)
    clause = " ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {clause}", params).fetchone()[0]
    # The join makes 'recalls' sortable; memory_usage shares no column with the filters
    # above, and NULL sorts as never-recalled.
    direction = "DESC" if f.descending else "ASC"
    rows = conn.execute(
        f"""SELECT m.*, COALESCE(u.recall_count, 0) AS recalls,
                   u.last_recalled_at AS last_recall
            FROM memories m LEFT JOIN memory_usage u ON u.memory_uid = m.uid
            WHERE {clause} ORDER BY {MEMORY_SORTS[f.sort]} {direction} LIMIT ? OFFSET ?""",
        [*params, f.limit, f.offset]).fetchall()
    return total, rows


def list_memories(conn: sqlite3.Connection, f: MemoryFilter) -> tuple[int, list]:
    """How many memories the filter selects, and its page of them.

    With `q` the page is search hits in rank order, as dicts without recall
    counts, and a memory whose uid is `q` comes first past every filter.
    Without it the page is rows carrying `recalls` and `last_recall`, in the
    filter's order.
    """
    return _search_page(conn, f) if f.q else _filtered_page(conn, f)


def task_progress(conn: sqlite3.Connection, uids: list[str]) -> dict[str, tuple[str, int, int]]:
    """For each of `uids` that is a task: its state, its items done, and its items in all."""
    if not uids:
        return {}
    marks = ",".join("?" * len(uids))
    states = dict(conn.execute(
        f"SELECT memory_uid, state FROM tasks WHERE memory_uid IN ({marks})", uids))
    counts = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"""SELECT memory_uid, SUM(state = 'done'), COUNT(*) FROM task_items
            WHERE memory_uid IN ({marks}) GROUP BY memory_uid""", uids)}
    return {uid: (state, *counts.get(uid, (0, 0))) for uid, state in states.items()}


_CONTRADICTED = "confidence = 'contradicted' AND (superseded_by IS NULL OR superseded_by = '')"


def count_active(conn: sqlite3.Connection, defect: str = "") -> int:
    """Active memories, or the active ones carrying one defect."""
    if not defect:
        return conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
    clause, params = _defects(today_iso())[defect]
    return conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE status = 'active' AND ({clause})",
        params).fetchone()[0]


def symptom_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Active memories per health symptom: 'contradicted', then each defect."""
    preds = {"contradicted": (_CONTRADICTED, []), **_defects(today_iso())}
    return {
        key: conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE status = 'active' AND ({clause})",
            params).fetchone()[0]
        for key, (clause, params) in preds.items()
    }


def overview_counts(conn: sqlite3.Connection) -> dict:
    """The store-wide figures of the overview: totals, splits, daily activity and open tasks."""
    activity = [
        {"day": r[0], "count": r[1]}
        for r in reversed(conn.execute(
            """SELECT substr(created_at, 1, 10) AS day, COUNT(*)
               FROM memories GROUP BY day ORDER BY day DESC LIMIT 45""").fetchall())
    ]
    # Confidence within each type: where the vetting is behind, which
    # the store-wide split cannot say.
    by_type_conf: dict[str, dict[str, int]] = {}
    for tp, conf, n in conn.execute(
            """SELECT type, confidence, COUNT(*) FROM memories
               WHERE status = 'active' GROUP BY type, confidence"""):
        by_type_conf.setdefault(tp, {})[conf] = n
    return {
        "total": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "by_status": dict(conn.execute(
            "SELECT status, COUNT(*) FROM memories GROUP BY status").fetchall()),
        "by_type": dict(conn.execute(
            "SELECT type, COUNT(*) FROM memories WHERE status='active' GROUP BY type").fetchall()),
        "by_confidence": dict(conn.execute(
            "SELECT confidence, COUNT(*) FROM memories WHERE status='active' "
            "GROUP BY confidence").fetchall()),
        "relations": conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
        "edits": conn.execute("SELECT COUNT(*) FROM edits").fetchone()[0],
        "sessions": conn.execute(
            "SELECT COUNT(DISTINCT session) FROM memories WHERE session <> ''").fetchone()[0],
        "activity": activity,
        "by_type_confidence": by_type_conf,
        "open_tasks": conn.execute(
            "SELECT COUNT(*) FROM tasks t JOIN memories m ON m.uid = t.memory_uid "
            "WHERE t.state = 'open' AND m.status = 'active'").fetchone()[0],
    }


def graph_rows(
    conn: sqlite3.Connection, *, domain: str, subtree: bool, status: str, type: str, limit: int,
) -> tuple[int, list[sqlite3.Row], list[dict]]:
    """A graph scope: how many memories it holds, its rows most-connected first and cut at
    `limit` (0 keeps all), and the edges whose both ends are among those rows."""
    where, params = ["1=1"], []
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        where.append(clause)
        params.extend(values)
    for column, value in (("status", status), ("type", type)):
        if value:
            where.append(f"AND {column} = ?")
            params.append(value)
    total = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {' '.join(where)}", params).fetchone()[0]
    # `deg` orders the cut; the degree a view reports counts only edges between included
    # nodes, so the legend matches the drawing.
    rows = conn.execute(
        f"SELECT uid, type, domain, also_domains, status, confidence, title, content, "
        f"       tags, created_at, (SELECT COUNT(*) FROM relations r "
        f"        WHERE r.from_uid = memories.uid OR r.to_uid = memories.uid) AS deg "
        f"FROM memories WHERE {' '.join(where)} "
        f"ORDER BY deg DESC, created_at DESC" + (" LIMIT ?" if limit else ""),
        [*params, limit] if limit else params).fetchall()
    uids = {r["uid"] for r in rows}
    edges = [
        dict(r) for r in conn.execute(
            "SELECT id, from_uid, to_uid, relation_type, note FROM relations").fetchall()
        if r["from_uid"] in uids and r["to_uid"] in uids
    ]
    return total, rows, edges


def domain_tree_counts(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Counts behind the domain tree: per filed path, status and type (`n` and the newest
    `latest`), then per cross-listed path (`n` and `latest`)."""
    filed = conn.execute(
        """SELECT domain, status, type, COUNT(*) AS n, MAX(created_at) AS latest
           FROM memories WHERE domain <> ''
           GROUP BY domain, status, type""").fetchall()
    crossing = conn.execute(
        """SELECT dl.domain AS domain, COUNT(*) AS n, MAX(m.created_at) AS latest
           FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid
           WHERE dl.domain <> '' GROUP BY dl.domain""").fetchall()
    return filed, crossing


def domain_preview(
    conn: sqlite3.Connection, domain: str, limit: int,
) -> tuple[list[sqlite3.Row], int, list[sqlite3.Row]]:
    """The newest active memories filed at exactly `domain`, how many are filed there, and the
    newest active ones cross-listed there."""
    filed = conn.execute(
        """SELECT * FROM memories WHERE domain = ? AND status = 'active'
           ORDER BY created_at DESC LIMIT ?""", (domain, limit)).fetchall()
    filed_total = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE domain = ? AND status = 'active'",
        (domain,)).fetchone()[0]
    crossing = conn.execute(
        """SELECT m.* FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid
           WHERE dl.domain = ? AND m.status = 'active'
           ORDER BY m.created_at DESC LIMIT ?""", (domain, limit)).fetchall()
    return filed, filed_total, crossing


def domain_spellings(conn: sqlite3.Connection) -> dict[str, int]:
    """Every domain string as written, filed or cross-listed, and how many rows carry it."""
    counts: dict[str, int] = {}
    for sql in (
        "SELECT domain, COUNT(*) AS n FROM memories WHERE domain <> '' GROUP BY domain",
        "SELECT domain, COUNT(*) AS n FROM memory_domains WHERE domain <> '' GROUP BY domain",
    ):
        for r in conn.execute(sql):
            counts[r["domain"]] = counts.get(r["domain"], 0) + r["n"]
    return counts


def edit_log(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """The newest entries of the edit history, each with the memory it belongs to."""
    rows = conn.execute(
        """SELECT e.id, e.memory_uid, e.edited_at, e.note,
                  LENGTH(e.prev_content) AS prev_len, LENGTH(e.new_content) AS new_len,
                  (e.prev_content <> e.new_content) AS content_changed,
                  m.type, m.domain, m.status
           FROM edits e JOIN memories m ON m.uid = e.memory_uid
           ORDER BY e.edited_at DESC, e.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
