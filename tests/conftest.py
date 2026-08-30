"""Shared fixtures and body-shaping helpers for the test suite."""

from __future__ import annotations

from memai import db, sections


def shaped(type_: str, text: str) -> str:
    """A body of `type_` that reads back into its fields, carrying `text`.

    For a test that needs a memory of some type and does not care what it
    says. `text` goes under the first field; the rest carry the same filler
    everywhere, so it is present in every document, distinguishes none of
    them, and carries no lexical weight.
    """
    spec = sections.spec_for(type_)
    if not spec:
        return text
    return sections.render(
        type_, {s.key: text if i == 0 else "nothing to add" for i, s in enumerate(spec)})


def unmigrated(conn) -> None:
    """Put the store in the state it is in before it has been read.

    Writes are queued rather than refused while this holds, which is how a
    body that predates the spec gets into a store at all.

    Readiness is derived from the rows, so this plants what an unread store
    has in it: one body of a sectioned type that nothing has read. A store
    holding none at all is vacuously read, which is right for a new one and
    useless for a test that needs the other state.
    """
    type_ = next(iter(sections.SECTION_SPEC))
    uid = db.insert_memory(conn, type=type_, domain="acme",
                           content=shaped(type_, "a body from before the spec"))
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
