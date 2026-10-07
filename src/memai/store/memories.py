"""Memory rows: write, restore, read and edit them, their also-paths, and their usage counters."""

from __future__ import annotations

import sqlite3

from memai import contract, sections
from memai.lite import normalize_domain, now_iso
from memai.store.connection import new_uid
from memai.store.domains import (
    ALSO_SEP,
    apply_domain_policy,
    apply_link_policy,
    in_domain,
    parse_domains,
)
from memai.store.health import normalize_review_after
from memai.store.sections import _refuse_leak, _refuse_unreadable, _write_sections, title_error

# Trust on its own axis beside `status`; retrieval reads it too: a contradicted memory sorts last
# and a warm-up leaves it out (search_ranked, _sound_clause).
CONFIDENCE_CONTRADICTED = "contradicted"


def migrate_sections(conn: sqlite3.Connection) -> dict:
    """Read every sectioned body in the store into memory_sections, once.

    Returns how many bodies already conformed, how many were rewritten to
    reach the canonical shape, and how many are left in the queue for a
    human. Re-running it rewrites nothing: what conformed on the first pass
    conforms on the second.

    A body whose only fault is text above its first label is rewritten from
    the fields that text hides -- the preamble goes, the fields are
    re-emitted in spec order. That rewrite goes through
    update_memory_content, so it lands in `edits` next to the body it
    replaced and the row is reindexed.

    A body left in the queue can only be written through set_sections,
    which builds the body from its fields and so cannot produce another one
    that does not conform.
    """
    types = tuple(sections.SECTION_SPEC)
    rows = conn.execute(
        f"SELECT uid, type, content FROM memories WHERE type IN ({','.join('?' * len(types))})",
        types,
    ).fetchall()
    conformed = rewritten = 0
    for row in rows:
        uid, type_, content = row["uid"], row["type"], row["content"]
        if sections.read(type_, content).conforms:
            _write_sections(conn, uid, type_, content)
            conformed += 1
            continue
        salvaged = sections.salvage(type_, content)
        if salvaged.conforms:
            update_memory_content(conn, uid, sections.render(type_, salvaged.sections),
                                  note="sections: rewritten to the canonical body")
            rewritten += 1
            continue
        _write_sections(conn, uid, type_, content)
    pending = conn.execute("SELECT COUNT(*) AS n FROM section_migration").fetchone()["n"]
    return {"total": len(rows), "conformed": conformed,
            "rewritten": rewritten, "needs_review": pending}


def set_sections(conn: sqlite3.Connection, uid: str, values: dict, note: str = "") -> bool:
    """Replace a memory's body with one rendered from its fields.

    The way out of the queue: the body is built from the fields rather than
    typed, so what it writes conforms as long as no field was left empty.
    Raises ValueError for a memory whose type has no spec, and for a field
    the spec does not name.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    spec = sections.spec_for(row["type"])
    if not spec:
        raise ValueError(f"a {row['type']} has no sections to set")
    unknown = sorted(set(values) - {s.key for s in spec})
    if unknown:
        raise ValueError(f"not a section of a {row['type']}: {', '.join(unknown)}")
    empty = [s.label for s in spec if not str(values.get(s.key, "")).strip()]
    if empty:
        raise ValueError(f"nothing under {', '.join(empty)}")
    return update_memory_content(conn, uid, sections.render(row["type"], values),
                                 note=note or "sections: set by hand")


def insert_memory(
    conn: sqlite3.Connection,
    *,
    type: str,
    content: str,
    title: str = "",
    domain: str = "",
    also: str = "",
    session: str = "",
    tags: str = "",
    confidence: str = "unverified",
    created_at: str | None = None,
    review_after: str = "",
    source_ref: str = "",
) -> str:
    _refuse_unreadable(conn, type, content)
    _refuse_leak(type, content, title, tags, source_ref)
    error = title_error(title)
    if error:
        raise ValueError(error)
    uid = new_uid()
    ts = created_at or now_iso()
    domain = apply_domain_policy(conn, domain)
    links = apply_link_policy(conn, also, domain)
    blob = ALSO_SEP.join(links)
    review_after = normalize_review_after(review_after, today=(created_at or ts)[:10])
    conn.execute(
        """INSERT INTO memories
           (uid, type, domain, also_domains, session, tags, title, content, status,
            confidence, created_at, updated_at, review_after, source_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
        (uid, type, domain, blob, session, tags, title.strip(), content, confidence,
         ts, ts, review_after, source_ref.strip()),
    )
    if links:
        conn.executemany(
            "INSERT INTO memory_domains (memory_uid, domain, created_at) VALUES (?, ?, ?)",
            [(uid, path, ts) for path in links],
        )
    _write_sections(conn, uid, type, content)
    return uid


