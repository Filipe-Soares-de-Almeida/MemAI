"""memai MCP server.

Tools for long-term agent memory: note/checkpoint/anti_pattern/
reasoning/handoff to write, search/recall/list_by_domain/list_recent/
list_domains/pulse to read, plus edit history, a relations graph, a
dedup-candidate scanner, confidence/status tracking, and help() for
self-documentation straight from these docstrings. Retrieval is hybrid
FTS5 (BM25) + local model2vec vectors in sqlite-vec, all in one ACID
SQLite file -- both retrievers only narrow candidates, the calling
agent judges relevance.

Writer tool names match the `type` value they store (note stores
type='note', reasoning stores type='reasoning', ...), so what an agent
calls is exactly what search/list_* filter on.
"""

from __future__ import annotations

import inspect
import json

from mcp.server.fastmcp import FastMCP

from memai import db

mcp = FastMCP("memai")


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


SNIPPET_LIMIT = 400
PULSE_NOTES = 5  # recent note()'d facts surfaced as warm-up breadcrumbs

# Memory type tag per writer -- the retrieval tools filter on these exact
# strings (search/recall/list_*(type=...)). Each writer tool is named
# after the type it stores, so tool name and stored type cannot drift.
TYPE_NOTE = "note"                  # note()
TYPE_CHECKPOINT = "checkpoint"      # checkpoint()
TYPE_ANTI_PATTERN = "anti_pattern"  # anti_pattern()
TYPE_REASONING = "reasoning"        # reasoning()
TYPE_HANDOFF = "handoff"            # handoff()


def _snippet_dict(d: dict) -> dict:
    """Truncate content in list-style results so N hits can't blow the
    caller's token budget. Full content is one get_memory(uid) away --
    this only needs to be enough for the agent to judge which
    candidates are worth opening.
    """
    content = d.get("content", "")
    if len(content) > SNIPPET_LIMIT:
        d["content"] = content[:SNIPPET_LIMIT].rstrip() + f"... [+{len(content) - SNIPPET_LIMIT} chars, see get_memory(uid)]"
    return d


def _list_scoped(conn, domain: str, type: str, limit: int) -> list:
    """Recency-ordered rows of one type: scoped to a domain if given, else global."""
    if domain:
        return db.list_by_domain(conn, domain, type=type, limit=limit)
    return db.list_recent(conn, type=type, limit=limit)


def _coerce_domain(conn, domain: str) -> tuple[str, dict | None]:
    """Apply the store's casing policy to a domain before a write.

    Returns (coerced_domain, warning). warning is None when the domain
    already conforms; otherwise it describes the adjustment so the tool
    can echo it back to the agent (coerce-and-warn, never reject).
    """
    coerced, mode = db.coerce_domain(conn, domain)
    if coerced == domain:
        return coerced, None
    return coerced, {"from": domain, "to": coerced, "policy": mode}


@mcp.tool()
def note(content: str, domain: str = "", tags: str = "", session: str = "") -> dict:
    """Save a general long-term memory (fact, decision, finding). Stored as type='note'.

    Timeless knowledge -- retrieved by relevance, not recency. Bring it
    back with recall() (or search(type='note')); pulse() also shows the
    few most recent ones as warm-up breadcrumbs.

    tags: comma-separated keywords/synonyms -- write generously; tags
    feed both the keyword index and the embedding, so they make the
    memory findable even when the vector side is unavailable.
    """
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(conn, type=TYPE_NOTE, content=content, domain=domain, session=session, tags=tags)
    result = {"uid": uid}
    if warning:
        result["domain_adjusted"] = warning
    return result


@mcp.tool()
def checkpoint(
    intent: str,
    established: str,
    pursuing: str,
    open_questions: str,
    session: str = "",
    domain: str = "",
) -> dict:
    """Snapshot current working state (intent/established/pursuing/open_questions).

    A summary of where the work stands, so the next session picks up
    the right bearing via pulse(). Fields are free-length; still prefer
    a readable summary here and put timeless detail into note() --
    checkpoints are read for bearing, not as an archive. Stored as
    type='checkpoint'.
    """
    content = (
        f"INTENT: {intent}\nESTABLISHED: {established}\n"
        f"PURSUING: {pursuing}\nOPEN QUESTIONS: {open_questions}"
    )
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(
            conn, type=TYPE_CHECKPOINT, content=content, domain=domain, session=session,
            tags="checkpoint",
        )
    result = {"uid": uid}
    if warning:
        result["domain_adjusted"] = warning
    return result


