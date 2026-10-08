"""Optimization runs as a reviewer meets them: suggestions, the run summary, decisions."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai.admin.api import api
from memai.admin.shared import _backup, _peer_card
from memai.store import connection, corpus, memories, optimizer, queries, sections

# The kinds whose payload rewrites the body, so a character count means
# something for them and for nothing else.
_CONTENT_KINDS = ("compact", "reword")


def _suggestion_json(conn, row) -> dict:
    """Serialize a staged suggestion for the UI, decorated with target/peer cards."""
    d = {
        "id": row["id"], "run_id": row["run_id"], "kind": row["kind"],
        "target_uid": row["target_uid"], "rationale": row["rationale"],
        "verified": row["verified"], "status": row["status"],
        "decided_at": row["decided_at"], "created_at": row["created_at"],
        "payload": json.loads(row["payload"]) if row["payload"] else {},
    }
    if row["target_uid"]:
        target = _peer_card(conn, row["target_uid"])
        if target is not None:
            trow = memories.memory_row(conn, row["target_uid"])
            target["tags"] = trow["tags"]
            target["review_after"] = trow["review_after"]
            # Before is the whole body: a cut snippet against a full After would read as removed
            # text. Once applied, prev_state is the Before, or both panes would show the same.
            if row["kind"] in _CONTENT_KINDS:
                before = trow["content"]
                if row["status"] == "applied" and row["prev_state"]:
                    before = json.loads(row["prev_state"]).get("content", before)
                d["content_before"] = before
                d["chars_before"] = len(before)
                d["chars_after"] = len(d["payload"].get("new_content", ""))
            # An unleak pair is of the one field the payload names, not of the content.
            if row["kind"] == "unleak":
                field = str(d["payload"].get("field", corpus.LEAK_FIELDS[0]))
                before = trow[field] if field in corpus.LEAK_FIELDS else ""
                if row["status"] == "applied" and row["prev_state"]:
                    before = json.loads(row["prev_state"]).get(field, before)
                d["text_before"] = before or ""
                d["chars_before"] = len(d["text_before"])
                d["chars_after"] = len(str(d["payload"].get("new_text", "")))
            # a crosslist suggestion replaces the whole set, so the Before
            # pane needs the whole set, not only the filed path
            target["also"] = memories.get_domain_links(conn, row["target_uid"])
            # Applied: prev_state is the Before. `_revert_kind` keys match this card's fields, so
            # one overlay serves every kind; keys the card lacks are ignored.
            if row["status"] == "applied" and row["prev_state"]:
                prev = json.loads(row["prev_state"])
                for field in ("tags", "title", "domain", "also", "confidence",
                              "review_after", "status"):
                    if field in prev:
                        target[field] = prev[field]
        d["target"] = target
    peers = {}
    for key in ("from_uid", "to_uid", "keep_uid", "drop_uid"):
        uid = d["payload"].get(key)
        if uid:
            peers[key] = _peer_card(conn, uid)
    if peers:
        d["peers"] = peers
    if row["kind"] == "distill":
        d["sources"] = [
            _peer_card(conn, u) or {"uid": u, "missing": True}
            for u in d["payload"].get("source_uids", [])
        ]
        if row["status"] == "applied" and row["prev_state"]:
            d["new_uid"] = json.loads(row["prev_state"]).get("new_uid")
    # One map of what each [[uid]] in this card's prose points at, so the renderer draws links
    # across the bodies and rationale read together.
    prose = "\n".join(p for p in (
        d["rationale"], d.get("content_before", ""),
        str(d["payload"].get("new_content", "")),
    ) if p)
    links = sections.body_links(conn, row["target_uid"] or "", prose)
    if links:
        d["body_links"] = links
    return d


def optimization_runs(request, payload) -> schema.OptimizationRuns:
    with connection.connect() as conn:
        rows = optimizer.list_optimization_runs(conn)
        kind_rows = optimizer.optimization_run_kind_counts(conn)
    kinds_by_run: dict[int, list[dict]] = {}
    for k in kind_rows:
        kinds_by_run.setdefault(k["run_id"], []).append(
            {"kind": k["kind"], "total": k["total"], "pending": k["pending"],
             "rejected": k["rejected"]}
        )
    runs = []
    for r in rows:
        d = dict(r)
        d["kinds"] = kinds_by_run.get(r["id"], [])
        runs.append(d)
    return {"runs": runs}


def optimization_suggestions(request, payload) -> schema.Suggestions:
    """A run's staged suggestions, or those of several runs at once.

    `runs` takes a comma-separated list and is what the calendar's day rail
    asks with: a day holds every run the agent staged that day -- thirteen
    of them on one day of this store -- and the rail decides across all of
    them. It is a list of RUN IDS and not a date on purpose: `created_at` is
    UTC and the calendar's day is the reader's local one, so a date filtered
    here would disagree with the grid that offered it. The client owns which
    runs a day holds; this only serves them.
    """
    runs_param = (request.query_params.get("runs") or "").strip()
    run_ids: list[int] = []
    if runs_param:
        try:
            run_ids = [int(p) for p in runs_param.split(",") if p.strip()]
        except ValueError as exc:
            raise ValueError("runs must be a comma-separated list of ints") from exc
        if not run_ids:
            raise ValueError("runs was empty")
    else:
        try:
            run_ids = [int(request.query_params.get("run", ""))]
        except (TypeError, ValueError) as exc:
            raise ValueError("run (int) or runs (comma-separated ints) required") from exc
    status = request.query_params.get("status", "")
    # one kind at a time, as the dashboard pages them, so a run's other bodies are not fetched
    kind = request.query_params.get("kind", "")
    items, runs = [], []
    with connection.connect() as conn:
        for rid in run_ids:
            run = optimizer.get_optimization_run(conn, rid)
            if run is None:
                raise ValueError(f"unknown run: {rid}")
            runs.append(dict(run))
            for row in optimizer.get_optimization_suggestions(conn, rid, status=status, kind=kind):
                items.append(_suggestion_json(conn, row))
    # `run` stays for the single-run callers that have always read it; `runs`
    # is the whole set, in the order asked for.
    return {"run": runs[0], "runs": runs, "suggestions": items}


def _tag_terms(s: str) -> set[str]:
    return {t.strip().casefold() for t in str(s or "").split(",") if t.strip()}


def _run_ledger(conn: sqlite3.Connection, pending: list) -> dict:
    """What the still-undecided half of a run would do to the store.

    Every figure is COUNTED from the staged payloads next to the rows they
    target -- nothing here projects a future state or scores it. `chars` is
    negative when the run removes text, which is the normal direction.
    """
    uids: set[str] = set()
    domains: set[str] = set()
    relations = confirmed = archived = chars = 0
    for row in pending:
        payload = json.loads(row["payload"]) if row["payload"] else {}
        kind, target = row["kind"], row["target_uid"]
        if target:
            uids.add(target)
            trow = memories.get_memory(conn, target)
            if trow is not None and trow["domain"]:
                domains.add(trow["domain"])
        if kind in _CONTENT_KINDS and target:
            trow = memories.get_memory(conn, target)
            if trow is not None:
                chars += len(payload.get("new_content", "")) - len(trow["content"])
        elif kind == "unleak":
            field = str(payload.get("field", corpus.LEAK_FIELDS[0]))
            trow = memories.get_memory(conn, target) if target else None
            if trow is not None and field in corpus.LEAK_FIELDS:
                chars += len(str(payload.get("new_text", ""))) - len(trow[field] or "")
        elif kind == "redomain":
            domains.add(str(payload.get("domain", "")).strip())
        elif kind == "crosslist":
            domains.update(p for p in payload.get("also", []) if p)
        elif kind == "set_confidence":
            confirmed += payload.get("confidence") == "confirmed"
        elif kind == "archive":
            archived += 1
        elif kind == "link":
            relations += 1
            uids.update(u for u in (payload.get("from_uid"), payload.get("to_uid")) if u)
        elif kind == "merge":
            relations += 1
            archived += 1
            uids.update(u for u in (payload.get("keep_uid"), payload.get("drop_uid")) if u)
        elif kind == "distill":
            sources = [u for u in payload.get("source_uids", []) if u]
            relations += len(sources)
            archived += len(sources)
            uids.update(sources)
            if payload.get("domain"):
                domains.add(str(payload["domain"]).strip())
    active = queries.count_active(conn)
    return {
        "memories": len(uids), "active": active, "domains": len(domains - {""}),
        "relations": relations, "confirmed": confirmed,
        "archived": archived, "chars": chars,
    }


def _group_facts(conn: sqlite3.Connection, kind: str, rows: list) -> dict:
    """The numbers one kind's sentence needs, beyond how many are pending.

    The sentence itself is a catalog string (`op.what.<kind>`): the server
    counts, the dashboard words it in the reader's language.
    """
    payloads = [json.loads(r["payload"]) if r["payload"] else {} for r in rows]
    if kind in _CONTENT_KINDS:
        chars = 0
        for row, payload in zip(rows, payloads, strict=True):
            trow = memories.get_memory(conn, row["target_uid"]) if row["target_uid"] else None
            if trow is not None:
                chars += len(payload.get("new_content", "")) - len(trow["content"])
        return {"chars": chars}
    if kind == "unleak":
        chars = 0
        for row, payload in zip(rows, payloads, strict=True):
            trow = memories.get_memory(conn, row["target_uid"]) if row["target_uid"] else None
            field = str(payload.get("field", corpus.LEAK_FIELDS[0]))
            if trow is not None and field in corpus.LEAK_FIELDS:
                chars += len(str(payload.get("new_text", ""))) - len(trow[field] or "")
        return {"chars": chars}
    if kind == "retag":
        terms = 0
        for row, payload in zip(rows, payloads, strict=True):
            trow = memories.get_memory(conn, row["target_uid"]) if row["target_uid"] else None
            before = _tag_terms(trow["tags"]) if trow is not None else set()
            terms += len(_tag_terms(payload.get("tags", "")) - before)
        return {"terms": terms}
    if kind == "redomain":
        paths = {str(p.get("domain", "")).strip() for p in payloads}
        # `to` names the destination only when the group has exactly one
        return {"paths": len(paths), "to": paths.pop() if len(paths) == 1 else ""}
    if kind == "crosslist":
        return {"paths": len({p for pl in payloads for p in pl.get("also", []) if p})}
    if kind == "set_confidence":
        levels = {p.get("confidence", "") for p in payloads}
        return {"conf": levels.pop() if len(levels) == 1 else ""}
    if kind == "link":
        types = {str(p.get("relation_type", "relates_to")).strip() for p in payloads}
        return {"rel": types.pop() if len(types) == 1 else ""}
    if kind == "distill":
        return {"sources": sum(len(p.get("source_uids", [])) for p in payloads)}
    return {}


def optimization_summary(request, payload) -> schema.OptimizationSummary:
    """The run's own head: how much of it was checked, and what it would do.

    Everything here is read off the staged rows. No projection of the health
    index: one suggestion moves it by 100/active/4 of a point, so a per-group
    or per-suggestion "gain" rounds to zero on every row and a whole run
    reaches +1 at best. The head reports `verified` instead, which varies
    from one suggestion to the next.
    """
    try:
        run_id = int(request.query_params.get("run", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("run query param (int) required") from exc
    with connection.connect() as conn:
        run = optimizer.get_optimization_run(conn, run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        rows = optimizer.get_optimization_suggestions(conn, run_id)
        pending = [r for r in rows if r["status"] == "pending"]
        ledger = _run_ledger(conn, pending)
        groups = []
        for kind in sorted({r["kind"] for r in rows}):
            mine = [r for r in rows if r["kind"] == kind]
            still = [r for r in mine if r["status"] == "pending"]
            groups.append({
                "kind": kind, "total": len(mine), "pending": len(still),
                "applied": sum(r["status"] == "applied" for r in mine),
                "rejected": sum(r["status"] == "rejected" for r in mine),
                "verified": sum(bool((r["verified"] or "").strip()) for r in still),
                "facts": _group_facts(conn, kind, still or mine),
            })
    return cast(schema.OptimizationSummary, {
        "run": dict(run),
        "total": len(rows),
        "pending": len(pending),
        "verified": sum(bool((r["verified"] or "").strip()) for r in pending),
        "ledger": ledger,
        "groups": groups,
    })


def _ensure_run_backup(run_id: int) -> str | None:
    """Take a whole-DB backup for a run once, before its first apply.

    The copy is taken between two short connection.connect() reads/writes, on its
    own autocommit connection (see store.backups.backup_to). Returns the backup path
    (existing or freshly created).
    """
    with connection.connect() as conn:
        run = optimizer.get_optimization_run(conn, run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        if run["backup_path"]:
            return run["backup_path"]
    dest = _backup(f"optimize-run{run_id}")
    with connection.connect() as conn:
        optimizer.set_run_backup(conn, run_id, str(dest))
    return str(dest)


def _has_pending(run_id: int) -> bool:
    with connection.connect() as conn:
        if optimizer.get_optimization_run(conn, run_id) is None:
            raise ValueError(f"unknown run: {run_id}")
        return bool(optimizer.get_optimization_suggestions(conn, run_id, status="pending"))


def optimization_apply(request, payload) -> schema.Applied:
    sug_id = payload.get("id")
    if not isinstance(sug_id, int):
        raise ValueError("id (int) required")
    with connection.connect() as conn:
        row = optimizer.get_suggestion(conn, sug_id)
        if row is None:
            raise ValueError(f"unknown suggestion: {sug_id}")
        run_id = row["run_id"]
    backup = _ensure_run_backup(run_id)
    with connection.connect() as conn:
        optimizer.apply_suggestion(conn, sug_id)
    return {"ok": True, "backup": backup}


def _decision_scope(payload) -> tuple[list[int], str, list[int] | None]:
    """Which pending suggestions a bulk decision is about: (run_ids, kind, ids).

    `run` names one run and `runs` a set of them -- a calendar day holds
    every run staged that day, and deciding the day is one request rather
    than one per run. `kind` narrows to a group. `ids` narrows to an explicit
    selection and is INTERSECTED with the pending rows of those runs, so an
    id from somewhere else, or one already decided, is dropped rather than
    acted on; an `ids` that survives as empty decides nothing, which is what
    an empty selection means.
    """
    runs = payload.get("runs")
    if runs is not None:
        if not (isinstance(runs, list) and runs
                and all(isinstance(i, int) for i in runs)):
            raise ValueError("runs must be a non-empty list of ints")
        run_ids = list(runs)
    else:
        run_id = payload.get("run")
        if not isinstance(run_id, int):
            raise ValueError("run (int) or runs (list of ints) required")
        run_ids = [run_id]
    kind = payload.get("kind", "")
    if not isinstance(kind, str):
        raise ValueError("kind must be a string")
    ids = payload.get("ids")
    if ids is not None and not (isinstance(ids, list)
                                and all(isinstance(i, int) for i in ids)):
        raise ValueError("ids must be a list of ints")
    return run_ids, kind, ids


def _pending_in_scope(conn, run_ids: list[int], kind: str, ids: list[int] | None) -> list:
    rows = []
    for rid in run_ids:
        if optimizer.get_optimization_run(conn, rid) is None:
            raise ValueError(f"unknown run: {rid}")
        rows.extend(optimizer.get_optimization_suggestions(conn, rid, status="pending", kind=kind))
    if ids is not None:
        chosen = set(ids)
        rows = [s for s in rows if s["id"] in chosen]
    return rows


def optimization_apply_all(request, payload) -> schema.AppliedAll:
    """Apply the pending suggestions of a run, a kind, a day, or a selection.

    Every run in the scope that still has something open gets its own
    backup, taken right before its first suggestion lands, so a run's copy is
    the store as it stood before that run -- also when the selection touches
    only some of the runs. A run that already carries a backup keeps it.
    `backups` lists the copies in run order; `backup` is the first.
    """
    run_ids, kind, ids = _decision_scope(payload)
    with connection.connect() as conn:
        pending = _pending_in_scope(conn, run_ids, kind, ids)
        run = optimizer.get_optimization_run(conn, run_ids[0])
    if not pending:
        return {"ok": True, "applied": 0, "failed": [],
                "backup": run["backup_path"] if run else None, "backups": []}
    by_run: dict[int, list] = {}
    for s in pending:
        by_run.setdefault(s["run_id"], []).append(s)
    applied, failed, backups = 0, [], []
    for rid in dict.fromkeys(run_ids):
        rows = by_run.get(rid, [])
        if not rows and not _has_pending(rid):
            continue
        backups.append(_ensure_run_backup(rid))
        for s in rows:
            try:
                with connection.connect() as conn:
                    optimizer.apply_suggestion(conn, s["id"])
                applied += 1
            except ValueError as e:
                failed.append({"id": s["id"], "error": str(e)})
    return {"ok": True, "applied": applied, "failed": failed,
            "backup": backups[0], "backups": backups}


def optimization_reject(request, payload) -> schema.Ok:
    sug_id = payload.get("id")
    if not isinstance(sug_id, int):
        raise ValueError("id (int) required")
    with connection.connect() as conn:
        optimizer.reject_suggestion(conn, sug_id)
    return {"ok": True}


def optimization_reject_all(request, payload) -> schema.RejectedAll:
    """Reject the pending suggestions of a scope, the way apply-all applies them.

    Takes no backup: rejecting writes nothing to any memory, it only marks
    the suggestion as answered.
    """
    run_ids, kind, ids = _decision_scope(payload)
    with connection.connect() as conn:
        pending = _pending_in_scope(conn, run_ids, kind, ids)
        for s in pending:
            optimizer.reject_suggestion(conn, s["id"])
    return {"ok": True, "rejected": len(pending)}


def optimization_revert(request, payload) -> schema.Ok:
    sug_id = payload.get("id")
    if not isinstance(sug_id, int):
        raise ValueError("id (int) required")
    with connection.connect() as conn:
        optimizer.revert_suggestion(conn, sug_id)
    return {"ok": True}


def optimization_delete_run(request, payload) -> schema.Ok:
    run_id = request.path_params["run_id"]
    with connection.connect() as conn:
        ok = optimizer.delete_optimization_run(conn, run_id)
    if not ok:
        raise ValueError(f"unknown run: {run_id}")
    return {"ok": True}


ROUTES = [
    Route("/api/optimization/runs", api(optimization_runs), methods=["GET"]),
    Route("/api/optimization/runs/{run_id:int}", api(optimization_delete_run), methods=["DELETE"]),
    Route("/api/optimization/suggestions", api(optimization_suggestions), methods=["GET"]),
    Route("/api/optimization/summary", api(optimization_summary), methods=["GET"]),
    Route("/api/optimization/apply", api(optimization_apply), methods=["POST"]),
    Route("/api/optimization/apply-all", api(optimization_apply_all), methods=["POST"]),
    Route("/api/optimization/reject", api(optimization_reject), methods=["POST"]),
    Route("/api/optimization/reject-all", api(optimization_reject_all), methods=["POST"]),
    Route("/api/optimization/revert", api(optimization_revert), methods=["POST"]),
]
