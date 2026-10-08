"""Optimization runs: the suggestions an agent stages, and applying, rejecting and undoing them."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from memai import contract, guard
from memai.lite import normalize_domain, now_iso
from memai.store.corpus import LEAK_FIELDS
from memai.store.diagrams.persist import is_diagram
from memai.store.domains import apply_domain_policy, apply_link_policy, parse_domains
from memai.store.health import normalize_review_after
from memai.store.memories import (
    DIAGRAM_TYPE,
    TASK_TYPE,
    get_domain_links,
    get_memory,
    insert_memory,
    memory_row,
    purge_memory,
    set_confidence,
    set_domain_links,
    set_meta_fields,
    set_review_after,
    set_status,
    update_memory_content,
)
from memai.store.relations import add_relation
from memai.store.sections import section_error, title_error

CONFIDENCE_VALUES = contract.CONFIDENCES
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


@dataclass
class _Draft:
    """A suggestion being staged: its kind's validator reads it and may normalize it."""
    kind: str
    target_uid: str | None
    payload: dict
    verified: str


def _validate_rewrite(conn: sqlite3.Connection, d: _Draft) -> str | None:
    err = _generated_content_error(conn, d.target_uid)
    if err:
        return err
    if not str(d.payload.get("new_content", "")).strip():
        return "payload.new_content required"
    # checked at staging, so nothing in the human's queue is waiting to fail on apply
    row = memory_row(conn, d.target_uid)
    return section_error(conn, row["type"], str(d.payload["new_content"]))


def _validate_unleak(conn: sqlite3.Connection, d: _Draft) -> str | None:
    field = str(d.payload.get("field", "")).strip() or LEAK_FIELDS[0]
    if field not in LEAK_FIELDS:
        return (f"payload.field must be one of: {', '.join(LEAK_FIELDS)}; "
                f"got {field!r}")
    if field == "content":
        err = _generated_content_error(conn, d.target_uid)
        if err:
            return err
    row = memory_row(conn, d.target_uid)
    text = row[field] or ""
    if not guard.leak_marks(row["type"], text):
        return f"nothing leaked in {field} of {d.target_uid}: no marks to remove"
    # the repair is computed HERE and travels in the payload, so the caller never retypes the
    # body, which is the defect this kind cleans up
    clean, _ = guard.strip_leak(row["type"], text)
    left = guard.leak_marks(row["type"], clean)
    if left:
        return (f"{field} of {d.target_uid} still carries {', '.join(left)} "
                "after the pass -- the marks are inside its prose. Rewrite "
                "it with a reword instead")
    if field == "content":
        err = section_error(conn, row["type"], clean)
        if err:
            return err
    d.payload = {"field": field, "new_text": clean}
    return None


def _validate_retag(conn: sqlite3.Connection, d: _Draft) -> str | None:
    return None if "tags" in d.payload else "payload.tags required"


def _validate_retitle(conn: sqlite3.Connection, d: _Draft) -> str | None:
    if not str(d.payload.get("title", "")).strip():
        return "payload.title required (a memory cannot be left unnamed)"
    too_long = title_error(str(d.payload["title"]))
    if too_long:
        return too_long
    if is_diagram(conn, d.target_uid):
        return (f"{d.target_uid} is a diagram: its title is part of what "
                "generates its body. Rename it through the graph.")
    return None


def _validate_review(conn: sqlite3.Connection, d: _Draft) -> str | None:
    if "review_after" not in d.payload:
        return "payload.review_after required ('' clears the date)"
    # normalized at staging, as redomain is: the panel shows it as what will hold, and '90d'
    # means a different day depending on when it is read
    try:
        d.payload = {**d.payload,
                     "review_after": normalize_review_after(str(d.payload["review_after"]))}
    except ValueError as exc:
        return str(exc)
    return None


def _validate_redomain(conn: sqlite3.Connection, d: _Draft) -> str | None:
    if "domain" not in d.payload:
        return "payload.domain required"
    # normalized at staging too: the panel must show the path the memory will end up in
    d.payload = {**d.payload, "domain": normalize_domain(str(d.payload["domain"]))}
    return None