@mcp.tool()
def anti_pattern(pattern: str, why_wrong: str, instead: str, domain: str = "", session: str = "") -> dict:
    """Record a mistake/temptation to avoid repeating, and the correct approach.

    Stored as type='anti_pattern'; open ones for a domain are surfaced by pulse().
    """
    content = f"TEMPTATION: {pattern}\nWHY WRONG: {why_wrong}\nINSTEAD: {instead}"
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(
            conn, type=TYPE_ANTI_PATTERN, content=content, domain=domain, session=session,
            tags="anti_pattern",
        )
    result = {"uid": uid}
    if warning:
        result["domain_adjusted"] = warning
    return result


@mcp.tool()
def reasoning(content: str, domain: str = "", session: str = "") -> dict:
    """Record a reasoning trace / analysis worth keeping (not a fact, a thought process).

    Stored as type='reasoning' -- filter search/list_* with
    type='reasoning' to get these back.
    """
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(conn, type=TYPE_REASONING, content=content, domain=domain, session=session)
    result = {"uid": uid}
    if warning:
        result["domain_adjusted"] = warning
    return result


@mcp.tool()
def handoff(content: str, domain: str = "", session: str = "") -> dict:
    """Leave a note for another agent/session picking up this work.

    Stored as type='handoff'; open ones for a domain are surfaced by pulse().
    """
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(conn, type=TYPE_HANDOFF, content=content, domain=domain, session=session)
    result = {"uid": uid}
    if warning:
        result["domain_adjusted"] = warning
    return result


@mcp.tool()
def search(query: str, domain: str = "", type: str = "", limit: int = 30) -> list[dict]:
    """Hybrid search over memory content+tags+domain: BM25 keywords + local-model vectors.

    Each result is annotated with match_source ("fts" | "vec" | "both"),
    fts_rank (bm25, lower = better) and/or vec_distance (cosine, lower =
    closer). Both retrievers only widen the candidate set -- judge the
    returned candidates yourself. Multiple space-separated paraphrases
    still help the keyword side (they're OR'd together). Only active
    memories by default. Falls back to keyword-only if the embedding
    model is unavailable. Content is snippet-truncated per result --
    call get_memory(uid) for the full record.

    type filters (one writer each): 'note', 'reasoning', 'checkpoint',
    'anti_pattern', 'handoff'. To recall note()'d knowledge
    specifically, recall() is the sugar for search(type='note').
    """
    with db.connect() as conn:
        results = db.search_hybrid(conn, query, domain=domain, type=type, limit=limit)
    return [_snippet_dict(r) for r in results]


@mcp.tool()
def recall(query: str, domain: str = "", limit: int = 20) -> list[dict]:
    """Recall long-term knowledge saved with note() (type='note').

    The dedicated verb for "bring back what I noted": a hybrid search
    (BM25 + vectors) scoped to type='note', ranked by relevance -- which
    is what you want for timeless facts/rules/decisions. note() has no
    recency warm-up hook the way checkpoints have pulse(); this (or
    search(type='note')) is how notes come back. Content is
    snippet-truncated -- call get_memory(uid) for the full record.
    """
    with db.connect() as conn:
        results = db.search_hybrid(conn, query, domain=domain, type=TYPE_NOTE, limit=limit)
    return [_snippet_dict(r) for r in results]


@mcp.tool()
def list_by_domain(domain: str, type: str = "", limit: int = 50) -> list[dict]:
    """List active memories for a domain, most recent first. Fallback when search misses.

    Matches domain exactly -- see list_domains() for the real strings in
    use. Content is snippet-truncated per result -- call get_memory(uid)
    for the full record.
    """
    with db.connect() as conn:
        rows = db.list_by_domain(conn, domain, type=type, limit=limit)
    return [_snippet_dict(_row_to_dict(r)) for r in rows]


