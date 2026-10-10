"""The store side of memory sections, and the checks a body, title or tag write passes."""

from __future__ import annotations

import re
import sqlite3

from memai import contract, guard, sections
from memai.lite import now_iso

# Characters a stripped title may hold: past this it restates the memory instead of naming it.
TITLE_MAX = contract.TITLE_MAX
# The same for the fields that point at a memory rather than state it, so one row always fits a
# page of any listing.
TAGS_MAX = contract.TAGS_MAX
SOURCE_REF_MAX = contract.SOURCE_REF_MAX
DOMAIN_MAX = contract.DOMAIN_MAX


# Whether every body in this store has been read into memory_sections. Set
def unread_sections(conn: sqlite3.Connection) -> int:
    """How many bodies of a sectioned type nobody has read yet.

    A body that has been read leaves something behind either way: the
    fields it was read into, or a queue row saying what stopped it. One
    with neither predates the spec its type now has.

    This is DERIVED rather than recorded, and that is the point. A stored
    "the migration ran" flag says nothing about which spec it ran under, so
    the day a type joins SECTION_SPEC the flag is a false claim nobody
    notices: the panel reports a clean store, the strict refusal stays on,
    and the bodies of the new type are frozen -- unreadable and unwritable
    at once. Counting them instead makes a store unread again the moment
    its spec grows, which is the state it is actually in.
    """
    types = tuple(sections.SECTION_SPEC)
    if not types:
        return 0
    marks = ",".join("?" * len(types))
    return conn.execute(
        f"""SELECT COUNT(*) FROM memories m
             WHERE m.type IN ({marks})
               AND NOT EXISTS (SELECT 1 FROM memory_sections s WHERE s.memory_uid = m.uid)
               AND NOT EXISTS (SELECT 1 FROM section_migration q WHERE q.memory_uid = m.uid)""",
        types,
    ).fetchone()[0]


def sections_read(conn: sqlite3.Connection) -> bool:
    """Whether every body whose type has fields has been read into them."""
    return unread_sections(conn) == 0


def section_error(conn: sqlite3.Connection, type: str, content: str) -> str | None:
    """Why this body cannot be written as this type, or None.

    Silent while any body of a sectioned type is still unread: refusing
    then would lock the very rows a human has to work through, and a store
    whose spec just grew is in exactly that state. Reading the store is
    what turns the refusal on.
    """
    if not sections.is_sectioned(type) or not sections_read(conn):
        return None
    problems = sections.read(type, content).problems
    if not problems:
        return None
    return (f"a {type} body is made of {', '.join(s.label for s in sections.spec_for(type))} "
            f"and this one does not read that way: {'; '.join(problems)}")


def _refuse_unreadable(conn: sqlite3.Connection, type: str, content: str) -> None:
    error = section_error(conn, type, content)
    if error:
        raise ValueError(error)


def leak_error(type: str, text: str) -> str | None:
    """Say why `text` cannot be stored, or None if it can.

    Refuses a body or a title carrying a tool call's own source -- a closing
    tag naming the call frame or one of the writer's own parameters. That
    text is a call whose parameter tags were typed without the antml:
    prefix: the fields after the first one are inside this text instead of
    in their own columns. The marks, and what is not one, are
    memai.guard.leak_marks; `type` selects the parameter names, so a type
    with no writer is read against the frame alone.

    The PreToolUse guard refuses such a call before it is made. This is the
    same refusal for one that arrives another way -- the dashboard, an
    import of staged text, a host with no hooks registered.
    """
    marks = guard.leak_marks(type, text)
    if not marks:
        return None
    return (
        f"the text carries a tool call's own source ({', '.join(marks)}): a "
        f"parameter tag typed without the antml: prefix stays in the text of "
        f"the parameter before it, so the fields it opened -- the domain, the "
        f"tags -- are in this text instead of their own columns. Retype the "
        f"call with every tag prefixed. If the memory is ABOUT this defect, "
        f"put a space inside the closing tag so the quote is not a mark."
    )


def _refuse_leak(type: str, *texts: str) -> None:
    """Refuse any of `texts` that carries a tool call's own source.

    Every text field a writer fills goes through this: the body, the title,
    the tags and the source_ref. A leaked call lands in whichever one it was
    typed under, and the tags are as common a landing place as the body.
    """
    for text in texts:
        error = leak_error(type, text)
        if error:
            raise ValueError(error)


def title_error(value: str) -> str | None:
    """Say why `value` cannot title a memory, or None if it can.

    Measures the stripped string against TITLE_MAX. An empty title is not an
    error here: the callers that require one reject it themselves, and the
    ones that allow it pass ''.
    """
    return length_error("title", value, TITLE_MAX)


