"""Pinned memories: the column, its writer, scope rules and what reads report.

Every test runs against a store under tmp_path (MEMAI_HOME), never the real
~/.memai.
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import shaped
from memai import db, pending, portable, server


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _note(conn, title="Parser fact", domain="acme/x100", also="") -> str:
    return db.insert_memory(conn, type="note", title=title, content="Lex once.",
                            domain=domain, also=also)


def test_a_new_memory_is_not_pinned(store):
    with db.connect() as conn:
        uid = _note(conn)
        assert db.get_memory(conn, uid)["pin"] == ""


@pytest.mark.parametrize("pin", ["global", "domain", ""])
def test_set_pin_stores_each_value(store, pin):
    with db.connect() as conn:
        uid = _note(conn)
        before = db.get_memory(conn, uid)["updated_at"]
        assert db.set_pin(conn, uid, pin) is True
        row = db.get_memory(conn, uid)
        assert row["pin"] == pin
        assert row["updated_at"] >= before


def test_set_pin_refuses_an_unknown_value(store):
    with db.connect() as conn:
        uid = _note(conn)
        with pytest.raises(ValueError, match="pin must be one of"):
            db.set_pin(conn, uid, "always")


def test_set_pin_refuses_a_domain_pin_without_a_domain(store):
    with db.connect() as conn:
        uid = _note(conn, domain="")
        with pytest.raises(ValueError, match="without a domain"):
            db.set_pin(conn, uid, "domain")
        assert db.set_pin(conn, uid, "global") is True


def test_set_pin_on_an_unknown_uid_is_false(store):
    with db.connect() as conn:
        assert db.set_pin(conn, "0" * 16, "global") is False


def test_an_existing_store_gains_the_column(store):
    with db.connect() as conn:
        _note(conn)
    raw = sqlite3.connect(db.default_db_path())
    raw.execute("ALTER TABLE memories DROP COLUMN pin")
    raw.commit()
    raw.close()
    with db.connect() as conn:
        assert {r["pin"] for r in conn.execute("SELECT pin FROM memories")} == {""}


def test_export_and_import_carry_the_pin(store, tmp_path, monkeypatch):
    with db.connect() as conn:
        uid = _note(conn)
        db.set_pin(conn, uid, "domain")
        records = list(portable.export_records(conn))
    assert next(r for r in records if r.get("uid") == uid)["pin"] == "domain"
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path / "other-store"))
    with db.connect() as conn:
        assert portable.import_records(conn, records)["errors"] == []
        assert db.get_memory(conn, uid)["pin"] == "domain"


def test_import_rejects_an_unknown_pin(store):
    record = {"record": "memory", "uid": "1" * 16, "type": "note",
              "title": "Parser fact", "content": "Lex once.", "pin": "always"}
    with db.connect() as conn:
        result = portable.import_records(conn, [record])
        assert result["added"] == 0 and len(result["errors"]) == 1
        assert db.get_memory(conn, "1" * 16) is None


def test_markdown_export_names_a_pin(store):
    with db.connect() as conn:
        uid = _note(conn)
        db.set_pin(conn, uid, "global")
        text = portable.to_markdown(portable.export_records(conn))
    assert "pin: global" in text


# ------------------------------------------------------------ scope and counts

def _pinned(conn, pin, domain="acme/x100", also="", type_="note", title="Pinned fact"):
    if type_ == "note":
        uid = _note(conn, title=title, domain=domain, also=also)
    else:
        uid = db.insert_memory(conn, type=type_, title=title,
                               content=shaped(type_, "a pinned body"), domain=domain)
    db.set_pin(conn, uid, pin)
    return uid


def _types(found):
    return {c["type"]: c["count"] for c in found}


def test_whole_project_counts_only_global_pins(store):
    with db.connect() as conn:
        _pinned(conn, "global")
        _pinned(conn, "domain")
        assert pending.pinned_counts(conn) == [{"type": "note", "count": 1}]


@pytest.mark.parametrize("asked, seen", [
    ("acme/x100", True), ("acme/x100/p200", True), ("acme/x100/new/deep", True),
    ("acme", False), ("acme/x1000", False), ("other", False),
])
def test_a_domain_pin_covers_its_subtree_only(store, asked, seen):
    with db.connect() as conn:
        _note(conn, title="Something under p200", domain="acme/x100/p200")
        _pinned(conn, "domain")
        assert pending.pinned_counts(conn, asked) == (
            [{"type": "note", "count": 1}] if seen else [])


def test_a_global_pin_is_in_every_scope(store):
    with db.connect() as conn:
        _pinned(conn, "global", domain="other")
        for asked in ("", "acme", "acme/x100/p200", "other"):
            assert _types(pending.pinned_counts(conn, asked)) == {"note": 1}


def test_a_domain_pin_covers_its_also_paths(store):
    with db.connect() as conn:
        _pinned(conn, "domain", also="other/y200")
        assert _types(pending.pinned_counts(conn, "other/y200/z")) == {"note": 1}
        assert pending.pinned_counts(conn, "other") == []


def test_a_pin_that_is_not_pending_is_not_counted_and_survives(store):
    with db.connect() as conn:
        archived = _pinned(conn, "global", title="Archived pin")
        db.set_status(conn, archived, "archived")
        wrong = _pinned(conn, "global", title="Wrong pin")
        db.set_confidence(conn, wrong, "contradicted")
        assert pending.pinned_counts(conn) == []
        db.set_status(conn, archived, "active")
        assert _types(pending.pinned_counts(conn)) == {"note": 1}
        assert db.get_memory(conn, wrong)["pin"] == "global"


def test_a_closed_task_pin_is_not_counted(store):
    uid = server.task(title="Ship the parser", goal="Parse every config file",
                      items="read the spec", domain="acme/x100")["uid"]
    with db.connect() as conn:
        db.set_pin(conn, uid, "global")
        assert _types(pending.pinned_counts(conn)) == {"task": 1}
    server.task_item(uid, "i1", state="done")
    with db.connect() as conn:
        assert pending.pinned_counts(conn) == []


def test_pinned_memories_still_count_in_their_category(store):
    with db.connect() as conn:
        _pinned(conn, "global")
        _note(conn, title="Plain fact")
        assert pending.counts(conn, "acme/x100") == [{"type": "note", "count": 2}]


def test_checkpoint_and_reasoning_pins_are_counted(store):
    with db.connect() as conn:
        _pinned(conn, "global", type_="checkpoint", title="Pinned bearing")
        _pinned(conn, "global", type_="reasoning", title="Pinned decision")
        assert pending.pinned_counts(conn) == [
            {"type": "checkpoint", "count": 1}, {"type": "reasoning", "count": 1}]


def test_pinned_headers_list_only_pins_of_the_type(store):
    with db.connect() as conn:
        uid = _pinned(conn, "domain")
        _note(conn, title="Plain fact")
        page = pending.headers(conn, "acme/x100", "note", pinned=True)
        assert page["total"] == 1 and [i["uid"] for i in page["items"]] == [uid]
        assert pending.headers(conn, "", "note", pinned=True)["total"] == 0


# ---------------------------------------------------------------- the tools

def _seed_pins():
    with db.connect() as conn:
        _pinned(conn, "global", title="Global pin")
        _pinned(conn, "domain", title="Domain pin")
        _note(conn, title="Plain fact")


def test_pending_tool_adds_pinned_counts_only_when_pinned(store):
    with db.connect() as conn:
        _note(conn)
    assert server.pending() == {"categories": [{"type": "note", "count": 1}]}
    _seed_pins()
    assert server.pending()["pinned"] == [{"type": "note", "count": 1}]
    assert server.pending("acme/x100")["pinned"] == [{"type": "note", "count": 2}]


def test_pending_tool_lists_pinned_headers(store):
    _seed_pins()
    page = server.pending("acme/x100", type="note", pinned=True)
    assert page["total"] == 2
    assert {i["title"] for i in page["items"]} == {"Global pin", "Domain pin"}


def test_pending_tool_accepts_checkpoint_only_with_pinned(store):
    assert server.pending(type="checkpoint")["errors"] == [
        "unknown type 'checkpoint'; valid types: task, anti_pattern, handoff, note, diagram"]
    assert server.pending(type="checkpoint", pinned=True)["total"] == 0
    assert "errors" in server.pending(type="optimizer", pinned=True)


def test_pulse_reports_pins_and_reads_them_first(store):
    _seed_pins()
    result = server.pulse("acme/x100")
    assert result["pinned"] == [{"type": "note", "count": 2}]
    assert result["read_next"].startswith(
        "Pinned memories are mandatory reading: for each type in `pinned`, "
        "pending('acme/x100', type=<t>, pinned=true), then get_memory(uid) on every "
        "one, skipping none. Then pending(")


def test_pulse_without_pins_reads_as_before(store):
    with db.connect() as conn:
        _note(conn)
    result = server.pulse("acme/x100")
    assert result["pinned"] == []
    assert result["read_next"].startswith("Then pending(")


def test_instructions_name_the_pins():
    assert "pinned=true" in server.INSTRUCTIONS
