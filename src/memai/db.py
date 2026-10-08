"""SQLite-backed store for memai.

One store is one WAL-mode file holding memory rows, an FTS5 index, edit
history, a relations graph, and the node/edge tables behind type='diagram'
memories together under one set of ACID transactions, so there is nothing
that can desync from the metadata on a hard-kill. One such file is a
PROJECT: a home directory holds any number of them and names the one every
connect() opens by default.

Retrieval is FTS5 BM25 keyword search. It only widens the candidate set --
semantic judgment is left to the calling agent, which reads the candidates
back and decides relevance itself.
"""

from __future__ import annotations

import json
import re
import sqlite3

from memai import contract, guard
from memai.lite import (  # noqa: F401
    DOMAIN_SEP,
    TASK_ASK_MINUTES_DEFAULT,
    WARDEN_MINUTES_DEFAULT,
    home,
    normalize_domain,
    now_iso,
    split_domain,
)
from memai.store.backups import (  # noqa: F401
    ARCHIVE_GROUPS,
    ARCHIVE_LABEL_MAX,
    ARCHIVES_DIRNAME,
    BACKUPS_DIRNAME,
    SHELF_META_FILE,
    archive_backups,
    archive_files,
    archive_group_name,
    archive_grouped,
    archive_label,
    archive_label_name,
    archive_members,
    archive_name,
    archive_plan,
    archives_dir,
    backup_files,
    backup_name,
    backup_to,
    backups_dir,
    delete_archive,
    delete_backups,
    forget_shelf_meta,
    rename_archive,
    restore_backup,
    set_shelf_meta,
    shelf_meta,
    unarchive,
)
from memai.store.connection import (  # noqa: F401
    _FTS_COLUMNS,
    CHARS_PER_TOKEN,
    COMPACT_REASON_KEY,
    COMPACT_REASON_VECTORS,
    SCHEMA,
    _drop_vector_store,
    _ensure_columns,
    clear_compact_reason,
    connect,
    est_tokens,
    get_compact_reason,
    new_uid,
)
from memai.store.dedup import (  # noqa: F401
    SIMILAR_ON_WRITE,
    SIMILAR_ON_WRITE_MAX,
    SIMILAR_SNIPPET,
    _pair_ratio,
    _Prepared,
    _ratio_bound,
    dedup_candidates,
    similar_memories,
    text_ratio,
)
from memai.store.diagrams.layout import (  # noqa: F401
    DECISION_DEFAULT_H,
    FONT_SCALE_MAX,
    FONT_SCALE_MIN,
    LAYOUT_COL_W,
    LAYOUT_GAP_X,
    LAYOUT_GAP_Y,
    LAYOUT_ROW_H,
    NODE_DEFAULT_H,
    NODE_DEFAULT_W,
    NODE_MAX_H,
    NODE_MAX_W,
    NODE_MIN_H,
    NODE_MIN_W,
    NODE_SHAPES,
    _layout_graph,
    loop_edges,
    node_box,
)
from memai.store.diagrams.persist import (  # noqa: F401
    DIAGRAM_KINDS,
    _load_graph,
    add_diagram_jump,
    add_node_link,
    delete_diagram_edge,
    delete_diagram_jump,
    delete_diagram_node,
    delete_node_link,
    diagram_overview,
    diagrams_referencing,
    get_diagram,
    get_diagram_jumps,
    get_diagram_row,
    get_node_links,
    insert_diagram,
    is_diagram,
    relayout_diagram,
    render_diagram_mermaid,
    render_diagram_text,
    replace_diagram_graph,
    reset_node_boxes,
    set_diagram_meta,
    set_node_positions,
    upsert_diagram_edge,
    upsert_diagram_node,
)
from memai.store.diagrams.render import (  # noqa: F401
    DIAGRAM_BODY_BUDGET,
    _render_mermaid,
)
from memai.store.domains import (  # noqa: F401
    ALSO_SEP,
    DOMAIN_CASE_DEFAULT,
    DOMAIN_CASE_KEY,
    DOMAIN_CASE_MODES,
    all_domains_sql,
    apply_domain_policy,
    apply_link_policy,
    case_domain,
    coerce_domain,
    domain_ancestors,
    domain_census,
    domain_clause,
    domain_depth,
    domain_parent,
    domain_scope_clause,
    get_domain_case,
    in_domain,
    list_domains,
    parse_domains,
    resolve_domain_scopes,
    set_domain_case,
)
from memai.store.health import (  # noqa: F401
    STALE_DAYS,
    _due_clause,
    health_axes,
    health_since,
    health_snapshot,
    normalize_review_after,
    today_iso,
)
from memai.store.memories import (  # noqa: F401
    CONFIDENCE_CONTRADICTED,
    DIAGRAM_TYPE,
    GENERATED_TYPES,
    MEMORY_TYPES,
    PIN_VALUES,
    TAG_SEP,
    TASK_TYPE,
    add_domain_link,
    domain_links_for,
    edit_count,
    get_domain_links,
    get_edit_history,
    get_memory,
    insert_memory,
    memory_row,
    merge_tags,
    migrate_sections,
    pin_error,
    purge_memories,
    purge_memory,
    record_recall,
    remove_domain_link,
    restore_diagram,
    restore_diagram_refs,
    restore_edit,
    restore_memory,
    search_share,
    set_confidence,
    set_domain,
    set_domain_links,
    set_generated_content,
    set_pin,
    set_review_after,
    set_sections,
    set_source_ref,
    set_status,
    set_tags,
    set_title,
    type_error,
    update_memory_content,
    usage_for,
)
from memai.store.paths import (  # noqa: F401
    ACTIVE_FILE,
    GENERAL_FILE,
    GENERAL_PROJECT,
    PROJECT_NAME_MAX,
    PROJECTS_DIRNAME,
    active_project,
    default_db_path,
    find_project,
    project_exists,
    project_name,
    project_name_error,
    project_path,
    set_active_project,
)
from memai.store.projects import (  # noqa: F401
    create_project,
    delete_project,
    list_projects,
)
from memai.store.relations import (  # noqa: F401
    add_relation,
    get_relations,
)
from memai.store.renders import (  # noqa: F401
    RENDER_SUFFIXES,
    prune_renders,
    prune_renders_all,
    renders_dir,
    renders_usage,
)
from memai.store.search import (  # noqa: F401
    _sound_clause,
    due_for_review,
    is_opaque_query,
    latest_by_type,
    list_by_domain,
    list_recent,
    search_memories,
    search_ranked,
    timeline_neighbours,
)
from memai.store.sections import (  # noqa: F401
    _BODY_LINK,
    TITLE_MAX,
    _refuse_leak,
    _write_sections,
    body_links,
    get_sections,
    leak_error,
    section_error,
    section_problem,
    section_queue,
    sections_read,
    title_error,
    unread_sections,
)
from memai.store.settings import (  # noqa: F401
    SVG_RETENTION_DEFAULT,
    SVG_RETENTION_KEY,
    SVG_RETENTION_MODES,
    TASK_ASK_ENABLED_DEFAULT,
    TASK_ASK_ENABLED_KEY,
    TASK_ASK_MINUTES_KEY,
    TASK_ASK_MINUTES_RANGE,
    WARDEN_ENABLED_DEFAULT,
    WARDEN_ENABLED_KEY,
    WARDEN_MINUTES_KEY,
    WARDEN_MINUTES_RANGE,
    _get_meta,
    _set_meta,
    get_svg_retention,
    get_task_ask_enabled,
    get_task_ask_minutes,
    get_warden_enabled,
    get_warden_minutes,
    set_svg_retention,
    set_task_ask_enabled,
    set_task_ask_minutes,
    set_warden_enabled,
    set_warden_minutes,
)
from memai.store.subtree import (  # noqa: F401
    move_domain,
    purge_domain,
    set_domain_status,
)