@mcp.tool()
def list_recent(type: str = "", domain: str = "", limit: int = 20) -> list[dict]:
    """List the most recent active memories, optionally filtered by type/domain.

    Content is snippet-truncated per result -- call get_memory(uid) for
    the full record.
    """
    with db.connect() as conn:
        rows = db.list_recent(conn, type=type, domain=domain, limit=limit)
    return [_snippet_dict(_row_to_dict(r)) for r in rows]


@mcp.tool()
def list_domains() -> list[dict]:
    """List distinct domains with their memory count and latest activity.

    Warm-up discovery. domain is free text and drifts over time (e.g.
    'PROJ-1042' vs 'proj-1042'), and pulse/list_by_domain match it
    exactly -- this surfaces the real strings so you target the right
    one instead of guessing. Ordered by most recent activity.

    Casing may be enforced store-wide -- call get_domain_case() to see
    the active policy before coining a new domain.
    """
    with db.connect() as conn:
        rows = db.list_domains(conn)
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def get_domain_case() -> dict:
    """Report the store's domain-casing policy.

    Returns {"mode": "preserve"|"lower"|"upper"}. 'preserve' stores
    domains as written; 'lower'/'upper' coerce every domain to that case
    on write (a non-conforming domain is adjusted, not rejected, and the
    writer's result carries a `domain_adjusted` note). Read this before
    coining a new domain so its casing matches what will be stored.
    """
    with db.connect() as conn:
        return {"mode": db.get_domain_case(conn)}


@mcp.tool()
def set_domain_case(mode: str) -> dict:
    """Set the store's domain-casing policy. mode: 'preserve' | 'lower' | 'upper'.

    'preserve' keeps free-text casing; 'lower'/'upper' coerce every
    domain written from now on to that case. This only governs new
    writes -- to bring already-stored domains into line, run the
    "Normalize domains" action in the admin dashboard (it previews
    collisions before merging variant spellings). Returns the stored
    {"mode": ...}.
    """
    with db.connect() as conn:
        return {"mode": db.set_domain_case(conn, mode)}


@mcp.tool()
def pulse(domain: str = "") -> dict:
    """Session warm-up: latest checkpoint + open handoffs/anti-patterns + recent notes.

    Picks the checkpoint by created_at DESC, never by similarity --
    a similarity-ranked top-1 can return a stale checkpoint over a
    same-day one, which is exactly the failure mode this avoids.
    latest_checkpoint is returned in full (that's the point of pulse),
    with its relations attached so linked memories are visible without
    a separate get_relations call. handoffs and anti_patterns are notes
    left for whoever resumes; recent_notes are the newest note()'d
    facts, as recency breadcrumbs -- for relevance-ranked recall use
    recall()/search(). Those three lists are snippet-truncated -- call
    get_memory(uid) for one in full.
    """
    with db.connect() as conn:
        latest_checkpoint = db.latest_by_type(conn, TYPE_CHECKPOINT, domain=domain)
        handoffs = _list_scoped(conn, domain, TYPE_HANDOFF, 5)
        anti_patterns = _list_scoped(conn, domain, TYPE_ANTI_PATTERN, 10)
        recent_notes = _list_scoped(conn, domain, TYPE_NOTE, PULSE_NOTES)
        checkpoint_dict = _row_to_dict(latest_checkpoint)
        if checkpoint_dict:
            checkpoint_dict["relations"] = [_row_to_dict(r) for r in db.get_relations(conn, checkpoint_dict["uid"])]
    return {
        "latest_checkpoint": checkpoint_dict,
        "handoffs": [_snippet_dict(_row_to_dict(r)) for r in handoffs],
        "anti_patterns": [_snippet_dict(_row_to_dict(r)) for r in anti_patterns],
        "recent_notes": [_snippet_dict(_row_to_dict(r)) for r in recent_notes],
    }


