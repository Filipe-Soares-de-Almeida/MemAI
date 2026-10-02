"""The type vocabulary and the reads that refuse a type outside it.

Every test runs against a store under tmp_path (MEMAI_HOME), never the real
~/.memai.
"""

from __future__ import annotations

import pytest

from memai import db, server

UNKNOWN = ("unknown type 'pitfall'; valid types: note, reasoning, anti_pattern, "
           "checkpoint, handoff, diagram, task")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def test_type_error_lists_the_vocabulary():
    assert db.type_error("pitfall") == UNKNOWN


def test_type_error_is_none_for_known_and_empty_types():
    assert db.type_error("") is None
    for type_ in db.MEMORY_TYPES:
        assert db.type_error(type_) is None
    assert db.type_error("Task") is not None


@pytest.mark.parametrize("call", [
    lambda: server.search("x", type="pitfall"),
    lambda: server.list_by_domain("acme", type="pitfall"),
    lambda: server.list_recent(type="pitfall"),
    lambda: server.timeline(query="x", type="pitfall"),
])
def test_reads_refuse_an_unknown_type(store, call):
    result = call()
    assert result["errors"] == [UNKNOWN]
    assert "results" not in result


def test_reads_accept_the_new_type(store):
    uid = server.task(title="Ship the parser", goal="Parse every config file",
                      items="read the spec", domain="acme/parser")["uid"]
    assert [r["uid"] for r in server.list_recent(type="task")["results"]] == [uid]
