"""Reading a memory's body into its named fields.

The body stays the record and the rows are read out of it, so what these
tests guarantee is that the two say the same thing after every write: an
insert, a rewrite, a restore, a retype and a purge.

Every example is synthetic: bodies about cache warmup and queue drains,
filed under acme/x100/p200.
"""

from __future__ import annotations

import pytest

from conftest import unmigrated
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


def test_a_body_that_stops_conforming_is_queued_while_the_store_is_unread(conn):
    unmigrated(conn)
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme/x100")
    assert db.update_memory_content(conn, uid, "just prose now") is True
    assert db.get_memory(conn, uid)["content"] == "just prose now"
    assert "no line opens with" in queued(conn)[uid]
    assert db.get_sections(conn, uid) == []


def test_a_body_that_starts_conforming_leaves_the_queue(conn):
    unmigrated(conn)
    uid = db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")
    assert uid in queued(conn)
    db.update_memory_content(conn, uid, CHECKPOINT)
    assert queued(conn) == {}
    assert sections_of(conn, uid)["intent"] == "drain the queue before the nightly export"


def test_an_empty_field_is_queued_and_the_others_still_read(conn):
    unmigrated(conn)
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
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    assert db.purge_memory(conn, uid) is True
    assert db.get_sections(conn, uid) == []
    assert queued(conn) == {}


# ------------------------------------------------------- whether it has been read

def test_a_store_with_nothing_sectioned_is_read(conn):
    assert db.sections_read(conn) is True
    assert db.unread_sections(conn) == 0


def test_a_body_nothing_has_read_leaves_the_store_unread(tmp_path):
    path = tmp_path / "legacy.db"
    with db.connect(path) as c:
        uid = db.insert_memory(c, type="checkpoint", content=CHECKPOINT, domain="acme")
        c.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    with db.connect(path) as c:
        assert db.sections_read(c) is False
        assert db.unread_sections(c) == 1


def test_a_body_in_the_queue_counts_as_read(conn):
    """It was read. What it says is that reading it did not work out."""
    unmigrated(conn)
    db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")
    db.migrate_sections(conn)
    assert db.section_queue(conn)
    assert db.sections_read(conn) is True


def test_a_type_joining_the_spec_makes_a_read_store_unread(conn, monkeypatch):
    """The defect this replaced: a stored flag recorded THAT the migration
    ran and not under which spec, so a type added afterwards left the flag
    claiming a clean store while its bodies were never read -- and the
    strict refusal, keyed on that flag, froze them."""
    db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    conn.execute("INSERT INTO memories (uid, type, content, created_at, updated_at) "
                 "VALUES ('c3d4e5f60718293a', 'handoff', 'pick it up here', '2026-01-01', '2026-01-01')")
    assert db.sections_read(conn) is True

    monkeypatch.setitem(sections.SECTION_SPEC, "handoff",
                        (sections.Section("content", "CONTENT"),))

    assert db.sections_read(conn) is False
    assert db.unread_sections(conn) == 1
    # and the refusal steps back off until the store is read again
    assert db.section_error(conn, "checkpoint", "not a checkpoint body") is None


# ----------------------------------------------------------------- the guard

@pytest.mark.parametrize("tool", sorted(sections.SECTION_SPEC))
def test_the_guard_requires_exactly_the_fields_the_spec_names(tool):
    # `title` is required of every writer and belongs to no spec; the fields
    # after it are the spec's, in its order.
    assert guard.GUARDED[tool] == ("title", *(s.key for s in sections.SECTION_SPEC[tool]))


@pytest.mark.parametrize("tool", sorted(sections.SECTION_SPEC))
def test_the_writing_tool_composes_a_body_the_parser_reads_back(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "tool.db")
    values = {s.key: f"what goes under {s.label.lower()}"
              for s in sections.SECTION_SPEC[tool]}
    result = getattr(server, tool)(
        title="a name for it", domain="acme/x100/p200", **values)
    with db.connect(tmp_path / "tool.db") as c:
        assert sections_of(c, result["uid"]) == values
        assert queued(c) == {}


# ------------------------------------------------------------ the migration

LEGACY_CHECKPOINT = "CHECKPOINT @ 2026-01-01T09:00:00\n" + CHECKPOINT
LEGACY_ANTI_PATTERN = "DOMAIN: acme-x100-queue-drain\n" + ANTI_PATTERN


def unread(conn, uid, content):
    """Put a body in the store the way it stood before anything read it.

    Taking its rows away is all it takes: readiness is derived, so a body
    with neither fields nor a queue row leaves the whole store unread.
    """
    conn.execute("UPDATE memories SET content = ? WHERE uid = ?", (content, uid))
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))


