"""The health index, its axes, and the symptom list behind the Health view.

Hermetic like the rest of the suite: MEMAI_HOME is a tmp dir per test.
Every number here is asserted against a store built one row at a time, so
a change to an axis definition fails on the axis and not on a total.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from memai import admin, db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _add(conn, **kw) -> str:
    body = {"type": "note", "title": "a fixture memory", "content": "a fact",
            "domain": "acme/x100", "tags": "alpha", **kw}
    return db.insert_memory(conn, **body)


def test_empty_store_is_healthy(client):
    """A store with nothing in it scores 100, not 0 -- nothing is wrong with it."""
    h = client.get("/api/overview").json()["health"]
    assert h["score"] == 100
    assert set(h["axes"]) == {"curation", "connectivity", "freshness", "organization"}
    assert all(v == 100 for v in h["axes"].values())


def test_curation_is_the_confirmed_share(client):
    with db.connect() as conn:
        _add(conn, confidence="confirmed")
        _add(conn, confidence="unverified")
        _add(conn, confidence="contradicted")
        _add(conn, confidence="confirmed")
    axes = client.get("/api/overview").json()["health"]["axes"]
    assert axes["curation"] == 50


def test_contradicted_weighs_like_unverified_until_it_is_superseded(client):
    """The contradicted row is not penalised twice: it is simply not confirmed.

    Superseding it does not raise curation either -- what raises curation is
    confirming something. What superseding clears is the symptom.
    """
    with db.connect() as conn:
        a = _add(conn, confidence="contradicted")
        b = _add(conn, confidence="confirmed")
        before = db.health_axes(conn)["axes"]["curation"]
        conn.execute("UPDATE memories SET superseded_by = ? WHERE uid = ?", (b, a))
        after = db.health_axes(conn)["axes"]["curation"]
    assert before == after == 50


def test_connectivity_counts_both_ends_of_a_relation(client):
    with db.connect() as conn:
        a, b, _c = _add(conn), _add(conn), _add(conn)
        db.add_relation(conn, a, b, "relates_to")
    axes = client.get("/api/overview").json()["health"]["axes"]
    assert axes["connectivity"] == 67


def test_freshness_drops_on_an_overdue_review_and_on_a_stale_unverified(client):
    with db.connect() as conn:
        _add(conn, confidence="confirmed")                       # fine
        _add(conn, confidence="confirmed", review_after="2020-01-01")   # overdue
        _add(conn, confidence="unverified", created_at=_ago(db.STALE_DAYS + 5))
        _add(conn, confidence="unverified")                      # young enough
    axes = client.get("/api/overview").json()["health"]["axes"]
    assert axes["freshness"] == 50


def test_organization_wants_a_title_real_tags_and_a_domain(client):
    with db.connect() as conn:
        _add(conn)                                   # all three
        _add(conn, title="")                         # no title
        _add(conn, tags="note")                      # tags are only the type
        _add(conn, domain="")                        # nowhere filed
    axes = client.get("/api/overview").json()["health"]["axes"]
    assert axes["organization"] == 25


def test_score_is_the_mean_of_the_four_axes(client):
    with db.connect() as conn:
        for _ in range(4):
            _add(conn, confidence="confirmed")
    h = client.get("/api/overview").json()["health"]
    assert h["score"] == round(sum(h["axes"].values()) / 4)


def test_archived_memories_are_outside_every_axis(client):
    with db.connect() as conn:
        _add(conn, confidence="confirmed")
        dead = _add(conn, confidence="unverified", title="", tags="", domain="")
        db.set_status(conn, dead, "archived")
    h = client.get("/api/overview").json()["health"]
    assert h["active"] == 1
    assert h["axes"]["curation"] == 100
    assert h["axes"]["organization"] == 100


# ------------------------------------------------------------------ delta

def test_no_delta_until_a_snapshot_that_old_exists(client):
    """A store the dashboard has not been open on for a month has nothing to
    compare against, and reports no delta rather than a misdated one."""
    assert client.get("/api/overview").json()["health"]["delta"] is None


def test_delta_is_measured_against_the_older_snapshot(client):
    with db.connect() as conn:
        _add(conn, confidence="unverified")
        old = (datetime.now(timezone.utc)
               - timedelta(days=admin.HEALTH_DELTA_DAYS + 1)).date().isoformat()
        db.health_snapshot(conn, {"score": 40, "axes": dict.fromkeys(
            ("curation", "connectivity", "freshness", "organization"), 40)}, day=old)
        expected = db.health_axes(conn)["score"] - 40
    h = client.get("/api/overview").json()["health"]
    assert h["delta"] == expected
    assert h["since"] == old


def test_the_day_is_snapshotted_once_and_not_overwritten(client):
    client.get("/api/overview")
    with db.connect() as conn:
        first = conn.execute("SELECT score FROM health_daily").fetchone()["score"]
        _add(conn, confidence="confirmed")
    client.get("/api/overview")
    with db.connect() as conn:
        rows = conn.execute("SELECT score FROM health_daily").fetchall()
    assert len(rows) == 1
    assert rows[0]["score"] == first


# --------------------------------------------------------------- symptoms

def _symptom(data, key):
    return next(s for s in data["symptoms"] if s["key"] == key)


def test_symptom_counts(client):
    with db.connect() as conn:
        _add(conn, confidence="contradicted")
        _add(conn, confidence="unverified", created_at=_ago(db.STALE_DAYS + 1))
        _add(conn, confidence="confirmed", review_after="2020-01-01")
        _add(conn, confidence="confirmed", title="")
    data = client.get("/api/overview").json()
    assert _symptom(data, "contradicted")["count"] == 1
    assert _symptom(data, "stale")["count"] == 1
    assert _symptom(data, "due")["count"] == 1
    assert _symptom(data, "untitled")["count"] == 1
    # nothing has a relation, so every one of the four is an island
    assert _symptom(data, "unlinked")["count"] == 4


def test_a_superseded_contradiction_is_a_decision(client):
    with db.connect() as conn:
        a = _add(conn, confidence="contradicted")
        b = _add(conn, confidence="confirmed")
        conn.execute("UPDATE memories SET superseded_by = ? WHERE uid = ?", (b, a))
    assert _symptom(client.get("/api/overview").json(), "contradicted")["count"] == 0


def test_the_diagram_symptom_counts_flows_not_memories(client):
    with db.connect() as conn:
        uid, errors = db.insert_diagram(
            conn, title="Queue drain, start to end", domain="acme/x100/p200",
            nodes=[{"key": "a", "shape": "start", "label": "trigger"},
                   {"key": "b", "shape": "end", "label": "done"}],
            edges=[{"from": "a", "to": "b"}])
        assert not errors, errors
        # insert_diagram refuses an unreachable node; the incremental writer
        # is the door a real one comes in through, and it skips that rule
        db.upsert_diagram_node(conn, uid, "x", shape="step", label="manual replay")
    sym = _symptom(client.get("/api/overview").json(), "diagrams")
    assert sym["count"] == 1
    assert sym["of"] == 1


def test_untagged_counts_a_tag_that_is_only_the_type(client):
    """A tag repeating the type carries no synonym, so it is not a tag."""
    with db.connect() as conn:
        _add(conn, tags="")
        _add(conn, type="note", tags="note")
        _add(conn, tags="  ")
        _add(conn, tags="queue drain")
    sym = _symptom(client.get("/api/overview").json(), "untagged")
    assert sym["count"] == 3
    listed = client.get("/api/memories", params=sym["params"]).json()
    assert listed["total"] == 3


def test_the_orphan_symptom_counts_relations_not_memories(client):
    """The symptom counts dangling relations, not the memories at their ends.
    db.connect enforces foreign keys, so the edge is written through a raw
    connection."""
    with db.connect() as conn:
        a, b = _add(conn), _add(conn)
        db.add_relation(conn, a, b, "relates_to")
    raw = sqlite3.connect(str(db.default_db_path()))
    try:
        raw.execute(
            """INSERT INTO relations (from_uid, to_uid, relation_type, created_at)
               VALUES (?, ?, 'relates_to', ?)""", (a, "gone", db.now_iso()))
        raw.commit()
    finally:
        raw.close()
    sym = _symptom(client.get("/api/overview").json(), "orphans")
    assert sym["count"] == 1
    assert sym["of"] == 2
    # no memory list can show a broken edge: the end that would name the row
    # is the end that is missing
    assert sym["params"] == {}


def test_every_symptom_filter_lists_exactly_what_it_counted(client):
    """The number on the dashboard and the list its button opens are one set."""
    with db.connect() as conn:
        _add(conn, confidence="contradicted")
        _add(conn, confidence="unverified", created_at=_ago(db.STALE_DAYS + 1))
        _add(conn, confidence="confirmed", review_after="2020-01-01")
        _add(conn, confidence="confirmed", title="")
        a, b = _add(conn), _add(conn)
        db.add_relation(conn, a, b, "relates_to")
    data = client.get("/api/overview").json()
    for s in data["symptoms"]:
        if not s["params"]:
            continue
        listed = client.get("/api/memories", params=s["params"]).json()
        assert listed["total"] == s["count"], s["key"]


def test_the_defect_filters_narrow_a_search_too(client):
    """A defect filter is a predicate over the rows, not a mode of the list:
    it has to apply on the keyword path as well as the browse path."""
    with db.connect() as conn:
        _add(conn, title="cache warmup reads the counter twice")
        linked = _add(conn, title="cache warmup drains the queue first")
        other = _add(conn, title="report export writes a footer")
        db.add_relation(conn, linked, other, "relates_to")
    hits = client.get("/api/memories", params={"q": "cache warmup", "linked": "no"}).json()
    assert hits["searched"] is True
    assert [i["title"] for i in hits["items"]] == ["cache warmup reads the counter twice"]


def test_confidence_by_type_splits_only_the_active_rows(client):
    with db.connect() as conn:
        _add(conn, type="note", confidence="confirmed")
        _add(conn, type="note", confidence="unverified")
        gone = _add(conn, type="note", confidence="unverified")
        db.set_status(conn, gone, "archived")
    split = client.get("/api/overview").json()["by_type_confidence"]
    assert split["note"] == {"confirmed": 1, "unverified": 1}
