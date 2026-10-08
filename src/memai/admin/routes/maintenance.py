"""Upkeep: the health report, FTS rebuild, orphans, renders, VACUUM, dedup and the audit log."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db
from memai.admin.api import api
from memai.admin.shared import _file_size, _int_param, _scope_echo, _subtree_param, _summary
from memai.store import maintenance, queries

DEDUP_SNIPPET = 480


def health(request, payload) -> schema.Health:
    project = db.active_project()
    dbfile = db.default_db_path()
    with db.connect() as conn:
        report = maintenance.integrity_report(conn)
        quick = report["quick_check"]
        integrity_ok = quick == ["ok"]
        active_count = queries.count_active(conn)
        untagged = queries.count_active(conn, "untagged")
        untitled = queries.count_active(conn, "untitled")
        compact_reason = db.get_compact_reason(conn)
        render_retention = db.get_svg_retention(conn)
    # the active project's own backups; another project's are listed when it is
    backups = [
        {"name": p.name, "size": _file_size(p),
         "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat()}
        for p in db.backup_files(project)]
    return cast(schema.Health, {
        "project": project,
        # same rule as maintenance.fts_integrity: "ok" is quick_check's way of
        # saying nothing is wrong, and the UI has its own words for that
        "integrity": {"ok": integrity_ok,
                      "detail": "" if integrity_ok else "; ".join(quick)[:400]},
        "fts": {"ok": report["fts_ok"], "detail": report["fts_detail"],
                "rows": report["fts_rows"], "expected": report["memories"]},
        "relations": {"orphans": report["orphan_relations"]},
        # BM25 reads content, tags and domain, so a row with no tags answers
        # only a query that quotes its own wording
        "tags": {"untagged": untagged, "active": active_count},
        # a row with no title is listed by the first line of its body, which
        # is the body doing a job it was not written for
        "title": {"untitled": untitled, "active": active_count},
        # generated SVGs are a cache, so what matters is what they cost and
        # whether the retention rule is actually clearing them
        "renders": {**db.renders_usage(), "retention": render_retention,
                    "path": str(db.renders_dir())},
        "file": {
            "path": str(dbfile),
            "size": _file_size(dbfile),
            "wal_size": _file_size(dbfile.with_name(dbfile.name + "-wal")),
            "reclaimable": report["reclaimable"],
            # what freed those pages, so the disk row can say what the space
            # is instead of only how much of it there is
            "compact_reason": compact_reason,
        },
        "backups": backups[:12],
    })


def fts_rebuild(request, payload) -> schema.FtsRebuilt:
    with db.connect() as conn:
        count = maintenance.rebuild_fts(conn)
    return {"ok": True, "rows": count}


def clean_orphans(request, payload) -> schema.OrphansCleaned:
    with db.connect() as conn:
        removed = maintenance.clean_orphans(conn)
    return {"ok": True, "relations_removed": removed["relations"],
            "suggestions_removed": removed["suggestions"],
            "node_links_removed": removed["node_links"],
            "jumps_removed": removed["jumps"], "task_links_removed": removed["task_links"]}


def prune_renders(request, payload) -> schema.RendersPruned:
    """Clear generated SVGs now, rather than waiting for the next render.

    `all=true` empties the folder regardless of age -- the retention rule
    answers "how long to keep them", this answers "get rid of them". The
    diagrams themselves are untouched either way: a render is a cache.
    """
    before = db.renders_usage()
    if payload.get("all"):
        swept = db.prune_renders_all()
    else:
        with db.connect() as conn:
            swept = db.prune_renders(db.get_svg_retention(conn))
    return cast(schema.RendersPruned, {"ok": True, **swept, "before": before, "after": db.renders_usage()})


def vacuum(request, payload) -> schema.Vacuumed:
    dbfile = db.default_db_path()
    before = _file_size(dbfile) + _file_size(dbfile.with_name(dbfile.name + "-wal"))
    maintenance.vacuum(dbfile)
    after = _file_size(dbfile) + _file_size(dbfile.with_name(dbfile.name + "-wal"))
    with db.connect() as conn:
        db.clear_compact_reason(conn)
    return {"ok": True, "before": before, "after": after}


def dedup(request, payload) -> schema.DedupPairs:
    threshold = min(max(float(request.query_params.get("threshold", 0.6)), 0.3), 0.99)
    domain = request.query_params.get("domain", "")
    with db.connect() as conn:
        scope = _scope_echo(conn, domain)
        pairs = db.dedup_candidates(
            conn,
            domain=domain,
            type=request.query_params.get("type", ""),
            threshold=threshold,
            subtree=_subtree_param(request),
            limit=_int_param(request, "limit", 20, 1, 60))
        result = [{"a": _summary(a, DEDUP_SNIPPET), "b": _summary(b, DEDUP_SNIPPET),
                   "ratio": round(score, 3), "method": method} for a, b, score, method in pairs]
    return cast(schema.DedupPairs, {"pairs": result, "threshold": threshold, **scope})


def audit(request, payload) -> schema.AuditLog:
    limit = _int_param(request, "limit", 100, 1, 400)
    with db.connect() as conn:
        entries = queries.edit_log(conn, limit)
    return cast(schema.AuditLog, {"entries": entries})


ROUTES = [
    Route("/api/maintenance/health", api(health)),
    Route("/api/maintenance/fts-rebuild", api(fts_rebuild), methods=["POST"]),
    Route("/api/maintenance/clean-orphans", api(clean_orphans), methods=["POST"]),
    Route("/api/maintenance/prune-renders", api(prune_renders), methods=["POST"]),
    Route("/api/maintenance/vacuum", api(vacuum), methods=["POST"]),
    Route("/api/maintenance/dedup", api(dedup)),
    Route("/api/audit", api(audit)),
]
