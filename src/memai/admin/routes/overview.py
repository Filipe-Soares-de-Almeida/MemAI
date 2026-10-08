"""The overview: store totals, the health index, and symptoms that open a filtered list."""

from __future__ import annotations

import sqlite3
from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db
from memai.admin.api import api
from memai.admin.shared import _file_size, _summary
from memai.store import maintenance, queries

# How far back the health index compares itself, against a db.health_daily snapshot; there is no
# delta until a snapshot that old exists.
HEALTH_DELTA_DAYS = 30


# One countable defect each, counted by queries.symptom_counts; `params` is the memory-list filter
# that opens the same rows. Ordered worst first, then by coverage.
_SYMPTOMS: tuple[tuple[str, str, dict], ...] = (
    ("contradicted", "bad", {"confidence": "contradicted"}),
    ("stale", "warn", {"stale": "1", "sort": "updated_at", "dir": "asc"}),
    ("due", "warn", {"due": "1"}),
    ("unlinked", "warn", {"linked": "no"}),
    ("untitled", "info", {"untitled": "1"}),
    ("untagged", "info", {"untagged": "1"}),
)


def _symptoms(conn: sqlite3.Connection, active: int) -> list[dict]:
    """One row per countable defect, with the filter that lists it.

    Deliberately NOT here: likely duplicates. Finding them is an O(n^2)
    difflib sweep (db.dedup_candidates), which is a scan the operator asks
    for -- /api/maintenance/dedup -- and not something a landing page runs
    on every paint. The dashboard shows that row without a count until it
    has been scanned.
    """
    counts = queries.symptom_counts(conn)
    out = []
    for key, severity, params in _SYMPTOMS:
        count = counts[key]
        out.append({
            "key": key, "severity": severity, "count": count,
            "share": round(count / active, 4) if active else 0.0,
            "params": {"status": "active", **params},
        })
    # A broken flow counts in diagrams, not memories: its own denominator, no share of the store.
    flows = db.diagram_overview(conn)
    broken = sum(1 for d in flows if d["issues"])
    out.append({
        "key": "diagrams", "severity": "info", "count": broken,
        "of": len(flows), "share": 0.0, "params": {},
    })
    # An edge whose endpoint does not exist. No list to open, so an empty filter; the view sends
    # its button to the operation that clears them.
    total_rels, orphans = maintenance.relation_counts(conn)
    out.append({
        "key": "orphans", "severity": "warn", "count": orphans,
        "of": total_rels, "share": 0.0, "params": {},
    })
    return out


def overview(request, payload) -> schema.Overview:
    dbfile = db.default_db_path()
    with db.connect() as conn:
        counts = queries.overview_counts(conn)
        health = db.health_axes(conn)
        db.health_snapshot(conn, health)
        was = db.health_since(conn, HEALTH_DELTA_DAYS)
        health["delta"] = health["score"] - was["score"] if was else None
        health["delta_days"] = HEALTH_DELTA_DAYS
        health["since"] = was["day"] if was else None
        symptoms = _symptoms(conn, health["active"])
        domains = db.list_domains(conn)
        recent = [_summary(r, 150) for r in db.list_recent(conn, limit=8)]
    return cast(schema.Overview, {
        "totals": {
            "memories": counts["total"],
            "active": counts["by_status"].get("active", 0),
            "archived": counts["by_status"].get("archived", 0),
            "relations": counts["relations"],
            "edits": counts["edits"],
            "sessions": counts["sessions"],
            # written-to paths only: an implicit ancestor is a level of the tree, not a named domain
            "domains": sum(1 for d in domains if not d["implicit"]),
        },
        "by_type": counts["by_type"],
        "by_confidence": counts["by_confidence"],
        "by_type_confidence": counts["by_type_confidence"],
        "open_tasks": counts["open_tasks"],
        "health": health,
        "symptoms": symptoms,
        "activity": counts["activity"],
        "domains": domains[:10],
        "recent": recent,
        "db": {
            "project": db.active_project(),
            "path": str(dbfile),
            "size": _file_size(dbfile),
            "wal_size": _file_size(dbfile.with_name(dbfile.name + "-wal")),
        },
    })


ROUTES = [
    Route("/api/overview", api(overview)),
]
