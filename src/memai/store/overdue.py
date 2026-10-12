"""The one checkpoint a domain may hold, and what has outlived the store's limits."""

from __future__ import annotations

import sqlite3
from datetime import date

from memai.lite import DOMAIN_SEP, normalize_domain, split_domain
from memai.store.domains import domain_scope_clause
from memai.store.health import days_before, today_iso
from memai.store.settings import (
    get_checkpoint_block_enabled,
    get_checkpoint_days,
    get_unverified_days,
)

CHECKPOINT = "checkpoint"

_HEADER = "uid, title, type, domain, created_at, updated_at"


def header(row: sqlite3.Row, *, since: str = "created_at", today: str = "") -> dict:
    """A row as the few fields an alert names it by, with its age in whole days."""
    age = (date.fromisoformat(today or today_iso())
           - date.fromisoformat(row[since][:10])).days
    return {"uid": row["uid"], "title": row["title"], "type": row["type"],
            "domain": row["domain"], since: row[since], "age_days": age}


def checkpoints_in(conn: sqlite3.Connection, domain: str) -> list[sqlite3.Row]:
    """The active checkpoints filed at exactly `domain`, oldest first."""
    return conn.execute(
        f"SELECT {_HEADER} FROM memories WHERE type = ? AND status = 'active' AND domain = ? "
        "ORDER BY created_at", (CHECKPOINT, domain)).fetchall()


def overdue_checkpoints(conn: sqlite3.Connection, *, domain: str = "",
                        at: str = "") -> list[sqlite3.Row]:
    """Active checkpoints created before the age limit, in a scope, oldest first.

    Counted whether or not the block is on: the overview measures them either way.
    """
    cutoff = days_before(at or today_iso(), get_checkpoint_days(conn))
    where, params = "", []
    if normalize_domain(domain):
        where, params, _ = domain_scope_clause(conn, domain, alias="")
    return conn.execute(
        f"SELECT {_HEADER} FROM memories WHERE type = ? AND status = 'active' "
        f"AND created_at < ? {where} ORDER BY created_at",
        [CHECKPOINT, cutoff, *params]).fetchall()


def alerting_checkpoints(conn: sqlite3.Connection, domain: str = "") -> list[sqlite3.Row]:
    """The overdue checkpoints an MCP result alerts about: none while the block is off."""
    if not get_checkpoint_block_enabled(conn):
        return []
    return overdue_checkpoints(conn, domain=domain)


def blocking_checkpoints(conn: sqlite3.Connection, domain: str) -> list[sqlite3.Row]:
    """The overdue checkpoints that refuse a write filed at `domain`.

    One filed at the path or at any path above it: a bearing covers its
    subject's subdomains, and the empty path is the whole project's.
    """
    if not get_checkpoint_block_enabled(conn):
        return []
    parts = split_domain(domain)
    paths = [DOMAIN_SEP.join(parts[:n]) for n in range(len(parts) + 1)]
    cutoff = days_before(today_iso(), get_checkpoint_days(conn))
    marks = ", ".join("?" * len(paths))
    return conn.execute(
        f"SELECT {_HEADER} FROM memories WHERE type = ? AND status = 'active' "
        f"AND created_at < ? AND domain IN ({marks}) ORDER BY created_at",
        [CHECKPOINT, cutoff, *paths]).fetchall()


def unverified_past_limit(conn: sqlite3.Connection, *, domain: str = "",
                          limit: int = 5) -> tuple[int, list[sqlite3.Row]]:
    """How many active memories stayed unverified past the limit, and the oldest `limit` of them.

    The same rows the overview's stale symptom counts, narrowed to a scope.
    """
    cutoff = days_before(today_iso(), get_unverified_days(conn))
    where, params = "", []
    if normalize_domain(domain):
        where, params, _ = domain_scope_clause(conn, domain, alias="")
    base = (f"FROM memories WHERE status = 'active' AND confidence = 'unverified' "
            f"AND updated_at < ? {where}")
    total = conn.execute(f"SELECT COUNT(*) {base}", [cutoff, *params]).fetchone()[0]
    rows = conn.execute(f"SELECT {_HEADER} {base} ORDER BY updated_at LIMIT ?",
                        [cutoff, *params, limit]).fetchall() if total else []
    return total, rows
