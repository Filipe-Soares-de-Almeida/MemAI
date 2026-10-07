"""Typed edges between memories."""

from __future__ import annotations

import sqlite3

from memai.lite import now_iso
from memai.store.memories import get_memory


def add_relation(
    conn: sqlite3.Connection, from_uid: str, to_uid: str, relation_type: str, note: str = ""
) -> int:
    """Create a typed edge, or raise ValueError saying which rule it broke.

    The checks live here rather than in each caller because the two
    surfaces have to agree: the dashboard refused a self-edge, an unknown
    uid and a duplicate, and the MCP tool refused nothing -- a typo'd uid
    reached the INSERT, where the foreign key turned it into a raw
    IntegrityError rather than something an agent could act on.
    """
    if not (from_uid and to_uid and relation_type):
        raise ValueError("from_uid, to_uid and relation_type are required")
    if from_uid == to_uid:
        raise ValueError("a memory cannot relate to itself")
    for uid in (from_uid, to_uid):
        if get_memory(conn, uid) is None:
            raise ValueError(f"unknown memory: {uid}")
    dup = conn.execute(
        "SELECT id FROM relations WHERE from_uid = ? AND to_uid = ? AND relation_type = ?",
        (from_uid, to_uid, relation_type)).fetchone()
    if dup:
        raise ValueError(f"identical relation already exists (id {dup['id']})")
    cur = conn.execute(
        "INSERT INTO relations (from_uid, to_uid, relation_type, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (from_uid, to_uid, relation_type, note, now_iso()),
    )
    return cur.lastrowid or 0


def get_relations(conn: sqlite3.Connection, uid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM relations WHERE from_uid = ? OR to_uid = ? ORDER BY created_at ASC",
        (uid, uid),
    ).fetchall()