@mcp.tool()
def get_memory(uid: str) -> dict:
    """Fetch a single memory's full record, including its edit history and relations."""
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            return {}
        edits = db.get_edit_history(conn, uid)
        rels = db.get_relations(conn, uid)
    result = _row_to_dict(row)
    result["edit_history"] = [_row_to_dict(e) for e in edits]
    result["relations"] = [_row_to_dict(r) for r in rels]
    return result


@mcp.tool()
def edit_memory(uid: str, new_content: str, note: str = "") -> dict:
    """Correct/update a memory's content, keeping the previous version in edit history.

    Corrections are common in append-only memory stores that only
    support delete, not edit; this preserves the old content instead
    of losing it.
    """
    with db.connect() as conn:
        ok = db.update_memory_content(conn, uid, new_content, note=note)
    return {"ok": ok}


@mcp.tool()
def link_memories(from_uid: str, to_uid: str, relation_type: str, note: str = "") -> dict:
    """Create a queryable edge between two memories.

    relation_type is free text but keep it consistent, e.g.
    'supersedes', 'relates_to', 'contradicts', 'links_to'.
    """
    with db.connect() as conn:
        rel_id = db.add_relation(conn, from_uid, to_uid, relation_type, note=note)
    return {"relation_id": rel_id}


@mcp.tool()
def get_relations(uid: str) -> list[dict]:
    """List all relations (incoming and outgoing) for a memory."""
    with db.connect() as conn:
        rows = db.get_relations(conn, uid)
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def set_confidence(uid: str, confidence: str) -> dict:
    """Set a memory's confidence: unverified | confirmed | contradicted."""
    if confidence not in ("unverified", "confirmed", "contradicted"):
        return {"ok": False, "error": "confidence must be unverified|confirmed|contradicted"}
    with db.connect() as conn:
        ok = db.set_confidence(conn, uid, confidence)
    return {"ok": ok}


@mcp.tool()
def forget(uid: str, reason: str = "", superseded_by: str = "") -> dict:
    """Archive a memory (soft delete -- content is kept, just excluded from default search/list).

    A `reason` is recorded as a status-change audit entry (without touching
    the content or recomputing its embedding).
    """
    with db.connect() as conn:
        ok = db.set_status(
            conn, uid, "archived",
            superseded_by=superseded_by or None,
            note=f"archived: {reason}" if reason else "",
        )
    return {"ok": ok}


@mcp.tool()
def purge_memory(uid: str, confirm_phrase: str) -> dict:
    """PERMANENTLY delete a memory + its edit history + relations. Irreversible.

    Use forget() instead unless the user explicitly asked to permanently
    remove data -- forget() is reversible (archived, content kept),
    this is not. Guardrail: confirm_phrase must exactly equal
    "DELETE <uid>", typed by the user in their own message. Do not
    construct this string yourself from an inferred "yes"/"confirm" --
    it must come from the user actually stating the uid back.
    """
    expected = f"DELETE {uid}"
    if confirm_phrase != expected:
        return {"ok": False, "error": f"confirm_phrase must exactly equal '{expected}'"}
    with db.connect() as conn:
        ok = db.purge_memory(conn, uid)
    return {"ok": ok}


@mcp.tool()
def dedup_scan(domain: str = "", type: str = "", threshold: float = 0.6, limit: int = 20) -> list[dict]:
    """Surface likely-duplicate/contradictory memory pairs.

    Semantic (cosine over the embedded store) when vectors are available,
    lexical overlap otherwise -- each pair carries its `method`. Same-
    domain/session checkpoint pairs are excluded (timelines, not dups)
    and checkpoint pairs rank below durable-type pairs. Not an automatic
    merge -- returns candidate pairs + similarity score for the agent to
    review and decide (link_memories / edit_memory / forget as
    appropriate).
    """
    with db.connect() as conn:
        pairs = db.dedup_candidates(conn, domain=domain, type=type, threshold=threshold, limit=limit)
    return [
        {"a": _row_to_dict(a), "b": _row_to_dict(b), "ratio": round(score, 3), "method": method}
        for a, b, score, method in pairs
    ]


