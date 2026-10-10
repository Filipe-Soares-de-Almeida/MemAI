"""Domain paths: casing, nesting, cross-listing, and the SQL that scopes a query to a subtree."""

from __future__ import annotations

import re
import sqlite3

from memai.lite import DOMAIN_SEP, normalize_domain, split_domain
from memai.store.health import _due_clause
from memai.store.sections import refuse_long
from memai.store.settings import _get_meta, _set_meta

# A domain is a PATH: segments between DOMAIN_SEP nest outermost first, and a scope includes its
# subtree. The nesting lives in the string, with no domains table; FTS indexes each ancestor.

# Domain-casing policy, stored in `meta` under DOMAIN_CASE_KEY and enforced on every domain write:
# 'preserve' keeps the casing written, 'lower'/'upper' coerce it.
DOMAIN_CASE_KEY = "domain_case"
DOMAIN_CASE_MODES = ("preserve", "lower", "upper")
DOMAIN_CASE_DEFAULT = "preserve"

# A memory is FILED at one path and may also BELONG to others, one `memory_domains` row each, read
# by every domain filter; `memories.also_domains` copies them for FTS only (_write_domain_links).
ALSO_SEP = "\n"


def get_domain_case(conn: sqlite3.Connection) -> str:
    """The active domain-casing policy (one of DOMAIN_CASE_MODES)."""
    mode = _get_meta(conn, DOMAIN_CASE_KEY)
    return mode if mode in DOMAIN_CASE_MODES else DOMAIN_CASE_DEFAULT


def set_domain_case(conn: sqlite3.Connection, mode: str) -> str:
    """Persist the domain-casing policy. Returns the normalized value stored."""
    mode = (mode or "").strip().lower()
    if mode not in DOMAIN_CASE_MODES:
        raise ValueError(f"domain_case must be one of {', '.join(DOMAIN_CASE_MODES)}")
    _set_meta(conn, DOMAIN_CASE_KEY, mode)
    return mode


def case_domain(mode: str, domain: str) -> str:
    """Apply a casing policy to one domain string. Idempotent; empty stays empty."""
    if not domain:
        return domain
    if mode == "lower":
        return domain.lower()
    if mode == "upper":
        return domain.upper()
    return domain


def domain_parent(domain: str) -> str:
    """The path one level up. '' for a root, and for no domain at all."""
    return DOMAIN_SEP.join(split_domain(domain)[:-1])


def domain_ancestors(domain: str, *, include_self: bool = False) -> list[str]:
    """Every enclosing path, outermost first: acme, acme/x100, acme/x100/p200."""
    segs = split_domain(domain)
    end = len(segs) if include_self else len(segs) - 1
    return [DOMAIN_SEP.join(segs[: i + 1]) for i in range(max(end, 0))]


def domain_depth(domain: str) -> int:
    """How deep a path sits. 0 for no domain, 1 for a root."""
    return len(split_domain(domain))


def in_domain(domain: str, scope: str) -> bool:
    """True when `domain` IS `scope` or sits under it. An empty scope holds all.

    Segment-wise, not string-wise: 'acme/x1000' is not inside 'acme/x100'
    however similar the two read.
    """
    segs, want = split_domain(domain), split_domain(scope)
    return segs[: len(want)] == want


def parse_domains(value) -> list[str]:
    """Several domain paths out of one field, normalized and deduped.

    Splits on commas, semicolons and newlines. A path's own separator is
    '/', so those three are free to mean "next path" -- which is what a
    form field and a string-typed MCP argument both hand over. A list or
    tuple is taken as already split.
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;\n]", str(value))
    out: list[str] = []
    for raw in items:
        path = normalize_domain(str(raw))
        if path and path not in out:
            out.append(path)
    return out


def coerce_domain(conn: sqlite3.Connection, domain: str) -> tuple[str, str]:
    """Coerce a domain to the store's policy. Returns (coerced_domain, active_mode).

    Two rules, one call: the casing policy, and the path shape every
    reader assumes (see normalize_domain). Raises for a path past
    DOMAIN_MAX: every new path comes through here, and a path rewritten as
    stored does not.
    """
    mode = get_domain_case(conn)
    path = normalize_domain(case_domain(mode, domain))
    refuse_long(domain=path)
    return path, mode


def apply_domain_policy(conn: sqlite3.Connection, domain: str) -> str:
    """Coerce a domain to the store's casing policy and canonical path shape."""
    return coerce_domain(conn, domain)[0]