def restore_memory(conn: sqlite3.Connection, record: dict) -> str:
    """Write a memory back exactly as it was, uid and timestamps included.

    insert_memory() coins a uid and stamps `now`, which is right for a
    memory being made and wrong for one being restored: an import that
    renumbered every row would break every relation, node link and jump
    pointing at it, and would date a two-year-old decision to today.

    Domain policy is NOT applied. A restore reproduces a store; coercing
    the paths on the way in would mean an export and its import disagree
    about where things are filed, which is the one thing a round trip has
    to get right.
    """
    uid = str(record["uid"])
    ts = record.get("created_at") or now_iso()
    domain = normalize_domain(record.get("domain", ""))
    links = [normalize_domain(p) for p in parse_domains(record.get("also") or [])]
    links = [p for p in links if p and not in_domain(domain, p)]
    pin = str(record.get("pin") or "")
    error = pin_error(pin, domain)
    if error:
        raise ValueError(error)
    conn.execute(
        """INSERT INTO memories
           (uid, type, domain, also_domains, session, tags, title, content, status,
            confidence, superseded_by, created_at, updated_at, review_after, source_ref, pin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, record["type"], domain, ALSO_SEP.join(links),
         record.get("session", ""), record.get("tags", ""),
         record.get("title", ""), record.get("content", ""),
         record.get("status", "active"), record.get("confidence", "unverified"),
         record.get("superseded_by") or None, ts, record.get("updated_at") or ts,
         record.get("review_after", ""), record.get("source_ref", ""), pin),
    )
    if links:
        conn.executemany(
            "INSERT INTO memory_domains (memory_uid, domain, created_at) VALUES (?, ?, ?)",
            [(uid, path, ts) for path in links])
    if record.get("recalls"):
        conn.execute(
            "INSERT INTO memory_usage (memory_uid, recall_count, last_recalled_at) "
            "VALUES (?, ?, ?)",
            (uid, int(record["recalls"]), record.get("last_recall") or ts))
    _write_sections(conn, uid, record["type"], record.get("content", ""))
    return uid


def restore_diagram(conn: sqlite3.Connection, record: dict) -> None:
    """Put a diagram's graph back under an already-restored memory row.

    Positions come from the export rather than the layout engine: an
    arrangement somebody made by hand is part of the record, and
    recomputing it on import would quietly redraw every flow in the store.
    """
    uid = str(record["uid"])
    conn.execute(
        "INSERT INTO diagrams (memory_uid, kind, title, summary, font_scale) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, record.get("diagram_kind", "flowchart"), record.get("title", ""),
         record.get("summary", ""), record.get("font_scale", 1)))
    # an export whose memory record carries no title holds the name here
    conn.execute("UPDATE memories SET title = ? WHERE uid = ? AND title = ''",
                 (record.get("title", ""), uid))
    conn.executemany(
        "INSERT INTO diagram_nodes (memory_uid, node_key, shape, label, note, seq, x, y, w, h) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(uid, n["key"], n.get("shape", "step"), n.get("label", ""), n.get("note", ""),
          i, n.get("x") or 0.0, n.get("y") or 0.0, n.get("w"), n.get("h"))
         for i, n in enumerate(record.get("nodes") or [])])
    conn.executemany(
        "INSERT INTO diagram_edges (memory_uid, from_key, to_key, label, seq) "
        "VALUES (?, ?, ?, ?, ?)",
        [(uid, e["from"], e["to"], e.get("label", ""), i)
         for i, e in enumerate(record.get("edges") or [])])


def restore_diagram_refs(conn: sqlite3.Connection, record: dict) -> None:
    """A diagram's links and jumps, once every memory they name exists.

    Separate from restore_diagram because both point at OTHER memories: a
    flow can link a step to a note filed later in the file, and a jump
    reaches a diagram that has not been read yet. Skips a reference whose
    other end is not in the import -- a partial export is a legitimate
    thing to restore, and a dangling row is not.
    """
    uid = str(record["uid"])
    ts = record.get("created_at") or now_iso()

    def known(other: str) -> bool:
        return get_memory(conn, other) is not None

    conn.executemany(
        "INSERT OR IGNORE INTO diagram_node_links "
        "(memory_uid, node_key, target_uid, relation_type, created_at) VALUES (?, ?, ?, ?, ?)",
        [(uid, link["node_key"], link["target_uid"], link.get("relation_type", "explains"),
          link.get("created_at") or ts)
         for link in (record.get("links") or []) if known(link["target_uid"])])
    # only the outgoing side: a jump is stored once and read from both ends,
    # so restoring both would write the same row twice
    conn.executemany(
        "INSERT OR IGNORE INTO diagram_jumps "
        "(from_uid, from_node, to_uid, to_node, label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [(uid, j["node_key"], j["peer_uid"], j.get("peer_node", ""), j.get("label", ""),
          j.get("created_at") or ts)
         for j in (record.get("jumps") or [])
         if j.get("direction") == "out" and known(j["peer_uid"])])


def restore_edit(conn: sqlite3.Connection, record: dict) -> None:
    """Put one row of a memory's edit history back as it was exported."""
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(record["uid"]), record.get("edited_at") or now_iso(),
         record.get("prev_content", ""), record.get("new_content", ""),
         record.get("note", "")))