# ------------------------------------------------------------------ optimization

CONFIDENCE_VALUES = contract.CONFIDENCES
SUGGESTION_KINDS = (
    "compact", "reword", "retag", "retitle", "redomain", "crosslist",
    "set_confidence", "review", "archive", "link", "merge", "distill",
    "unleak",
)
# Text fields a leaked tool call lands in and `unleak` repairs, one per suggestion so each can be
# decided and undone on its own.
LEAK_FIELDS = ("content", "tags", "source_ref")
# Findings a scan reports; the count of what it left behind comes with it.
LEAK_SCAN_CAP = 40
# distill targets must be durable knowledge types -- distilling INTO a
# checkpoint/handoff would just recreate the ephemera it exists to retire
DISTILL_TYPES = ("note", "reasoning", "anti_pattern")
# the payload keys distill applies; any other key is a staging error
DISTILL_PAYLOAD_KEYS = ("source_uids", "new_type", "new_content", "title", "tags", "domain")
# Kinds staging refuses without a non-empty `verified`, each archiving a memory; set_confidence
# needs it only for `contradicted`, checked where that payload is read.
VERIFIED_REQUIRED = {
    "archive": "verified required: describe the live-facts check that makes this memory archivable",
    "merge": "verified required: merge archives payload.drop_uid -- describe the live-facts check",
    "distill": "verified required: distill archives its sources -- describe the live-facts check",
}


