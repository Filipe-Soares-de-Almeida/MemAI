"""The corpus an optimization pass reads: a slimmed page of memories, its stats and its hints."""

from __future__ import annotations

import re
import sqlite3

from memai import budget, guard
from memai.lite import DOMAIN_SEP, split_domain
from memai.store.domains import domain_scope_clause, parse_domains
from memai.store.health import _due_clause, today_iso
from memai.store.memories import get_domain_links, usage_for

# Text fields a leaked tool call lands in and `unleak` repairs, one per suggestion so each can be
# decided and undone on its own.
LEAK_FIELDS = ("content", "tags", "source_ref")
# Findings a scan reports; the count of what it left behind comes with it.
LEAK_SCAN_CAP = 40

CORPUS_SNIPPET_LEN = 120
CORPUS_TAGS_LEN = 100
CORPUS_ANCHORS_CAP = 5
# Per-page ceiling on the listing and the relations touching it, measured as sent. Hosts cap output
# near 25k tokens and dense JSON runs ~3 chars/token; 28k keeps the full response near 12k tokens.
CORPUS_CHAR_BUDGET = 28_000
# A full=True body longer than this is cut; get_memory(uid, content_offset=...) reads the rest.
CORPUS_FULL_LEN = 8_000
# stats.by_domain lists this many of the largest domains; stats.domains counts them all.
CORPUS_DOMAINS_CAP = 150
DOMAIN_HINT_CAP = 40
# What `count`, `truncated` and `relations_truncated` can still add once the empty response has
# been measured.
_PAGE_SLACK = 48

# Verifiable anchors an agent can go check against live facts: URLs,
# file paths, table/field-style identifiers and SNAKE_CASE constants.
_ANCHOR_PATTERNS = (
    re.compile(r"""https?://[^\s)>\]"']+"""),
    re.compile(
        r"[\w./\\~-]*\w\.(?:pas|py|js|ts|tsx|sql|json|ya?ml|toml|md|css|html|ini|cfg|bat|ps1|sh)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z][A-Z0-9]{0,4}\d{3,}\b"),          # X100, AB1234 …
    re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b"),      # F100_TOTAL, SOME_FLAG …
)


def _extract_anchors(content: str, cap: int = 8) -> list[str]:
    """Pull the verifiable anchors out of a memory's full content."""
    seen: list[str] = []
    for pat in _ANCHOR_PATTERNS:
        for m in pat.findall(content):
            if m not in seen:
                seen.append(m)
            if len(seen) >= cap:
                return seen
    return seen