def get_memory(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM memories WHERE uid = ?", (uid,)).fetchone()


def memory_row(conn: sqlite3.Connection, uid: str | None) -> sqlite3.Row:
    """The memory `uid` names; ValueError when it names none."""
    row = get_memory(conn, uid) if uid else None
    if row is None:
        raise ValueError(f"no memory {uid!r}")
    return row


def update_memory_content(
    conn: sqlite3.Connection, uid: str, new_content: str, note: str = "",
    *, append: bool = False, leaked_ok: bool = False,
) -> bool:
    """Replace a memory's content, or add to the end of it.

    append exists because the alternative is a caller reading the whole
    body, restating it, and sending it back to add one line -- which costs
    the body twice and stakes the existing text on it being copied
    faithfully. The edit history records the same thing either way: what it
    said before, and what it says now.

    leaked_ok writes a body carrying a tool call's own source, which every
    other caller is refused (see leak_error). It is for restoring a body that
    was already stored -- undoing an `unleak` puts back what the row held.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    if append:
        new_content = f"{row['content']}\n{new_content}" if row["content"] else new_content
    _refuse_unreadable(conn, row["type"], new_content)
    if not leaked_ok:
        _refuse_leak(row["type"], new_content)
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], new_content, note),
    )
    conn.execute(
        "UPDATE memories SET content = ?, updated_at = ? WHERE uid = ?",
        (new_content, now_iso(), uid),
    )
    _write_sections(conn, uid, row["type"], new_content)
    return True


def set_generated_content(conn: sqlite3.Connection, uid: str, content: str) -> None:
    """Rewrite a body generated from other rows, recording no edit: those rows are the history."""
    row = memory_row(conn, uid)
    conn.execute("UPDATE memories SET content = ?, updated_at = ? WHERE uid = ?",
                 (content, now_iso(), uid))
    _write_sections(conn, uid, row["type"], content)


def edit_count(conn: sqlite3.Connection, uid: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM edits WHERE memory_uid = ?", (uid,)).fetchone()[0]


def get_edit_history(conn: sqlite3.Connection, uid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM edits WHERE memory_uid = ? ORDER BY edited_at ASC", (uid,)
    ).fetchall()


def set_status(
    conn: sqlite3.Connection,
    uid: str,
    status: str,
    superseded_by: str | None = None,
    note: str = "",
) -> bool:
    """Change a memory's status; optionally record why in the audit log.

    A task keeps its state in step: archiving an open task cancels it, and
    restoring reopens it only when an item is still todo or doing.

    When `note` is given it is stored as a status-change audit entry in
    `edits` (prev_content == new_content, since the content itself is not
    touched). Archiving does not change what the memory says, so nothing
    here goes through update_memory_content.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute(
        "UPDATE memories SET status = ?, superseded_by = ?, updated_at = ? WHERE uid = ?",
        (status, superseded_by, now_iso(), uid),
    )
    if row["type"] == TASK_TYPE:
        if status == "archived":
            conn.execute(
                "UPDATE tasks SET state = 'cancelled' WHERE memory_uid = ? AND state = 'open'", (uid,)
            )
        elif status == "active":
            conn.execute(
                "UPDATE tasks SET state = 'open', completed_at = '' WHERE memory_uid = ? "
                "AND EXISTS (SELECT 1 FROM task_items WHERE memory_uid = ? "
                "AND state IN ('todo', 'doing'))", (uid, uid)
            )
    if note:
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
            (uid, now_iso(), row["content"], row["content"], note),
        )
    return True


