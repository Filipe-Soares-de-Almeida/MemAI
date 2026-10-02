"""Pinned memories: the column, its writer, scope rules and what reads report.

Every test runs against a store under tmp_path (MEMAI_HOME), never the real
~/.memai.
"""

from __future__ import annotations

import sqlite3

import pytest

from memai import db, portable


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