def apply_link_policy(
    conn: sqlite3.Connection, also, primary: str, *, coerce: bool = True
) -> list[str]:
    """The cross-listings worth storing for a memory filed at `primary`.

    Casing and path shape come from the store's policy, same as the primary
    domain. A path the primary already satisfies is dropped: with the memory
    filed at 'acme/x100/p200', a cross-listing at 'acme' -- or at the
    primary itself -- matches nothing the prefix arm of a domain filter did
    not already match, and storing it would count the memory twice in its
    own branch. A path BELOW the primary is kept: it is a narrower scope,
    which is a real thing to say.

    coerce=False keeps the casing as given, for a caller rewriting exact
    stored strings rather than accepting new ones -- move_domain leaves the
    primary domain's casing alone for the same reason, and the pass that
    repairs casing (the dashboard's normalize_domains) decides which strings it
    touches and reports them.

    Sorted, because this is a set and the order it arrives in means nothing.
    Sorting HERE rather than at each read is what keeps the `memory_domains`
    rows (read back ORDER BY domain) and the `also_domains` mirror (read back
    in stored order) telling one story: the same memory's memberships came
    back in two different orders while the mirror kept write order.
    """
    primary = normalize_domain(primary)
    out: list[str] = []
    for path in parse_domains(also):
        if coerce:
            path = apply_domain_policy(conn, path)
        if path and not in_domain(primary, path) and path not in out:
            out.append(path)
    return sorted(out)


