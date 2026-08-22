"""A store that can say which of its own memories has gone doubtful.

A memory about code is true until the code changes, and nothing in a
memory store notices that. `review_after` is the writer's own estimate of
when the claim stops being safe to trust unchecked, which turns "is this
still right" from a judgement into a comparison -- one a warm-up can make
without reading anything. `source_ref` says what to check it against.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from memai import admin, db, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
NEXT_YEAR = (date.today() + timedelta(days=365)).isoformat()


# --------------------------------------------------------------- the date

@pytest.mark.parametrize("given", ["2026-11-01", "2026-11-01T09:30:00+00:00"])
def test_a_date_is_kept_as_a_date(given):
    assert db.normalize_review_after(given) == "2026-11-01"


def test_a_span_is_resolved_against_today():
    """How the answer actually arrives: a writer knows "recheck in a
    quarter" and does not know today's date without asking."""
    assert db.normalize_review_after("90d", today="2026-08-06") == "2026-11-04"
    assert db.normalize_review_after("1 D", today="2026-08-06") == "2026-08-07"


def test_empty_means_never():
    assert db.normalize_review_after("") == ""


def test_nonsense_is_refused_rather_than_stored(conn):
    """Free text kept in the column would simply never come due."""
    with pytest.raises(ValueError, match="review_after must be"):
        db.normalize_review_after("sometime soon")


# ------------------------------------------------------------- on a memory

def test_a_writer_records_both_fields(store):
    uid = server.note(content="the export window is inclusive on both ends",
                      domain="acme/x100", review_after="30d",
                      source_ref="src/acme/x100/export.py")["uid"]
    row = server.get_memory(uid)
    assert row["review_after"] == db.normalize_review_after("30d")
    assert row["source_ref"] == "src/acme/x100/export.py"


def test_a_memory_that_does_not_go_stale_carries_no_field(store):
    """Most do not, and an empty field costs its name in every result."""
    uid = server.note(content="row merge keeps the older identifier")["uid"]
    row = server.get_memory(uid)
    assert "review_after" not in row and "source_ref" not in row


def test_search_results_stay_clean_too(store):
    server.note(content="cache warmup runs nightly")
    hit = server.search("cache warmup")["results"][0]
    assert "review_after" not in hit and "source_ref" not in hit


def test_a_diagram_can_be_dated_too(store):
    """It describes code, which is the thing that moves."""
    uid = server.diagram(title="Cache warmup", domain="acme/x100", review_after="180d",
                         source_ref="src/acme/x100/warmup.py", nodes=[
                             {"key": "start", "shape": "start", "label": "begin"},
                             {"key": "done", "shape": "end", "label": "done"}],
                         edges=[{"from": "start", "to": "done"}])["uid"]
    assert server.get_memory(uid)["source_ref"] == "src/acme/x100/warmup.py"


# ------------------------------------------------------------- coming due

def test_due_lists_the_overdue_oldest_first(conn):
    later = db.insert_memory(conn, type="note", content="b", review_after=YESTERDAY)
    older = db.insert_memory(conn, type="note", content="a", review_after="2020-01-01")
    db.insert_memory(conn, type="note", content="c", review_after=NEXT_YEAR)
    db.insert_memory(conn, type="note", content="d")
    assert [r["uid"] for r in db.due_for_review(conn)] == [older, later]


def test_an_archived_memory_is_not_due(conn):
    uid = db.insert_memory(conn, type="note", content="a", review_after=YESTERDAY)
    db.set_status(conn, uid, "archived")
    assert db.due_for_review(conn) == []


def test_a_warm_up_counts_what_is_overdue_in_the_scope(store):
    server.note(content="cache warmup runs nightly", domain="acme/x100",
                review_after=YESTERDAY)
    server.note(content="row merge keeps the older id", domain="acme/x100")
    assert server.pulse("acme/x100")["scope"]["stale"] == 1


def test_a_scope_with_nothing_overdue_does_not_carry_the_field(store):
    server.note(content="cache warmup runs nightly", domain="acme/x100",
                review_after=NEXT_YEAR)
    assert "stale" not in server.pulse("acme/x100")["scope"]


# --------------------------------------------------------------- curation

def test_the_corpus_flags_what_is_due_and_what_to_check_it_against(store):
    due = server.note(content="the export window is inclusive", review_after=YESTERDAY,
                      source_ref="src/acme/x100/export.py")["uid"]
    fine = server.note(content="row merge keeps the older id", review_after=NEXT_YEAR)["uid"]
    corpus = server.optimize_scan()
    by_uid = {m["uid"]: m for m in corpus["memories"]}
    assert by_uid[due]["due"] is True
    assert by_uid[due]["source_ref"] == "src/acme/x100/export.py"
    assert "due" not in by_uid[fine] and by_uid[fine]["review_after"] == NEXT_YEAR
    assert corpus["stats"]["due_for_review"] == 1