CORPUS_SNIPPET_LEN = 120
CORPUS_TAGS_LEN = 100
CORPUS_ANCHORS_CAP = 5
# Per-page ceiling on the serialized listing (compact-JSON chars). Hosts cap output near 25k tokens
# and dense JSON runs ~3 chars/token; 28k keeps the full response near 12k tokens.
CORPUS_CHAR_BUDGET = 28_000
# A full=True body longer than this is cut; get_memory(uid, content_offset=...) reads the rest.
CORPUS_FULL_LEN = 8_000

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


def optimization_corpus(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    since: str = "", include_archived: bool = False, limit: int = 500,
    offset: int = 0, full: bool = False, subtree: bool = True,
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
    Beyond `limit`, a page also ends early when the serialized listing
    reaches CORPUS_CHAR_BUDGET -- the guarantee is that ONE response
    always fits an MCP host's output cap, whatever the store looks like.
    A `stats` block aggregates the filtered corpus regardless of limit,
    `domain_hints` clusters likely-variant domain strings,
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
    today = today_iso()
    base_where_sql, base_params = " ".join(where), list(params)
    if since:
        where.append("AND updated_at >= ?")
        params.append(since)
    where_sql = " ".join(where)

    rows = conn.execute(
        f"""SELECT uid, type, domain, also_domains, session, tags, content, status,
                   confidence, superseded_by, created_at, updated_at,
                   review_after, source_ref
            FROM memories WHERE {where_sql}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    mems = []
    budget_used = 0
    for r in rows:
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
        mems.append(m)
        budget_used += len(json.dumps(m, ensure_ascii=False))
        if budget_used >= CORPUS_CHAR_BUDGET:
            break
    uids = {m["uid"] for m in mems}

    # What each one has been worth, omitted when zero like every default here; a memory with no
    # `recalls` is the interesting case.
    usage = usage_for(conn, uids)
    for m in mems:
        u = usage.get(m["uid"])
        if u:
            m["recalls"], m["last_recall"] = u["recalls"], u["last_recall"][:19]
    rels = conn.execute(
        "SELECT id, from_uid, to_uid, relation_type FROM relations"
    ).fetchall()
    edges = [dict(r) for r in rels if r["from_uid"] in uids or r["to_uid"] in uids]

    # stats over the WHOLE filtered corpus (not just the LIMIT window)
    total = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params).fetchone()[0]
    def agg(col: str) -> dict:
        return dict(conn.execute(
            f"SELECT {col}, COUNT(*) FROM memories WHERE {where_sql} GROUP BY {col} ORDER BY COUNT(*) DESC",
            params).fetchall())
    by_domain = agg("domain")
    stats = {
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

    # Leaked calls over the scan's own window, not the page: pagination would hide findings.
    leaked, leaked_total = _leak_findings(conn, where_sql, params)
    stats["leaked_calls"] = leaked_total

    return {
        "memories": mems,
        "relations": edges,
        "count": len(mems),
        "offset": offset,
        "truncated": offset + len(mems) < total,
        "stats": stats,
        "domain_hints": hints,
        "domain_nesting": nesting,
        "leaked_calls": leaked,
    }


def _memory_exists(conn: sqlite3.Connection, uid: str | None) -> bool:
    return bool(uid) and get_memory(conn, uid) is not None


def _generated_content_error(conn: sqlite3.Connection, uid: str | None) -> str | None:
    """The free-text editors' refusal, for the suggestion kinds that rewrite content.

    A diagram's or a task's content is the projection of its rows, so a
    hand-authored body applied over it matches no row and survives only
    until the next structural edit regenerates it (see is_diagram).
    """
    row = get_memory(conn, uid) if uid else None
    if row is None:
        return None
    if row["type"] == DIAGRAM_TYPE:
        return (f"{uid} is a diagram: its content is generated from the graph. "
                "Use diagram_node/diagram_edge to change the flow.")
    if row["type"] == TASK_TYPE:
        return (f"{uid} is a task: its content is generated from the goal and items. "
                "Change them through the task tools.")
    return None


def _validate_suggestion(conn: sqlite3.Connection, s: object) -> tuple[dict | None, str | None]:
    """Return (normalized_row, error). error is a human-readable string or None."""
    if not isinstance(s, dict):
        return None, "suggestion must be an object"
    kind = str(s.get("kind", "")).strip()
    if kind not in SUGGESTION_KINDS:
        return None, f"unknown kind {kind!r} (allowed: {', '.join(SUGGESTION_KINDS)})"
    payload = s.get("payload") or {}
    if not isinstance(payload, dict):
        return None, "payload must be an object"
    target_uid = (str(s.get("target_uid", "")) or "").strip() or None
    rationale = str(s.get("rationale", "")).strip()
    verified = str(s.get("verified", "")).strip()

    def target_err() -> str | None:
        return None if _memory_exists(conn, target_uid) else f"target_uid not found: {target_uid!r}"

    if kind in ("compact", "reword"):
        err = target_err() or _generated_content_error(conn, target_uid)
        if err:
            return None, err
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        # checked at staging, so nothing in the human's queue is waiting to fail on apply
        row = memory_row(conn, target_uid)
        err = section_error(conn, row["type"], str(payload["new_content"]))
        if err:
            return None, err
    elif kind == "unleak":
        err = target_err()
        if err:
            return None, err
        field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
        if field not in LEAK_FIELDS:
            return None, (f"payload.field must be one of: {', '.join(LEAK_FIELDS)}; "
                          f"got {field!r}")
        if field == "content":
            err = _generated_content_error(conn, target_uid)
            if err:
                return None, err
        row = memory_row(conn, target_uid)
        text = row[field] or ""
        if not guard.leak_marks(row["type"], text):
            return None, f"nothing leaked in {field} of {target_uid}: no marks to remove"
        # the repair is computed HERE and travels in the payload, so the caller never retypes the
        # body, which is the defect this kind cleans up
        clean, _ = guard.strip_leak(row["type"], text)
        left = guard.leak_marks(row["type"], clean)
        if left:
            return None, (f"{field} of {target_uid} still carries {', '.join(left)} "
                          "after the pass -- the marks are inside its prose. Rewrite "
                          "it with a reword instead")
        if field == "content":
            err = section_error(conn, row["type"], clean)
            if err:
                return None, err
        payload = {"field": field, "new_text": clean}
    elif kind == "retag":
        err = target_err()
        if err:
            return None, err
        if "tags" not in payload:
            return None, "payload.tags required"
    elif kind == "retitle":
        err = target_err()
        if err:
            return None, err
        if not str(payload.get("title", "")).strip():
            return None, "payload.title required (a memory cannot be left unnamed)"
        too_long = title_error(str(payload["title"]))
        if too_long:
            return None, too_long
        if is_diagram(conn, target_uid):
            return None, (f"{target_uid} is a diagram: its title is part of what "
                          "generates its body. Rename it through the graph.")
    elif kind == "review":
        err = target_err()
        if err:
            return None, err
        if "review_after" not in payload:
            return None, "payload.review_after required ('' clears the date)"
        # normalized at staging, as redomain is: the panel shows it as what will hold, and '90d'
        # means a different day depending on when it is read
        try:
            payload = {**payload,
                       "review_after": normalize_review_after(str(payload["review_after"]))}
        except ValueError as exc:
            return None, str(exc)
    elif kind == "redomain":
        err = target_err()
        if err:
            return None, err
        if "domain" not in payload:
            return None, "payload.domain required"
        # normalized at staging too: the panel must show the path the memory will end up in
        payload = {**payload, "domain": normalize_domain(str(payload["domain"]))}
    elif kind == "crosslist":
        err = target_err()
        if err:
            return None, err
        if "also" not in payload:
            return None, "payload.also required"
        # the whole set is REPLACED, so staging runs the apply's policy (casing, path shape, dropping
        # a path the own domain covers) and the panel shows what will hold
        row = memory_row(conn, target_uid)
        given = parse_domains(payload["also"])
        want = apply_link_policy(conn, given, row["domain"])
        # an empty list is a legitimate suggestion, but a non-empty one that empties would apply as
        # a clear, so say so instead
        if given and not want:
            return None, (
                f"every path given is already covered by the memory's domain "
                f"{row['domain']!r}: {', '.join(given)}")
        payload = {**payload, "also": want}
    elif kind == "set_confidence":
        err = target_err()
        if err:
            return None, err
        if payload.get("confidence") not in CONFIDENCE_VALUES:
            return None, f"payload.confidence must be one of {CONFIDENCE_VALUES}"
        if payload["confidence"] == "contradicted" and not verified:
            return None, "verified required: describe the live-facts check that contradicts this memory"
    elif kind == "archive":
        err = target_err()
        if err:
            return None, err
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
    elif kind == "link":
        f = (str(payload.get("from_uid", "")) or "").strip()
        t = (str(payload.get("to_uid", "")) or "").strip()
        if not _memory_exists(conn, f):
            return None, f"payload.from_uid not found: {f!r}"
        if not _memory_exists(conn, t):
            return None, f"payload.to_uid not found: {t!r}"
        if f == t:
            return None, "cannot link a memory to itself"
        if not str(payload.get("relation_type", "")).strip():
            return None, "payload.relation_type required"
        if target_uid and target_uid != f:
            return None, "link derives target_uid from payload.from_uid; omit target_uid or make them match"
        target_uid = f
    elif kind == "merge":
        keep = (str(payload.get("keep_uid", "")) or "").strip()
        drop = (str(payload.get("drop_uid", "")) or "").strip()
        if not _memory_exists(conn, keep):
            return None, f"payload.keep_uid not found: {keep!r}"
        if not _memory_exists(conn, drop):
            return None, f"payload.drop_uid not found: {drop!r}"
        if keep == drop:
            return None, "cannot merge a memory with itself"
        if target_uid and target_uid != drop:
            return None, "merge derives target_uid from payload.drop_uid; omit target_uid or make them match"
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
        target_uid = drop
    elif kind == "distill":
        if target_uid:
            return None, "distill creates a new memory; omit target_uid"
        extra = sorted(k for k in payload if k not in DISTILL_PAYLOAD_KEYS)
        if extra:
            return None, (f"payload keys not accepted by distill: {', '.join(extra)} "
                          f"(allowed: {', '.join(DISTILL_PAYLOAD_KEYS)})")
        sources = payload.get("source_uids")
        if not isinstance(sources, list) or not sources:
            return None, "payload.source_uids must be a non-empty list"
        sources = [str(u).strip() for u in sources]
        if len(set(sources)) != len(sources):
            return None, "payload.source_uids contains duplicates"
        for u in sources:
            if not _memory_exists(conn, u):
                return None, f"payload.source_uids not found: {u!r}"
            if is_diagram(conn, u):
                return None, (f"{u} is a diagram: distill archives its sources. "
                              "Use archive to retire a flow on its own.")
            if memory_row(conn, u)["type"] == TASK_TYPE:
                return None, (f"{u} is a task: distill archives its sources, and a task "
                              "closes through its own items.")
        if payload.get("new_type") not in DISTILL_TYPES:
            return None, f"payload.new_type must be one of {DISTILL_TYPES}"
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        # no writing tool names this memory afterwards, so a distill with no title stays unnamed
        if not str(payload.get("title", "")).strip():
            return None, "payload.title required (the distilled memory needs a name)"
        too_long = title_error(str(payload["title"]))
        if too_long:
            return None, too_long
        # anti_pattern is a distill target and is made of fields, so the body
        # a distill writes has to read back the same way any other one does
        err = section_error(conn, str(payload["new_type"]), str(payload["new_content"]))
        if err:
            return None, err
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
        payload = {**payload, "source_uids": sources}

    return {
        "kind": kind, "target_uid": target_uid, "payload": payload,
        "rationale": rationale, "verified": verified,
    }, None


# Characters a run note may hold. A longer note is refused, not truncated.
RUN_NOTE_MAX = 250


def stage_optimization(conn: sqlite3.Connection, note: str, suggestions: list) -> dict:
    """Validate a batch of suggestions and write them to a new run.

    Invalid suggestions are skipped and reported in `errors`; only valid
    ones are staged. Returns {run_id, staged, errors}. No run is created
    when nothing validates.

    `note` summarises the run in at most RUN_NOTE_MAX characters; a longer
    one raises and nothing is staged.
    """
    if not isinstance(suggestions, list) or not suggestions:
        raise ValueError("suggestions must be a non-empty list")
    note = str(note or "")
    if len(note) > RUN_NOTE_MAX:
        raise ValueError(
            f"note is {len(note)} characters; the limit is {RUN_NOTE_MAX}")
    valid, errors = [], []
    for i, s in enumerate(suggestions):
        norm, err = _validate_suggestion(conn, s)
        if err:
            errors.append({"index": i, "error": err})
        else:
            valid.append(norm)
    if not valid:
        return {"run_id": None, "staged": 0, "errors": errors}
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO optimization_runs (created_at, note, status) VALUES (?, ?, 'open')",
        (ts, note),
    )
    run_id = cur.lastrowid
    for v in valid:
        conn.execute(
            """INSERT INTO optimization_suggestions
               (run_id, kind, target_uid, payload, rationale, verified, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (run_id, v["kind"], v["target_uid"], json.dumps(v["payload"]),
             v["rationale"], v["verified"], ts),
        )
    return {"run_id": run_id, "staged": len(valid), "errors": errors}


def list_optimization_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id) AS total,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'pending') AS pending,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'applied') AS applied,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'rejected') AS rejected
           FROM optimization_runs r ORDER BY r.created_at DESC, r.id DESC"""
    ).fetchall()


def optimization_run_kind_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per-run, per-kind suggestion counts across all runs.

    All three states, because a reader of the counts alone has to be able to
    say how many of a kind were APPLIED -- total minus pending minus
    rejected. Without `rejected` a rejected suggestion counts as applied,
    and the day's summary claims work that was turned down.
    """
    return conn.execute(
        """SELECT run_id, kind,
                  COUNT(*) AS total,
                  SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
           FROM optimization_suggestions
           GROUP BY run_id, kind
           ORDER BY run_id, kind"""
    ).fetchall()