def length_error(field: str, value: str, limit: int) -> str | None:
    """Say that `value`, stripped, is too long for `field`, or None when it fits `limit`."""
    value = value.strip()
    if len(value) > limit:
        return f"{field} is {len(value)} characters; the limit is {limit}"
    return None


def refuse_long(**fields: str) -> None:
    """Raise for the first of `fields` (tags, source_ref, domain) past its ceiling."""
    limits = {"tags": TAGS_MAX, "source_ref": SOURCE_REF_MAX, "domain": DOMAIN_MAX}
    for field, value in fields.items():
        error = length_error(field, value, limits[field])
        if error:
            raise ValueError(error)


def _write_sections(conn: sqlite3.Connection, uid: str, type: str, content: str) -> None:
    """Read a body into its fields, replacing whatever was stored for it.

    The only writer of memory_sections and section_migration. Every writer
    of `memories.content` calls it with the body it just wrote, so the rows
    describe the text that is there now.

    A type with no spec keeps no rows in either table. A body that does not
    conform leaves a queue row saying what stops it, and whatever fields
    could still be read.
    """
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
    spec = sections.spec_for(type)
    if not spec:
        return
    reading = sections.read(type, content)
    seq = {s.key: i for i, s in enumerate(spec)}
    if reading.sections:
        conn.executemany(
            "INSERT INTO memory_sections (memory_uid, seq, key, text) VALUES (?, ?, ?, ?)",
            [(uid, seq[k], k, v) for k, v in reading.sections.items()],
        )
    if reading.problems:
        conn.execute(
            "INSERT INTO section_migration (memory_uid, verdict, detail, decided_at) "
            "VALUES (?, 'needs_review', ?, ?)",
            (uid, "; ".join(reading.problems), now_iso()),
        )


_BODY_LINK = re.compile(r"\[\[([0-9a-f]{16})\]\]")


def body_links(conn: sqlite3.Connection, uid: str, content: str) -> dict[str, dict]:
    """What each [[uid]] written in a body points at, keyed by that uid.

    A wikilink is written INSIDE the text, so it says nothing about the
    relations table: `linked` reports whether an edge exists either way
    between the two, which is what turns a reference somebody typed into a
    reference the graph can be queried for. A uid nothing resolves comes
    back as {"missing": True} rather than being left out, so a reader is
    told the target is gone instead of being handed a link that fails.
    """
    targets = {m.group(1) for m in _BODY_LINK.finditer(content or "")} - {uid}
    if not targets:
        return {}
    edges = {
        r["other"] for r in conn.execute(
            "SELECT to_uid AS other FROM relations WHERE from_uid = ? "
            "UNION SELECT from_uid FROM relations WHERE to_uid = ?", (uid, uid))
    }
    found = {}
    marks = ",".join("?" * len(targets))
    for row in conn.execute(
        f"SELECT uid, type, domain, status, content FROM memories WHERE uid IN ({marks})",
        tuple(targets),
    ):
        found[row["uid"]] = {
            "uid": row["uid"], "type": row["type"], "domain": row["domain"],
            "status": row["status"], "snippet": (row["content"] or "")[:120],
            "linked": row["uid"] in edges,
        }
    for target in targets - set(found):
        found[target] = {"uid": target, "missing": True}
    return found


def section_problem(conn: sqlite3.Connection, uid: str) -> str:
    """What stops this memory's body conforming, or "" when nothing does."""
    row = conn.execute(
        "SELECT detail FROM section_migration WHERE memory_uid = ?", (uid,)
    ).fetchone()
    return row["detail"] if row else ""


def section_queue(conn: sqlite3.Connection) -> list[dict]:
    """The bodies that do not conform, newest first, with what stops each."""
    return [
        {"uid": r["uid"], "type": r["type"], "domain": r["domain"],
         "status": r["status"], "detail": r["detail"],
         "snippet": (r["content"] or "")[:160],
         "created_at": r["created_at"]}
        for r in conn.execute(
            """SELECT m.uid, m.type, m.domain, m.status, m.content, m.created_at, s.detail
                 FROM section_migration s JOIN memories m ON m.uid = s.memory_uid
                ORDER BY m.created_at DESC"""
        )
    ]


def get_sections(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """A memory's fields in spec order; empty for a type with no spec."""
    return [
        {"key": r["key"], "text": r["text"]}
        for r in conn.execute(
            "SELECT key, text FROM memory_sections WHERE memory_uid = ? ORDER BY seq",
            (uid,),
        )
    ]