def test_a_pass_can_push_the_date_out(store):
    uid = server.note(content="the export window is inclusive",
                      review_after=YESTERDAY)["uid"]
    run = server.optimize_stage([{
        "kind": "review", "target_uid": uid, "payload": {"review_after": "180d"},
        "rationale": "rechecked against the source", "verified": "read export.py",
    }])
    assert run["staged"] == 1 and not run["errors"]
    # staged, not applied: the human still decides
    assert server.get_memory(uid)["review_after"] == YESTERDAY


def test_the_staged_date_is_resolved_when_it_is_staged(store):
    """'180d' means a different day depending on when the panel is read."""
    uid = server.note(content="x", review_after=YESTERDAY)["uid"]
    run = server.optimize_stage([{
        "kind": "review", "target_uid": uid, "payload": {"review_after": "180d"},
        "rationale": "r", "verified": "v"}])
    staged = server.optimize_status(run["run_id"])["suggestions"][0]
    assert staged["payload"]["review_after"] == db.normalize_review_after("180d")


def test_an_unparseable_date_is_rejected_at_staging(store):
    uid = server.note(content="x")["uid"]
    run = server.optimize_stage([{
        "kind": "review", "target_uid": uid, "payload": {"review_after": "whenever"},
        "rationale": "r"}])
    assert run["staged"] == 0
    assert "review_after must be" in run["errors"][0]["error"]


def test_applying_and_undoing_a_review_restores_the_date(conn):
    uid = db.insert_memory(conn, type="note", content="x", review_after=YESTERDAY)
    db.set_review_after(conn, uid, "2030-01-01")
    assert db.get_memory(conn, uid)["review_after"] == "2030-01-01"
    db.set_review_after(conn, uid, YESTERDAY)
    assert db.get_memory(conn, uid)["review_after"] == YESTERDAY


def test_moving_the_date_is_audited_but_not_re_embedded(conn):
    uid = db.insert_memory(conn, type="note", content="x", review_after=YESTERDAY)
    db.set_review_after(conn, uid, "2030-01-01")
    notes = [e["note"] for e in db.get_edit_history(conn, uid)]
    assert any("review_after" in n for n in notes)


# --------------------------------------------- setting it after the fact

def test_a_reference_can_be_added_to_a_memory_that_has_none(store):
    """`source_ref` is settable on its own, after the fact, with no content
    edit -- the case of a memory written before anyone knew where the code
    lived."""
    uid = server.note(content="the export window is inclusive")["uid"]
    res = server.edit_memory(uid, source_ref="src/acme/x100/export.py")
    assert res["ok"] and res["changed"] == ["source_ref"]
    assert server.get_memory(uid)["source_ref"] == "src/acme/x100/export.py"


def test_repointing_leaves_the_body_alone_and_says_where_it_moved(store):
    uid = server.note(content="the export window is inclusive",
                      source_ref="src/acme/x100/old.py")["uid"]
    server.edit_memory(uid, source_ref="src/acme/x100/export.py", note="the file moved")
    row = server.get_memory(uid)
    assert row["content"] == "the export window is inclusive"
    assert row["edit_history"][-1]["note"] == (
        "meta: source_ref 'src/acme/x100/old.py' -> 'src/acme/x100/export.py' "
        "(the file moved)")


def test_the_body_and_the_reference_can_move_in_one_call(store):
    uid = server.note(content="the export window is inclusive")["uid"]
    res = server.edit_memory(uid, "the export window excludes the last day",
                             source_ref="src/acme/x100/export.py")
    assert res["changed"] == ["content", "source_ref"]
    row = server.get_memory(uid)
    assert row["content"] == "the export window excludes the last day"
    assert row["source_ref"] == "src/acme/x100/export.py"


def test_repointing_at_the_same_reference_writes_no_audit_entry(conn):
    uid = db.insert_memory(conn, type="note", content="x",
                           source_ref="src/acme/x100/export.py")
    assert db.set_source_ref(conn, uid, "src/acme/x100/export.py")
    assert db.get_edit_history(conn, uid) == []


# -------------------------------------------------------------- dashboard

def test_the_dashboard_can_set_and_clear_the_date(client):
    uid = client.post("/api/memories",
                      json={"type": "note", "content": "x"}).json()["uid"]
    client.post(f"/api/memories/{uid}/meta", json={"review_after": "90d",
                                                   "source_ref": "src/acme/x100/export.py"})
    body = client.get(f"/api/memories/{uid}").json()
    assert body["review_after"] == db.normalize_review_after("90d")
    assert body["source_ref"] == "src/acme/x100/export.py"

    client.post(f"/api/memories/{uid}/meta", json={"review_after": ""})
    assert client.get(f"/api/memories/{uid}").json()["review_after"] == ""


def test_the_dashboard_refuses_a_date_it_cannot_parse(client):
    uid = client.post("/api/memories", json={"type": "note", "content": "x"}).json()["uid"]
    res = client.post(f"/api/memories/{uid}/meta", json={"review_after": "soon"})
    assert res.status_code == 400
