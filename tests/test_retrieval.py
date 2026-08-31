"""How the candidate set is built, before anyone judges it.

Retrieval only widens candidates -- the calling agent still decides what
answers the query -- but which candidates it widens to, and in what order,
is the whole value of the store. These cover the three places that ordering
is decided: how a match is weighted, how a query is turned into terms, and
the relations graph a result is read against.
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


# ------------------------------------------------------------ ranked output

def test_the_ranked_search_keeps_the_keyword_order(conn):
    """search_ranked reorders only for confidence, so the row bm25 puts
    first is the row it returns first."""
    for i in range(12):
        db.insert_memory(conn, type="note", content=f"unrelated filler number {i}")
    target = db.insert_memory(conn, type="note",
                              content="the export window is inclusive on both ends")
    top_fts = db.search_memories(conn, "export window inclusive", limit=1)[0]["uid"]
    ranked = [r["uid"] for r in db.search_ranked(conn, "export window inclusive", limit=3)]
    assert top_fts == target and ranked[0] == target


def test_it_returns_only_what_was_asked_for(conn):
    for i in range(20):
        db.insert_memory(conn, type="note", content=f"index rebuild step {i} of the pass")
    assert len(db.search_ranked(conn, "rebuild", limit=3)) == 3


def test_every_hit_says_the_keyword_index_found_it(conn):
    db.insert_memory(conn, type="note", content="the export window is inclusive")
    hits = db.search_ranked(conn, "export window", limit=5)
    assert hits and all(h["match_source"] == "fts" for h in hits)
    assert all(h["fts_rank"] is not None for h in hits)


# ------------------------------------------------------------- what tags reach

# every writer that takes tags, with the fields it cannot do without
_TAGGED_WRITERS = (
    ("note", {"title": "blank parts in the loader",
              "content": "the loader skips a blank part"}),
    ("checkpoint", {"title": "loader work", "intent": "finish the loader",
                    "established": "parts stream",
                    "pursuing": "the blank case", "open_questions": "none"}),
    ("anti_pattern", {"title": "batch retry after one bad part",
                      "pattern": "retrying the whole batch",
                      "why_wrong": "it doubles writes",
                      "instead": "retry the failed part"}),
    ("reasoning", {"title": "why the loader stalls",
                   "hypothesis": "the loader stalls on blanks", "reasoning": "read the trace",
                   "result": "it skips them", "revised_belief": "blanks are handled",
                   "next_time": "read the trace first"}),
    ("handoff", {"title": "where the drain step stands",
                 "content": "pick up at the drain step"}),
)


@pytest.mark.parametrize("tool, fields", _TAGGED_WRITERS)
def test_a_tag_finds_a_memory_whose_body_never_says_the_word(store, tool, fields):
    """Tags weigh second only to the body and carry the synonyms it never
    uses, so every type that can be written has to be able to reach them."""
    uid = getattr(server, tool)(domain="acme/x100", tags="cold-start, warmup",
                                **fields)["uid"]
    assert uid in [h["uid"] for h in server.search(query="cold-start")["results"]]


@pytest.mark.parametrize("tool, fields", _TAGGED_WRITERS)
def test_no_writer_stamps_its_own_type_into_the_tags_column(store, tool, fields):
    """The type is a column of its own that every read can filter on. A copy
    of it in the weighted tags column buys no reach and costs a term."""
    result = getattr(server, tool)(**fields)
    with db.connect() as conn:
        assert db.get_memory(conn, result["uid"])["tags"] == ""


@pytest.mark.parametrize("tool, fields", _TAGGED_WRITERS)
def test_an_untagged_write_says_so_while_the_writer_can_still_fix_it(store, tool, fields):
    result = getattr(server, tool)(**fields)
    assert result["tags_indexed"] == 0
    assert "tags_hint" in result


def test_a_tagged_write_reports_what_it_indexed(store):
    result = server.note("fixture title", content="the drain retries twice", tags="queue, drain, retry")
    assert result["tags_indexed"] == 3
    assert "tags_hint" not in result


# ----------------------------------------------------------- what came after

def test_a_superseded_hit_names_its_successor(conn):
    old = db.insert_memory(conn, type="note", content="the export window is inclusive")
    new = db.insert_memory(conn, type="note", content="the export window excludes its end")
    db.add_relation(conn, new, old, "supersedes")
    by_uid = {r["uid"]: r for r in db.search_ranked(conn, "export window")}
    assert by_uid[old]["succeeded_by"] == [new]
    assert "succeeded_by" not in by_uid[new]


def test_an_unsuperseded_result_says_nothing(conn):
    db.insert_memory(conn, type="note", content="row merge keeps the older id")
    assert all("succeeded_by" not in r for r in db.search_ranked(conn, "row merge"))


def test_another_relation_type_is_not_succession(conn):
    a = db.insert_memory(conn, type="note", content="queue drain notes")
    b = db.insert_memory(conn, type="note", content="queue drain more notes")
    db.add_relation(conn, a, b, "relates_to")
    assert all("succeeded_by" not in r for r in db.search_ranked(conn, "queue drain"))


# ------------------------------------------------------------- near-copies

_COPY = "the cache warmup runs before the first request of the day, every day"


def test_near_copies_spend_one_slot(conn):
    kept = db.insert_memory(conn, type="note", content=_COPY)
    dup_a = db.insert_memory(conn, type="note", content=_COPY)
    dup_b = db.insert_memory(conn, type="note", content=_COPY + ".")
    other = db.insert_memory(conn, type="note", content="cache eviction is size based")
    hits = db.search_ranked(conn, "cache", collapse=True)
    assert {h["uid"] for h in hits} == {kept, other}
    assert set(next(h for h in hits if h["uid"] == kept)["collapsed"]) == {dup_a, dup_b}


def test_collapse_is_off_by_default(conn):
    """The dashboard must see that one fact was written three times."""
    for _ in range(3):
        db.insert_memory(conn, type="note", content=_COPY)
    hits = db.search_ranked(conn, "cache")
    assert len(hits) == 3
    assert all("collapsed" not in h for h in hits)


def test_two_takes_on_one_subject_are_not_copies(conn):
    """Collapsing merely RELATED memories would be deciding relevance."""
    a = db.insert_memory(conn, type="note", content="cache warmup runs nightly")
    b = db.insert_memory(conn, type="note", content="cache warmup is skipped on holidays")
    assert len(db.search_ranked(conn, "cache warmup", collapse=True)) == 2


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
        server.note("fixture title", content=content, tags="finding")
    assert len(server.search("finding")["results"]) == 10
    assert len(server.search("finding", limit=12)["results"]) == 12


def test_recall_defaults_the_same_way(store):
    for content in _FINDINGS:
        server.note("fixture title", content=content, tags="finding")
    assert len(server.recall("finding")["results"]) == 10


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
    db.search_ranked(conn, query, limit=5)


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


def test_the_hint_names_a_call_that_works(store):
    """TAGS_HINT tells an untagged writer to fix it with edit_memory(tags=...).

    The hint is the only place that call is advertised, so a signature
    without it would send every untagged write at a tool that refuses.
    """
    assert "edit_memory(uid, tags='...')" in server.TAGS_HINT
    uid = server.note("The drain retry window", content="the drain retries twice")["uid"]
    assert server.edit_memory(uid, tags="queue, drain, retry") == {
        "ok": True, "changed": ["tags"]}
    with db.connect() as conn:
        assert db.get_memory(conn, uid)["tags"] == "queue, drain, retry"


def test_tags_added_after_the_write_are_searchable(store):
    """The tags column is indexed, so the update has to reindex the row."""
    uid = server.note("The drain retry window", content="it gives up on the third pass")["uid"]
    with db.connect() as conn:
        assert db.search_memories(conn, "backoff") == []
    server.edit_memory(uid, tags="backoff, retry")
    with db.connect() as conn:
        assert [r["uid"] for r in db.search_memories(conn, "backoff")] == [uid]


def test_editing_tags_replaces_the_set(store):
    uid = server.note("The drain retry window", content="the drain retries twice",
                      tags="queue, drain")["uid"]
    server.edit_memory(uid, tags="backoff")
    with db.connect() as conn:
        assert db.get_memory(conn, uid)["tags"] == "backoff"


def test_empty_tags_leave_the_stored_set_alone(store):
    """'' is 'do not touch', like source_ref: clearing is a dashboard edit."""
    uid = server.note("The drain retry window", content="the drain retries twice",
                      tags="queue, drain")["uid"]
    assert server.edit_memory(uid, tags="   ")["ok"] is False
    with db.connect() as conn:
        assert db.get_memory(conn, uid)["tags"] == "queue, drain"
