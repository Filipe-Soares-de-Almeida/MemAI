"""Memories: the list, one record, creating and editing, purges, bulk edits and lookup."""

from __future__ import annotations

import sqlite3
from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db, sections, tasks
from memai.admin.api import api
from memai.admin.routes.diagrams import _diagram_json
from memai.admin.routes.tasks import _task_view
from memai.admin.shared import (
    BULK_MAX,
    STATUSES,
    _backup,
    _int_param,
    _paths,
    _peer_card,
    _scope_echo,
    _section_spec,
    _snip,
    _summary,
)
from memai.store import queries

KNOWN_TYPES = db.MEMORY_TYPES

# What the dashboard's new-memory form writes. A handoff stays a type to read and
# edit; a diagram is created with its graph and a task with its items.
CREATABLE_TYPES = tuple(
    t for t in db.MEMORY_TYPES if t not in ("handoff", db.DIAGRAM_TYPE, db.TASK_TYPE))

CONFIDENCES = db.CONFIDENCE_VALUES


def _with_usage(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Attach recall counts to rows that were selected without the join."""
    usage = db.usage_for(conn, [i["uid"] for i in items])
    for i in items:
        u = usage.get(i["uid"])
        i["recalls"] = u["recalls"] if u else 0
        i["last_recall"] = u["last_recall"] if u else None
    return items


def _with_tasks(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Give the rows that are tasks their `progress` ({done, total}) and `task_state`."""
    progress = queries.task_progress(
        conn, [i["uid"] for i in items if i.get("type") == db.TASK_TYPE])
    for i in items:
        if i["uid"] in progress:
            state, done, total = progress[i["uid"]]
            i["progress"] = {"done": done, "total": total}
            i["task_state"] = state
    return items


def list_memories(request, payload) -> schema.MemoryPage:
    f = queries.MemoryFilter.from_query_params(request.query_params)
    with db.connect() as conn:
        scope = _scope_echo(conn, f.domain)
        total, rows = queries.list_memories(conn, f)
        items = [_summary(r) for r in rows]
        if f.q:
            items = _with_usage(conn, items)
        items = _with_tasks(conn, items)
    return cast(schema.MemoryPage, {"total": total, "items": items, "searched": bool(f.q),
                                    **scope})


def memory_detail(request, payload) -> schema.MemoryRecord:
    uid = request.path_params["uid"]
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            raise ValueError(f"unknown memory: {uid}")
        result = _paths(dict(row))
        usage = db.usage_for(conn, [uid]).get(uid)
        result["recalls"] = usage["recalls"] if usage else 0
        result["last_recall"] = usage["last_recall"] if usage else None
        result["edit_history"] = [dict(e) for e in db.get_edit_history(conn, uid)]
        # `spec` is what the type should hold and `sections` what the body yielded, so a view
        # can show a missing field instead of a gap
        result["spec"] = [_section_spec(s) for s in sections.spec_for(row["type"])]
        result["sections"] = db.get_sections(conn, uid)
        result["section_problem"] = db.section_problem(conn, uid)
        result["body_links"] = db.body_links(conn, uid, row["content"])
        rels = []
        for r in db.get_relations(conn, uid):
            other = r["to_uid"] if r["from_uid"] == uid else r["from_uid"]
            rels.append({
                **dict(r),
                "direction": "out" if r["from_uid"] == uid else "in",
                "peer": _peer_card(conn, other) or {"uid": other, "missing": True},
            })
        result["relations"] = rels
        if result.get("superseded_by"):
            result["superseded_by_peer"] = _peer_card(conn, result["superseded_by"])
        if row["type"] == db.TASK_TYPE:
            result["task"] = _task_view(conn, uid)
        if row["type"] == db.DIAGRAM_TYPE:
            result["diagram"] = _diagram_json(conn, uid)
        else:
            result["referenced_by_diagrams"] = [
                dict(r) for r in db.diagrams_referencing(conn, uid)
            ]
    return cast(schema.MemoryRecord, result)


def create_memory(request, payload) -> schema.MemoryCreated:
    type_ = (payload.get("type") or "").strip()
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()
    confidence = payload.get("confidence") or "unverified"
    if not type_:
        raise ValueError("type is required")
    if not title:
        raise ValueError("title is required")
    if type_ not in KNOWN_TYPES:
        raise ValueError(f"type must be one of {CREATABLE_TYPES}")
    if type_ == db.DIAGRAM_TYPE:
        # a diagram row with no graph behind it is a broken half-state: its
        # content is generated, so there would be nothing to generate from
        raise ValueError("create a diagram through POST /api/diagrams -- it needs a graph")
    if type_ == db.TASK_TYPE:
        # a task row with no tasks row behind it has no checklist to generate from
        raise ValueError("a task is created through POST /api/tasks, not as a plain memory")
    if type_ not in CREATABLE_TYPES:
        raise ValueError(f"a {type_} is not created from the dashboard; "
                         f"type must be one of {CREATABLE_TYPES}")
    if sections.is_sectioned(type_):
        # built from the fields rather than typed, so what lands conforms
        given = payload.get("sections")
        if not isinstance(given, dict):
            raise ValueError(f"a {type_} is created from its sections, not from content")
        empty = [s.label for s in sections.spec_for(type_)
                 if not str(given.get(s.key, "")).strip()]
        if empty:
            raise ValueError(f"nothing under {', '.join(empty)}")
        content = sections.render(type_, {k: str(v) for k, v in given.items()})
    if not content:
        raise ValueError("content is required")
    if confidence not in CONFIDENCES:
        raise ValueError(f"confidence must be one of {CONFIDENCES}")
    with db.connect() as conn:
        uid = db.insert_memory(
            conn, type=type_, content=content, title=title,
            domain=(payload.get("domain") or "").strip(),
            also=payload.get("also") or "",
            session=(payload.get("session") or "").strip(),
            tags=(payload.get("tags") or "").strip(),
            confidence=confidence,
        )
        return {"uid": uid, "also": db.get_domain_links(conn, uid)}


def edit_content(request, payload) -> schema.Ok:
    uid = request.path_params["uid"]
    content = payload.get("content", "")
    if not content.strip():
        raise ValueError("content cannot be empty")
    with db.connect() as conn:
        if db.is_diagram(conn, uid):
            raise ValueError(
                "this memory is a diagram: its content is generated from the graph, "
                "so a hand-written version would be overwritten by the next change. "
                "Edit the flow instead."
            )
        if tasks.is_task(conn, uid):
            raise ValueError(
                "this memory is a task: its content is generated from the goal and "
                "items, so a hand-written version would be overwritten by the next "
                "change. Edit the goal or the items instead."
            )
        ok = db.update_memory_content(conn, uid, content, note=payload.get("note", ""))
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def edit_meta(request, payload) -> schema.MetaSaved:
    """Update domain/also/tags/session/type. Every change leaves an audit
    entry in edits, so curation stays traceable.

    `also` is the set of extra domains the memory belongs to, replaced whole
    -- a list, or one string of comma-separated paths. It is applied AFTER a
    domain change in the same request, because the policy that drops a
    redundant cross-listing reads the domain the memory ends up with."""
    uid = request.path_params["uid"]
    allowed = ("title", "domain", "tags", "session", "type", "review_after", "source_ref")
    updates = {k: str(payload[k]).strip() for k in allowed if k in payload}
    if not updates and "also" not in payload:
        raise ValueError(f"nothing to update (fields: {(*allowed, 'also')})")
    if "review_after" in updates:
        # normalized to a date or rejected loudly: free text would never come due
        updates["review_after"] = db.normalize_review_after(updates["review_after"])
    if "type" in updates and not updates["type"]:
        raise ValueError("type cannot be empty")
    if "title" in updates and not updates["title"]:
        raise ValueError("title cannot be empty")
    if "title" in updates:
        error = db.title_error(updates["title"])
        if error:
            raise ValueError(error)
    if "type" in updates and updates["type"] not in KNOWN_TYPES:
        raise ValueError(f"type must be one of {KNOWN_TYPES}")
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            raise ValueError(f"unknown memory: {uid}")
        if updates.get("type") == "handoff" and row["type"] != "handoff":
            # a handoff is read and edited where it is; a new one is a task
            raise ValueError("a memory cannot be retyped to handoff; a task carries "
                             "work to the next session")
        if "type" in updates and updates["type"] != row["type"] and (
            db.DIAGRAM_TYPE in (updates["type"], row["type"])
        ):
            # retyping away from 'diagram' orphans the graph; retyping into
            # it claims a generated content field with nothing generating it
            raise ValueError("a diagram's type cannot be changed")
        if "type" in updates and updates["type"] != row["type"] and (
            db.TASK_TYPE in (updates["type"], row["type"])
        ):
            # a task's content is generated from its rows, and a retyped memory
            # has no rows to generate it from
            raise ValueError("a task's type cannot be changed")
        if "type" in updates:
            # only on the way IN: leaving a type that has fields just drops
            # a cache, but claiming one means the body has to read that way
            error = db.section_error(conn, updates["type"], row["content"])
            if error:
                raise ValueError(error)
        if "domain" in updates:
            updates["domain"] = db.apply_domain_policy(conn, updates["domain"])
        changed: dict[str, object] = {k: v for k, v in updates.items() if v != row[k]}
        if changed:
            db.set_meta_fields(conn, uid, row, changed)
        # a domain change re-runs the link policy even without `also`: the new path may cover a
        # membership the old one needed (db.apply_link_policy)
        before = db.get_domain_links(conn, uid)
        if "also" in payload or ("domain" in changed and before):
            want = payload.get("also", before)
            if db.set_domain_links(conn, uid, want) != before:
                changed["also"] = True
        result = {"ok": True, "changed": list(changed)}
        if "also" in changed:
            result["also"] = db.get_domain_links(conn, uid)
        return cast(schema.MetaSaved, result)


def edit_confidence(request, payload) -> schema.Ok:
    uid = request.path_params["uid"]
    confidence = payload.get("confidence", "")
    if confidence not in CONFIDENCES:
        raise ValueError(f"confidence must be one of {CONFIDENCES}")
    with db.connect() as conn:
        ok = db.set_confidence(conn, uid, confidence)
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def edit_pin(request, payload) -> schema.PinSaved:
    uid = request.path_params["uid"]
    pin = payload.get("pin", "")
    with db.connect() as conn:
        ok = db.set_pin(conn, uid, pin)
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True, "pin": pin}


