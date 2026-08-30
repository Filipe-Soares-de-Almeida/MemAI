"""`est_tokens`: what a record a read found would cost to open.

Every list-style read is snippet-truncated, so each result carries the
token estimate for its FULL content and the response carries the sum over
what it returned. Covers the estimator, the per-record field on
search/recall/list_by_domain/list_recent/pulse, the aggregate, and the one
read that does not carry it.
"""

from __future__ import annotations

import pytest

from memai import db, server


def _long(text: str) -> str:
    """A body past SNIPPET_LIMIT, so every list result of it is truncated."""
    return (text + ". ") * 15


# Distinct enough that search's near-copy collapse keeps them apart.
EXPORT = _long("the export window is inclusive on both ends")
RETRY = _long("a retry without backoff produces a stampede")
MERGE = _long("row merge keeps the older identifier")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _full_estimate(text: str) -> int:
    return db.est_tokens(len(text))


# --------------------------------------------------------------- the estimator

def test_the_estimate_is_the_character_length_over_a_fixed_ratio():
    assert db.est_tokens(0) == 0
    assert db.est_tokens(1) == 1                          # rounds up
    assert db.est_tokens(db.CHARS_PER_TOKEN) == 1
    assert db.est_tokens(db.CHARS_PER_TOKEN + 1) == 2
    assert db.est_tokens(400) == 400 // db.CHARS_PER_TOKEN


# ------------------------------------------------- the full record, not the snippet

def test_a_search_result_is_priced_by_the_full_record(store):
    server.note("fixture title", content=EXPORT, tags="finding")
    hit = server.search("export window")["results"][0]
    assert len(hit["content"]) < len(EXPORT)              # snippet-truncated
    assert hit["est_tokens"] == _full_estimate(EXPORT)
    assert hit["est_tokens"] > db.est_tokens(len(hit["content"]))


def test_the_estimate_matches_what_the_full_record_holds(store):
    uid = server.note("fixture title", content=EXPORT)["uid"]
    hit = server.search("export window")["results"][0]
    assert hit["est_tokens"] == db.est_tokens(len(server.get_memory(uid)["content"]))


@pytest.mark.parametrize("read", [
    lambda: server.search("export window"),
    lambda: server.recall("export window"),
    lambda: server.list_by_domain("acme/x100"),
    lambda: server.list_recent(),
])
def test_every_list_read_prices_each_result(store, read):
    server.note("fixture title", content=EXPORT, domain="acme/x100")
    results = read()["results"]
    assert results
    assert all(r["est_tokens"] == _full_estimate(EXPORT) for r in results)


def test_a_short_record_is_priced_too(store):
    """Present on every result, not only the ones a snippet cut."""
    server.note("fixture title", content="row merge keeps the older identifier")
    hit = server.list_recent()["results"][0]
    assert hit["content"] == "row merge keeps the older identifier"
    assert hit["est_tokens"] == _full_estimate("row merge keeps the older identifier")


# -------------------------------------------------------------- the aggregate

@pytest.mark.parametrize("read", [
    lambda: server.search("finding"),
    lambda: server.recall("finding"),
    lambda: server.list_by_domain("acme/x100"),
    lambda: server.list_recent(),
])
def test_the_response_sums_what_it_returned(store, read):
    for content in (EXPORT, RETRY, MERGE):
        server.note("fixture title", content=content, domain="acme/x100", tags="finding")
    response = read()
    assert len(response["results"]) == 3
    assert response["est_tokens"] == sum(r["est_tokens"] for r in response["results"])
    assert response["est_tokens"] == sum(
        _full_estimate(c) for c in (EXPORT, RETRY, MERGE))


def test_an_empty_response_costs_nothing(store):
    assert server.search("nothing here says any of these words") == {
        "results": [], "est_tokens": 0}


def test_the_aggregate_prices_the_results_and_not_the_store(store):
    """A limit that cut the list cuts the aggregate with it."""
    for content in (EXPORT, RETRY, MERGE):
        server.note("fixture title", content=content, tags="finding")
    one = server.search("finding", limit=1)
    three = server.search("finding", limit=3)
    assert one["est_tokens"] == one["results"][0]["est_tokens"]
    assert three["est_tokens"] > one["est_tokens"]


# ------------------------------------------------------------------- warm-up

def test_a_pulse_prices_every_record_it_hands_over(store):
    server.checkpoint("fixture title", intent=EXPORT, established="the index is rebuilt",
                      pursuing="the batch retry", open_questions="none",
                      domain="acme/x100")
    server.handoff("fixture title", content=RETRY, domain="acme/x100")
    server.anti_pattern("fixture title", pattern=RETRY, why_wrong="it drops rows",
                        instead="drain the queue first", domain="acme/x100")
    server.note("fixture title", content=MERGE, domain="acme/x100")

    p = server.pulse(domain="acme/x100")
    for key in ("handoffs", "anti_patterns", "recent_notes"):
        assert p[key], key
        assert all(r["est_tokens"] > 0 for r in p[key]), key
    assert p["handoffs"][0]["est_tokens"] == _full_estimate(RETRY)
    assert p["recent_notes"][0]["est_tokens"] == _full_estimate(MERGE)


def test_the_checkpoint_is_priced_by_the_body_pulse_returned_whole(store):
    """latest_checkpoint is not truncated, so its estimate covers the body
    that is already in the response."""
    server.checkpoint("fixture title", intent=EXPORT, established="the index is rebuilt",
                      pursuing="the batch retry", open_questions="none",
                      domain="acme/x100")
    checkpoint = server.pulse(domain="acme/x100")["latest_checkpoint"]
    assert checkpoint["est_tokens"] == _full_estimate(checkpoint["content"])


def test_a_scope_with_no_checkpoint_has_nothing_to_price(store):
    assert server.pulse(domain="acme/x100")["latest_checkpoint"] == {}


# ---------------------------------------------- the read that is already full

def test_a_full_record_is_not_priced(store):
    """get_memory returns the whole thing -- there is no fetch left to budget."""
    uid = server.note("fixture title", content=EXPORT)["uid"]
    record = server.get_memory(uid)
    assert record["content"] == EXPORT
    assert "est_tokens" not in record