def get_optimization_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM optimization_runs WHERE id = ?", (run_id,)).fetchone()


def get_optimization_suggestions(
    conn: sqlite3.Connection, run_id: int, status: str = "", kind: str = ""
) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM optimization_suggestions WHERE run_id = ?"]
    params: list = [run_id]
    if status:
        sql.append("AND status = ?")
        params.append(status)
    if kind:
        sql.append("AND kind = ?")
        params.append(kind)
    sql.append("ORDER BY id ASC")
    return conn.execute(" ".join(sql), params).fetchall()


def get_suggestion(conn: sqlite3.Connection, sug_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM optimization_suggestions WHERE id = ?", (sug_id,)
    ).fetchone()


def set_run_backup(conn: sqlite3.Connection, run_id: int, backup_path: str) -> None:
    conn.execute(
        "UPDATE optimization_runs SET backup_path = ? WHERE id = ?", (backup_path, run_id)
    )


def _update_meta_field(conn: sqlite3.Connection, uid: str, field: str, value: str) -> None:
    """Mirror admin.edit_meta for one tag/domain field: UPDATE + audit.

    `field` is only ever 'tags', 'title' or 'domain' (caller-controlled), so
    the f-string interpolation is not an injection surface.

    A domain change re-runs the cross-listing policy: the memory's new path
    may already satisfy a membership the old path needed (see
    apply_link_policy), and leaving that row would count it twice in its
    own branch.
    """
    if field == "domain":
        value = apply_domain_policy(conn, value)
    row = memory_row(conn, uid)
    conn.execute(
        f"UPDATE memories SET {field} = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid),
    )
    note = f"meta: {field} '{row[field]}' → '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"], note),
    )
    if field == "domain" and row["also_domains"]:
        set_domain_links(conn, uid, get_domain_links(conn, uid))