@mcp.tool()
def optimize_scan(
    domain: str = "", type: str = "", since: str = "",
    include_archived: bool = False, limit: int = 500, offset: int = 0,
    full: bool = False,
) -> dict:
    """Dump the memory corpus compactly so you can plan a curation pass.

    Step 1 of the "optimize my memories" workflow. Returns every memory's
    curation-relevant fields, the relation edges among them, and
    dedup-candidate pairs as a starting hint. Read this, then decide what
    to compact/reword/retag/redomain/set_confidence/archive/link/merge/
    distill and stage it with optimize_stage.

    The listing is slim on purpose so a few-hundred-memory store fits one
    response: content is a ~120-char snippet plus `content_len` (tags cut
    at ~100 with `tags_len`); empty/default fields are omitted (incl.
    confidence 'unverified' -- stats keeps the aggregate); created_at
    drops sub-second precision. Pass full=True for whole
    bodies, or fetch one with get_memory(uid) when a snippet is not
    enough. A page also ends early if its serialized size hits an
    internal budget, so one response ALWAYS fits the host's output cap.
    `truncated: true` means the listing stopped before the corpus ended
    -- page onward with offset = offset + count (stats.total is the
    whole corpus).

    On a grown store, prefer INCREMENTAL curation over full-corpus
    passes: `since` limits the scan to memories created or updated
    at/after an ISO timestamp or date ('2026-07-01'), so a recurring
    "optimize my memories" only reviews the delta since the last run
    (optimize_runs shows when that was). Cross-window collisions are
    still caught: dedup_hints probe FROM the new memories against the
    whole store (a new memory duplicating an old one outside the window
    surfaces; old x old pairs are skipped), and domain_hints report any
    store-wide domain cluster the delta touches. Combine with
    domain/type to curate one slice at a time. Also included:
      - stats: totals for the whole filtered corpus (by_type,
        by_confidence, by_domain, empty_domain) -- computed regardless of
        `limit`,
      - domain_hints: clusters of domain-string variants that likely mean
        the same thing (case/separator drift, ticket-id spellings), with
        a suggested canonical -- ready-made redomain candidates,
      - anchors: per memory, the verifiable references found in its FULL
        content (URLs, file paths, table/field identifiers, constants),
        space-joined -- the things to go check against live facts.

    Before proposing any change, CHECK IT AGAINST LIVE FACTS -- do not
    rewrite or archive something that was true then but stale now, and do
    not "correct" something that is still true:
      - cross-check newer memories already in this corpus (supersession /
        contradiction),
      - for code/config memories, verify the anchors against the live repo,
      - for world-facts, web-check current truth.
    Record what you verified in each suggestion's `verified` field --
    destructive suggestions (archive, set_confidence=contradicted) are
    rejected without it.
    """
    with db.connect() as conn:
        corpus = db.optimization_corpus(
            conn, domain=domain, type=type, since=since,
            include_archived=include_archived,
            limit=limit, offset=offset, full=full)
        pairs = db.dedup_candidates(conn, domain=domain, type=type, since=since, limit=20)
    corpus["dedup_hints"] = [
        {"a": a["uid"], "b": b["uid"], "ratio": round(score, 3), "method": method}
        for a, b, score, method in pairs
    ]
    return corpus


@mcp.tool()
def optimize_stage(suggestions: list[dict], note: str = "") -> dict:
    """Stage a batch of curation suggestions for human review in the dashboard.

    Step 2 of the "optimize my memories" workflow. Writes the suggestions
    to a new optimization run; they are NOT applied here -- the user
    reviews and applies/rejects each one in the admin dashboard's
    Optimization tab, where a backup is taken before the first apply and
    every applied change can be undone.

    Each suggestion is an object:
      {"kind": ..., "target_uid": ..., "payload": {...},
       "rationale": "why", "verified": "what live-facts check you did"}

    Kinds and their payload:
      compact / reword   {"new_content": str}
      retag              {"tags": str}                 comma-separated
      redomain           {"domain": str}
      set_confidence     {"confidence": "unverified|confirmed|contradicted"}
      archive            {"reason": str}               soft/reversible; never hard-deletes
      link               {"from_uid", "to_uid", "relation_type", "note"?}
      merge              {"keep_uid", "drop_uid", "note"?}   links supersedes + archives drop
      distill            {"source_uids": [uid, ...], "new_type": "note|reasoning|anti_pattern",
                          "new_content": str, "tags"?, "domain"?}

    distill extracts the durable knowledge out of one or MORE source
    memories into a newly authored one: creates it, links it `supersedes`
    each source and archives the sources (all reversible). Use it to
    retire closed-ticket checkpoints without losing what they taught, or
    as an n-ary merge when the survivor needs synthesized content.

    link/merge derive target_uid from the payload (from_uid / drop_uid)
    and distill creates its target -- omit target_uid for those kinds.
    Destructive suggestions (archive, set_confidence=contradicted,
    distill) require a non-empty `verified` describing the live-facts
    check that justifies them.

    Invalid suggestions are skipped and reported in `errors`; the rest are
    staged. Returns {run_id, staged, errors}.
    """
    with db.connect() as conn:
        result = db.stage_optimization(conn, note, suggestions)
    return result