def edit_status(request, payload) -> schema.Ok:
    uid = request.path_params["uid"]
    status = payload.get("status", "")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    reason = (payload.get("reason") or "").strip()
    verb = "archived" if status == "archived" else "restored"
    with db.connect() as conn:
        ok = db.set_status(
            conn, uid, status,
            superseded_by=(payload.get("superseded_by") or "").strip() or None,
            note=f"{verb}: {reason}" if reason else "")
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def purge(request, payload) -> schema.Ok:
    """Same guardrail as the MCP purge_memory tool: the operator must type
    the literal phrase 'DELETE <uid>' -- the UI never pre-fills it."""
    uid = request.path_params["uid"]
    expected = f"DELETE {uid}"
    if payload.get("confirm", "") != expected:
        raise ValueError(f"confirm phrase must exactly equal '{expected}'")
    with db.connect() as conn:
        ok = db.purge_memory(conn, uid)
    if not ok:
        raise ValueError(f"unknown memory: {uid}")
    return {"ok": True}


def purge_many(request, payload) -> schema.Purged:
    """Permanently delete many memories at once.

    The guardrail is the per-memory purge's, scaled: the operator types the
    literal phrase 'DELETE <n>', n being how many memories are named, and the
    UI never pre-fills it. A copy of the store is taken first, since a bulk
    delete is the one act in the app with no per-row way back.
    """
    uids = payload.get("uids")
    if not (isinstance(uids, list) and uids
            and all(isinstance(u, str) and u for u in uids)):
        raise ValueError("uids must be a non-empty list of strings")
    uids = list(dict.fromkeys(uids))
    if len(uids) > BULK_MAX:
        raise ValueError(f"at most {BULK_MAX} uids per operation")
    expected = f"DELETE {len(uids)}"
    if payload.get("confirm", "") != expected:
        raise ValueError(f"confirm phrase must exactly equal '{expected}'")
    dest = _backup("pre-purge")
    with db.connect() as conn:
        gone = db.purge_memories(conn, uids)
    return cast(schema.Purged, {"ok": True, "backup": dest.name, **gone})