def _like_needle(value: str) -> str:
    """Escape a literal so it matches itself inside a LIKE ... ESCAPE '\\'.

    `_` is a single-character wildcard, and real identifiers carry one
    ('anti_pattern', 'F100_TOTAL'), so an unescaped needle silently
    matches rows nobody asked for.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _path_predicate(col: str, path: str, subtree: bool) -> tuple[str, list]:
    """"this column holds that path" -- the scope arm, or just the bucket."""
    if not subtree:
        return f"{col} = ?", [path]
    return (
        f"({col} = ? OR {col} LIKE ? ESCAPE '\\')",
        [path, _like_needle(path) + DOMAIN_SEP + "%"],
    )


def domain_clause(
    domain: str, *, alias: str = "m", subtree: bool = True, also: bool = True
) -> tuple[str, list]:
    """SQL fragment + bound values that scope a query to one domain.

    Subtree by default, because a domain names a scope and the reason the
    scope nests is that asking about 'acme/x100' must not hide what is
    filed under 'acme/x100/p200'. In a store with no nesting the prefix
    arm matches nothing extra, so this returns exactly the rows plain
    equality would.

    subtree=False is for the questions about the bucket itself rather than
    the scope: what is filed at THIS path, and which rows a rename of this
    exact string has to rewrite.

    A scope also holds what is CROSS-LISTED into it -- the same predicate,
    over the extra memberships in `memory_domains` -- because a memory that
    belongs to two subjects belongs to both when either one is asked about.
    also=False drops that arm, for the operations that mean the filed path
    and nothing else: a re-home rewrites where a memory LIVES, and matching
    a cross-listing there would move a memory that only passes through.
    """
    col = f"{alias}.domain" if alias else "domain"
    path = normalize_domain(domain)
    own, values = _path_predicate(col, path, subtree)
    if not also:
        return f"AND {own}", values
    linked, link_values = _path_predicate("dl.domain", path, subtree)
    uid = f"{alias}.uid" if alias else "uid"
    return (
        f"AND ({own} OR EXISTS (SELECT 1 FROM memory_domains dl "
        f"WHERE dl.memory_uid = {uid} AND {linked}))",
        [*values, *link_values],
    )


def all_domains_sql() -> str:
    """Every distinct path in use, filed or cross-listed, in a `domain` column.

    One statement, because "which domains exist" has two sources now and a
    caller that reads only `memories` would miss a subject that exists
    purely as a cross-listing -- which is a legitimate way for one to exist.
    """
    return (
        "SELECT DISTINCT domain FROM ("
        "SELECT domain FROM memories WHERE domain <> '' "
        "UNION SELECT domain FROM memory_domains WHERE domain <> '')"
    )


def _fold(segments: list[str]) -> list[str]:
    """Segments as a filter compares them: case is not part of the name.

    A domain is a name a caller types from memory, and the two casings of
    one subject are the same subject -- which is why the dashboard reports
    'acme/Cache' next to 'acme/cache' as drift to merge rather than as two
    scopes. Matching folds; the STORED spelling is what comes back, so a
    resolved scope is always a real path and its equality arm hits.
    """
    return [s.lower() for s in segments]


def _inner_scope(stored: list[str], want: list[str]) -> str | None:
    """Where `want` sits inside a stored path, as a path down to its end.

    'p200' inside 'acme/x100/p200/warmup' is the scope 'acme/x100/p200'
    -- the level asked for, with whatever it contains. The OUTERMOST
    occurrence wins: a repeated segment name inside one path is a level and
    a sublevel of itself, and the level is the one that was asked for.

    Compared folded, returned as stored (see _fold). Position 0 is not a
    special case here: a path matching at the front is the literal reading,
    and resolve_domain_scopes takes that pass before this one.
    """
    folded_stored, folded_want = _fold(stored), _fold(want)
    for i in range(len(stored) - len(want) + 1):
        if folded_stored[i:i + len(want)] == folded_want:
            return DOMAIN_SEP.join(stored[: i + len(want)])
    return None


def resolve_domain_scopes(conn: sqlite3.Connection, domain: str) -> list[str]:
    """The paths a domain filter should cover, given what the caller wrote.

    A path taken from the tree matches as a prefix, which is the normal
    case and settles here immediately. But the name a caller has in hand is
    usually the DEEP end of the path -- a routine code, not the product it
    belongs to -- and 'p200' matches no prefix once that routine lives at
    'acme/x100/p200'. So when nothing in the store starts with the string,
    it is tried as a run of segments anywhere inside a path, and every
    branch it names becomes a scope.

    Two deliberate properties. The literal reading always wins: if 'p200'
    also exists as a top-level domain, that is what a filter on 'p200'
    means, and the routine buried elsewhere is not silently mixed in. And
    an ambiguous name broadens instead of guessing -- a code filed under
    two modules resolves to both scopes, and callers are told which
    (`scope.paths` in pulse(), `domain_scope` in the admin responses),
    because picking one silently is the failure this exists to avoid.

    Returns the requested path unchanged when nothing matches, so an
    unknown domain still means "no rows" rather than "everything".

    Cross-listings count as paths in use on both readings: a subject that
    exists only because memories were cross-listed into it is a real scope,
    and it resolves literally like any other.

    Casing is not part of either reading. The store's policy is applied
    first, so a store that coerces to one case resolves a filter written in
    the other; and both passes then match folded (see _fold), because a
    'preserve' store keeps whatever spelling each write happened to use and
    a caller has no way to know which one that was. Every scope returned is
    a path as STORED. `LIKE` ignores case and `=` does not, so a scope
    resolved to the caller's spelling hands the equality arm a string no row
    carries: the descendants match and the path itself is missed.
    """
    path = normalize_domain(case_domain(get_domain_case(conn), domain))
    if not path:
        return []
    # Fast path: something is filed at exactly this string, the common case of a path passed back
    # from list_domains().
    if conn.execute(
        "SELECT 1 FROM (SELECT domain FROM memories UNION "
        "SELECT domain FROM memory_domains) WHERE domain = ? LIMIT 1",
        (path,),
    ).fetchone():
        return [path]

    want = split_domain(path)
    stored = [split_domain(r["domain"]) for r in conn.execute(all_domains_sql())]
    # The literal reading, folded: every stored path this one prefixes, cut to the asked depth,
    # covering a spelling variant and an implicit level.
    literal = {
        DOMAIN_SEP.join(segments[:len(want)])
        for segments in stored
        if _fold(segments[:len(want)]) == _fold(want)
    }
    if literal:
        return sorted(literal)

    scopes: set[str] = set()
    for segments in stored:
        found = _inner_scope(segments, want)
        if found:
            scopes.add(found)
    # No scope here contains another, since _inner_scope stops at the outermost occurrence; nothing
    # to collapse.
    return sorted(scopes) or [path]


def domain_scope_clause(
    conn: sqlite3.Connection, domain: str, *, alias: str = "m", subtree: bool = True,
    also: bool = True,
) -> tuple[str, list, list[str]]:
    """domain_clause over every scope the filter resolves to.

    Returns (sql, params, scopes) -- `scopes` is what the query actually
    covers, which is the string the caller passed unless resolution had to
    reach for it (see resolve_domain_scopes).
    """
    scopes = resolve_domain_scopes(conn, domain)
    parts: list[str] = []
    params: list = []
    for scope in scopes:
        clause, values = domain_clause(scope, alias=alias, subtree=subtree, also=also)
        parts.append(clause.removeprefix("AND "))
        params.extend(values)
    return f"AND ({' OR '.join(parts)})", params, scopes


def list_domains(
    conn: sqlite3.Connection, *, status: str = "active"
) -> list[dict]:
    """Every domain in the store, as the nodes of the domain tree.

    Warm-up discovery. domain is free text and drifts over time (e.g.
    'proj-1042' vs 'proj-1042-cache-warmup'), so listing the real
    strings lets the caller target the right one instead of guessing.

    Both counts are reported, because they answer different questions:
    `count` is what is filed at exactly this path, `subtree` is that plus
    everything nested under it. A domain holding nothing of its own can
    still be the right thing to warm up -- that is what a parent IS.

    `also` and `subtree_also` are the same two questions for the memories
    CROSS-LISTED here rather than filed here -- counted apart, because a
    scope's own size and how much of another subject passes through it are
    different facts and adding them would make the tree deeper than the
    store. A node with `count` 0 and `also` above it exists purely as a
    cross-cutting subject, which is a legitimate way for one to exist.

    Ancestors nobody wrote to directly still get a node, flagged
    `implicit`: one 'acme/x100/p200' means the tree has an 'acme' and an
    'acme/x100', whether or not a memory was ever filed at either. Being
    cross-listed at a path names it as surely as being filed there, so that
    clears `implicit` too.
    Ordering stays recency-first (by subtree activity, so a parent sorts
    with its liveliest child), alphabetical within a tie. `latest_at` counts
    both kinds of activity, or a subject that only ever gets cross-listed
    into would sink to the bottom of the tree it organizes.
    """
    where = "AND m.status = ?" if status else ""
    params: list = [status] if status else []
    rows = conn.execute(
        "SELECT m.domain AS domain, COUNT(*) AS count, MAX(m.created_at) AS latest_at "
        f"FROM memories m WHERE m.domain <> '' {where} GROUP BY m.domain", params).fetchall()
    link_rows = conn.execute(
        "SELECT dl.domain AS domain, COUNT(*) AS count, MAX(m.created_at) AS latest_at "
        "FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid "
        f"WHERE dl.domain <> '' {where} GROUP BY dl.domain", params).fetchall()

    nodes: dict[str, dict] = {}

    def node(path: str) -> dict:
        return nodes.setdefault(path, {
            "domain": path, "count": 0, "latest_at": "",
            "parent": domain_parent(path), "depth": domain_depth(path),
            "subtree": 0, "subtree_latest_at": "", "children": 0,
            "also": 0, "subtree_also": 0,
            "implicit": True,
        })

    for source, key in ((rows, "count"), (link_rows, "also")):
        for r in source:
            # normalized on every write path; normalizing again keeps a store
            # written to directly from splitting one path across two nodes
            n = node(normalize_domain(r["domain"]))
            n[key] += r["count"]
            n["latest_at"] = max(n["latest_at"], r["latest_at"] or "")
            n["implicit"] = False
            for ancestor in domain_ancestors(n["domain"]):
                node(ancestor)

    for n in list(nodes.values()):
        for scope in domain_ancestors(n["domain"], include_self=True):
            holder = nodes[scope]
            holder["subtree"] += n["count"]
            holder["subtree_also"] += n["also"]
            holder["subtree_latest_at"] = max(holder["subtree_latest_at"], n["latest_at"])
        if n["parent"]:
            nodes[n["parent"]]["children"] += 1

    out = sorted(nodes.values(), key=lambda n: n["domain"])
    out.sort(key=lambda n: n["subtree_latest_at"], reverse=True)
    return out


def domain_census(
    conn: sqlite3.Connection, domain: str = "", *, status: str = "active"
) -> dict:
    """What a domain scope holds, and how it splits one level down.

    pulse() carries this census as its `scope`: `by_type` counts the whole
    scope, so a caller can see that it holds 13 notes and reach for
    search()/list_by_domain() with the domain it already has.

    `children` stops at the NEXT level rather than walking the subtree: "what
    else is in here" is answered a level at a time, and the child's own
    census is one call away when the work goes there. Each child reports
    `own` (filed at that path) and `subtree` (that plus everything under it),
    because a child that holds nothing itself can still be where the branch
    lives.

    An empty `domain` censuses the whole store, and then `children` are its
    roots.

    A memory CROSS-LISTED into the scope is part of the scope -- it counts
    in `total` and `by_type` like any other. But its filed path is somewhere
    else entirely, so it cannot be placed by that path: the level it sits at
    HERE is the one its cross-listing names. `also` reports how much of the
    scope arrives that way, and each child carries its own `also`/
    `subtree_also`, so a drill-down plan built from this never points at a
    child that turns out to hold nothing. All three are omitted when zero --
    a store that never cross-lists should not pay for the field on every
    child of every pulse.
    """
    scopes = resolve_domain_scopes(conn, domain)
    where, params = ["1=1"], []
    if scopes:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=True)
        where.append(clause)
        params.extend(values)
    if status:
        where.append("AND status = ?")
        params.append(status)
    where_sql = " ".join(where)
    rows = conn.execute(
        f"SELECT domain, type, COUNT(*) AS n FROM memories WHERE {where_sql} "
        "GROUP BY domain, type", params).fetchall()
    # cross-listings of in-scope memories by the path they name, one row per (link path, type), so
    # a child is placed by the membership that put the memory in scope
    link_rows = conn.execute(
        "SELECT dl.domain AS domain, m.type AS type, COUNT(*) AS n "
        "FROM memory_domains dl JOIN memories m ON m.uid = dl.memory_uid "
        f"WHERE m.rowid_pk IN (SELECT rowid_pk FROM memories WHERE {where_sql}) "
        "GROUP BY dl.domain, m.type", params).fetchall()

    by_type: dict[str, int] = {}
    kids: dict[str, dict] = {}

    def kid(child: str) -> dict:
        return kids.setdefault(child, {
            "domain": child, "own": 0, "subtree": 0, "also": 0, "subtree_also": 0})

    def trim(k: dict) -> dict:
        return {key: v for key, v in k.items() if v or key not in ("also", "subtree_also")}

    def place(path: str, n: int, own_key: str, subtree_key: str) -> None:
        for scope in (scopes or [""]):
            if path == scope or not in_domain(path, scope):
                continue
            child = DOMAIN_SEP.join(split_domain(path)[: domain_depth(scope) + 1])
            k = kid(child)
            k[subtree_key] += n
            if path == child:
                k[own_key] += n

    also_total = 0
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + r["n"]
        # in scope only because of a cross-listing: nothing about its filed
        # path belongs to this census beyond the count
        if scopes and not any(in_domain(r["domain"], s) for s in scopes):
            also_total += r["n"]
            continue
        place(r["domain"], r["n"], "own", "subtree")
    for r in link_rows:
        place(r["domain"], r["n"], "also", "subtree_also")
    out = {
        "paths": scopes,
        "total": sum(by_type.values()),
        "by_type": by_type,
        "children": [trim(k) for k in sorted(
            kids.values(),
            key=lambda k: (-(k["subtree"] + k["subtree_also"]), k["domain"]))],
    }
    if also_total:
        out["also"] = also_total
    # Overdue for a recheck: one count, omitted when zero, the only thing a warm-up says about
    # decay rather than contents.
    due_clause, due_params = _due_clause()
    stale = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND {due_clause}",
        [*params, *due_params]).fetchone()[0]
    if stale:
        out["stale"] = stale
    return out
