"""Store upkeep: integrity checks, the FTS index, orphaned rows, and compaction."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_ORPHAN_RELATIONS = """relations
   WHERE from_uid NOT IN (SELECT uid FROM memories)
      OR to_uid NOT IN (SELECT uid FROM memories)"""


def fts_integrity(conn: sqlite3.Connection) -> tuple[bool, str]:
    """FTS5 integrity-check; the 2-arg form also verifies the index against
    the external content table where supported.

    `detail` is empty when the check passes: the "all good" wording is a UI
    string and belongs in webui/i18n, not in an API response. Only the
    failure detail crosses the wire, because that is SQLite's own message
    and translating it would lose the thing an operator needs to read.
    """
    try:
        try:
            conn.execute("INSERT INTO memories_fts(memories_fts, rank) VALUES ('integrity-check', 1)")
        except sqlite3.OperationalError:
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
        return True, ""
    except sqlite3.DatabaseError as exc:
        return False, str(exc)


def relation_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """Every relation, and the ones with an endpoint missing from memories."""
    total = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    orphans = conn.execute(f"SELECT COUNT(*) FROM {_ORPHAN_RELATIONS}").fetchone()[0]
    return total, orphans


def integrity_report(conn: sqlite3.Connection) -> dict:
    """SQLite's quick_check, the FTS index checked and counted against its table,
    orphaned relations, and the bytes a VACUUM would give back."""
    quick = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
    fts_ok, fts_detail = fts_integrity(conn)
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return {
        "quick_check": quick,
        "fts_ok": fts_ok,
        "fts_detail": fts_detail,
        "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "fts_rows": conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0],
        "orphan_relations": relation_counts(conn)[1],
        "reclaimable": page_size * freelist,
    }


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """Rebuild the FTS index from the memories table; returns the rows it holds."""
    conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
    return conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]


def clean_orphans(conn: sqlite3.Connection) -> dict[str, int]:
    """Delete the rows that point at something gone, and count them per table."""
    rels = conn.execute(f"DELETE FROM {_ORPHAN_RELATIONS}").rowcount
    sugs = conn.execute(
        """DELETE FROM optimization_suggestions
           WHERE status = 'pending'
             AND target_uid IS NOT NULL
             AND target_uid NOT IN (SELECT uid FROM memories)""").rowcount
    links = conn.execute(
        """DELETE FROM diagram_node_links
           WHERE memory_uid NOT IN (SELECT uid FROM memories)
              OR target_uid NOT IN (SELECT uid FROM memories)
              OR node_key NOT IN (
                    SELECT node_key FROM diagram_nodes
                    WHERE diagram_nodes.memory_uid = diagram_node_links.memory_uid)""").rowcount
    task_links = conn.execute(
        """DELETE FROM task_item_links
           WHERE target_uid NOT IN (SELECT uid FROM memories)""").rowcount
    # a jump has four things that can rot -- both diagrams and both node
    # keys -- and `to_node` is legitimately empty for a whole-diagram jump
    jumps = conn.execute(
        """DELETE FROM diagram_jumps
           WHERE from_uid NOT IN (SELECT memory_uid FROM diagrams)
              OR to_uid NOT IN (SELECT memory_uid FROM diagrams)
              OR from_node NOT IN (
                    SELECT node_key FROM diagram_nodes
                    WHERE diagram_nodes.memory_uid = diagram_jumps.from_uid)
              OR (to_node <> '' AND to_node NOT IN (
                    SELECT node_key FROM diagram_nodes
                    WHERE diagram_nodes.memory_uid = diagram_jumps.to_uid))""").rowcount
    return {"relations": rels, "suggestions": sugs, "node_links": links,
            "task_links": task_links, "jumps": jumps}


def vacuum(path: Path) -> None:
    """Fold the WAL into the file and VACUUM it.

    Neither statement runs inside a transaction, so this opens its own
    autocommit connection instead of going through connect().
    """
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    finally:
        conn.close()
