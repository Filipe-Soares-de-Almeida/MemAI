"""How the candidate set is built, before anyone judges it.

Retrieval only widens candidates -- the calling agent still decides what
answers the query -- but which candidates it widens to, and in what order,
is the whole value of the store. These cover the four places that ordering
was being decided badly: an unweighted keyword index, terms only ever OR'd,
a fusion that could not see past its own output limit, and a relations
graph nobody read.
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


# ----------------------------------------------------- what a match is worth

def test_content_outranks_a_domain_that_merely_spells_the_word(conn):
    """A store organised by domain used to rank every row filed under
    'acme/cache' for the word "cache", whether or not it said anything
    about one -- and in such a store that is most of the store."""
    filed = [
        db.insert_memory(conn, type="note", domain="acme/cache", content=text)
        for text in ("the queue drain retries twice before giving up",
                     "row merge keeps the older identifier",
                     "an empty part is rejected on upload")
    ]
    about = db.insert_memory(conn, type="note", domain="zeta/x100",
                             content="cache warmup runs before the first request")
    order = [r["uid"] for r in db.search_memories(conn, "cache")]
    assert order[0] == about
    assert set(filed) <= set(order)  # still findable by the path they are filed under


def test_a_path_that_repeats_the_word_does_not_win_on_repetition(conn):
    echo = db.insert_memory(conn, type="note", domain="acme/cache/cache-warmup",
                            content="runs on boot")
    about = db.insert_memory(conn, type="note", domain="zeta/x100",
                             content="the cache is warmed before the first request of the day")
    assert [r["uid"] for r in db.search_memories(conn, "cache")] == [about, echo]


def test_a_scope_name_is_still_searchable(conn):
    uid = db.insert_memory(conn, type="note", domain="omni/x900",
                           content="nothing here repeats the path")
    assert [r["uid"] for r in db.search_memories(conn, "x900")] == [uid]


# ------------------------------------------------------------- fusion depth

def test_each_arm_fetches_deeper_than_the_caller_asked(conn, monkeypatch):
    """Fusion exists to recover the row ranked just outside one retriever's
    window and near the top of the other's. Fetching `limit` per arm threw
    that row away before the merge could see it."""
    asked: list[int] = []
    real = db.search_memories

    def spy(conn_, query, **kw):
        asked.append(kw["limit"])
        return real(conn_, query, **kw)

    monkeypatch.setattr(db, "search_memories", spy)
    db.insert_memory(conn, type="note", content="file upload retry")
    db.search_hybrid(conn, "upload", limit=5)
    assert asked == [5 * db._FUSION_FETCH]


def test_fusion_still_returns_only_what_was_asked_for(conn):
    for i in range(20):
        db.insert_memory(conn, type="note", content=f"index rebuild step {i} of the pass")
    assert len(db.search_hybrid(conn, "rebuild", limit=3)) == 3


# ----------------------------------------------------------- what came after

def test_a_superseded_hit_names_its_successor(conn):
    old = db.insert_memory(conn, type="note", content="the export window is inclusive")
    new = db.insert_memory(conn, type="note", content="the export window excludes its end")
    db.add_relation(conn, new, old, "supersedes")
    by_uid = {r["uid"]: r for r in db.search_hybrid(conn, "export window")}
    assert by_uid[old]["succeeded_by"] == [new]
    assert "succeeded_by" not in by_uid[new]


def test_an_unsuperseded_result_says_nothing(conn):
    db.insert_memory(conn, type="note", content="row merge keeps the older id")
    assert all("succeeded_by" not in r for r in db.search_hybrid(conn, "row merge"))


def test_another_relation_type_is_not_succession(conn):
    a = db.insert_memory(conn, type="note", content="queue drain notes")
    b = db.insert_memory(conn, type="note", content="queue drain more notes")
    db.add_relation(conn, a, b, "relates_to")
    assert all("succeeded_by" not in r for r in db.search_hybrid(conn, "queue drain"))


# ------------------------------------------------------------- near-copies

_COPY = "the cache warmup runs before the first request of the day, every day"


def test_near_copies_spend_one_slot(conn):
    kept = db.insert_memory(conn, type="note", content=_COPY)
    dup_a = db.insert_memory(conn, type="note", content=_COPY)
    dup_b = db.insert_memory(conn, type="note", content=_COPY + ".")
    other = db.insert_memory(conn, type="note", content="cache eviction is size based")
    hits = db.search_hybrid(conn, "cache", collapse=True)
    assert {h["uid"] for h in hits} == {kept, other}
    assert set(next(h for h in hits if h["uid"] == kept)["collapsed"]) == {dup_a, dup_b}


def test_collapse_is_off_by_default(conn):
    """The dashboard must see that one fact was written three times."""
    for _ in range(3):
        db.insert_memory(conn, type="note", content=_COPY)
    hits = db.search_hybrid(conn, "cache")
    assert len(hits) == 3
    assert all("collapsed" not in h for h in hits)


def test_two_takes_on_one_subject_are_not_copies(conn):
    """Collapsing merely RELATED memories would be deciding relevance."""
    a = db.insert_memory(conn, type="note", content="cache warmup runs nightly")
    b = db.insert_memory(conn, type="note", content="cache warmup is skipped on holidays")
    assert len(db.search_hybrid(conn, "cache warmup", collapse=True)) == 2


# ------------------------------------------------------------- tool defaults

_FINDINGS = (
    "the export window is inclusive on both ends",
    "a retry without backoff produces a stampede",
    "row merge keeps the older identifier",
    "the digest mailout runs after the audit sweep",
    "token refresh happens on the first failure",
    "index rebuild drops its triggers before anything else",
    "log rotation is size based, never daily",
    "quota reset lands at midnight in the store's own zone",
    "shard rebalance moves one range at a time",
    "session cleanup skips whatever is still writing",
    "file upload rejects an empty part",
    "schema migration runs inside a single transaction",
)


def test_search_defaults_to_a_handful(store):
    """Thirty results at 400 chars each is twelve thousand characters of
    context for one call. The caller can still ask for more."""
    for content in _FINDINGS:
        server.note(content=content, tags="finding")
    assert len(server.search("finding")) == 10
    assert len(server.search("finding", limit=12)) == 12


def test_recall_defaults_the_same_way(store):
    for content in _FINDINGS:
        server.note(content=content, tags="finding")
    assert len(server.recall("finding")) == 10