def _target(kind: str, target_uid: str | None) -> str:
    """The uid a suggestion acts on; link, merge and distill name theirs in the payload."""
    if target_uid:
        return target_uid
    if kind in ("link", "merge", "distill"):
        return ""
    raise ValueError(f"{kind} needs a target_uid")


def _apply_kind(conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict) -> dict:
    """Execute one suggestion and return the prev_state dict for undo."""
    uid = _target(kind, target_uid)
    if kind in ("compact", "reword"):
        # staging refuses these on a diagram or task, but a staged run may still
        # hold one; applying it would write over the projection
        err = _generated_content_error(conn, uid)
        if err:
            raise ValueError(err)
        row = memory_row(conn, uid)
        prev = {"content": row["content"]}
        update_memory_content(conn, uid, payload["new_content"], note=f"optimize:{kind}")
        return prev
    if kind == "unleak":
        field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
        if field == "content":
            err = _generated_content_error(conn, uid)
            if err:
                raise ValueError(err)
        row = memory_row(conn, uid)
        prev = {field: row[field]}
        text = str(payload["new_text"])
        if field == "content":
            update_memory_content(conn, uid, text, note=f"optimize:{kind}")
        else:
            _update_meta_field(conn, uid, field, text)
        return prev
    if kind == "retag":
        row = memory_row(conn, uid)
        prev = {"tags": row["tags"]}
        _update_meta_field(conn, uid, "tags", str(payload["tags"]).strip())
        return prev
    if kind == "retitle":
        row = memory_row(conn, uid)
        prev = {"title": row["title"]}
        _update_meta_field(conn, uid, "title", str(payload["title"]).strip())
        return prev
    if kind == "review":
        row = memory_row(conn, uid)
        prev = {"review_after": row["review_after"]}
        set_review_after(conn, uid, str(payload["review_after"]))
        return prev
    if kind == "redomain":
        row = memory_row(conn, uid)
        prev = {"domain": row["domain"]}
        _update_meta_field(conn, uid, "domain", str(payload["domain"]).strip())
        return prev
    if kind == "crosslist":
        # the whole set, not an addition: undo restores exactly this list
        prev = {"also": get_domain_links(conn, uid)}
        set_domain_links(conn, uid, payload["also"], note=f"optimize:{kind}")
        return prev
    if kind == "set_confidence":
        row = memory_row(conn, uid)
        prev = {"confidence": row["confidence"]}
        set_confidence(conn, uid, payload["confidence"])
        return prev
    if kind == "archive":
        row = memory_row(conn, uid)
        prev = {"status": row["status"], "superseded_by": row["superseded_by"]}
        reason = str(payload.get("reason", "")).strip() or "optimize: archived"
        set_status(conn, uid, "archived", note=reason)
        return prev
    if kind == "link":
        rid = add_relation(
            conn, payload["from_uid"].strip(), payload["to_uid"].strip(),
            str(payload["relation_type"]).strip(), str(payload.get("note", "")).strip(),
        )
        return {"relation_id": rid}
    if kind == "merge":
        keep, drop = payload["keep_uid"].strip(), payload["drop_uid"].strip()
        drow = memory_row(conn, drop)
        prev = {"drop_status": drow["status"], "drop_superseded_by": drow["superseded_by"]}
        rid = add_relation(conn, keep, drop, "supersedes", str(payload.get("note", "")).strip())
        prev["relation_id"] = rid
        set_status(conn, drop, "archived", superseded_by=keep, note="optimize: merged")
        return prev
    if kind == "distill":
        new_uid = insert_memory(
            conn, type=payload["new_type"], content=payload["new_content"],
            # staging requires a title; .get keeps a run staged before it did
            # appliable rather than failing here
            title=str(payload.get("title", "")).strip(),
            tags=str(payload.get("tags", "")).strip(),
            domain=str(payload.get("domain", "")).strip(),
        )
        prev = {"new_uid": new_uid, "relation_ids": [], "sources": []}
        for u in payload["source_uids"]:
            row = memory_row(conn, u)
            prev["sources"].append(
                {"uid": u, "status": row["status"], "superseded_by": row["superseded_by"]})
            prev["relation_ids"].append(
                add_relation(conn, new_uid, u, "supersedes", "optimize: distilled"))
            set_status(conn, u, "archived", superseded_by=new_uid,
                       note=f"optimize: distilled into {new_uid}")
        return prev
    raise ValueError(f"unknown kind: {kind}")


