"""Retrieval: BM25 search and its ranking, and the by-domain, recent, timeline and overdue lists."""

from __future__ import annotations

import re
import sqlite3

from memai.store.connection import _FTS_COLUMNS
from memai.store.dedup import _pair_ratio, _Prepared
from memai.store.domains import _like_needle, domain_scope_clause
from memai.store.health import _due_clause
from memai.store.memories import CONFIDENCE_CONTRADICTED, get_memory

# BM25 weights in _FTS_COLUMNS order, keyed by name so an unweighted new column fails at import.
# Paths stay findable but never outrank a memory about the subject; a title outranks the body.
_FTS_WEIGHTS = {"title": 1.5, "content": 1.0, "tags": 0.8,
                "domain": 0.3, "also_domains": 0.3}
_BM25 = f"bm25(memories_fts, {', '.join(str(_FTS_WEIGHTS[c]) for c in _FTS_COLUMNS)})"


def _fts_query(raw: str) -> str:
    """Turn free-text/multi-term input into an FTS5 OR query across terms.

    Lets the calling agent pass several paraphrases in one call
    ("reranking teacher model" or "best of n dpo critic") and get the union
    of matches back, instead of one narrow AND match. BM25 scores a row
    matching every term far above a row matching one, so the union does not
    cost the precise query its rank.

    Recall rises with the number of terms and barely moves with their
    phrasing, which is why search() tells callers to spend terms rather than
    to word the query carefully. tools/bench-retrieval.py measures both.
    """
    terms = [t.strip() for t in raw.replace(" OR ", " ").split() if t.strip()]
    if not terms:
        return raw
    # EVERY term is quoted: bare 'AND', 'NOT' and 'NEAR' are fts5 operators and raise an
    # OperationalError; a quoted single token matches what the bare one would.
    escaped = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(escaped)


def _tag_clause(tag: str, alias: str = "m") -> tuple[str, str]:
    """SQL fragment + bound value for "this row carries this tag".

    `tags` is one comma-separated string, so a bare LIKE '%flag%' also
    matches 'flagged' and 'feature-flag'. Padding both the column and the
    needle with commas makes the boundaries explicit.

    Two details the shape forces. Spaces are stripped from both sides
    because the column is hand-written and 'a, b' is as common as 'a,b';
    the cost is that a tag with an interior space matches without it,
    which is a trade the alternative (a tags table) is not worth here.
    And % _ \\ are escaped, because a tag like 'anti_pattern' would
    otherwise be a LIKE pattern matching 'anti-pattern' too.
    """
    col = f"{alias}.tags" if alias else "tags"
    needle = _like_needle(tag.replace(" ", ""))
    return (
        f"AND (',' || REPLACE({col}, ' ', '') || ',') LIKE ('%,' || ? || ',%') ESCAPE '\\'",
        needle,
    )