def _validate_crosslist(conn: sqlite3.Connection, d: _Draft) -> str | None:
    if "also" not in d.payload:
        return "payload.also required"
    # the whole set is REPLACED, so staging runs the apply's policy (casing, path shape, dropping
    # a path the own domain covers) and the panel shows what will hold
    row = memory_row(conn, d.target_uid)
    given = parse_domains(d.payload["also"])
    want = apply_link_policy(conn, given, row["domain"])
    # an empty list is a legitimate suggestion, but a non-empty one that empties would apply as
    # a clear, so say so instead
    if given and not want:
        return (f"every path given is already covered by the memory's domain "
                f"{row['domain']!r}: {', '.join(given)}")
    d.payload = {**d.payload, "also": want}
    return None


def _validate_set_confidence(conn: sqlite3.Connection, d: _Draft) -> str | None:
    if d.payload.get("confidence") not in CONFIDENCE_VALUES:
        return f"payload.confidence must be one of {CONFIDENCE_VALUES}"
    if d.payload["confidence"] == "contradicted" and not d.verified:
        return "verified required: describe the live-facts check that contradicts this memory"
    return None


def _validate_archive(conn: sqlite3.Connection, d: _Draft) -> str | None:
    return None if d.verified else VERIFIED_REQUIRED[d.kind]


def _validate_link(conn: sqlite3.Connection, d: _Draft) -> str | None:
    f = (str(d.payload.get("from_uid", "")) or "").strip()
    t = (str(d.payload.get("to_uid", "")) or "").strip()
    if not _memory_exists(conn, f):
        return f"payload.from_uid not found: {f!r}"
    if not _memory_exists(conn, t):
        return f"payload.to_uid not found: {t!r}"
    if f == t:
        return "cannot link a memory to itself"
    if not str(d.payload.get("relation_type", "")).strip():
        return "payload.relation_type required"
    if d.target_uid and d.target_uid != f:
        return "link derives target_uid from payload.from_uid; omit target_uid or make them match"
    d.target_uid = f
    return None


def _validate_merge(conn: sqlite3.Connection, d: _Draft) -> str | None:
    keep = (str(d.payload.get("keep_uid", "")) or "").strip()
    drop = (str(d.payload.get("drop_uid", "")) or "").strip()
    if not _memory_exists(conn, keep):
        return f"payload.keep_uid not found: {keep!r}"
    if not _memory_exists(conn, drop):
        return f"payload.drop_uid not found: {drop!r}"
    if keep == drop:
        return "cannot merge a memory with itself"
    if d.target_uid and d.target_uid != drop:
        return "merge derives target_uid from payload.drop_uid; omit target_uid or make them match"
    if not d.verified:
        return VERIFIED_REQUIRED[d.kind]
    d.target_uid = drop
    return None


def _distill_source_error(conn: sqlite3.Connection, uid: str) -> str | None:
    if not _memory_exists(conn, uid):
        return f"payload.source_uids not found: {uid!r}"
    if is_diagram(conn, uid):
        return (f"{uid} is a diagram: distill archives its sources. "
                "Use archive to retire a flow on its own.")
    if memory_row(conn, uid)["type"] == TASK_TYPE:
        return (f"{uid} is a task: distill archives its sources, and a task "
                "closes through its own items.")
    return None