def test_salvage_forgives_a_preamble_and_nothing_else():
    assert sections.salvage("checkpoint", LEGACY_CHECKPOINT).conforms
    assert not sections.salvage("checkpoint", "CHECKPOINT @ 2026-01-01\nINTENT: only this").conforms


def test_the_migration_rewrites_a_body_hidden_under_a_header(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme/x100")
    unread(conn, uid, LEGACY_CHECKPOINT)

    result = db.migrate_sections(conn)

    assert result == {"total": 1, "conformed": 0, "rewritten": 1, "needs_review": 0}
    assert db.get_memory(conn, uid)["content"] == CHECKPOINT
    assert sections_of(conn, uid)["intent"] == "drain the queue before the nightly export"
    assert queued(conn) == {}


def test_the_rewrite_keeps_the_body_it_replaced(conn):
    uid = db.insert_memory(conn, type="anti_pattern", content=ANTI_PATTERN, domain="acme")
    unread(conn, uid, LEGACY_ANTI_PATTERN)
    db.migrate_sections(conn)

    history = db.get_edit_history(conn, uid)
    assert history[-1]["prev_content"] == LEGACY_ANTI_PATTERN
    assert history[-1]["new_content"] == ANTI_PATTERN


def test_the_migration_leaves_a_body_it_cannot_read_alone(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    unread(conn, uid, "a refutation written over the whole thing")

    result = db.migrate_sections(conn)

    assert result["needs_review"] == 1 and result["rewritten"] == 0
    assert db.get_memory(conn, uid)["content"] == "a refutation written over the whole thing"
    assert uid in queued(conn)


def test_the_migration_rewrites_nothing_on_a_second_run(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    unread(conn, uid, LEGACY_CHECKPOINT)

    db.migrate_sections(conn)
    again = db.migrate_sections(conn)

    assert again == {"total": 1, "conformed": 1, "rewritten": 0, "needs_review": 0}
    assert len(db.get_edit_history(conn, uid)) == 1


def test_the_migration_leaves_the_store_read(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    unread(conn, uid, "nothing readable here")
    assert db.sections_read(conn) is False

    db.migrate_sections(conn)

    # read is not the same as clean: this one came out in the queue
    assert db.sections_read(conn) is True
    assert uid in {e["uid"] for e in db.section_queue(conn)}


def test_the_queue_says_what_stops_each_body(conn):
    unmigrated(conn)
    uid = db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme/x100")
    entry = next(e for e in db.section_queue(conn) if e["uid"] == uid)
    assert entry["type"] == "checkpoint" and entry["domain"] == "acme/x100"
    assert "no line opens with" in entry["detail"]


# ----------------------------------------------------- the way out of the queue

def test_setting_the_fields_by_hand_builds_a_body_that_conforms(conn):
    unmigrated(conn)
    uid = db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")
    assert uid in queued(conn)

    db.set_sections(conn, uid, {
        "intent": "drain the queue",
        "established": "the worker parks a row after three tries",
        "pursuing": "the parked rows",
        "open_questions": "whether a parked row should age out",
    })

    assert queued(conn) == {}
    assert db.get_memory(conn, uid)["content"].startswith("INTENT: drain the queue\n")
    assert sections_of(conn, uid)["pursuing"] == "the parked rows"


@pytest.mark.parametrize("values, complaint", [
    ({"intent": "a", "established": "b", "pursuing": "c", "open_questions": " "},
     "nothing under OPEN QUESTIONS"),
    ({"intent": "a", "established": "b", "pursuing": "c", "open_questions": "d", "extra": "e"},
     "not a section of a checkpoint: extra"),
])
def test_setting_the_fields_refuses_what_would_not_conform(conn, values, complaint):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    with pytest.raises(ValueError, match=complaint):
        db.set_sections(conn, uid, values)


def test_a_type_with_no_spec_has_no_fields_to_set(conn):
    uid = db.insert_memory(conn, type="note", content="the cache warms on boot", domain="acme")
    with pytest.raises(ValueError, match="no sections"):
        db.set_sections(conn, uid, {"intent": "x"})


def test_reclassifying_a_stuck_body_is_the_other_way_out(conn):
    unmigrated(conn)
    uid = db.insert_memory(conn, type="checkpoint", content="a refutation", domain="acme")
    assert uid in queued(conn)

    conn.execute("UPDATE memories SET type = 'note' WHERE uid = ?", (uid,))
    db._write_sections(conn, uid, "note", "a refutation")

    assert queued(conn) == {}
    assert db.get_sections(conn, uid) == []


# ---------------------------------------------------- refusing what cannot be read

def test_a_read_store_refuses_a_body_that_does_not_conform(conn):
    with pytest.raises(ValueError, match="does not read that way"):
        db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")


def test_the_refusal_names_the_fields_the_type_holds(conn):
    with pytest.raises(ValueError, match="INTENT, ESTABLISHED, PURSUING, OPEN QUESTIONS"):
        db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")


def test_a_rewrite_that_would_break_the_shape_is_refused(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    with pytest.raises(ValueError, match="nothing under PURSUING"):
        db.update_memory_content(
            conn, uid, CHECKPOINT.replace("PURSUING: the parked rows from the last run",
                                          "PURSUING:"))
    assert db.get_memory(conn, uid)["content"] == CHECKPOINT


def test_a_type_with_no_spec_takes_any_body(conn):
    uid = db.insert_memory(conn, type="note", content="the cache warms on boot", domain="acme")
    assert db.update_memory_content(conn, uid, "anything at all") is True


def test_an_unread_store_queues_instead_of_refusing(conn):
    unmigrated(conn)
    uid = db.insert_memory(conn, type="checkpoint", content="just prose", domain="acme")
    assert uid in queued(conn)


def test_a_restore_is_never_refused(conn):
    """An import reproduces a store, legacy bodies and all; the migration is
    what settles them afterwards."""
    db.restore_memory(conn, {"uid": "b2c3d4e5f6071829", "type": "checkpoint",
                             "content": "a body from before the spec", "domain": "acme"})
    assert db.get_memory(conn, "b2c3d4e5f6071829") is not None
    assert "b2c3d4e5f6071829" in queued(conn)


# ---------------------------------------------------- the optimize suggestions

def test_a_reword_that_would_break_the_shape_is_refused_at_staging(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    result = db.stage_optimization(conn, "tighten it", [
        {"kind": "reword", "target_uid": uid,
         "payload": {"new_content": "a much tighter body"}, "rationale": "shorter"}])
    assert result["staged"] == 0 and result["run_id"] is None
    assert "does not read that way" in result["errors"][0]["error"]


def test_a_reword_that_keeps_the_shape_still_stages(conn):
    uid = db.insert_memory(conn, type="checkpoint", content=CHECKPOINT, domain="acme")
    tighter = sections.render("checkpoint", {
        "intent": "drain the queue", "established": "the worker parks a row",
        "pursuing": "the parked rows", "open_questions": "none"})
    result = db.stage_optimization(conn, "tighten it", [
        {"kind": "reword", "target_uid": uid,
         "payload": {"new_content": tighter}, "rationale": "shorter"}])
    assert result["staged"] == 1
    db.apply_suggestion(conn, db.get_optimization_suggestions(conn, result["run_id"])[0]["id"])
    assert sections_of(conn, uid)["open_questions"] == "none"


def test_a_distill_into_a_sectioned_type_is_held_to_the_shape(conn):
    src = db.insert_memory(conn, type="note", content="the drain stalls on a wide batch",
                           domain="acme")
    result = db.stage_optimization(conn, "distill it", [
        {"kind": "distill", "verified": "checked against the worker log",
         "payload": {"source_uids": [src], "new_type": "anti_pattern",
                     "new_content": "widening the batch does not help",
                     "title": "Widening the batch does not help"}}])
    assert result["staged"] == 0
    assert "TEMPTATION, WHY WRONG, INSTEAD" in result["errors"][0]["error"]


# ------------------------------------------------------------- field ceilings

def test_a_field_with_a_ceiling_refuses_a_body_that_passes_it(conn):
    long_intent = "drain the queue " * 60
    body = CHECKPOINT.replace("INTENT: drain the queue before the nightly export",
                              f"INTENT: {long_intent}")
    with pytest.raises(ValueError, match=r"INTENT runs to \d+ characters and holds 800"):
        db.insert_memory(conn, type="checkpoint", content=body, domain="acme")


def test_a_field_with_no_ceiling_takes_whatever_it_is_given(conn):
    body = CHECKPOINT.replace("ESTABLISHED: the worker retries three times, then parks the row",
                              "ESTABLISHED: " + "the worker parks a row " * 400)
    uid = db.insert_memory(conn, type="checkpoint", content=body, domain="acme")
    assert len(sections_of(conn, uid)["established"]) > 8000


def test_every_ceiling_clears_the_bodies_the_store_already_holds():
    """Set from what the store measured, so no existing memory is refused
    the day the ceiling arrives. The margins here are those measurements."""
    ceilings = {s.label: s.max_len for spec in sections.SECTION_SPEC.values() for s in spec}
    assert ceilings["INTENT"] >= 627
    assert ceilings["PURSUING"] >= 1291
    assert ceilings["TEMPTATION"] >= 731
    assert ceilings["ESTABLISHED"] == 0


def test_the_spec_a_form_reads_carries_the_ceiling(conn):
    uid = db.insert_memory(conn, type="anti_pattern", content=ANTI_PATTERN, domain="acme")
    assert db.get_memory(conn, uid) is not None
    assert sections.spec_for("anti_pattern")[0].max_len == 800
    assert sections.spec_for("anti_pattern")[1].max_len == 0


# ------------------------------------------------- the shapes an older writer left

REASONING = (
    "HYPOTHESIS: the drain falls behind because the batch is too small\n"
    "REASONING: compared drain rate against batch width over four runs\n"
    "RESULT: the rate is flat from 50 rows up; the lock wait is not\n"
    "REVISED BELIEF: the batch is not the bound, the single cursor is\n"
    "NEXT TIME: measure lock wait before touching batch width"
)

LEGACY_REASONING = (
    "DOMAIN: acme-x100-queue-drain\n"
    + REASONING
    + "\nCONFIDENCE: 0.9"
)


def test_the_legacy_blocks_are_dropped_and_the_fields_survive():
    salvaged = sections.salvage("reasoning", LEGACY_REASONING)
    assert salvaged.conforms
    assert sections.render("reasoning", salvaged.sections) == REASONING


def test_a_dropped_block_takes_its_continuation_lines_with_it():
    body = LEGACY_REASONING.replace("CONFIDENCE: 0.9",
                                    "CONFIDENCE: 0.9\nchecked live on the worker log")
    salvaged = sections.salvage("reasoning", body)
    assert salvaged.conforms
    assert "checked live" not in sections.render("reasoning", salvaged.sections)


def test_a_field_is_never_swallowed_by_a_legacy_block():
    """CONFIDENCE trails the fields, so dropping it must not take NEXT TIME."""
    salvaged = sections.salvage("reasoning", LEGACY_REASONING)
    assert salvaged.sections["next_time"] == "measure lock wait before touching batch width"


def test_a_type_with_no_legacy_labels_is_left_alone():
    assert sections._drop_legacy("checkpoint", CHECKPOINT) == CHECKPOINT


def test_the_migration_reshapes_a_legacy_reasoning(conn):
    uid = db.insert_memory(conn, type="reasoning", content=REASONING, domain="acme/x100")
    unread(conn, uid, LEGACY_REASONING)

    result = db.migrate_sections(conn)

    assert result["rewritten"] == 1 and result["needs_review"] == 0
    assert db.get_memory(conn, uid)["content"] == REASONING
    assert sections_of(conn, uid)["hypothesis"].startswith("the drain falls behind")


def test_a_reasoning_that_only_has_prose_goes_to_the_queue(conn):
    """The three in the real store that carry an ACHADO and no fields: the
    migration must leave every character of them alone."""
    prose = "DOMAIN: acme-x100-queue-drain\nACHADO: the drain and the cursor are one problem"
    uid = db.insert_memory(conn, type="reasoning", content=REASONING, domain="acme")
    unread(conn, uid, prose)

    db.migrate_sections(conn)

    assert db.get_memory(conn, uid)["content"] == prose
    assert "no line opens with" in queued(conn)[uid]


def test_reasoning_ceilings_clear_the_bodies_the_store_already_holds():
    spec = {s.label: s.max_len for s in sections.SECTION_SPEC["reasoning"]}
    assert spec["HYPOTHESIS"] >= 318      # the longest one measured
    assert spec["NEXT TIME"] >= 601
    assert spec["REASONING"] == spec["RESULT"] == spec["REVISED BELIEF"] == 0


# ----------------------------------------------- the field names on screen

def _catalogs() -> tuple[dict, dict]:
    import json
    from pathlib import Path

    from memai import admin
    i18n = Path(admin.WEBUI_DIR) / "i18n"
    return (json.loads((i18n / "en.json").read_text(encoding="utf-8"))["strings"],
            json.loads((i18n / "pt-BR.json").read_text(encoding="utf-8"))["strings"])


def test_every_field_has_a_name_in_every_catalog():
    """A field with no entry falls back to the label stored in the body, which
    is English -- so it reads as a lapse rather than as a decision."""
    en, pt = _catalogs()
    for type_, spec in sections.SECTION_SPEC.items():
        for s in spec:
            key = f"sec.{type_}.{s.key}"
            assert en.get(key), f"{key} missing from en"
            assert pt.get(key), f"{key} missing from pt-BR"


def test_the_english_name_is_the_label_written_in_the_body():
    en, _ = _catalogs()
    for type_, spec in sections.SECTION_SPEC.items():
        for s in spec:
            assert en[f"sec.{type_}.{s.key}"] == s.label


def test_a_translated_name_does_not_change_what_is_stored():
    _, pt = _catalogs()
    assert pt["sec.checkpoint.intent"] != "INTENT"
    body = sections.render("checkpoint", sections.read("checkpoint", CHECKPOINT).sections)
    assert body.startswith("INTENT:")
