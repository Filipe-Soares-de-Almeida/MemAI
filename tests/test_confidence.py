"""What `confidence` buys on the reading side.

`status` says whether a row is in play; `confidence` says whether what it
claims still holds. Marking a memory contradicted used to change nothing a
reader saw -- it went on competing for the top of a search and went on
being presented by pulse() as the current state.
"""

from __future__ import annotations

import pytest

from memai import db, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------- ranking

def test_contradicted_sorts_behind_what_still_holds(conn):
    wrong = db.insert_memory(conn, type="note", content="cache warmup runs hourly",
                             domain="acme/x100")
    right = db.insert_memory(conn, type="note", content="cache warmup runs nightly",
                             domain="acme/x100")
    db.set_confidence(conn, wrong, "contradicted")
    order = [r["uid"] for r in db.search_hybrid(conn, "cache warmup")]
    assert order == [right, wrong]


def test_contradicted_still_comes_back(conn):
    """Hiding it invites writing the same wrong thing again."""
    uid = db.insert_memory(conn, type="note", content="queue drain is single threaded")
    db.set_confidence(conn, uid, "contradicted")
    hits = db.search_hybrid(conn, "queue drain")
    assert [r["uid"] for r in hits] == [uid]
    assert hits[0]["confidence"] == "contradicted"


# ---------------------------------------------------------------- the warm-up

def test_pulse_leaves_a_contradicted_anti_pattern_out(store):
    live = server.anti_pattern(pattern="retry without backoff", why_wrong="stampede",
                               instead="exponential backoff", domain="acme/x100")["uid"]
    stale = server.anti_pattern(pattern="batch over 100 rows", why_wrong="times out",
                                instead="page it", domain="acme/x100")["uid"]
    server.set_confidence(stale, "contradicted")
    assert [r["uid"] for r in server.pulse("acme/x100")["anti_patterns"]] == [live]


def test_pulse_falls_through_to_a_sound_checkpoint(store):
    good = server.checkpoint(intent="i", established="e", pursuing="p",
                             open_questions="q", domain="acme/x100")["uid"]
    bad = server.checkpoint(intent="i2", established="e2", pursuing="p2",
                            open_questions="q2", domain="acme/x100")["uid"]
    server.set_confidence(bad, "contradicted")
    assert server.pulse("acme/x100")["latest_checkpoint"]["uid"] == good


def test_listing_a_scope_still_means_everything_in_it(store):
    """The exclusion is pulse's, not the list tools'."""
    uid = server.note(content="row merge keeps the older id", domain="acme/x100")["uid"]
    server.set_confidence(uid, "contradicted")
    assert [r["uid"] for r in server.list_by_domain("acme/x100")] == [uid]
    assert [r["uid"] for r in server.list_recent(domain="acme/x100")] == [uid]