@mcp.tool()
def optimize_runs() -> list[dict]:
    """List optimization runs with their review progress.

    Read-only companion to optimize_stage: after staging, use this to see
    whether the user has applied/rejected your suggestions in the admin
    dashboard. Each run carries total/pending/applied/rejected counts,
    its note, and the safety-backup path once the first apply happened.
    Applying/rejecting stays in the dashboard by design -- the agent
    proposes, the human disposes.
    """
    with db.connect() as conn:
        rows = db.list_optimization_runs(conn)
    return [dict(r) for r in rows]


@mcp.tool()
def optimize_status(run_id: int) -> dict:
    """Inspect one optimization run: every suggestion and its decision.

    Read-only. Returns the run header plus each suggestion's kind,
    target_uid, payload, rationale, verified, status
    (pending/applied/rejected) and decided_at -- so you can tell which
    proposals landed, follow up on rejected ones, or build on applied
    ones in a later pass.
    """
    with db.connect() as conn:
        run = db.get_optimization_run(conn, run_id)
        if run is None:
            return {"error": f"unknown run: {run_id}"}
        sugs = db.get_optimization_suggestions(conn, run_id)
    return {
        "run": dict(run),
        "suggestions": [
            {**dict(s), "payload": json.loads(s["payload"]) if s["payload"] else {}}
            for s in sugs
        ],
    }


@mcp.tool()
def help(command: str = "") -> dict:
    """Explain the memai tools, read directly from their code docstrings.

    Without arguments: every tool with its one-line summary. With
    command='<name>': that tool's full signature and docstring. The
    docs can't drift from behavior because they ARE the code's own
    docstrings, extracted at call time.
    """
    if not command:
        return {
            "tools": {
                name: (inspect.getdoc(fn) or "").split("\n", 1)[0]
                for name, fn in _TOOLS.items()
            },
            "hint": "call help(command='<name>') for a tool's full signature and documentation",
        }
    fn = _TOOLS.get(command)
    if fn is None:
        return {"error": f"unknown command: {command}", "available": sorted(_TOOLS)}
    return {
        "command": command,
        "signature": f"{command}{inspect.signature(fn)}",
        "doc": inspect.getdoc(fn) or "",
    }


# Registry for help(): the decorated functions themselves, so signatures
# and docstrings are read from the exact code that runs.
_TOOLS = {
    "note": note,
    "checkpoint": checkpoint,
    "anti_pattern": anti_pattern,
    "reasoning": reasoning,
    "handoff": handoff,
    "search": search,
    "recall": recall,
    "list_by_domain": list_by_domain,
    "list_recent": list_recent,
    "list_domains": list_domains,
    "get_domain_case": get_domain_case,
    "set_domain_case": set_domain_case,
    "pulse": pulse,
    "get_memory": get_memory,
    "edit_memory": edit_memory,
    "link_memories": link_memories,
    "get_relations": get_relations,
    "set_confidence": set_confidence,
    "forget": forget,
    "purge_memory": purge_memory,
    "dedup_scan": dedup_scan,
    "optimize_scan": optimize_scan,
    "optimize_stage": optimize_stage,
    "optimize_runs": optimize_runs,
    "optimize_status": optimize_status,
    "help": help,
}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
