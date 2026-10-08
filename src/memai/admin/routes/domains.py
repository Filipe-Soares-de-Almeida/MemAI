"""Domains: the tree, a level's preview, renames, status, deletion and casing repair."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db
from memai.admin.api import api
from memai.admin.shared import BULK_MAX, STATUSES, _int_param, _summary
from memai.store import queries


def domains(request, payload) -> schema.DomainTree:
    """The domain tree, one entry per path, for the Domains view.

    Every field the table draws: both status counts, the type mix, the
    spelling-variant warning, and the tree position (parent/depth/
    children) with the subtree rollups the parent rows are drawn from.

    A level nobody wrote to directly still gets an entry, flagged
    `implicit`: 'acme/x100/p200' means the tree HAS an 'acme/x100', and a
    view that skipped it could not draw the branch its children hang from.

    `also` and `subtree_also` count the memories CROSS-LISTED at a path
    rather than filed there -- kept out of the status counts, because the
    tree would otherwise total more than the store. A path with no counts of
    its own and an `also` above zero is a purely cross-cutting subject, and
    the view says so instead of drawing it as empty.
    """
    with db.connect() as conn:
        rows, link_rows = queries.domain_tree_counts(conn)
    agg: dict[str, dict] = {}

    def node(path: str) -> dict:
        return agg.setdefault(path, {
            "domain": path, "active": 0, "archived": 0, "types": {},
            "latest_at": "", "parent": db.domain_parent(path),
            "depth": db.domain_depth(path), "children": 0,
            "subtree_active": 0, "subtree_archived": 0,
            "also": 0, "subtree_also": 0,
            "subtree_latest_at": "", "implicit": True,
        })

    for r in rows:
        d = node(db.normalize_domain(r["domain"]))
        d["implicit"] = False
        if r["status"] == "active":
            d["active"] += r["n"]
        else:
            d["archived"] += r["n"]
        d["types"][r["type"]] = d["types"].get(r["type"], 0) + r["n"]
        d["latest_at"] = max(d["latest_at"], r["latest"])
        for ancestor in db.domain_ancestors(d["domain"]):
            node(ancestor)

    # being cross-listed at a path names it as surely as being filed there,
    # so it clears `implicit` and counts as activity for the ordering
    for r in link_rows:
        d = node(db.normalize_domain(r["domain"]))
        d["implicit"] = False
        d["also"] += r["n"]
        d["latest_at"] = max(d["latest_at"], r["latest"])
        for ancestor in db.domain_ancestors(d["domain"]):
            node(ancestor)

    for d in list(agg.values()):
        for scope in db.domain_ancestors(d["domain"], include_self=True):
            holder = agg[scope]
            holder["subtree_active"] += d["active"]
            holder["subtree_archived"] += d["archived"]
            holder["subtree_also"] += d["also"]
            holder["subtree_latest_at"] = max(holder["subtree_latest_at"], d["latest_at"])
        if d["parent"]:
            agg[d["parent"]]["children"] += 1

    # Spelling variants compared per level: 'Cache' beside 'cache' is drift to merge, the same
    # word at two depths is two scopes.
    by_sibling: dict[tuple[str, str], list[str]] = {}
    for path, d in agg.items():
        if d["implicit"]:
            continue
        by_sibling.setdefault(
            (d["parent"], db.split_domain(path)[-1].lower()), []).append(path)
    for names in by_sibling.values():
        if len(names) > 1:
            for n in names:
                agg[n]["collides_with"] = [x for x in names if x != n]

    result = sorted(agg.values(), key=lambda d: d["domain"])
    result.sort(key=lambda d: d["subtree_latest_at"], reverse=True)
    return cast(schema.DomainTree, {"domains": result})


def domain_detail(request, payload) -> schema.DomainDetail:
    """What one level of the tree holds, for the pane beside the columns.

    Two lists, because they are two different facts and a pane that ran
    them together would claim the second is filed where it is not:

      filed     the memories whose OWN domain is exactly this path. Not the
                subtree -- the columns are how you walk into a child.
      crossing  the memories cross-listed here that live somewhere else.
                memory_domains never holds a memory's own path or an
                ancestor of it (see the schema), so every row it returns
                for this path is filed outside it, and each carries the
                branch it does live in.

    Both are capped: this is a preview under a set of columns, and the
    memory list is where a whole scope is read.
    """
    domain = db.normalize_domain(request.query_params.get("domain", ""))
    if not domain:
        raise ValueError("domain is required")
    limit = _int_param(request, "limit", 6, 1, 30)
    with db.connect() as conn:
        filed, filed_total, crossing = queries.domain_preview(conn, domain, limit)
    return cast(schema.DomainDetail, {
        "domain": domain,
        "filed": [_summary(r, 160) for r in filed],
        "filed_total": filed_total,
        "crossing": [_summary(r, 160) for r in crossing],
    })


def rename_domain(request, payload) -> schema.DomainRenamed:
    """Rename, re-home or merge a domain, subdomains included.

    'to' is a full path, so this is also how a domain is nested: renaming
    'x100' to 'acme/x100' moves the bucket (and its subtree) under 'acme'.
    Every affected row is reindexed (domain is part of the index source)
    and audited in edits -- see db.move_domain.

    Cross-listings into the renamed scope follow it (`also_affected`), so a
    memory that merely belongs to the subject is not left pointing at a path
    that does not exist. It is not moved: where it is filed is untouched.
    """
    src = (payload.get("from") or "").strip()
    dst = (payload.get("to") or "").strip()
    if not src:
        raise ValueError("'from' is required")
    if not dst:
        raise ValueError("'to' is required")
    with db.connect() as conn:
        moved = db.move_domain(conn, src, dst)
    return {"ok": True, "affected": moved["moved"],
            "also_affected": moved["also_moved"],
            "domains": moved["domains"], "merged": moved["merged"]}


def domain_status(request, payload) -> schema.DomainStatusSaved:
    """Archive or restore a whole domain, subdomains included.

    A domain has no status column -- it is named by the memories filed under
    it -- so this is the scope-wide reading of the per-memory archive, and
    the tree draws a level with archived memories and no active ones as an
    archived branch.

    `uids` is what actually changed, so the UI can offer an exact Undo
    instead of restoring everything in the scope (see db.set_domain_status).
    Withheld past BULK_MAX, which is the most /api/bulk would take back.
    """
    domain = (payload.get("domain") or "").strip()
    status = payload.get("status", "")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    reason = (payload.get("reason") or "").strip()
    verb = "archived" if status == "archived" else "restored"
    note = f"{verb} with domain '{domain}'" + (f": {reason}" if reason else "")
    with db.connect() as conn:
        moved = db.set_domain_status(conn, domain, status, note=note)
    uids = moved["uids"]
    return {"ok": True, "affected": len(uids), "domains": moved["domains"],
            "uids": uids if len(uids) <= BULK_MAX else []}


def delete_domain(request, payload) -> schema.DomainDeleted:
    """Permanently delete a domain and every memory filed in it.

    Same guardrail as the MCP purge_memory tool and the per-memory purge
    above, for the same reason and at a much larger blast radius: the
    operator must type the literal phrase 'DELETE <domain>', and the UI
    never pre-fills it. Archiving the domain is the reversible option and
    is what the view offers first.
    """
    domain = (payload.get("domain") or "").strip()
    if not domain:
        raise ValueError("'domain' is required")
    expected = f"DELETE {domain}"
    if payload.get("confirm", "") != expected:
        raise ValueError(f"confirm phrase must exactly equal '{expected}'")
    with db.connect() as conn:
        gone = db.purge_domain(conn, domain)
    return cast(schema.DomainDeleted, {"ok": True, **gone})


def _normalize_plan(mode: str, counts: dict[str, int]) -> list[dict]:
    """Compute the per-domain moves that bring `counts` in line with policy.

    Policy is the casing `mode` plus the canonical path shape, the same
    pair every write path applies -- so this also repairs a domain that
    reached the table with a blank or padded segment ('acme//x100',
    'acme / x100'), which no prefix query could match as written.

    Each entry: {from, to, count, action}. action is 'merge' when the
    target already exists or more than one source collapses into it,
    otherwise 'rename'. Domains that already conform are omitted.
    """
    existing = set(counts)
    targets: dict[str, list[str]] = {}
    for d in counts:
        targets.setdefault(db.normalize_domain(db.case_domain(mode, d)), []).append(d)
    plan: list[dict] = []
    for target, srcs in targets.items():
        changing = [s for s in srcs if s != target]
        if not changing:
            continue
        merge = (target in existing) or len(srcs) > 1
        for s in changing:
            plan.append({
                "from": s, "to": target, "count": counts[s],
                "action": "merge" if merge else "rename",
            })
    return sorted(plan, key=lambda e: e["from"].lower())


def normalize_domains(request, payload) -> schema.NormalizePlan | schema.NormalizeDone:
    """Bring already-stored domains in line with the casing + path policy.

    dry_run (default true) returns the plan for preview -- what renames
    and what merges -- without touching data. dry_run=false applies it,
    reusing the rename/merge path (UPDATE + audit). Each entry
    names one exact stored domain, so the moves are exact-path (a
    descendant appears as its own entry, or does not need moving at all).
    No-op when everything already conforms.

    A path that exists only as a cross-listing is in the plan too: it is a
    stored domain string like any other, and a repair pass that skipped it
    would leave the one spelling no prefix query can match.
    """
    dry_run = bool(payload.get("dry_run", True))
    with db.connect() as conn:
        mode = db.get_domain_case(conn)
        plan = _normalize_plan(mode, queries.domain_spellings(conn))
        if dry_run:
            return cast(schema.NormalizePlan, {
                "mode": mode, "dry_run": True, "plan": plan,
                "renames": sum(1 for e in plan if e["action"] == "rename"),
                "merges": sum(1 for e in plan if e["action"] == "merge")})
        moved = [db.move_domain(conn, e["from"], e["to"], subtree=False) for e in plan]
    return {"ok": True, "mode": mode, "moved": len(plan),
            "affected": sum(m["moved"] for m in moved),
            "also_affected": sum(m["also_moved"] for m in moved)}


ROUTES = [
    Route("/api/domains", api(domains)),
    Route("/api/domains/detail", api(domain_detail)),
    Route("/api/domains/rename", api(rename_domain), methods=["POST"]),
    Route("/api/domains/normalize", api(normalize_domains), methods=["POST"]),
    Route("/api/domains/status", api(domain_status), methods=["POST"]),
    Route("/api/domains/delete", api(delete_domain), methods=["POST"]),
]
