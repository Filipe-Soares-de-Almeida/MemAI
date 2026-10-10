"""The length ceilings on a memory's tags, source_ref and domain paths.

Each one is held on every path that accepts a new value -- the store's writers, the tools, the
dashboard's meta editor and the curation kinds -- while a restore writes back what it was given.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memai import server
from memai.admin.app import app as admin_app
from memai.store import connection, memories, optimizer, sections, subtree


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(store):
    with TestClient(admin_app) as c:
        yield c


def _tags(n: int) -> str:
    return ("kiln, " * n)[:n]


def _path(n: int) -> str:
    return "acme/" + "k" * (n - len("acme/"))


def _note(conn, **kw) -> str:
    return memories.insert_memory(conn, type="note", title="Kiln firing", content="a fact", **kw)


# ------------------------------------------------------------------ the store

@pytest.mark.parametrize(("field", "limit", "value"), [
    ("tags", sections.TAGS_MAX, _tags),
    ("source_ref", sections.SOURCE_REF_MAX, lambda n: "s" * n),
    ("domain", sections.DOMAIN_MAX, _path),
], ids=["tags", "source_ref", "domain"])
def test_a_field_at_the_limit_is_stored_and_one_past_it_is_refused(conn, field, limit, value):
    uid = _note(conn, **{field: value(limit)})
    assert len(memories.get_memory(conn, uid)[field]) == limit
    with pytest.raises(ValueError, match=f"{field} is {limit + 1} characters; the limit is {limit}"):
        _note(conn, **{field: value(limit + 1)})


def test_a_cross_listing_is_held_to_the_domain_limit(conn):
    with pytest.raises(ValueError, match=str(sections.DOMAIN_MAX)):
        _note(conn, domain="acme/kiln", also=_path(sections.DOMAIN_MAX + 1))
    uid = _note(conn, domain="acme/kiln")
    with pytest.raises(ValueError, match=str(sections.DOMAIN_MAX)):
        memories.add_domain_link(conn, uid, _path(sections.DOMAIN_MAX + 1))
    assert memories.get_domain_links(conn, uid) == []


def test_an_edit_over_the_limit_leaves_the_old_value(conn):
    uid = _note(conn, tags="kiln", source_ref="kiln.md", domain="acme/kiln")
    with pytest.raises(ValueError, match=str(sections.TAGS_MAX)):
        memories.set_tags(conn, uid, _tags(sections.TAGS_MAX + 1))
    with pytest.raises(ValueError, match=str(sections.SOURCE_REF_MAX)):
        memories.set_source_ref(conn, uid, "s" * (sections.SOURCE_REF_MAX + 1))
    with pytest.raises(ValueError, match=str(sections.DOMAIN_MAX)):
        memories.set_domain(conn, uid, _path(sections.DOMAIN_MAX + 1))
    row = memories.get_memory(conn, uid)
    assert (row["tags"], row["source_ref"], row["domain"]) == ("kiln", "kiln.md", "acme/kiln")


def test_the_length_is_measured_after_stripping(conn):
    uid = _note(conn, source_ref=f"  {'s' * sections.SOURCE_REF_MAX}  ")
    assert len(memories.get_memory(conn, uid)["source_ref"]) == sections.SOURCE_REF_MAX


def test_a_subtree_move_that_would_pass_the_limit_moves_nothing(conn):
    uid = _note(conn, domain="acme/kiln/firing")
    with pytest.raises(ValueError, match=str(sections.DOMAIN_MAX)):
        subtree.move_domain(conn, "acme/kiln", _path(sections.DOMAIN_MAX - len("/firing") + 1))
    assert memories.get_memory(conn, uid)["domain"] == "acme/kiln/firing"


def test_a_restore_writes_back_a_value_past_the_limit(conn):
    """An export holding a value past a ceiling imports whole."""
    uid = _note(conn)
    record = dict(memories.get_memory(conn, uid))
    memories.purge_memory(conn, uid)
    record["tags"] = _tags(sections.TAGS_MAX + 50)
    memories.restore_memory(conn, record)
    assert len(memories.get_memory(conn, uid)["tags"]) == sections.TAGS_MAX + 50


# ------------------------------------------------------------- the tools

def test_the_writing_tools_refuse_a_field_past_the_limit(store):
    with pytest.raises(ValueError, match=str(sections.TAGS_MAX)):
        server.note(title="Kiln firing", content="a fact", tags=_tags(sections.TAGS_MAX + 1))
    with pytest.raises(ValueError, match=str(sections.DOMAIN_MAX)):
        server.note(title="Kiln firing", content="a fact", domain=_path(sections.DOMAIN_MAX + 1))
    res = server.task("Fire the kiln", "the batch is fired", "load\nfire",
                      domain=_path(sections.DOMAIN_MAX + 1))
    assert res["ok"] is False and str(sections.DOMAIN_MAX) in res["errors"][0]


def test_edit_memory_refuses_tags_or_a_source_past_the_limit(store):
    uid = server.note(title="Kiln firing", content="a fact", tags="kiln")["uid"]
    res = server.edit_memory(uid, tags=_tags(sections.TAGS_MAX + 1))
    assert res["ok"] is False and str(sections.TAGS_MAX) in res["errors"][0]
    res = server.edit_memory(uid, source_ref="s" * (sections.SOURCE_REF_MAX + 1))
    assert res["ok"] is False and str(sections.SOURCE_REF_MAX) in res["errors"][0]
    assert server.get_memory(uid)["tags"] == "kiln"


def test_also_domain_refuses_a_path_past_the_limit(store):
    uid = server.note(title="Kiln firing", content="a fact", domain="acme/kiln")["uid"]
    res = server.also_domain(uid, _path(sections.DOMAIN_MAX + 1))
    assert res["ok"] is False and str(sections.DOMAIN_MAX) in res["errors"][0]


def test_the_writer_parameters_state_their_limits():
    doc = " ".join(server.note.__doc__.split()) + " ".join(
        " ".join(text.split()) for text in server._PARAM_TEXT["note"].values())
    for limit in (sections.TAGS_MAX, sections.SOURCE_REF_MAX, sections.DOMAIN_MAX):
        assert f"At most {limit} characters" in doc


# --------------------------------------------------------- the dashboard

@pytest.mark.parametrize(("field", "value"), [
    ("tags", _tags(sections.TAGS_MAX + 1)),
    ("source_ref", "s" * (sections.SOURCE_REF_MAX + 1)),
    ("domain", _path(sections.DOMAIN_MAX + 1)),
], ids=["tags", "source_ref", "domain"])
def test_the_meta_editor_refuses_a_field_past_the_limit(client, field, value):
    uid = client.post("/api/memories", json={
        "title": "Kiln firing", "type": "note", "content": "a fact"}).json()["uid"]
    res = client.post(f"/api/memories/{uid}/meta", json={field: value})
    assert res.status_code == 400
    assert client.get(f"/api/memories/{uid}").json()[field] == ""


# ------------------------------------------------------- the curation kinds

@pytest.mark.parametrize(("kind", "payload", "limit"), [
    ("retag", {"tags": _tags(sections.TAGS_MAX + 1)}, sections.TAGS_MAX),
    ("redomain", {"domain": _path(sections.DOMAIN_MAX + 1)}, sections.DOMAIN_MAX),
    ("crosslist", {"also": [_path(sections.DOMAIN_MAX + 1)]}, sections.DOMAIN_MAX),
], ids=["retag", "redomain", "crosslist"])
def test_a_suggestion_past_the_limit_is_not_staged(conn, kind, payload, limit):
    uid = _note(conn, domain="acme/kiln")
    run = optimizer.stage_optimization(conn, "r", [
        {"kind": kind, "target_uid": uid, "payload": payload, "rationale": "why"}])
    assert run["staged"] == 0
    assert str(limit) in run["errors"][0]["error"]
