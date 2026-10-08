"""What `confidence` does on the reading side.

`status` says whether a row is in play; `confidence` says whether what it
claims still holds. A contradicted memory sorts behind everything that
still holds, in search and in pulse(), without disappearing.
"""

from __future__ import annotations

import pytest

from memai import server
from memai.store import connection, memories, search


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------- ranking

def test_contradicted_sorts_behind_what_still_holds(conn):
    wrong = memories.insert_memory(conn, type="note", content="cache warmup runs hourly",
                             domain="acme/x100")
    right = memories.insert_memory(conn, type="note", content="cache warmup runs nightly",
                             domain="acme/x100")
    memories.set_confidence(conn, wrong, "contradicted")
    order = [r["uid"] for r in search.search_ranked(conn, "cache warmup")]
    assert order == [right, wrong]


def test_contradicted_still_comes_back(conn):
    """Hiding it invites writing the same wrong thing again."""
    uid = memories.insert_memory(conn, type="note", content="queue drain is single threaded")
    memories.set_confidence(conn, uid, "contradicted")
    hits = search.search_ranked(conn, "queue drain")
    assert [r["uid"] for r in hits] == [uid]
    assert hits[0]["confidence"] == "contradicted"


# ---------------------------------------------------------------- the warm-up

def test_pulse_does_not_count_a_contradicted_anti_pattern(store):
    server.anti_pattern("fixture title", pattern="retry without backoff", why_wrong="stampede",
                        instead="exponential backoff", domain="acme/x100")
    stale = server.anti_pattern("fixture title", pattern="batch over 100 rows", why_wrong="times out",
                                instead="page it", domain="acme/x100")["uid"]
    server.set_confidence(stale, "contradicted")
    assert server.pulse("acme/x100")["must_read"] == [{"type": "anti_pattern", "count": 1}]


def test_pulse_falls_through_to_a_sound_checkpoint(store):
    good = server.checkpoint("fixture title", intent="i", established="e", pursuing="p",
                             open_questions="q", domain="acme/x100")["uid"]
    bad = server.checkpoint("fixture title", intent="i2", established="e2", pursuing="p2",
                            open_questions="q2", domain="acme/x100")["uid"]
    server.set_confidence(bad, "contradicted")
    assert server.pulse("acme/x100")["latest_checkpoint"]["uid"] == good


def test_listing_a_scope_still_means_everything_in_it(store):
    """The exclusion is pulse's, not the list tools'."""
    uid = server.note("fixture title", content="row merge keeps the older id", domain="acme/x100")["uid"]
    server.set_confidence(uid, "contradicted")
    assert [r["uid"] for r in server.list_by_domain("acme/x100")["results"]] == [uid]
    assert [r["uid"] for r in server.list_recent(domain="acme/x100")["results"]] == [uid]