def bulk(request, payload) -> schema.BulkDone:
    uids = payload.get("uids") or []
    action = payload.get("action", "")
    if not isinstance(uids, list) or not uids:
        raise ValueError("uids must be a non-empty list")
    if len(uids) > BULK_MAX:
        raise ValueError(f"at most {BULK_MAX} uids per operation")
    reason = (payload.get("reason") or "").strip()
    value = str(payload.get("value") or "").strip()
    # Validated once, before the loop: an action that would fail on every row
    # must not archive the first forty and then raise on the forty-first.
    if action == "confidence" and value not in CONFIDENCES:
        raise ValueError(f"value must be one of {CONFIDENCES}")
    if action in ("tag", "rehome") and not value:
        raise ValueError(f"{action} needs a value")
    done = 0
    with db.connect() as conn:
        for uid in uids:
            if action == "confidence":
                done += 1 if db.set_confidence(conn, uid, value) else 0
            elif action == "archive":
                done += 1 if db.set_status(
                    conn, uid, "archived",
                    note=f"archived: {reason}" if reason else "") else 0
            elif action == "restore":
                done += 1 if db.set_status(
                    conn, uid, "active",
                    note=f"restored: {reason}" if reason else "") else 0
            elif action == "tag":
                # ADDS: replacing would wipe the synonyms keyword search runs on.
                row = db.get_memory(conn, uid)
                if row is None:
                    continue
                merged = db.merge_tags(row["tags"], value)
                if merged != row["tags"]:
                    done += 1 if db.set_tags(conn, uid, merged, note="bulk") else 0
            elif action == "rehome":
                done += 1 if db.set_domain(conn, uid, value, note="bulk") else 0
            else:
                raise ValueError(
                    "action must be confidence|archive|restore|tag|rehome")
    return {"ok": True, "affected": done}


