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
def vec_conn(tmp_path, fake_embedder):
    """A store whose vector table exists: _ensure_vec runs at connect time,
    so the embedder has to be in place before the connection is opened."""
    with db.connect(tmp_path / "vec.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


# ----------------------------------------------------- what a match is worth

def test_content_outranks_a_domain_that_merely_spells_the_word(conn):
    """Content outranks a domain that merely spells the query word. In a
    store organised by domain that would otherwise be most of the store."""
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

def test_the_arms_fetch_what_the_fusion_depth_says(conn, monkeypatch):
    """Each arm is asked for `limit * _FUSION_FETCH` rows, so the knob
    reaches the retrievers rather than sitting unread.
    """
    asked: list[int] = []
    real = db.search_memories

    def spy(conn_, query, **kw):
        asked.append(kw["limit"])
        return real(conn_, query, **kw)

    monkeypatch.setattr(db, "search_memories", spy)
    db.insert_memory(conn, type="note", content="file upload retry")
    db.search_hybrid(conn, "upload", limit=5)
    assert asked == [5 * db._FUSION_FETCH]


def test_fusion_does_not_cost_the_keyword_arm_its_own_top_hit(conn):
    """The regression the depth caused, in miniature: a row the keyword side
    ranks first must still be in the fused result."""
    for i in range(12):
        db.insert_memory(conn, type="note", content=f"unrelated filler number {i}")
    target = db.insert_memory(conn, type="note",
                              content="the export window is inclusive on both ends")
    top_fts = db.search_memories(conn, "export window inclusive", limit=1)[0]["uid"]
    fused = [r["uid"] for r in db.search_hybrid(conn, "export window inclusive", limit=3)]
    assert top_fts == target and target in fused


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
    assert len(server.search("finding")["results"]) == 10
    assert len(server.search("finding", limit=12)["results"]) == 12


def test_recall_defaults_the_same_way(store):
    for content in _FINDINGS:
        server.note(content=content, tags="finding")
    assert len(server.recall("finding")["results"]) == 10


# ------------------------------------------------------- how far is too far

def test_a_knn_asked_for_more_than_it_has_does_not_invent_neighbours(vec_conn):
    """The defect this floor exists for: a search for a word the store has
    never seen came back with the WHOLE store, every row past the keyword
    matches labelled match_source='vec' as if the vector side had found it.
    A KNN has no notion of "nothing here is close" -- ask for 200 and it
    returns the 200 nearest, however far away."""
    for text in ("car maintenance schedule", "database tuning note",
                 "alpha beta note", "car schedule"):
        db.insert_memory(vec_conn, type="note", content=text)
    swept = db.search_semantic(vec_conn, "car", limit=100, max_distance=1)
    bounded = db.search_semantic(vec_conn, "car", limit=100)
    assert len(swept) == 4          # the KNN happily returns everything
    assert len(bounded) < len(swept)  # the floor does not


def test_the_floor_keeps_what_is_actually_close(vec_conn):
    uid = db.insert_memory(vec_conn, type="note", content="car maintenance schedule")
    db.insert_memory(vec_conn, type="note", content="alpha beta note")
    assert [r["uid"] for r in db.search_semantic(vec_conn, "automobile maintenance")] == [uid]


def test_a_far_row_is_not_labelled_as_a_vector_match(vec_conn):
    """match_source has to mean something: 'vec' should say the vector arm
    found this, not that the keyword arm did not."""
    db.insert_memory(vec_conn, type="note", content="alpha beta note")
    hits = db.search_hybrid(vec_conn, "car maintenance", limit=50)
    assert all(h["match_source"] != "vec" for h in hits)


def test_the_vector_arm_does_not_get_an_equal_vote(vec_conn):
    """The vector arm's weight stays between silence and an equal vote."""
    assert 0 < db.VEC_WEIGHT < 1


def test_a_keyword_hit_outranks_a_vector_hit_at_the_same_position(vec_conn):
    keyword = db.insert_memory(vec_conn, type="note", content="the alpha schedule note")
    semantic = db.insert_memory(vec_conn, type="note", content="car maintenance")
    # 'alpha' is a keyword match; 'automobile' reaches the other by vector
    order = [r["uid"] for r in db.search_hybrid(vec_conn, "alpha automobile", limit=2)]
    assert order.index(keyword) < order.index(semantic)


def test_the_vector_arm_still_contributes_what_keywords_miss(vec_conn):
    """A vector hit is still returned when the query shares no keyword with it."""
    uid = db.insert_memory(vec_conn, type="note", content="car maintenance schedule")
    hits = db.search_hybrid(vec_conn, "automobile", limit=5)
    assert [h["uid"] for h in hits] == [uid]
    assert hits[0]["match_source"] == "vec"


# ------------------------------------------------- queries that are not text

@pytest.mark.parametrize("query", [
    "cache AND warmup", "AND", "nota NOT fiscal", "termo NEAR outro",
    'aspas"no"meio', "*", "(", "a\"b\" OR c", "^caret", "-minus",
])
def test_an_fts5_operator_in_a_query_is_a_term_not_syntax(conn, query):
    """Every term is quoted, so a bare 'AND', 'NOT' or 'NEAR' reaches the
    engine as a term. Unquoted it is an fts5 operator and the search raises
    OperationalError, which out of a tool call is a crash."""
    db.insert_memory(conn, type="note", content="cache warmup runs nightly")
    db.search_memories(conn, query, limit=5)          # must not raise
    db.search_hybrid(conn, query, limit=5)


def test_quoting_did_not_change_what_a_plain_query_matches(conn):
    uid = db.insert_memory(conn, type="note", content="cache warmup runs nightly")
    db.insert_memory(conn, type="note", content="row merge keeps the older id")
    assert [r["uid"] for r in db.search_memories(conn, "cache warmup")] == [uid]


def test_more_terms_widen_the_net(conn):
    """Each added term widens the match set: the terms are OR'd, not AND'd."""
    a = db.insert_memory(conn, type="note", content="the nightly cache warmup")
    b = db.insert_memory(conn, type="note", content="an index rebuild drops triggers")
    assert {r["uid"] for r in db.search_memories(conn, "cache")} == {a}
    assert {r["uid"] for r in db.search_memories(conn, "cache rebuild triggers")} == {a, b}