def set_confidence(conn: sqlite3.Connection, uid: str, confidence: str) -> bool:
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute(
        "UPDATE memories SET confidence = ?, updated_at = ? WHERE uid = ?",
        (confidence, now_iso(), uid),
    )
    return True


PIN_VALUES = ("", *contract.PINS)


def pin_error(pin: str, domain: str) -> str | None:
    """Why `pin` cannot be stored on a memory filed at `domain`, or None."""
    if pin not in PIN_VALUES:
        return f"pin must be one of '', 'global', 'domain'; got {pin!r}"
    if pin == "domain" and not domain:
        return "a memory without a domain can only be pinned 'global'"
    return None


def set_pin(conn: sqlite3.Connection, uid: str, pin: str) -> bool:
    """Pin or unpin a memory. False for an unknown uid; ValueError on a bad pin."""
    if pin not in PIN_VALUES:
        raise ValueError(pin_error(pin, ""))
    row = get_memory(conn, uid)
    if row is None:
        return False
    error = pin_error(pin, row["domain"])
    if error:
        raise ValueError(error)
    conn.execute("UPDATE memories SET pin = ?, updated_at = ? WHERE uid = ?",
                 (pin, now_iso(), uid))
    return True


def set_review_after(conn: sqlite3.Connection, uid: str, value: str) -> bool:
    """Move (or clear) a memory's recheck date, and audit the move.

    Nothing is reindexed: the index covers content, tags and domains, and a
    date is none of those. Audited, because "this was rechecked and
    pushed out six months" is exactly the kind of decision a later pass
    needs to be able to see it did not invent.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    value = normalize_review_after(value)
    if value == row["review_after"]:
        return True
    conn.execute(
        "UPDATE memories SET review_after = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid))
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"meta: review_after '{row['review_after']}' -> '{value}'"))
    return True


def set_source_ref(conn: sqlite3.Connection, uid: str, value: str, note: str = "") -> bool:
    """Point (or repoint) a memory at what it came from, and audit the move.

    Nothing is reindexed, for the same reason as the date: the index covers
    content, tags and domains. Audited, because the reference is what a
    later pass checks the claim against, and "this was pointed at that file
    on purpose" is not something it should have to infer.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    value = value.strip()
    if value == row["source_ref"]:
        return True
    _refuse_leak(row["type"], value)
    conn.execute(
        "UPDATE memories SET source_ref = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid))
    audit = f"meta: source_ref '{row['source_ref']}' -> '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"{audit} ({note})" if note else audit))
    return True


def set_tags(conn: sqlite3.Connection, uid: str, value: str, note: str = "") -> bool:
    """Replace a memory's tags, and audit the change.

    The update reindexes: `tags` is an indexed column, so the FTS trigger
    fires on it the way it does for the body. `value` replaces the field
    rather than adding to it -- pass the whole set that should survive,
    comma-separated.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    value = value.strip()
    if value == row["tags"]:
        return True
    _refuse_leak(row["type"], value)
    conn.execute(
        "UPDATE memories SET tags = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid))
    audit = f"meta: tags '{row['tags']}' -> '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"{audit} ({note})" if note else audit))
    return True


def set_title(conn: sqlite3.Connection, uid: str, value: str, note: str = "") -> bool:
    """Rename a memory, and audit the rename.

    The update reindexes: `title` is an indexed column, so the FTS trigger
    fires on it the way it does for the body. Refuses to clear one -- every
    writer requires a title, and a rename to nothing would leave a row no
    writer could have made.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    value = value.strip()
    if not value or value == row["title"]:
        return bool(value)
    _refuse_leak(row["type"], value)
    error = title_error(value)
    if error:
        raise ValueError(error)
    conn.execute(
        "UPDATE memories SET title = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid))
    audit = f"meta: title '{row['title']}' -> '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"{audit} ({note})" if note else audit))
    return True


def purge_memory(conn: sqlite3.Connection, uid: str) -> bool:
    """Irreversibly delete a memory row plus its edit history and relations.

    The memories_ad trigger removes the matching FTS row as part of the
    DELETE. Callers must gate this behind explicit user confirmation --
    forget() (soft-delete/archive) is the default and should be used
    unless the user specifically asked for permanent removal.

    Diagram tables cascade both ways: the graph of a purged diagram goes,
    and so does any OTHER diagram's node link or jump that pointed at this
    memory -- otherwise a purged note leaves a node link dangling at a uid
    that resolves to nothing.

    A task's rows (comments, item links, items, the head row) go with it,
    and so does any task item link that pointed at this memory.

    `memory_domains` goes with it for the same reason, and the FK on that
    table means it HAS to: the DELETE below is refused outright while a
    cross-listing still names this uid. No mirror to rewrite -- the row
    itself is on its way out. `memory_sections` and `section_migration`
    carry the same FK and go the same way.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    conn.execute("DELETE FROM memory_domains WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM memory_usage WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM edits WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM relations WHERE from_uid = ? OR to_uid = ?", (uid, uid))
    conn.execute("DELETE FROM optimization_suggestions WHERE target_uid = ?", (uid,))
    conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? OR target_uid = ?", (uid, uid)
    )
    conn.execute("DELETE FROM diagram_jumps WHERE from_uid = ? OR to_uid = ?", (uid, uid))
    conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM diagram_edges WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM diagrams WHERE memory_uid = ?", (uid,))
    conn.execute(
        "DELETE FROM task_note_items WHERE note_id IN "
        "(SELECT id FROM task_notes WHERE memory_uid = ?)", (uid,))
    conn.execute("DELETE FROM task_notes WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM task_comments WHERE memory_uid = ?", (uid,))
    conn.execute(
        "DELETE FROM task_item_links WHERE memory_uid = ? OR target_uid = ?", (uid, uid)
    )
    conn.execute("DELETE FROM task_items WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM tasks WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM memories WHERE uid = ?", (uid,))
    return True


def purge_memories(conn: sqlite3.Connection, uids: list[str]) -> dict:
    """purge_memory over many uids in the caller's transaction.

    Returns `{"purged": n, "missing": [uids that named no memory]}`. A uid
    that is already gone is reported, not an error, and a failure part-way
    rolls the whole batch back with the transaction. The same gate as
    purge_memory applies: the caller confirms before calling.
    """
    missing = [uid for uid in uids if not purge_memory(conn, uid)]
    return {"purged": len(uids) - len(missing), "missing": missing}


def record_recall(
    conn: sqlite3.Connection, uids, *, at: str | None = None,
    sources: dict[str, str] | None = None,
) -> int:
    """Count one read of each of these memories. Returns how many it touched.

    Called from the MCP tools and from nowhere else, deliberately. What
    this measures is "an agent was handed this memory in answer to
    something", and a person scrolling the dashboard is not that -- letting
    the admin surface inflate the counters would turn the one signal the
    curation pass has into a record of who browsed what.

    `sources` maps uid -> match_source for reads that came out of a search,
    so the store can later say how much of what it holds is ever found
    rather than merely listed. A read with no search behind it passes none,
    and counts only in recall_count.

    None of this may ever reach a ranking -- see the schema comment on
    memory_usage for why, and test_usage.py for the test that says so.

    Best-effort: a uid that does not exist is skipped rather than failing
    the read that produced it.
    """
    seen = [u for u in dict.fromkeys(uids) if u]
    if not seen:
        return 0
    ts = at or now_iso()
    live = {r["uid"] for r in conn.execute(
        f"SELECT uid FROM memories WHERE uid IN ({', '.join('?' * len(seen))})", seen)}
    rows = []
    for u in seen:
        if u not in live:
            continue
        found_by = (sources or {}).get(u)
        rows.append((u, ts, int(found_by == "fts")))
    conn.executemany(
        "INSERT INTO memory_usage "
        "(memory_uid, recall_count, last_recalled_at, via_fts) "
        "VALUES (?, 1, ?, ?) ON CONFLICT(memory_uid) DO UPDATE SET "
        "recall_count = recall_count + 1, last_recalled_at = excluded.last_recalled_at, "
        "via_fts = via_fts + excluded.via_fts",
        rows,
    )
    return len(rows)


def usage_for(conn: sqlite3.Connection, uids) -> dict[str, dict]:
    """{uid: {"recalls": n, "last_recall": iso}} for the ones ever read."""
    seen = [u for u in dict.fromkeys(uids) if u]
    if not seen:
        return {}
    rows = conn.execute(
        f"SELECT memory_uid, recall_count, last_recalled_at FROM memory_usage "
        f"WHERE memory_uid IN ({', '.join('?' * len(seen))})", seen).fetchall()
    return {r["memory_uid"]: {"recalls": r["recall_count"],
                              "last_recall": r["last_recalled_at"]} for r in rows}


def search_share(conn: sqlite3.Connection) -> dict:
    """How much of what got read a search found, against everything read.

    Answered by the store itself, over real use, instead of by a benchmark's
    guess at what a query looks like. Read it as a ratio and not as an
    absolute: a memory can be acted on from its snippet without ever being
    opened, so the found count is short by an unknown amount.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(via_fts), 0) AS fts, "
        "COALESCE(SUM(recall_count), 0) AS reads FROM memory_usage").fetchone()
    return {"fts": row["fts"], "reads": row["reads"], "from_search": row["fts"]}