def lookup(request, payload) -> schema.Lookup:
    """Finder for the memory-link picker in a record and on a diagram step.

    Every field returned is one the picker renders. The operator is
    choosing which memory to point at, and a uid is not something a human
    recognizes -- so domain, status and the retrieval provenance travel
    with the snippet, and the UI is free to show why a row is in the list
    rather than asking the reader to trust the ranking.

    Defaults to active memories. Archived ones are still reachable (the
    picker has a toggle), but they are the exception: linking to something
    already retired is a deliberate act, not the resting state.
    """
    q = request.query_params.get("q", "").strip()
    exclude = request.query_params.get("exclude", "")
    type_ = request.query_params.get("type", "")
    domain = request.query_params.get("domain", "")
    tag = request.query_params.get("tag", "").strip()
    status = request.query_params.get("status", "active")
    limit = _int_param(request, "limit", 20, 1, 50)
    # One row past the cap answers "is there more?" without a second COUNT
    # over the same predicate, and the excluded row costs one more on top.
    fetch = limit + 1 + (1 if exclude else 0)
    with db.connect() as conn:
        if not q:
            rows = [dict(r) for r in db.list_recent(
                conn, type=type_, domain=domain, tag=tag, status=status, limit=fetch)]
        else:
            # A pasted uid names one memory, so it answers past every filter, status included.
            exact = db.get_memory(conn, q)
            # the one row in the list that did not match a word
            rows = [{**dict(exact), "match_source": "uid"}] if exact is not None else \
                db.search_ranked(conn, q, type=type_, domain=domain, tag=tag,
                                 status=status, limit=fetch)
    rows = [r for r in rows if r["uid"] != exclude]
    items = [{
        "uid": r["uid"], "type": r["type"], "domain": r["domain"],
        "status": r["status"], "snippet": _snip(r["content"], 110),
        "match_source": r.get("match_source", ""),
        "fts_rank": r.get("fts_rank"),
    } for r in rows[:limit]]
    return {"items": items, "has_more": len(rows) > limit}


ROUTES = [
    Route("/api/memories", api(list_memories), methods=["GET"]),
    Route("/api/memories", api(create_memory), methods=["POST"]),
    Route("/api/memories/{uid}", api(memory_detail), methods=["GET"]),
    Route("/api/memories/{uid}/content", api(edit_content), methods=["POST"]),
    Route("/api/memories/{uid}/meta", api(edit_meta), methods=["POST"]),
    Route("/api/memories/{uid}/confidence", api(edit_confidence), methods=["POST"]),
    Route("/api/memories/{uid}/pin", api(edit_pin), methods=["POST"]),
    Route("/api/memories/{uid}/status", api(edit_status), methods=["POST"]),
    Route("/api/memories/{uid}/purge", api(purge), methods=["POST"]),
    Route("/api/memories/purge", api(purge_many), methods=["POST"]),
    Route("/api/bulk", api(bulk), methods=["POST"]),
    Route("/api/lookup", api(lookup)),
]
