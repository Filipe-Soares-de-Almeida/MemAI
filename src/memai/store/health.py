"""Review dates, and the health axes measured over a store's active memories."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta

from memai.lite import now_iso

# `review_after` is the writer's estimate of when a claim stops being safe unchecked; a date, so
# "what is overdue" is a comparison a warm-up can make without reading.
_REVIEW_RELATIVE = re.compile(r"^(\d{1,4})\s*d$", re.I)


def today_iso() -> str:
    return now_iso()[:10]


def normalize_review_after(value: str, *, today: str | None = None) -> str:
    """A review date as 'YYYY-MM-DD', from a date or from '90d'.

    The relative form is there because that is how the answer arrives: a
    writer knows "this is worth rechecking in a quarter" and does not know
    today's date without asking. Empty means never -- most memories are not
    about anything that goes stale, and a store that made everyone pick a
    date would get dates nobody meant.
    """
    v = (value or "").strip()
    if not v:
        return ""
    rel = _REVIEW_RELATIVE.match(v)
    if rel:
        return (date.fromisoformat(today or today_iso())
                + timedelta(days=int(rel.group(1)))).isoformat()
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"review_after must be a date ('2026-11-01') or a span ('90d'); got {value!r}"
        ) from exc


def _due_clause(at: str | None = None) -> tuple[str, list]:
    """"this memory is overdue for a recheck", as SQL."""
    return "review_after <> '' AND review_after <= ?", [at or today_iso()]


# How long a memory nobody has vetted may sit before it counts as stale.
# Deliberately the same span the writing tools suggest for review_after.
STALE_DAYS = 90

# The health axes, each the share of ACTIVE memories satisfying its SQL (written out to be read):
# curation (confirmed), connectivity, freshness (STALE_DAYS) and organization.
_HEALTH_AXES: tuple[tuple[str, str], ...] = (
    ("curation", "confidence = 'confirmed'"),
    ("connectivity",
     "uid IN (SELECT from_uid FROM relations UNION SELECT to_uid FROM relations)"),
    ("freshness",
     "(review_after = '' OR review_after > :today) "
     "AND NOT (confidence = 'unverified' AND updated_at < :stale)"),
    ("organization",
     "TRIM(title) <> '' AND TRIM(tags) <> '' AND TRIM(tags) <> type "
     "AND TRIM(domain) <> ''"),
)


def health_axes(conn: sqlite3.Connection, *, at: str | None = None) -> dict:
    """The four axes and the index over them, each 0-100 over active memories.

    `at` is the day the spans are measured from (YYYY-MM-DD), defaulting to
    today. An empty store scores 100 on every axis: nothing is wrong with
    it, and 0 would read as a store in trouble on the day it is created.
    """
    today = at or today_iso()
    stale = (datetime.fromisoformat(today) - timedelta(days=STALE_DAYS)).isoformat()
    active = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
    axes = {}
    for name, clause in _HEALTH_AXES:
        if not active:
            axes[name] = 100
            continue
        met = conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE status = 'active' AND ({clause})",
            {"today": today, "stale": stale}).fetchone()[0]
        axes[name] = round(met * 100 / active)
    return {"score": round(sum(axes.values()) / len(axes)), "axes": axes, "active": active}


def health_snapshot(conn: sqlite3.Connection, health: dict, *, day: str = "") -> None:
    """Record today's index, once. A day already written is left as it was."""
    conn.execute(
        """INSERT OR IGNORE INTO health_daily
           (day, score, curation, connectivity, freshness, organization)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (day or today_iso(), health["score"], health["axes"]["curation"],
         health["axes"]["connectivity"], health["axes"]["freshness"],
         health["axes"]["organization"]))


def health_since(conn: sqlite3.Connection, days: int = 30) -> sqlite3.Row | None:
    """The newest snapshot at least `days` old, or None if none is that old.

    A delta against a younger reading would say "over the {days} days" about
    a shorter window, so a store the dashboard has not been open on for long
    enough reports no delta rather than a misdated one.
    """
    cutoff = (datetime.fromisoformat(today_iso()) - timedelta(days=days)).date().isoformat()
    return conn.execute(
        "SELECT * FROM health_daily WHERE day <= ? ORDER BY day DESC LIMIT 1",
        (cutoff,)).fetchone()
