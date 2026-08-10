"""timeline(): the records written either side of one memory.

The anchor is named by uid or found by query; the neighbours are the
records immediately older and newer by created_at. Covers both anchorings,
the ordering, the before/after counts, the domain scope (which a
cross-listed memory satisfies), the type filter, what is left out, and the
call with no anchor at all.
"""

from __future__ import annotations

import pytest

from memai import db, server


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _at(day: int) -> str:
    return f"2026-03-{day:02d}T09:00:00+00:00"


def _seed(rows: list[dict]) -> list[str]:
    """Insert rows in the given order, one per day from 2026-03-01."""
    uids = []
    with db.connect() as conn:
        for i, row in enumerate(rows, start=1):
            uids.append(db.insert_memory(
                conn, type=row.get("type", "note"), content=row["content"],
                domain=row.get("domain", ""), also=row.get("also", ""),
                created_at=_at(i)))
    return uids


# A week of generic-engineering work, one memory a day.
DAYS = [
    {"content": "cache warmup runs before the first request"},
    {"content": "the queue drain retries twice"},
    {"content": "token refresh happens on the first failure"},
    {"content": "index rebuild drops its triggers first"},
    {"content": "row merge keeps the older identifier"},
    {"content": "batch retry backs off before the second attempt"},
    {"content": "file upload rejects an empty part"},
]


# ------------------------------------------------------------- what the anchor is

def test_a_uid_names_the_anchor(store):
    uids = _seed(DAYS)
    result = server.timeline(uid=uids[3])
    assert result["anchored_by"] == "uid"
    assert result["anchor"]["uid"] == uids[3]


def test_a_query_finds_the_anchor(store):
    uids = _seed(DAYS)
    result = server.timeline(query="index rebuild triggers")
    assert result["anchored_by"] == "query"
    assert result["anchor"]["uid"] == uids[3]


def test_a_uid_wins_over_a_query(store):
    uids = _seed(DAYS)
    result = server.timeline(uid=uids[0], query="index rebuild triggers")
    assert (result["anchored_by"], result["anchor"]["uid"]) == ("uid", uids[0])


def test_neither_uid_nor_query_is_an_error(store):
    _seed(DAYS)
    result = server.timeline()
    assert result["ok"] is False
    assert "uid or query" in result["errors"][0]


def test_a_uid_naming_nothing_is_an_error(store):
    _seed(DAYS)
    assert server.timeline(uid="nope")["errors"] == ["no memory nope"]


def test_a_query_matching_nothing_is_an_error(store):
    _seed(DAYS)
    result = server.timeline(query="nothing here says any of these words")
    assert result["ok"] is False
    assert "no memory matches" in result["errors"][0]


# ------------------------------------------------------------------- the order

def test_the_neighbours_run_oldest_first_around_the_anchor(store):
    uids = _seed(DAYS)
    result = server.timeline(uid=uids[3])
    assert [r["uid"] for r in result["before"]] == uids[0:3]
    assert [r["uid"] for r in result["after"]] == uids[4:7]


def test_the_anchor_is_in_neither_list(store):
    uids = _seed(DAYS)
    result = server.timeline(uid=uids[3])
    assert uids[3] not in {r["uid"] for r in [*result["before"], *result["after"]]}


def test_the_counts_are_what_was_asked_for(store):
    uids = _seed(DAYS)
    result = server.timeline(uid=uids[3], before=1, after=2)
    assert [r["uid"] for r in result["before"]] == [uids[2]]
    assert [r["uid"] for r in result["after"]] == [uids[4], uids[5]]


def test_a_count_of_zero_returns_that_side_empty(store):
    uids = _seed(DAYS)
    result = server.timeline(uid=uids[3], before=0, after=1)
    assert result["before"] == []
    assert [r["uid"] for r in result["after"]] == [uids[4]]


def test_an_edge_of_the_store_runs_out_rather_than_wrapping(store):
    uids = _seed(DAYS)
    first = server.timeline(uid=uids[0])
    last = server.timeline(uid=uids[-1])
    assert first["before"] == []
    assert [r["uid"] for r in first["after"]] == uids[1:4]
    assert last["after"] == []
    assert [r["uid"] for r in last["before"]] == uids[3:6]


def test_records_written_in_the_same_instant_land_on_one_side_each(store):
    """created_at is not unique; insertion order separates a tie, so no row
    can be both older and newer than the anchor."""
    with db.connect() as conn:
        uids = [db.insert_memory(conn, type="note", content=f"queue drain step {i}",
                                 created_at=_at(1))
                for i in range(3)]
    result = server.timeline(uid=uids[1])
    assert [r["uid"] for r in result["before"]] == [uids[0]]
    assert [r["uid"] for r in result["after"]] == [uids[2]]


# ---------------------------------------------------------------- what is filtered

def test_a_domain_scopes_the_neighbourhood(store):
    uids = _seed([
        {"content": "cache warmup runs nightly", "domain": "acme/x100/p200"},
        {"content": "the queue drain retries twice", "domain": "zeta/x200"},
        {"content": "index rebuild drops its triggers", "domain": "acme/x100/p200"},
        {"content": "row merge keeps the older identifier", "domain": "zeta/x200"},
        {"content": "file upload rejects an empty part", "domain": "acme/x100/p300"},
    ])
    result = server.timeline(uid=uids[2], domain="acme/x100")
    assert [r["uid"] for r in result["before"]] == [uids[0]]
    assert [r["uid"] for r in result["after"]] == [uids[4]]