def _revert_kind(
    conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict, prev: dict
) -> None:
    uid = _target(kind, target_uid)
    if kind in ("compact", "reword"):
        update_memory_content(conn, uid, prev["content"], note=f"optimize:undo {kind}")
    elif kind == "unleak":
        field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
        if field == "content":
            # restores a leaked body that writers are refused: an undo puts back what was there
            update_memory_content(conn, uid, prev["content"],
                                  note=f"optimize:undo {kind}", leaked_ok=True)
        else:
            _update_meta_field(conn, uid, field, prev[field])
    elif kind == "retag":
        _update_meta_field(conn, uid, "tags", prev["tags"])
    elif kind == "retitle":
        _update_meta_field(conn, uid, "title", prev["title"])
    elif kind == "review":
        set_review_after(conn, uid, prev["review_after"])
    elif kind == "redomain":
        _update_meta_field(conn, uid, "domain", prev["domain"])
    elif kind == "crosslist":
        set_domain_links(conn, uid, prev["also"], coerce=False,
                         note="optimize:undo crosslist")
    elif kind == "set_confidence":
        set_confidence(conn, uid, prev["confidence"])
    elif kind == "archive":
        set_status(conn, uid, prev["status"],
                   superseded_by=prev.get("superseded_by"), note="optimize: undo archive")
    elif kind == "link":
        conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
    elif kind == "merge":
        conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
        set_status(conn, payload["drop_uid"].strip(), prev["drop_status"],
                   superseded_by=prev.get("drop_superseded_by"), note="optimize: undo merge")
    elif kind == "distill":
        for s in prev.get("sources", []):
            set_status(conn, s["uid"], s["status"],
                       superseded_by=s.get("superseded_by"), note="optimize: undo distill")
        # purge (not archive) the distilled memory: it was born from this
        # apply, so undo removes it entirely; its relations go with it
        if prev.get("new_uid"):
            purge_memory(conn, prev["new_uid"])
    else:
        raise ValueError(f"unknown kind: {kind}")