DIAGRAM_TYPE = "diagram"


TASK_TYPE = "task"
# types whose content is generated from rows, so no prose scan or merge applies
GENERATED_TYPES = (DIAGRAM_TYPE, TASK_TYPE)
MEMORY_TYPES = contract.MEMORY_TYPES


def type_error(type_: str, allowed: tuple[str, ...] = MEMORY_TYPES) -> str | None:
    """The refusal for a type outside `allowed`, or None. An empty type means all."""
    if not type_ or type_ in allowed:
        return None
    return f"unknown type '{type_}'; valid types: {', '.join(allowed)}"


def get_domain_links(conn: sqlite3.Connection, uid: str) -> list[str]:
    """The extra domains one memory belongs to, beside the one it is filed at."""
    return [r["domain"] for r in conn.execute(
        "SELECT domain FROM memory_domains WHERE memory_uid = ? ORDER BY domain",
        (uid,))]


def domain_links_for(conn: sqlite3.Connection, uids) -> dict[str, list[str]]:
    """get_domain_links for a page of rows, in one query.

    A list view showing rows under a domain filter has to be able to say
    which of them are only cross-listed there, and a per-row lookup would
    make that N queries for a cosmetic truth.
    """
    wanted = set(uids)
    if not wanted:
        return {}
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT memory_uid, domain FROM memory_domains ORDER BY domain"):
        if r["memory_uid"] in wanted:
            out.setdefault(r["memory_uid"], []).append(r["domain"])
    return out