def test_a_cross_listed_memory_satisfies_the_scope(store):
    """The scope arm covers what BELONGS to a subject as well as what is
    filed under it, so a step of a flow filed in another branch is a
    neighbour of the flow's other steps."""
    uids = _seed([
        {"content": "cache warmup runs nightly", "domain": "omni/x900"},
        {"content": "the queue drain retries twice", "domain": "acme/x100/p200",
         "also": "omni/x900"},
        {"content": "row merge keeps the older identifier", "domain": "zeta/x200"},
        {"content": "token refresh happens on the first failure",
         "domain": "zeta/x200/p300", "also": "omni/x900"},
    ])
    result = server.timeline(uid=uids[0], domain="omni/x900", after=3)
    assert [r["uid"] for r in result["after"]] == [uids[1], uids[3]]


def test_a_bare_deep_segment_resolves_like_the_other_scoped_reads(store):
    uids = _seed([
        {"content": "cache warmup runs nightly", "domain": "acme/x100/p200"},
        {"content": "the queue drain retries twice", "domain": "zeta/x200"},
        {"content": "index rebuild drops its triggers", "domain": "acme/x100/p200"},
    ])
    result = server.timeline(uid=uids[0], domain="p200")
    assert [r["uid"] for r in result["after"]] == [uids[2]]


def test_a_type_filters_the_neighbourhood(store):
    uids = _seed([
        {"content": "cache warmup runs nightly", "type": "note"},
        {"content": "the queue drain retries twice", "type": "handoff"},
        {"content": "index rebuild drops its triggers", "type": "note"},
        {"content": "row merge keeps the older identifier", "type": "note"},
    ])
    result = server.timeline(uid=uids[0], type="note")
    assert [r["uid"] for r in result["after"]] == [uids[2], uids[3]]


def test_the_filters_scope_the_neighbours_and_not_the_anchor(store):
    uids = _seed([
        {"content": "cache warmup runs nightly", "domain": "acme/x100"},
        {"content": "the queue drain retries twice", "domain": "zeta/x200"},
        {"content": "index rebuild drops its triggers", "domain": "acme/x100"},
    ])
    result = server.timeline(uid=uids[1], domain="acme/x100")
    assert result["anchor"]["uid"] == uids[1]
    assert [r["uid"] for r in result["before"]] == [uids[0]]
    assert [r["uid"] for r in result["after"]] == [uids[2]]


def test_a_query_anchor_is_scoped_too(store):
    uids = _seed([
        {"content": "cache warmup runs nightly", "domain": "acme/x100"},
        {"content": "cache warmup runs nightly", "domain": "zeta/x200"},
    ])
    result = server.timeline(query="cache warmup", domain="zeta/x200")
    assert result["anchor"]["uid"] == uids[1]


def test_an_archived_neighbour_is_left_out(store):
    uids = _seed(DAYS)
    with db.connect() as conn:
        db.set_status(conn, uids[2], "archived")
    result = server.timeline(uid=uids[3], before=2)
    assert [r["uid"] for r in result["before"]] == [uids[0], uids[1]]


def test_an_archived_record_can_still_be_the_anchor(store):
    """A uid names one record; the filters are the neighbourhood's."""
    uids = _seed(DAYS)
    with db.connect() as conn:
        db.set_status(conn, uids[3], "archived")
    result = server.timeline(uid=uids[3], before=1, after=1)
    assert result["anchor"]["uid"] == uids[3]
    assert [r["uid"] for r in result["before"]] == [uids[2]]
    assert [r["uid"] for r in result["after"]] == [uids[4]]


# ------------------------------------------------------------- what each record is

def test_each_record_is_snippet_truncated_and_priced(store):
    long_body = "cache warmup runs before the first request of the day. " * 12
    uids = _seed([{"content": long_body}, {"content": "the queue drain retries twice"}])
    result = server.timeline(uid=uids[1])
    neighbour = result["before"][0]
    assert len(neighbour["content"]) < len(long_body)
    assert neighbour["est_tokens"] == db.est_tokens(len(long_body))
    assert result["anchor"]["est_tokens"] == db.est_tokens(
        len("the queue drain retries twice"))


def test_a_timeline_counts_what_it_handed_over(store):
    uids = _seed(DAYS)
    server.timeline(uid=uids[3], before=1, after=1)
    with db.connect() as conn:
        assert set(db.usage_for(conn, uids)) == {uids[2], uids[3], uids[4]}


def test_the_indexing_mirror_never_leaves(store):
    uids = _seed([{"content": "queue drain step", "domain": "acme/x100",
                   "also": "omni/x900"},
                  {"content": "row merge keeps the older identifier"}])
    result = server.timeline(uid=uids[1])
    assert "also_domains" not in result["before"][0]
    assert result["before"][0]["also"] == ["omni/x900"]