def _norm_domain(d: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-")


def _domain_hints(domain_counts: dict[str, int]) -> list[dict]:
    """Cluster domain-string variants that likely mean the same thing.

    Groups by normalized form (lowercase, separators collapsed) and, when
    the domain embeds a ticket-style id (e.g. proj-1042), by that id --
    so 'PROJ-1042', 'proj_1042' and 'proj-1042-fix' all cluster.
    Returns only clusters with 2+ distinct raw strings, canonical first.
    """
    groups: dict[str, list[str]] = {}
    for raw in domain_counts:
        if not raw:
            continue
        norm = _norm_domain(raw)
        m = re.search(r"[a-z]{2,}-\d{3,}", norm)
        key = m.group(0) if m else norm
        groups.setdefault(key, []).append(raw)
    hints = []
    for variants in groups.values():
        if len(variants) < 2:
            continue
        variants.sort(key=lambda v: (-domain_counts[v], len(v)))
        hints.append({
            "canonical": variants[0],
            "variants": [{"domain": v, "count": domain_counts[v]} for v in variants],
            "total": sum(domain_counts[v] for v in variants),
        })
    hints.sort(key=lambda h: -h["total"])
    return hints


# A token that reads as a code rather than as prose: 'x100', 'p200',
# 'x1042'. Two digits minimum, so a word like 'v2' does not pass for one.
_CODE_TOKEN = re.compile(r"^[a-z]{1,4}\d{2,}[a-z0-9]*$")
# How many domains a leading token must head before it counts as a root of
# the tree on its own (a code needs no such evidence).
_ROOT_MIN = 3
NESTING_HINT_CAP = 40


def _nesting_hints(domain_counts: dict[str, int]) -> list[dict]:
    """Propose a nested path for each flat domain that already reads like one.

    'acme-x100-p200-cache-warmup' states a hierarchy in a string
    nothing can group by. This lifts its leading part into path segments
    and keeps the descriptive tail as the leaf --
    'acme/x100/p200/cache-warmup' -- so the diagrams and notes of one
    module can be asked for as one scope.

    Only leading tokens that LOOK structural are lifted: a code
    (_CODE_TOKEN), or a token heading _ROOT_MIN or more domains in the
    store, which is what a root looks like from here whatever it means.
    The first token that is neither ends the path, tail included.

    Advisory, and deliberately so: nothing here can tell a real level from
    a hyphen inside a name, so each proposal is meant for a human -- or for
    an agent staging a `redomain` suggestion the human then approves --
    never for the rows directly.
    """
    heads: dict[str, int] = {}
    for raw in domain_counts:
        segs = split_domain(raw)
        if segs:
            head = segs[0].split("-")[0].lower()
            heads[head] = heads.get(head, 0) + 1

    def structural(token: str) -> bool:
        token = token.lower()
        return bool(_CODE_TOKEN.match(token)) or heads.get(token, 0) >= _ROOT_MIN

    hints: list[dict] = []
    for raw, count in sorted(domain_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if not raw or DOMAIN_SEP in raw:
            continue                      # blank, or already a path
        tokens = raw.split("-")
        cut = 0
        while cut < len(tokens) - 1 and structural(tokens[cut]):
            cut += 1
        if not cut:
            continue
        hints.append({
            "domain": raw,
            "count": count,
            "proposed": DOMAIN_SEP.join([*tokens[:cut], "-".join(tokens[cut:])]),
        })
        if len(hints) >= NESTING_HINT_CAP:
            break
    return hints


def _leak_findings(
    conn: sqlite3.Connection, where_sql: str, params: list, *, cap: int = LEAK_SCAN_CAP,
) -> tuple[list[dict], int]:
    """Rows whose text carries a tool call's own source, and how many exist.

    SQL narrows to the rows holding a closing tag at all, which is the cheap
    half of the test; guard.leak_marks judges each candidate, because which
    marks count depends on the row's own type.

    A finding names the fields that carry a mark, what a repair takes out of
    each, and whether the repair CLEARS the field -- `clean: false` is a
    field whose marks sit inside prose, which `unleak` refuses and a reword
    has to rewrite by hand. `declares` is what the debris was trying to
    write, reported only for the columns that are still empty: those are the
    ones with a `redomain`, `crosslist` or `retag` waiting beside the
    `unleak`.
    """
    like = " OR ".join(f"{f} LIKE '%</%'" for f in LEAK_FIELDS)
    rows = conn.execute(
        f"""SELECT uid, type, title, content, tags, source_ref, domain
            FROM memories WHERE {where_sql} AND ({like})
            ORDER BY created_at DESC""", params).fetchall()
    findings, total = [], 0
    for r in rows:
        marks = {f: guard.leak_marks(r["type"], r[f] or "") for f in LEAK_FIELDS}
        marks = {f: m for f, m in marks.items() if m}
        if not marks:
            continue
        total += 1
        if len(findings) >= cap:
            continue
        fields, declares = {}, {}
        for f, found in marks.items():
            clean, dropped = guard.strip_leak(r["type"], r[f])
            declares.update(guard.declared(dropped))
            fields[f] = {
                "marks": found,
                "removes": len(r[f]) - len(clean),
                "clean": not guard.leak_marks(r["type"], clean),
            }
        # `also` is read from memory_domains, never from the one-field
        # mirror beside it: the rows are where a membership lives
        held = {"domain": r["domain"], "also": get_domain_links(conn, r["uid"]),
                "tags": r["tags"], "source_ref": r["source_ref"]}
        entry = {"uid": r["uid"], "type": r["type"], "fields": fields}
        if r["title"]:
            entry["title"] = r["title"][:CORPUS_SNIPPET_LEN]
        empty = {k: v for k, v in declares.items() if k in held and not held[k]}
        if empty:
            entry["declares"] = empty
        findings.append(entry)
    return findings, total


def _corpus_scope(
    conn: sqlite3.Connection, *, domain: str, type: str, since: str,
    include_archived: bool, subtree: bool,
) -> tuple[str, list, str, list]:
    """The scan's WHERE clause and params, then the same without `since` for the hints."""
    status = "" if include_archived else "active"
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
    base_where_sql, base_params = " ".join(where), list(params)
    if since:
        where.append("AND updated_at >= ?")
        params.append(since)
    return " ".join(where), params, base_where_sql, base_params


def _corpus_entry(r: sqlite3.Row, *, include_archived: bool, full: bool, today: str) -> dict:
    """One memory as the listing carries it, every empty or default field left out."""
    m = {"uid": r["uid"], "type": r["type"]}
    if r["confidence"] != "unverified":
        m["confidence"] = r["confidence"]
    if r["domain"]:
        m["domain"] = r["domain"]
    # what it already belongs to besides its own path, or a curation pass
    # proposing a cross-listing cannot tell a new one from one that holds
    if r["also_domains"]:
        m["also"] = parse_domains(r["also_domains"])
    if r["session"]:
        m["session"] = r["session"]
    if r["tags"]:
        tags = r["tags"]
        if len(tags) > CORPUS_TAGS_LEN:
            m["tags_len"] = len(tags)
            tags = tags[: CORPUS_TAGS_LEN - 1] + "…"
        m["tags"] = tags
    if include_archived and r["status"] != "active":
        m["status"] = r["status"]
    if r["superseded_by"]:
        m["superseded_by"] = r["superseded_by"]
    # What the writer said needs rechecking and where; `due` answers "is this overdue" so the
    # reader does no date arithmetic.
    if r["review_after"]:
        m["review_after"] = r["review_after"]
        if r["review_after"] <= today:
            m["due"] = True
    if r["source_ref"]:
        m["source_ref"] = r["source_ref"]
    m["created_at"] = r["created_at"][:19]
    content = r["content"]
    m["content_len"] = len(content)
    cap = CORPUS_FULL_LEN if full else CORPUS_SNIPPET_LEN
    m["content"] = content if len(content) <= cap else content[: cap - 1] + "…"
    anchors = _extract_anchors(content, cap=CORPUS_ANCHORS_CAP)
    if anchors:
        m["anchors"] = " ".join(anchors)
    return m


def _corpus_page(
    conn: sqlite3.Connection, where_sql: str, params: list, *, limit: int, offset: int,
    include_archived: bool, full: bool, today: str, room: int,
) -> tuple[list[dict], list[dict], bool]:
    """The listing, the relations touching it, and whether any of those were left out.

    `limit` rows from `offset`, ended before the row whose entry and new edges would take the two
    past `room` characters as sent. The first row always goes in, its edges while they fit.
    """
    rows = conn.execute(
        f"""SELECT uid, type, domain, also_domains, session, tags, content, status,
                   confidence, superseded_by, created_at, updated_at,
                   review_after, source_ref
            FROM memories WHERE {where_sql}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    usage = usage_for(conn, [r["uid"] for r in rows])
    rels = [dict(r) for r in conn.execute(
        "SELECT id, from_uid, to_uid, relation_type FROM relations").fetchall()]
    touching: dict[str, list[dict]] = {}
    for e in rels:
        touching.setdefault(e["from_uid"], []).append(e)
        touching.setdefault(e["to_uid"], []).append(e)
    mems: list[dict] = []
    listed: set[int] = set()
    used = 0
    clipped = False
    for r in rows:
        m = _corpus_entry(r, include_archived=include_archived, full=full, today=today)
        # What each one has been worth, omitted when zero like every default here; a memory with
        # no `recalls` is the interesting case.
        u = usage.get(m["uid"])
        if u:
            m["recalls"], m["last_recall"] = u["recalls"], u["last_recall"][:19]
        new = {e["id"]: budget.item_chars(e) for e in touching.get(m["uid"], [])
               if e["id"] not in listed}
        cost = budget.item_chars(m)
        if mems and used + cost + sum(new.values()) > room:
            break
        mems.append(m)
        used += cost
        for eid, chars in new.items():
            if used + chars > room:
                clipped = True
                break
            listed.add(eid)
            used += chars
    return mems, [e for e in rels if e["id"] in listed], clipped


def _corpus_stats(conn: sqlite3.Connection, where_sql: str, params: list, today: str) -> dict:
    """Counts over the WHOLE filtered corpus, not just the page the listing holds."""
    total = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params).fetchone()[0]
    def agg(col: str) -> dict:
        return dict(conn.execute(
            f"SELECT {col}, COUNT(*) FROM memories WHERE {where_sql} GROUP BY {col} ORDER BY COUNT(*) DESC",
            params).fetchall())
    by_domain = agg("domain")
    return {
        "total": total,
        "by_type": agg("type"),
        "by_confidence": agg("confidence"),
        "by_domain": by_domain,
        "empty_domain": by_domain.get("", 0),
        # Over the whole filtered corpus. Read as UNPROVEN, never useless: telling store-wide
        # (a write log if near total), meaningless for any single row.
        "never_recalled": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND uid NOT IN "
            "(SELECT memory_uid FROM memory_usage)", params).fetchone()[0],
        # Rechecks a writer dated and nobody did. Tags empty or only the type carry no synonym;
        # `retag` fixes them.
        "untagged": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} "
            "AND (TRIM(tags) = '' OR TRIM(tags) = type)", params).fetchone()[0],
        # No name of its own, so every list falls back to the opening line of
        # its body. `retitle` is the kind that fixes it.
        "untitled": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND TRIM(title) = ''",
            params).fetchone()[0],
        "due_for_review": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND {_due_clause(today)[0]}",
            [*params, today]).fetchone()[0],
    }


def _corpus_hints(
    conn: sqlite3.Connection, by_domain: dict, since: str, base_where_sql: str, base_params: list,
) -> tuple[list[dict], list[dict]]:
    """The domain-variant clusters and the nesting proposals, in that order."""
    # Domain and nesting hints cluster over the WHOLE store; with `since`, only clusters touching
    # the delta are kept (counts stay store-wide).
    if since:
        by_domain_global = dict(conn.execute(
            f"SELECT domain, COUNT(*) FROM memories WHERE {base_where_sql} "
            "GROUP BY domain ORDER BY COUNT(*) DESC", base_params).fetchall())
        hints = [h for h in _domain_hints(by_domain_global)
                 if any(v["domain"] in by_domain for v in h["variants"])]
        nesting = [n for n in _nesting_hints(by_domain_global) if n["domain"] in by_domain]
    else:
        hints = _domain_hints(by_domain)
        nesting = _nesting_hints(by_domain)
    return hints, nesting


def optimization_corpus(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    since: str = "", include_archived: bool = False, limit: int = 500,
    offset: int = 0, full: bool = False, subtree: bool = True, extra: dict | None = None,
) -> dict:
    """Compact whole-corpus dump for an agent to reason over in one call.

    Returns every memory's curation-relevant fields plus the relation edges
    touching them, so the agent can spot missing links, duplicates, stale or
    mis-scoped rows without hundreds of individual reads.

    The listing is aggressively slimmed so a few-hundred-memory store fits
    one MCP response (the full-body version of a real 200-memory store was
    ~450KB; even snippet-only it overflowed on metadata alone):
      - content is a snippet with content_len alongside (full=True keeps
        bodies up to CORPUS_FULL_LEN; get_memory fetches one on demand)
      - tags longer than CORPUS_TAGS_LEN are cut, with tags_len alongside
      - empty/default fields are omitted (blank domain/session/tags, no
        cross-listings, null superseded_by, status matching the filter
        default, confidence 'unverified' -- stats.by_confidence keeps the
        aggregate view)
      - `also` lists the domains a memory belongs to besides its own path,
        because a pass proposing a `crosslist` has to be able to tell a new
        membership from one that already holds
      - created_at drops sub-second precision; updated_at is not listed
        at all (get_memory has it)
      - anchors come as one space-joined string, capped at
        CORPUS_ANCHORS_CAP
    Beyond `limit`, a page also ends early when the listing and the relations
    touching it reach CORPUS_CHAR_BUDGET, or whatever the rest of the
    response and the caller's `extra` fields leave of
    budget.MCP_RESULT_MAX_CHARS, both measured as sent -- the guarantee is
    that ONE response always fits an MCP host's output cap, whatever the
    store looks like. A page's first memory is listed with as many of its
    edges as fit, and `relations_truncated: true` says some were left out:
    get_relations(uid) lists them all. A `stats` block aggregates the
    filtered corpus regardless of limit, its `by_domain` naming the
    CORPUS_DOMAINS_CAP largest domains and `domains` counting them all,
    `domain_hints` clusters likely-variant domain strings (at most
    DOMAIN_HINT_CAP),
    `domain_nesting` proposes a path for each flat domain that already
    spells a hierarchy out (see _nesting_hints -- the raw material for
    `redomain` suggestions), `leaked_calls` lists the rows carrying a tool
    call's own source with `stats.leaked_calls` counting them all (see
    _leak_findings -- the raw material for `unleak`), and `truncated` flags
    when the listing stopped before the corpus ended -- page onward with
    offset (offset + count is the next page's offset).

    `since` makes curation incremental: only memories created OR updated
    at/after the given ISO timestamp (a date like '2026-07-01' works --
    string comparison over ISO values). Stats then describe that delta,
    but domain_hints stay cross-window: clusters are computed over the
    WHOLE store and reported when they touch the delta, so a new
    domain-string variant still pairs with an old spelling that sits
    outside the scan window.
    """
    where_sql, params, base_where_sql, base_params = _corpus_scope(
        conn, domain=domain, type=type, since=since,
        include_archived=include_archived, subtree=subtree)
    today = today_iso()
    stats = _corpus_stats(conn, where_sql, params, today)
    hints, nesting = _corpus_hints(conn, stats["by_domain"], since, base_where_sql, base_params)

    # Leaked calls over the scan's own window, not the page: pagination would hide findings.
    leaked, leaked_total = _leak_findings(conn, where_sql, params)
    stats["leaked_calls"] = leaked_total
    stats["domains"] = len(stats["by_domain"])
    stats["by_domain"] = dict(list(stats["by_domain"].items())[:CORPUS_DOMAINS_CAP])

    response = {
        "memories": [],
        "relations": [],
        "count": 0,
        "offset": offset,
        "truncated": False,
        "stats": stats,
        "domain_hints": hints[:DOMAIN_HINT_CAP],
        "domain_nesting": nesting,
        "leaked_calls": leaked,
        **(extra or {}),
    }
    room = budget.MCP_RESULT_MAX_CHARS - budget.result_chars(response) - _PAGE_SLACK
    mems, edges, clipped = _corpus_page(conn, where_sql, params, limit=limit, offset=offset,
                                        include_archived=include_archived, full=full,
                                        today=today, room=min(room, CORPUS_CHAR_BUDGET))
    response.update(memories=mems, relations=edges, count=len(mems),
                    truncated=offset + len(mems) < stats["total"])
    if clipped:
        response["relations_truncated"] = True
    return response
