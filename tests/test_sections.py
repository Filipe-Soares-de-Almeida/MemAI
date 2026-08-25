"""Reading a memory's body into its named fields.

The body stays the record and the rows are read out of it, so what these
tests guarantee is that the two say the same thing after every write: an
insert, a rewrite, a restore, a retype and a purge.

Every example is synthetic: bodies about cache warmup and queue drains,
filed under acme/x100/p200.
"""

from __future__ import annotations

import pytest

from memai import db, guard, sections, server

CHECKPOINT = (
    "INTENT: drain the queue before the nightly export\n"
    "ESTABLISHED: the worker retries three times, then parks the row\n"
    "PURSUING: the parked rows from the last run\n"
    "OPEN QUESTIONS: whether a parked row should age out"
)

ANTI_PATTERN = (
    "TEMPTATION: widen the batch until the drain keeps up\n"
    "WHY WRONG: a wider batch holds the lock longer and the writers pile up\n"
    "INSTEAD: keep the batch and add a second worker on its own cursor"
)


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


def sections_of(conn, uid) -> dict[str, str]:
    return {s["key"]: s["text"] for s in db.get_sections(conn, uid)}


def queued(conn) -> dict[str, str]:
    return {r["memory_uid"]: r["detail"]
            for r in conn.execute("SELECT memory_uid, detail FROM section_migration")}


# --------------------------------------------------------------- the parser

def test_a_conforming_body_reads_into_its_fields():
    reading = sections.read("checkpoint", CHECKPOINT)
    assert reading.conforms
    assert reading.sections["intent"] == "drain the queue before the nightly export"
    assert reading.sections["open_questions"] == "whether a parked row should age out"


def test_rendering_what_was_read_gives_the_body_back():
    for type_, body in (("checkpoint", CHECKPOINT), ("anti_pattern", ANTI_PATTERN)):
        assert sections.render(type_, sections.read(type_, body).sections) == body


def test_a_type_with_no_spec_conforms_whatever_it_says():
    assert sections.read("note", "a fact with no shape").conforms
    assert sections.spec_for("note") == ()


@pytest.mark.parametrize("body, complaint", [
    ("CHECKPOINT @ 2026-01-01\n" + CHECKPOINT, "does not open with INTENT:"),
    (CHECKPOINT.replace("PURSUING: the parked rows from the last run", "PURSUING:"),
     "nothing under PURSUING"),
    (CHECKPOINT.replace("ESTABLISHED: the worker retries three times, then parks the row\n", ""),
     "no line opens with ESTABLISHED"),
    (CHECKPOINT + "\nPURSUING: and one more", "more than one line opens with PURSUING"),
    ("a body with none of the labels at all", "no line opens with"),
])
def test_a_body_that_does_not_conform_says_what_stops_it(body, complaint):
    reading = sections.read("checkpoint", body)
    assert not reading.conforms
    assert any(complaint in p for p in reading.problems)


def test_labels_out_of_order_do_not_conform():
    body = "\n".join([
        "INTENT: drain the queue",
        "PURSUING: the parked rows",
        "ESTABLISHED: the worker retries three times",
        "OPEN QUESTIONS: whether a parked row should age out",
    ])
    assert "out of order" in " ".join(sections.read("checkpoint", body).problems)


def test_a_field_keeps_the_line_breaks_inside_it():
    body = CHECKPOINT.replace(
        "ESTABLISHED: the worker retries three times, then parks the row",
        "ESTABLISHED: it retries three times\n\n- then it parks the row",
    )
    established = sections.read("checkpoint", body).sections["established"]
    assert established == "it retries three times\n\n- then it parks the row"


# ----------------------------------------------------- the rows and the body

def test_writing_a_memory_fills_its_sections(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme/x100/p200")
    assert sections_of(conn, uid)["pursuing"] == "the parked rows from the last run"
    assert queued(conn) == {}


def test_a_type_with_no_spec_keeps_no_rows(conn):
    uid = db.insert_memory(conn, type="note", content="the cache warms on boot", domain="acme")
    assert db.get_sections(conn, uid) == []
    assert queued(conn) == {}


def test_rewriting_the_body_rewrites_the_rows(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme/x100")
    db.update_memory_content(
        conn, uid, CHECKPOINT.replace("PURSUING: the parked rows from the last run",
                                      "PURSUING: nothing, the queue is empty"))
    assert sections_of(conn, uid)["pursuing"] == "nothing, the queue is empty"


def test_a_body_that_stops_conforming_is_queued_not_refused(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme/x100")
    assert db.update_memory_content(conn, uid, "just prose now") is True
    assert db.get_memory(conn, uid)["content"] == "just prose now"
    assert "no line opens with" in queued(conn)[uid]
    assert db.get_sections(conn, uid) == []


def test_a_body_that_starts_conforming_leaves_the_queue(conn):
    uid = db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")
    assert uid in queued(conn)
    db.update_memory_content(conn, uid, CHECKPOINT)
    assert queued(conn) == {}
    assert sections_of(conn, uid)["intent"] == "drain the queue before the nightly export"


def test_an_empty_field_is_queued_and_the_others_still_read(conn):
    uid = db.insert_memory(
        conn, type="anti_pattern", domain="acme/x100",
        content=ANTI_PATTERN.replace(
            "INSTEAD: keep the batch and add a second worker on its own cursor", "INSTEAD:"))
    assert "nothing under INSTEAD" in queued(conn)[uid]
    assert sections_of(conn, uid)["pattern"] == "widen the batch until the drain keeps up"


def test_restoring_a_memory_fills_its_sections(conn):
    db.restore_memory(conn, {"uid": "a1b2c3d4e5f60718", "type": "anti_pattern",
                             "content": ANTI_PATTERN, "domain": "acme/x100"})
    assert sections_of(conn, "a1b2c3d4e5f60718")["instead"].startswith("keep the batch")


def test_purging_a_memory_takes_its_sections_with_it(conn):
    uid = db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")
    assert db.purge_memory(conn, uid) is True
    assert db.get_sections(conn, uid) == []
    assert queued(conn) == {}


# ------------------------------------------------------------ the migrated flag

def test_a_store_with_nothing_sectioned_counts_as_migrated(conn):
    assert db.sections_migrated(conn) is True


def test_a_store_holding_a_sectioned_body_does_not(tmp_path):
    path = tmp_path / "legacy.db"
    with db.connect(path) as c:
        db.insert_memory(c, type="checkpoint", content=CHECKPOINT, domain="acme")
        c.execute("DELETE FROM meta WHERE key = ?", (db.SECTIONS_MIGRATED_KEY,))
    with db.connect(path) as c:
        assert db.sections_migrated(c) is False


# ----------------------------------------------------------------- the guard

@pytest.mark.parametrize("tool", sorted(sections.SECTION_SPEC))
def test_the_guard_requires_exactly_the_fields_the_spec_names(tool):
    assert guard.GUARDED[tool] == tuple(s.key for s in sections.SECTION_SPEC[tool])


@pytest.mark.parametrize("tool", sorted(sections.SECTION_SPEC))
def test_the_writing_tool_composes_a_body_the_parser_reads_back(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "tool.db")
    values = {s.key: f"what goes under {s.label.lower()}"
              for s in sections.SECTION_SPEC[tool]}
    result = getattr(server, tool)(domain="acme/x100/p200", **values)
    with db.connect(tmp_path / "tool.db") as c:
        assert sections_of(c, result["uid"]) == values
        assert queued(c) == {}