def _write_domain_links(conn: sqlite3.Connection, row: sqlite3.Row, paths: list[str]) -> list[str]:
    """Rewrite one memory's cross-listings: the rows and the index text.

    `memory_domains` is the truth every domain filter reads.
    `memories.also_domains` is the same paths as one field, and exists for
    the reader that cannot join it: the FTS index. Nothing filters on that
    field -- which is why it is written here, and only here, in the same
    breath as the rows it mirrors.
    """
    uid = row["uid"]
    conn.execute("DELETE FROM memory_domains WHERE memory_uid = ?", (uid,))
    now = now_iso()
    if paths:
        conn.executemany(
            "INSERT INTO memory_domains (memory_uid, domain, created_at) VALUES (?, ?, ?)",
            [(uid, path, now) for path in paths])
    blob = ALSO_SEP.join(paths)
    conn.execute(
        "UPDATE memories SET also_domains = ?, updated_at = ? WHERE uid = ?",
        (blob, now, uid))
    return paths


def set_domain_links(
    conn: sqlite3.Connection, uid: str, also, *, note: str = "", coerce: bool = True
) -> list[str]:
    """Replace the extra domains a memory belongs to. Returns what was stored.

    Audited in `edits` like any other metadata change, so a membership that
    was added and later dropped is still reconstructible. coerce=False keeps
    the casing as given -- see apply_link_policy.
    """
    row = get_memory(conn, uid)
    if row is None:
        raise ValueError(f"no memory {uid}")
    before = get_domain_links(conn, uid)
    paths = apply_link_policy(conn, also, row["domain"], coerce=coerce)
    if paths == before:
        return paths
    _write_domain_links(conn, row, paths)
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         note or f"meta: also '{', '.join(before)}' → '{', '.join(paths)}'"))
    return paths