def _validate_distill(conn: sqlite3.Connection, d: _Draft) -> str | None:
    if d.target_uid:
        return "distill creates a new memory; omit target_uid"
    extra = sorted(k for k in d.payload if k not in DISTILL_PAYLOAD_KEYS)
    if extra:
        return (f"payload keys not accepted by distill: {', '.join(extra)} "
                f"(allowed: {', '.join(DISTILL_PAYLOAD_KEYS)})")
    sources = d.payload.get("source_uids")
    if not isinstance(sources, list) or not sources:
        return "payload.source_uids must be a non-empty list"
    sources = [str(u).strip() for u in sources]
    if len(set(sources)) != len(sources):
        return "payload.source_uids contains duplicates"
    for u in sources:
        err = _distill_source_error(conn, u)
        if err:
            return err
    if d.payload.get("new_type") not in DISTILL_TYPES:
        return f"payload.new_type must be one of {DISTILL_TYPES}"
    if not str(d.payload.get("new_content", "")).strip():
        return "payload.new_content required"
    # no writing tool names this memory afterwards, so a distill with no title stays unnamed
    if not str(d.payload.get("title", "")).strip():
        return "payload.title required (the distilled memory needs a name)"
    too_long = title_error(str(d.payload["title"]))
    if too_long:
        return too_long
    # anti_pattern is a distill target and is made of fields, so the body
    # a distill writes has to read back the same way any other one does
    err = section_error(conn, str(d.payload["new_type"]), str(d.payload["new_content"]))
    if err:
        return err
    if not d.verified:
        return VERIFIED_REQUIRED[d.kind]
    d.payload = {**d.payload, "source_uids": sources}
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

    spec = KINDS[kind]
    if spec.needs_target and not _memory_exists(conn, target_uid):
        return None, f"target_uid not found: {target_uid!r}"
    draft = _Draft(kind=kind, target_uid=target_uid, payload=payload, verified=verified)
    err = spec.validate(conn, draft)
    if err:
        return None, err

    return {
        "kind": kind, "target_uid": draft.target_uid, "payload": draft.payload,
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
    """Write one metadata field the way the dashboard's meta editor does, audit included.

    A domain change re-runs the cross-listing policy: the memory's new path
    may already satisfy a membership the old path needed (see
    apply_link_policy), and leaving that row would count it twice in its
    own branch.
    """
    if field == "domain":
        value = apply_domain_policy(conn, value)
    row = memory_row(conn, uid)
    set_meta_fields(conn, uid, row, {field: value})
    if field == "domain" and row["also_domains"]:
        set_domain_links(conn, uid, get_domain_links(conn, uid))


def _apply_rewrite(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    # staging refuses these on a diagram or task, but a staged run may still
    # hold one; applying it would write over the projection
    err = _generated_content_error(conn, uid)
    if err:
        raise ValueError(err)
    row = memory_row(conn, uid)
    prev = {"content": row["content"]}
    update_memory_content(conn, uid, payload["new_content"], note=f"optimize:{kind}")
    return prev


def _revert_rewrite(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                    prev: dict) -> None:
    update_memory_content(conn, uid, prev["content"], note=f"optimize:undo {kind}")


def _apply_unleak(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
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


def _revert_unleak(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                   prev: dict) -> None:
    field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
    if field == "content":
        # restores a leaked body that writers are refused: an undo puts back what was there
        update_memory_content(conn, uid, prev["content"],
                              note=f"optimize:undo {kind}", leaked_ok=True)
    else:
        _update_meta_field(conn, uid, field, prev[field])


# The column each one-field kind rewrites, named the same in its payload.
_META_FIELDS = {"retag": "tags", "retitle": "title", "redomain": "domain"}


def _apply_meta(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    field = _META_FIELDS[kind]
    row = memory_row(conn, uid)
    prev = {field: row[field]}
    _update_meta_field(conn, uid, field, str(payload[field]).strip())
    return prev


def _revert_meta(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                 prev: dict) -> None:
    field = _META_FIELDS[kind]
    _update_meta_field(conn, uid, field, prev[field])


def _apply_review(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    row = memory_row(conn, uid)
    prev = {"review_after": row["review_after"]}
    set_review_after(conn, uid, str(payload["review_after"]))
    return prev


def _revert_review(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                   prev: dict) -> None:
    set_review_after(conn, uid, prev["review_after"])


def _apply_crosslist(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    # the whole set, not an addition: undo restores exactly this list
    prev = {"also": get_domain_links(conn, uid)}
    set_domain_links(conn, uid, payload["also"], note=f"optimize:{kind}")
    return prev


def _revert_crosslist(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                      prev: dict) -> None:
    set_domain_links(conn, uid, prev["also"], coerce=False,
                     note="optimize:undo crosslist")


def _apply_set_confidence(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    row = memory_row(conn, uid)
    prev = {"confidence": row["confidence"]}
    set_confidence(conn, uid, payload["confidence"])
    return prev


def _revert_set_confidence(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                           prev: dict) -> None:
    set_confidence(conn, uid, prev["confidence"])


def _apply_archive(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    row = memory_row(conn, uid)
    prev = {"status": row["status"], "superseded_by": row["superseded_by"]}
    reason = str(payload.get("reason", "")).strip() or "optimize: archived"
    set_status(conn, uid, "archived", note=reason)
    return prev


def _revert_archive(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                    prev: dict) -> None:
    set_status(conn, uid, prev["status"],
               superseded_by=prev.get("superseded_by"), note="optimize: undo archive")


def _apply_link(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    rid = add_relation(
        conn, payload["from_uid"].strip(), payload["to_uid"].strip(),
        str(payload["relation_type"]).strip(), str(payload.get("note", "")).strip(),
    )
    return {"relation_id": rid}


def _revert_link(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                 prev: dict) -> None:
    conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))


def _apply_merge(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
    keep, drop = payload["keep_uid"].strip(), payload["drop_uid"].strip()
    drow = memory_row(conn, drop)
    prev = {"drop_status": drow["status"], "drop_superseded_by": drow["superseded_by"]}
    rid = add_relation(conn, keep, drop, "supersedes", str(payload.get("note", "")).strip())
    prev["relation_id"] = rid
    set_status(conn, drop, "archived", superseded_by=keep, note="optimize: merged")
    return prev


def _revert_merge(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                  prev: dict) -> None:
    conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
    set_status(conn, payload["drop_uid"].strip(), prev["drop_status"],
               superseded_by=prev.get("drop_superseded_by"), note="optimize: undo merge")


def _apply_distill(conn: sqlite3.Connection, kind: str, uid: str, payload: dict) -> dict:
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


def _revert_distill(conn: sqlite3.Connection, kind: str, uid: str, payload: dict,
                    prev: dict) -> None:
    for s in prev.get("sources", []):
        set_status(conn, s["uid"], s["status"],
                   superseded_by=s.get("superseded_by"), note="optimize: undo distill")
    # purge (not archive) the distilled memory: it was born from this
    # apply, so undo removes it entirely; its relations go with it
    if prev.get("new_uid"):
        purge_memory(conn, prev["new_uid"])


@dataclass(frozen=True)
class KindSpec:
    """One suggestion kind: its staging check, its apply, and the undo of that apply.

    `validate` may normalize the draft it is handed. `apply` returns the
    prev_state that `revert` reads back. `needs_target` off means the kind
    names the memories it acts on in its payload instead of in target_uid.
    """
    validate: Callable[[sqlite3.Connection, _Draft], str | None]
    apply: Callable[[sqlite3.Connection, str, str, dict], dict]
    revert: Callable[[sqlite3.Connection, str, str, dict, dict], None]
    needs_target: bool = True


KINDS: dict[str, KindSpec] = {
    "compact": KindSpec(_validate_rewrite, _apply_rewrite, _revert_rewrite),
    "reword": KindSpec(_validate_rewrite, _apply_rewrite, _revert_rewrite),
    "retag": KindSpec(_validate_retag, _apply_meta, _revert_meta),
    "retitle": KindSpec(_validate_retitle, _apply_meta, _revert_meta),
    "redomain": KindSpec(_validate_redomain, _apply_meta, _revert_meta),
    "crosslist": KindSpec(_validate_crosslist, _apply_crosslist, _revert_crosslist),
    "set_confidence": KindSpec(_validate_set_confidence, _apply_set_confidence,
                               _revert_set_confidence),
    "review": KindSpec(_validate_review, _apply_review, _revert_review),
    "archive": KindSpec(_validate_archive, _apply_archive, _revert_archive),
    "link": KindSpec(_validate_link, _apply_link, _revert_link, needs_target=False),
    "merge": KindSpec(_validate_merge, _apply_merge, _revert_merge, needs_target=False),
    "distill": KindSpec(_validate_distill, _apply_distill, _revert_distill, needs_target=False),
    "unleak": KindSpec(_validate_unleak, _apply_unleak, _revert_unleak),
}
SUGGESTION_KINDS = tuple(KINDS)


def _target(kind: str, target_uid: str | None) -> str:
    """The uid a suggestion acts on; link, merge and distill name theirs in the payload."""
    if target_uid:
        return target_uid
    spec = KINDS.get(kind)
    if spec is not None and not spec.needs_target:
        return ""
    raise ValueError(f"{kind} needs a target_uid")


def _apply_kind(conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict) -> dict:
    """Execute one suggestion and return the prev_state dict for undo."""
    uid = _target(kind, target_uid)
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")
    return KINDS[kind].apply(conn, kind, uid, payload)


def _revert_kind(
    conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict, prev: dict
) -> None:
    uid = _target(kind, target_uid)
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")
    KINDS[kind].revert(conn, kind, uid, payload, prev)


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