def apply_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] != "pending":
        raise ValueError(f"suggestion already {row['status']}")
    payload = json.loads(row["payload"])
    prev = _apply_kind(conn, row["kind"], row["target_uid"], payload)
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'applied', prev_state = ?, decided_at = ? WHERE id = ?",
        (json.dumps(prev), now_iso(), sug_id),
    )
    return True


def reject_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] == "applied":
        raise ValueError("cannot reject an applied suggestion; revert it first")
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'rejected', decided_at = ? WHERE id = ?",
        (now_iso(), sug_id),
    )
    return True


def revert_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    """Put a decided suggestion back on the table.

    An APPLIED one is undone in the store first, from the prev_state its
    apply recorded. A REJECTED one wrote nothing to any memory, so taking
    the answer back is only a change of status.

    Raises ValueError for an unknown id and for one that is already pending.
    """
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] == "pending":
        raise ValueError("suggestion is already pending")
    if row["status"] == "applied":
        payload = json.loads(row["payload"])
        prev = json.loads(row["prev_state"]) if row["prev_state"] else {}
        _revert_kind(conn, row["kind"], row["target_uid"], payload, prev)
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'pending', prev_state = NULL, decided_at = NULL WHERE id = ?",
        (sug_id,),
    )
    return True


def delete_optimization_run(conn: sqlite3.Connection, run_id: int) -> bool:
    conn.execute("DELETE FROM optimization_suggestions WHERE run_id = ?", (run_id,))
    cur = conn.execute("DELETE FROM optimization_runs WHERE id = ?", (run_id,))
    return cur.rowcount > 0