def search_memories(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    tag: str = "",
    status: str = "active",
    limit: int = 30,
    subtree: bool = True,
) -> list[sqlite3.Row]:
    sql = [
        f"""SELECT m.*, {_BM25} AS rank
           FROM memories_fts
           JOIN memories m ON m.rowid_pk = memories_fts.rowid
           WHERE memories_fts MATCH ?"""
    ]
    params: list = [_fts_query(query)]
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, subtree=subtree)
        sql.append(clause)
        params.extend(values)
    if type:
        sql.append("AND m.type = ?")
        params.append(type)
    if tag:
        clause, needle = _tag_clause(tag)
        sql.append(clause)
        params.append(needle)
    if status:
        sql.append("AND m.status = ?")
        params.append(status)
    sql.append("ORDER BY rank, m.rowid_pk LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


# A query that is only an opaque token (uid, sha, hex) names a row the index cannot match. Twelve
# is the floor: a uid is 16, a short sha 7-12, and fewer matches words like 'facade'.
_OPAQUE_QUERY = re.compile(r"[0-9a-f]{12,}", re.IGNORECASE)


def is_opaque_query(raw: str) -> bool:
    """True when the whole query is one opaque identifier, nothing else.

    Only the whole query counts: a uid inside a sentence leaves the rest of
    the words to search on, and that search is worth running.
    """
    q = raw.strip()
    return bool(q) and _OPAQUE_QUERY.fullmatch(q) is not None


def search_ranked(
    conn: sqlite3.Connection,
    query: str,
    *,
    domain: str = "",
    type: str = "",
    tag: str = "",
    status: str = "active",
    limit: int = 30,
    subtree: bool = True,
    collapse: bool = False,
) -> list[dict]:
    """FTS5 BM25 over content, tags and domain paths, ranked.

    Each result dict carries `match_source` ("fts", or "uid" for the row a
    pasted identifier names) and `fts_rank` (bm25, lower = better) so the
    agent can judge each candidate. The order is a candidate ordering, not a
    verdict -- the agent decides relevance.

    Type is not part of the ordering. A diagram earns its place the way
    every other memory does: it comes back when it matches, where its score
    puts it.

    collapse=True folds near-identical results into the best-ranked one of
    them (see _collapse_near_copies). Off by default: it is a concession to
    a caller paying for every row in a context window, and the dashboard is
    the opposite case -- a human curating the store needs to SEE that the
    same fact was written five times, which is what the dedup queue is for.
    """
    # A uid query matches only [[uid]] in other bodies, so the named row is pinned above its
    # referrers here, for every caller, as queries.list_memories does for a pasted uid.
    pinned: dict | None = None
    if is_opaque_query(query):
        row = get_memory(conn, query.strip())
        if row is not None and (not status or row["status"] == status):
            pinned = dict(row)
            pinned["match_source"] = "uid"

    hits: list[dict] = []
    for row in search_memories(conn, query, domain=domain, type=type, tag=tag,
                               status=status, limit=limit, subtree=subtree):
        d = dict(row)
        d["fts_rank"] = d.pop("rank")
        d["match_source"] = "fts"
        hits.append(d)

    # Contradicted last, then by score (bm25 ascends): a known-wrong memory never leads one that holds.
    hits.sort(key=lambda d: (d.get("confidence") == CONFIDENCE_CONTRADICTED,
                             d["fts_rank"], d.get("rowid_pk", 0)))
    results = (_collapse_near_copies(hits) if collapse else hits)[:limit]
    if pinned is not None:
        results = [pinned] + [r for r in results if r["uid"] != pinned["uid"]]
        results = results[:limit]
    _attach_succession(conn, results)
    return results


# Above this ratio two results are the same text; it drops copies, it does not rank related memories.
_COPY_RATIO = 0.92


def _collapse_near_copies(ranked: list[dict]) -> list[dict]:
    """Drop a result that repeats one already kept, and say so on the keeper.

    Lexical, the same measure dedup_candidates uses (see _pair_ratio): it
    matches near-identical text, not paraphrases.
    """
    kept: list[tuple[dict, _Prepared]] = []
    for d in ranked:
        prepared = _Prepared(d.get("content") or "")
        for keeper, kept_prepared in kept:
            if _pair_ratio(prepared, kept_prepared, _COPY_RATIO) >= _COPY_RATIO:
                keeper.setdefault("collapsed", []).append(d["uid"])
                break
        else:
            kept.append((d, prepared))
    return [d for d, _ in kept]


def _attach_succession(conn: sqlite3.Connection, results: list[dict]) -> None:
    """Mark a result that something in the store already supersedes.

    The relations graph knew the answer and retrieval did not read it, so a
    superseded memory came back looking current with its replacement sitting
    one edge away. `succeeded_by` is the uid(s) to read instead -- separate
    from the `superseded_by` COLUMN, which set_status writes when archiving
    and which says nothing about an edge drawn between two active rows.
    """
    if not results:
        return
    uids = [d["uid"] for d in results]
    rows = conn.execute(
        f"SELECT from_uid, to_uid FROM relations WHERE relation_type = 'supersedes' "
        f"AND to_uid IN ({', '.join('?' * len(uids))})",
        uids,
    ).fetchall()
    after: dict[str, list[str]] = {}
    for r in rows:
        after.setdefault(r["to_uid"], []).append(r["from_uid"])
    for d in results:
        if d["uid"] in after:
            d["succeeded_by"] = after[d["uid"]]


def _sound_clause(exclude_contradicted: bool) -> str:
    """The arm that keeps known-wrong memories out of a list.

    Off by default: list_by_domain/list_recent are the fallback for a search
    that came back thin, and a caller asking for everything in a scope means
    everything. pulse() opts in for the checkpoint it returns, because a
    warm-up presents that as the current state -- a contradicted checkpoint
    would hand the next session a bearing already ruled out.
    """
    return f" AND confidence <> '{CONFIDENCE_CONTRADICTED}'" if exclude_contradicted else ""


def list_by_domain(
    conn: sqlite3.Connection, domain: str, *, type: str = "", status: str = "active",
    limit: int = 50, subtree: bool = True, exclude_contradicted: bool = False,
) -> list[sqlite3.Row]:
    """Recency-ordered rows of one domain, its subdomains included.

    subtree=False narrows to what is filed at exactly this path.
    """
    clause, params, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
    sql = [f"SELECT * FROM memories WHERE 1=1 {clause}{_sound_clause(exclude_contradicted)}"]
    if type:
        sql.append("AND type = ?")
        params.append(type)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY created_at DESC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def list_recent(
    conn: sqlite3.Connection, *, type: str = "", domain: str = "", tag: str = "",
    status: str = "active", limit: int = 20, subtree: bool = True,
    exclude_contradicted: bool = False,
) -> list[sqlite3.Row]:
    sql = [f"SELECT * FROM memories WHERE 1=1{_sound_clause(exclude_contradicted)}"]
    params: list = []
    if type:
        sql.append("AND type = ?")
        params.append(type)
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        sql.append(clause)
        params.extend(values)
    if tag:
        clause, needle = _tag_clause(tag, alias="")
        sql.append(clause)
        params.append(needle)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY created_at DESC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()


def timeline_neighbours(
    conn: sqlite3.Connection, anchor: sqlite3.Row, *, before: int = 3, after: int = 3,
    domain: str = "", type: str = "", status: str = "active", subtree: bool = True,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """The rows either side of `anchor` in creation order.

    Returns (older, newer): the `before` rows created immediately before the
    anchor and the `after` rows created immediately after it, both
    oldest-first. The anchor is in neither list. `domain`/`type`/`status`
    narrow the neighbourhood only -- the anchor is the row the caller passes
    in, wherever it is filed and whatever its status.

    Ordering is (created_at, rowid_pk). created_at is not unique: two rows
    written in the same instant compare equal, and the insertion order is
    what separates them -- without it a row can land on both sides.
    """
    where = ["1=1"]
    params: list = []
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        where.append(clause)
        params.extend(values)
    if type:
        where.append("AND type = ?")
        params.append(type)
    if status:
        where.append("AND status = ?")
        params.append(status)
    where_sql = " ".join(where)
    at = [anchor["created_at"], anchor["created_at"], anchor["rowid_pk"]]

    older = conn.execute(
        f"""SELECT * FROM memories WHERE {where_sql}
              AND (created_at < ? OR (created_at = ? AND rowid_pk < ?))
            ORDER BY created_at DESC, rowid_pk DESC LIMIT ?""",
        [*params, *at, max(before, 0)],
    ).fetchall()
    newer = conn.execute(
        f"""SELECT * FROM memories WHERE {where_sql}
              AND (created_at > ? OR (created_at = ? AND rowid_pk > ?))
            ORDER BY created_at ASC, rowid_pk ASC LIMIT ?""",
        [*params, *at, max(after, 0)],
    ).fetchall()
    return list(reversed(older)), list(newer)


def latest_by_type(
    conn: sqlite3.Connection, type: str, *, domain: str = "", status: str = "active",
    exclude_contradicted: bool = False,
) -> sqlite3.Row | None:
    rows = list_recent(conn, type=type, domain=domain, status=status, limit=1,
                       exclude_contradicted=exclude_contradicted)
    return rows[0] if rows else None


def due_for_review(
    conn: sqlite3.Connection, *, domain: str = "", limit: int = 20,
    at: str | None = None, status: str = "active",
) -> list[sqlite3.Row]:
    """Active memories whose own review date has passed, oldest date first."""
    clause, params = _due_clause(at)
    sql = [f"SELECT * FROM memories WHERE {clause}"]
    if domain:
        scope, values, _ = domain_scope_clause(conn, domain, alias="", subtree=True)
        sql.append(scope)
        params.extend(values)
    if status:
        sql.append("AND status = ?")
        params.append(status)
    sql.append("ORDER BY review_after ASC LIMIT ?")
    params.append(limit)
    return conn.execute(" ".join(sql), params).fetchall()