def set_domain(conn: sqlite3.Connection, uid: str, domain: str, note: str = "") -> bool:
    """Re-home a memory: change the path it is FILED at, and audit it.

    The cross-listings are re-run afterwards even when the caller named
    none, because the policy that drops a redundant one reads the domain
    the memory ends up with -- a membership the old path needed can be
    covered by the new path's own prefix (apply_link_policy).

    Returns whether the row exists. Filing it where it already is is not a
    change and writes nothing.
    """
    row = get_memory(conn, uid)
    if row is None:
        return False
    domain = apply_domain_policy(conn, domain)
    if domain == row["domain"]:
        return True
    conn.execute(
        "UPDATE memories SET domain = ?, updated_at = ? WHERE uid = ?",
        (domain, now_iso(), uid))
    audit = f"meta: domain '{row['domain']}' -> '{domain}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"],
         f"{audit} ({note})" if note else audit))
    links = get_domain_links(conn, uid)
    if links:
        set_domain_links(conn, uid, links)
    return True


TAG_SEP = ", "


def merge_tags(existing: str, added: str) -> str:
    """`existing` with `added` appended, keeping order and dropping repeats.

    Case-insensitive on the comparison and case-preserving on the value: a
    store that already says 'F100_TOTAL' does not gain 'f100_total' beside
    it. Tags are free text separated by commas, which is what BM25 indexes,
    so nothing here reshapes a tag beyond trimming it.
    """
    out: list[str] = []
    seen: set[str] = set()
    for tag in (*existing.split(","), *added.split(",")):
        tag = tag.strip()
        if not tag or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        out.append(tag)
    return TAG_SEP.join(out)


def add_domain_link(conn: sqlite3.Connection, uid: str, domain: str) -> list[str]:
    """Cross-list a memory into one more domain. Returns the resulting set."""
    if not normalize_domain(domain):
        raise ValueError("domain is required")
    return set_domain_links(conn, uid, [*get_domain_links(conn, uid), domain])


def remove_domain_link(conn: sqlite3.Connection, uid: str, domain: str) -> list[str]:
    """Drop one cross-listing. Returns the resulting set.

    Matched on the exact path: dropping 'acme' does not drop a separate
    membership in 'acme/x100', which is a scope of its own.
    """
    path = normalize_domain(domain)
    return set_domain_links(
        conn, uid, [p for p in get_domain_links(conn, uid) if p != path])
