"""Tests for the memory-optimization workflow.

Covers the db-layer staging/apply/revert dispatchers per suggestion kind
plus the admin API (backup-before-apply, revert, reject). Same hermetic
setup as the rest of the suite: the admin client points MEMAI_HOME at a
tmp dir.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import shaped
from memai import admin, db


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


# ------------------------------------------------------------------ db layer

def _mk(conn, content="a fact", **kw):
    """A memory of any type, with `content` shaped to what that type holds."""
    type_ = kw.pop("type", "note")
    return db.insert_memory(conn, type=type_, content=shaped(type_, content), **kw)


def _mk_diagram(conn):
    uid, errors = db.insert_diagram(
        conn, title="Cache warmup routine",
        nodes=[
            {"key": "start", "shape": "start", "label": "Receive the warmup trigger"},
            {"key": "fill", "label": "Load every hot key"},
            {"key": "done", "shape": "end", "label": "Report the warmup as finished"},
        ],
        edges=[{"from": "start", "to": "fill"}, {"from": "fill", "to": "done"}],
    )
    assert errors == []
    return uid


def test_stage_validates_and_reports_errors(conn):
    uid = _mk(conn, content="keep me")
    res = db.stage_optimization(conn, "run", [
        {"kind": "reword", "target_uid": uid, "payload": {"new_content": "better"}},
        {"kind": "bogus", "target_uid": uid, "payload": {}},
        {"kind": "reword", "target_uid": "deadbeef", "payload": {"new_content": "x"}},
        {"kind": "set_confidence", "target_uid": uid, "payload": {"confidence": "nope"}},
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {1, 2, 3}
    sugs = db.get_optimization_suggestions(conn, res["run_id"])
    assert len(sugs) == 1 and sugs[0]["kind"] == "reword"


def test_a_note_over_the_cap_stages_nothing(conn):
    """The run note holds a summary, so an oversized one raises before any
    suggestion is written."""
    uid = _mk(conn, content="keep me")
    sug = [{"kind": "reword", "target_uid": uid, "payload": {"new_content": "better"}}]
    with pytest.raises(ValueError, match=f"the limit is {db.RUN_NOTE_MAX}"):
        db.stage_optimization(conn, "x" * (db.RUN_NOTE_MAX + 1), sug)
    assert db.list_optimization_runs(conn) == []
    res = db.stage_optimization(conn, "x" * db.RUN_NOTE_MAX, sug)
    assert res["staged"] == 1


def test_stage_no_valid_suggestions_creates_no_run(conn):
    res = db.stage_optimization(conn, "", [{"kind": "bogus", "payload": {}}])
    assert res["run_id"] is None and res["staged"] == 0
    assert db.list_optimization_runs(conn) == []


@pytest.mark.parametrize("kind", ["compact", "reword"])
def test_stage_refuses_rewriting_a_diagram(conn, kind):
    """A diagram's content is the projection of its graph, so the panel never
    gets to offer a rewrite the next structural edit would regenerate over."""
    uid = _mk_diagram(conn)
    res = db.stage_optimization(conn, "r", [
        {"kind": kind, "target_uid": uid, "payload": {"new_content": "hand written"}},
    ])
    assert res["staged"] == 0 and res["run_id"] is None
    assert "generated from the graph" in res["errors"][0]["error"]
    assert db.get_memory(conn, uid)["content"].startswith("DIAGRAM:")


def test_apply_refuses_a_diagram_rewrite_staged_before_the_guard(conn):
    """Runs staged before staging refused this still hold one, so apply checks too."""
    diag, note = _mk_diagram(conn), _mk(conn, content="untouched")
    before = db.get_memory(conn, diag)["content"]
    run = db.stage_optimization(conn, "r", [
        {"kind": "reword", "target_uid": note, "payload": {"new_content": "better"}},
    ])
    conn.execute(
        """INSERT INTO optimization_suggestions
           (run_id, kind, target_uid, payload, rationale, verified, status, created_at)
           VALUES (?, 'reword', ?, ?, '', '', 'pending', ?)""",
        (run["run_id"], diag, json.dumps({"new_content": "hand written"}), db.now_iso()),
    )
    sug = db.get_optimization_suggestions(conn, run["run_id"])[-1]
    with pytest.raises(ValueError, match="generated from the graph"):
        db.apply_suggestion(conn, sug["id"])
    assert db.get_memory(conn, diag)["content"] == before
    assert db.get_suggestion(conn, sug["id"])["status"] == "pending"


@pytest.mark.parametrize("kind,payload,check", [
    ("reword", {"new_content": "reworded"}, lambda r: r["content"] == "reworded"),
    ("compact", {"new_content": "short"}, lambda r: r["content"] == "short"),
    ("retag", {"tags": "x, y"}, lambda r: r["tags"] == "x, y"),
    ("retitle", {"title": "How the drain retries"},
     lambda r: r["title"] == "How the drain retries"),
    ("redomain", {"domain": "newdom"}, lambda r: r["domain"] == "newdom"),
    ("set_confidence", {"confidence": "confirmed"}, lambda r: r["confidence"] == "confirmed"),
    ("archive", {"reason": "stale"}, lambda r: r["status"] == "archived"),
])
def test_apply_and_revert_roundtrip(conn, kind, payload, check):
    uid = _mk(conn, content="original", domain="d0", tags="old", type="note")
    before = dict(db.get_memory(conn, uid))
    run = db.stage_optimization(conn, "r", [
        {"kind": kind, "target_uid": uid, "payload": payload, "rationale": "why", "verified": "checked newer memories"},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]

    db.apply_suggestion(conn, sug["id"])
    assert check(db.get_memory(conn, uid))
    applied = db.get_suggestion(conn, sug["id"])
    assert applied["status"] == "applied" and applied["prev_state"]

    db.revert_suggestion(conn, sug["id"])
    after = db.get_memory(conn, uid)
    assert after["content"] == before["content"]
    assert after["tags"] == before["tags"]
    # a row that had no name goes back to having none: undo restores the
    # state a backfill found, not a state a writer could have made
    assert after["title"] == before["title"]
    assert after["domain"] == before["domain"]
    assert after["confidence"] == before["confidence"]
    assert after["status"] == before["status"]
    assert db.get_suggestion(conn, sug["id"])["status"] == "pending"


def test_crosslist_replaces_the_whole_set_and_reverts(conn):
    uid = _mk(conn, content="queue drain step", domain="acme/x100/p200",
              also="omni/x900")
    run = db.stage_optimization(conn, "r", [
        {"kind": "crosslist", "target_uid": uid,
         "payload": {"also": ["omni/x900", "omni/x800"]}, "rationale": "why"},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]

    db.apply_suggestion(conn, sug["id"])
    assert db.get_domain_links(conn, uid) == ["omni/x800", "omni/x900"]
    # the scope reads it, which is the point of staging this at all
    assert [r["uid"] for r in db.list_by_domain(conn, "omni/x800")] == [uid]

    db.revert_suggestion(conn, sug["id"])
    assert db.get_domain_links(conn, uid) == ["omni/x900"]
    assert db.list_by_domain(conn, "omni/x800") == []


def test_crosslist_can_drop_every_membership(conn):
    uid = _mk(conn, content="x", domain="acme", also="omni/x900")
    run = db.stage_optimization(conn, "r", [
        {"kind": "crosslist", "target_uid": uid, "payload": {"also": []}},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    db.apply_suggestion(conn, sug["id"])
    assert db.get_domain_links(conn, uid) == []
    db.revert_suggestion(conn, sug["id"])
    assert db.get_domain_links(conn, uid) == ["omni/x900"]


def test_crosslist_stages_the_paths_that_will_actually_hold(conn):
    """The panel shows this payload as the proposal, so the policy that drops
    a redundant path has to run at staging, not only on apply."""
    uid = _mk(conn, content="x", domain="acme/x100/p200")
    run = db.stage_optimization(conn, "r", [
        {"kind": "crosslist", "target_uid": uid,
         "payload": {"also": ["acme", " omni // x900 ", "omni/x900"]}},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    assert json.loads(sug["payload"])["also"] == ["omni/x900"]


def test_crosslist_of_only_redundant_paths_is_rejected(conn):
    """It would stage as a clear, which is not the suggestion it looks like."""
    uid = _mk(conn, content="x", domain="acme/x100/p200")
    res = db.stage_optimization(conn, "r", [
        {"kind": "crosslist", "target_uid": uid, "payload": {"also": ["acme", "acme/x100"]}},
    ])
    assert res["staged"] == 0
    assert "already covered" in res["errors"][0]["error"]


def test_crosslist_requires_the_payload_field(conn):
    uid = _mk(conn, content="x", domain="acme")
    res = db.stage_optimization(conn, "r", [
        {"kind": "crosslist", "target_uid": uid, "payload": {}},
    ])
    assert res["staged"] == 0 and "payload.also required" in res["errors"][0]["error"]


def test_apply_link_and_revert(conn):
    a, b = _mk(conn, content="one"), _mk(conn, content="two")
    run = db.stage_optimization(conn, "r", [
        {"kind": "link", "payload": {"from_uid": a, "to_uid": b, "relation_type": "relates_to"}},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    db.apply_suggestion(conn, sug["id"])
    assert len(db.get_relations(conn, a)) == 1
    db.revert_suggestion(conn, sug["id"])
    assert db.get_relations(conn, a) == []


def test_apply_merge_archives_drop_and_links(conn):
    keep, drop = _mk(conn, content="canonical"), _mk(conn, content="dupe")
    run = db.stage_optimization(conn, "r", [
        {"kind": "merge", "payload": {"keep_uid": keep, "drop_uid": drop},
         "verified": "read both against the live routine"},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    db.apply_suggestion(conn, sug["id"])
    drow = db.get_memory(conn, drop)
    assert drow["status"] == "archived" and drow["superseded_by"] == keep
    assert len(db.get_relations(conn, keep)) == 1

    db.revert_suggestion(conn, sug["id"])
    drow = db.get_memory(conn, drop)
    assert drow["status"] == "active" and drow["superseded_by"] is None
    assert db.get_relations(conn, keep) == []


def test_a_rejection_can_be_taken_back(conn):
    """Reject is a decision a reader can get wrong.

    Nothing was written to any memory, so taking the answer back is only a
    change of status -- but it has to be possible, or a mis-click in the
    day rail is a dead end no undo reaches.
    """
    uid = _mk(conn, content="original")
    staged = db.stage_optimization(conn, "reject then think again", [
        {"kind": "retag", "target_uid": uid, "payload": {"tags": "x"}, "rationale": "r"},
    ])
    sug = db.get_optimization_suggestions(conn, staged["run_id"])[0]

    db.reject_suggestion(conn, sug["id"])
    assert db.get_suggestion(conn, sug["id"])["status"] == "rejected"

    db.revert_suggestion(conn, sug["id"])
    back = db.get_suggestion(conn, sug["id"])
    assert back["status"] == "pending"
    assert back["decided_at"] is None
    # and it can then be applied for real
    db.apply_suggestion(conn, sug["id"])
    assert db.get_memory(conn, uid)["tags"] == "x"


def test_reverting_a_pending_suggestion_is_refused(conn):
    """It is already on the table; saying so beats performing a no-op."""
    uid = _mk(conn)
    staged = db.stage_optimization(conn, "nothing decided", [
        {"kind": "retag", "target_uid": uid, "payload": {"tags": "x"}, "rationale": "r"},
    ])
    sug = db.get_optimization_suggestions(conn, staged["run_id"])[0]
    with pytest.raises(ValueError, match="already pending"):
        db.revert_suggestion(conn, sug["id"])


def test_reverting_a_rejection_writes_nothing_to_the_memory(conn):
    uid = _mk(conn, content="untouched")
    before = dict(db.get_memory(conn, uid))
    staged = db.stage_optimization(conn, "reject and revert", [
        {"kind": "reword", "target_uid": uid, "payload": {"new_content": "rewritten"},
         "rationale": "r"},
    ])
    sug = db.get_optimization_suggestions(conn, staged["run_id"])[0]
    db.reject_suggestion(conn, sug["id"])
    db.revert_suggestion(conn, sug["id"])
    after = dict(db.get_memory(conn, uid))
    assert after["content"] == before["content"] == shaped("note", "untouched")
    assert after["updated_at"] == before["updated_at"]


def test_reject_leaves_memory_untouched(conn):
    uid = _mk(conn, content="untouched")
    run = db.stage_optimization(conn, "r", [
        {"kind": "reword", "target_uid": uid, "payload": {"new_content": "changed"}},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    db.reject_suggestion(conn, sug["id"])
    assert db.get_memory(conn, uid)["content"] == "untouched"
    assert db.get_suggestion(conn, sug["id"])["status"] == "rejected"


def test_run_summary_counts(conn):
    uid = _mk(conn)
    run = db.stage_optimization(conn, "counts", [
        {"kind": "set_confidence", "target_uid": uid, "payload": {"confidence": "confirmed"}},
        {"kind": "archive", "target_uid": uid, "payload": {}, "verified": "ticket closed upstream"},
    ])
    sugs = db.get_optimization_suggestions(conn, run["run_id"])
    db.apply_suggestion(conn, sugs[0]["id"])
    db.reject_suggestion(conn, sugs[1]["id"])
    r = db.list_optimization_runs(conn)[0]
    assert (r["total"], r["applied"], r["rejected"], r["pending"]) == (2, 1, 1, 0)


def test_purge_removes_suggestions(conn):
    uid = _mk(conn)
    run = db.stage_optimization(conn, "r", [
        {"kind": "reword", "target_uid": uid, "payload": {"new_content": "x"}},
    ])
    db.purge_memory(conn, uid)
    assert db.get_optimization_suggestions(conn, run["run_id"]) == []


def test_destructive_kinds_require_verified(conn):
    uid = _mk(conn)
    res = db.stage_optimization(conn, "guards", [
        {"kind": "archive", "target_uid": uid, "payload": {}},
        {"kind": "set_confidence", "target_uid": uid, "payload": {"confidence": "contradicted"}},
        # non-destructive: verified stays optional
        {"kind": "set_confidence", "target_uid": uid, "payload": {"confidence": "confirmed"}},
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0, 1}
    assert all("verified required" in e["error"] for e in res["errors"])


def test_every_kind_in_verified_required_is_actually_refused(conn):
    """The mapping is the list of kinds that demand a live-facts check, and
    the checks read their message from it -- so a kind added to the mapping
    without a check site would ask for nothing. Each one is staged here with
    an otherwise-valid payload and no `verified`.
    """
    a, b = _mk(conn, content="one"), _mk(conn, content="two")
    payloads = {
        "archive": ({"target_uid": a}, {}),
        "merge": ({}, {"keep_uid": a, "drop_uid": b}),
        "distill": ({}, {"source_uids": [a], "new_type": "note",
                         "new_content": "the durable fact",
                         "title": "What the drain retries"}),
    }
    assert set(payloads) == set(db.VERIFIED_REQUIRED), (
        "a kind entered VERIFIED_REQUIRED without a case here")
    for kind, (extra, payload) in payloads.items():
        res = db.stage_optimization(conn, f"{kind} guard",
                                    [{"kind": kind, "payload": payload, **extra}])
        assert res["staged"] == 0, kind
        assert res["errors"][0]["error"] == db.VERIFIED_REQUIRED[kind], kind


def test_merge_requires_verified(conn):
    """merge archives payload.drop_uid, so it takes the same verified as archive."""
    keep, drop = _mk(conn, content="canonical"), _mk(conn, content="dupe")
    res = db.stage_optimization(conn, "merge guard", [
        {"kind": "merge", "payload": {"keep_uid": keep, "drop_uid": drop}},
        {"kind": "merge", "payload": {"keep_uid": keep, "drop_uid": drop},
         "verified": "read both against the live routine"},
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0}
    assert "verified required" in res["errors"][0]["error"]
    assert db.get_memory(conn, drop)["status"] == "active"


def test_link_merge_reject_mismatched_target_uid(conn):
    a, b = _mk(conn, content="one"), _mk(conn, content="two")
    res = db.stage_optimization(conn, "targets", [
        {"kind": "link", "target_uid": b,  # mismatch: derived is from_uid
         "payload": {"from_uid": a, "to_uid": b, "relation_type": "relates_to"}},
        {"kind": "merge", "target_uid": a,  # mismatch: derived is drop_uid
         "payload": {"keep_uid": a, "drop_uid": b}, "verified": "read both"},
        {"kind": "link", "target_uid": a,  # matching is fine
         "payload": {"from_uid": a, "to_uid": b, "relation_type": "relates_to"}},
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0, 1}
    sug = db.get_optimization_suggestions(conn, res["run_id"])[0]
    assert sug["target_uid"] == a


def test_distill_validation(conn):
    a, b = _mk(conn, content="one"), _mk(conn, content="two")
    ok = {"source_uids": [a, b], "new_type": "note", "new_content": "the durable fact",
          "title": "What the drain retries"}
    res = db.stage_optimization(conn, "distill guards", [
        {"kind": "distill", "payload": ok},                                     # no verified
        {"kind": "distill", "target_uid": a, "payload": ok, "verified": "v"},   # target_uid forbidden
        {"kind": "distill", "payload": {**ok, "source_uids": []}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "source_uids": [a, a]}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "source_uids": [a, "deadbeef"]}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "new_type": "checkpoint"}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "new_content": "  "}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "title": "  "}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "title": "N" + "a" * db.TITLE_MAX},
         "verified": "v"},
        {"kind": "distill", "payload": ok, "verified": "checked repo"},         # valid
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0, 1, 2, 3, 4, 5, 6, 7, 8}


def test_distill_rejects_a_payload_key_it_does_not_apply(conn):
    """A key outside the distill payload comes back in errors instead of being dropped."""
    a = _mk(conn, content="one")
    ok = {"source_uids": [a], "new_type": "note", "new_content": "the durable fact",
          "title": "What the drain retries"}
    res = db.stage_optimization(conn, "distill keys", [
        {"kind": "distill", "payload": {**ok, "review_after": "90d"}, "verified": "checked repo"},
        {"kind": "distill", "payload": {**ok, "source_ref": "src/memai/db.py", "session": "s"},
         "verified": "checked repo"},
        {"kind": "distill", "payload": ok, "verified": "checked repo"},
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0, 1}
    assert "review_after" in res["errors"][0]["error"]
    assert "session, source_ref" in res["errors"][1]["error"]
    assert all("not accepted by distill" in e["error"] for e in res["errors"])


def test_distill_refuses_a_diagram_as_a_source(conn):
    """A diagram stays out of source_uids: distill archives every source it names."""
    diag, note = _mk_diagram(conn), _mk(conn, content="the warmup fills every hot key")
    res = db.stage_optimization(conn, "distill sources", [
        {"kind": "distill", "payload": {
            "source_uids": [note, diag], "new_type": "note",
            "new_content": "warmup loads the hot keys before traffic",
            "title": "How warmup loads the hot keys",
        }, "verified": "walked the routine in the code"},
    ])
    assert res["staged"] == 0 and res["run_id"] is None
    assert "is a diagram" in res["errors"][0]["error"]
    assert db.get_memory(conn, diag)["status"] == "active"


def test_distill_apply_and_revert(conn):
    a = _mk(conn, content="checkpoint one", type="checkpoint", domain="proj-1042")
    b = _mk(conn, content="checkpoint two", type="checkpoint", domain="proj-1042")
    run = db.stage_optimization(conn, "distill", [
        {"kind": "distill", "payload": {
            "source_uids": [a, b], "new_type": "note",
            "new_content": "root cause: retry loop lacked backoff",
            "title": "Why the retry loop stalled",
            "tags": "retry, timeout", "domain": "proj-1042",
        }, "verified": "checked repo, fix merged"},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    assert sug["target_uid"] is None

    db.apply_suggestion(conn, sug["id"])
    prev = json.loads(db.get_suggestion(conn, sug["id"])["prev_state"])
    new_uid = prev["new_uid"]
    new = db.get_memory(conn, new_uid)
    assert new["type"] == "note" and new["content"].startswith("root cause")
    assert new["tags"] == "retry, timeout" and new["domain"] == "proj-1042"
    for src in (a, b):
        row = db.get_memory(conn, src)
        assert row["status"] == "archived" and row["superseded_by"] == new_uid
    rels = db.get_relations(conn, new_uid)
    assert len(rels) == 2 and all(r["relation_type"] == "supersedes" for r in rels)

    db.revert_suggestion(conn, sug["id"])
    assert db.get_memory(conn, new_uid) is None          # created memory purged
    for src in (a, b):
        row = db.get_memory(conn, src)
        assert row["status"] == "active" and row["superseded_by"] is None
        assert db.get_relations(conn, src) == []
    assert db.get_suggestion(conn, sug["id"])["status"] == "pending"

    # re-apply after revert mints a fresh memory
    db.apply_suggestion(conn, sug["id"])
    prev2 = json.loads(db.get_suggestion(conn, sug["id"])["prev_state"])
    assert prev2["new_uid"] != new_uid
    assert db.get_memory(conn, prev2["new_uid"]) is not None


# ------------------------------------------------------------------ corpus / scan

def test_corpus_snippets_by_default_full_on_demand(conn):
    long_body = "x" * 1000
    uid = _mk(conn, content=long_body)
    corpus = db.optimization_corpus(conn)
    m = next(m for m in corpus["memories"] if m["uid"] == uid)
    assert m["content_len"] == 1000
    assert len(m["content"]) == db.CORPUS_SNIPPET_LEN and m["content"].endswith("…")

    full = db.optimization_corpus(conn, full=True)
    m = next(m for m in full["memories"] if m["uid"] == uid)
    assert m["content"] == long_body


def test_corpus_truncated_flag_and_stats_ignore_limit(conn):
    for i in range(5):
        _mk(conn, content=f"fact {i}", domain="d1" if i < 3 else "")
    corpus = db.optimization_corpus(conn, limit=2)
    assert corpus["count"] == 2 and corpus["truncated"] is True
    assert corpus["stats"]["total"] == 5          # whole corpus, not the window
    assert corpus["stats"]["by_type"] == {"note": 5}
    assert corpus["stats"]["by_domain"]["d1"] == 3
    assert corpus["stats"]["empty_domain"] == 2

    all_of_it = db.optimization_corpus(conn)
    assert all_of_it["truncated"] is False


def test_corpus_counts_what_a_retag_would_reach(conn):
    """A tags column holding nothing -- or holding nothing but the type every
    read already filters on -- leaves a memory findable only by its own words."""
    _mk(conn, content="the drain retries twice", tags="queue, drain")
    _mk(conn, content="the loader skips a blank part")
    _mk(conn, content="a temptation and its cure", type="anti_pattern",
        tags="anti_pattern")

    assert db.optimization_corpus(conn)["stats"]["untagged"] == 2


def test_corpus_counts_what_a_retitle_would_reach(conn):
    """A memory with no title of its own is listed everywhere by the opening
    line of its body."""
    _mk(conn, content="the drain retries twice", title="How the drain retries")
    _mk(conn, content="the loader skips a blank part")
    _mk(conn, content="a temptation and its cure", type="anti_pattern")

    assert db.optimization_corpus(conn)["stats"]["untitled"] == 2


def test_a_retitle_needs_a_name(conn):
    uid = _mk(conn, content="the drain retries twice")
    run = db.stage_optimization(conn, "r", [
        {"kind": "retitle", "target_uid": uid, "payload": {"title": "   "},
         "rationale": "why", "verified": "read the body"},
    ])
    assert run["staged"] == 0
    assert "payload.title required" in run["errors"][0]["error"]


def test_a_diagram_is_not_retitled_through_a_suggestion(conn):
    """Its title generates part of its body, so an applied rename would last
    until the next structural change."""
    diag = _mk_diagram(conn)
    run = db.stage_optimization(conn, "r", [
        {"kind": "retitle", "target_uid": diag, "payload": {"title": "Another name"},
         "rationale": "why", "verified": "read the graph"},
    ])
    assert run["staged"] == 0
    assert "generates its body" in run["errors"][0]["error"]
    assert db.get_memory(conn, diag)["title"] == "Cache warmup routine"


def test_corpus_extracts_anchors(conn):
    uid = _mk(conn, content=(
        "fix lives in src/core/parser.py, field F100_TOTAL of table X100; "
        "spec at https://example.com/spec and flag USE_NEW_PARSER"
    ))
    corpus = db.optimization_corpus(conn)
    m = next(m for m in corpus["memories"] if m["uid"] == uid)
    assert "https://example.com/spec" in m["anchors"]
    assert "src/core/parser.py" in m["anchors"]
    assert "X100" in m["anchors"]
    assert "F100_TOTAL" in m["anchors"]
    assert "USE_NEW_PARSER" in m["anchors"]

    plain = _mk(conn, content="just prose, nothing checkable")
    corpus = db.optimization_corpus(conn)
    m = next(m for m in corpus["memories"] if m["uid"] == plain)
    assert "anchors" not in m


def test_corpus_omits_empty_fields_and_trims_timestamps(conn):
    uid = _mk(conn, content="bare fact")          # no domain/session/tags
    corpus = db.optimization_corpus(conn)
    m = next(m for m in corpus["memories"] if m["uid"] == uid)
    for absent in ("domain", "also", "session", "tags", "superseded_by", "status",
                   "updated_at", "confidence"):     # confidence: unverified is the default
        assert absent not in m
    assert len(m["created_at"]) == 19             # sub-second precision dropped


def test_corpus_lists_the_cross_listings(conn):
    """A pass proposing a crosslist has to tell a new membership from one
    that already holds."""
    uid = _mk(conn, content="queue drain step", domain="acme/x100/p200",
              also="omni/x900")
    corpus = db.optimization_corpus(conn)
    m = next(m for m in corpus["memories"] if m["uid"] == uid)
    assert m["also"] == ["omni/x900"]
    assert "also_domains" not in m                # the indexing mirror stays put


def test_corpus_truncates_long_tags(conn):
    long_tags = ", ".join(f"tag{i}" for i in range(40))
    uid = _mk(conn, content="tagged", tags=long_tags)
    corpus = db.optimization_corpus(conn)
    m = next(m for m in corpus["memories"] if m["uid"] == uid)
    assert m["tags_len"] == len(long_tags)
    assert len(m["tags"]) == db.CORPUS_TAGS_LEN and m["tags"].endswith("…")


def test_corpus_char_budget_caps_a_page(conn, monkeypatch):
    monkeypatch.setattr(db, "CORPUS_CHAR_BUDGET", 1200)
    for i in range(20):
        _mk(conn, content=f"memory number {i} with some padding text")
    page1 = db.optimization_corpus(conn)
    assert 0 < page1["count"] < 20 and page1["truncated"] is True
    # paging by offset walks the whole corpus without overlap
    seen, offset = set(), 0
    while True:
        page = db.optimization_corpus(conn, offset=offset)
        uids = {m["uid"] for m in page["memories"]}
        assert not seen & uids
        seen |= uids
        if not page["truncated"]:
            break
        offset += page["count"]
    assert len(seen) == 20


def test_corpus_pages_with_offset(conn):
    for i in range(5):
        _mk(conn, content=f"fact {i}")
    first = db.optimization_corpus(conn, limit=3)
    rest = db.optimization_corpus(conn, limit=3, offset=3)
    assert first["truncated"] is True and rest["truncated"] is False
    assert first["count"] == 3 and rest["count"] == 2
    assert rest["offset"] == 3
    assert not {m["uid"] for m in first["memories"]} & {m["uid"] for m in rest["memories"]}


def test_corpus_since_filters_incrementally(conn):
    old = db.insert_memory(conn, type="note", content="old fact",
                           created_at="2026-01-05T10:00:00+00:00")
    new = db.insert_memory(conn, type="note", content="new fact",
                           created_at="2026-03-20T10:00:00+00:00")
    corpus = db.optimization_corpus(conn, since="2026-02-01")
    uids = {m["uid"] for m in corpus["memories"]}
    assert uids == {new}
    assert corpus["stats"]["total"] == 1        # stats describe the delta

    # an EDIT pulls an old memory back into the incremental window
    db.update_memory_content(conn, old, "old fact, revised", note="touch")
    corpus = db.optimization_corpus(conn, since="2026-02-01")
    assert {m["uid"] for m in corpus["memories"]} == {old, new}


def test_dedup_since_pairs_new_against_old_lexical(conn):
    # identical contents guarantee a hit
    old_a = db.insert_memory(conn, type="note", content="the retry loop lacks backoff",
                             created_at="2026-01-05T10:00:00+00:00")
    db.insert_memory(conn, type="note", content="the retry loop lacks backoff",
                     created_at="2026-01-06T10:00:00+00:00")
    new = db.insert_memory(conn, type="note", content="the retry loop lacks backoff",
                           created_at="2026-03-20T10:00:00+00:00")
    pairs = db.dedup_candidates(conn, threshold=0.9, since="2026-02-01")
    assert pairs, "new x old collision must surface"
    # every pair touches the delta -- the old x old duplicate (a x b) is
    # a full-pass concern, not this run's
    for a, b, _s, _m in pairs:
        assert new in (a["uid"], b["uid"])
    assert any(old_a in (a["uid"], b["uid"]) for a, b, _s, _m in pairs)


def test_corpus_domain_hints_cross_window_with_since(conn):
    db.insert_memory(conn, type="note", content="a", domain="PROJ-1042",
                     created_at="2026-01-05T10:00:00+00:00")     # old spelling
    db.insert_memory(conn, type="note", content="b", domain="proj_1042-fix",
                     created_at="2026-03-20T10:00:00+00:00")     # new variant
    db.insert_memory(conn, type="note", content="c", domain="OTHER-100",
                     created_at="2026-01-05T10:00:00+00:00")     # old-only cluster seed
    db.insert_memory(conn, type="note", content="d", domain="other_100",
                     created_at="2026-01-06T10:00:00+00:00")
    corpus = db.optimization_corpus(conn, since="2026-02-01")
    hints = corpus["domain_hints"]
    assert len(hints) == 1                        # old-only cluster stays out of the delta run
    assert {v["domain"] for v in hints[0]["variants"]} == {"PROJ-1042", "proj_1042-fix"}


def test_corpus_domain_hints_cluster_variants(conn):
    _mk(conn, content="a", domain="PROJ-1042")
    _mk(conn, content="b", domain="proj-1042")
    _mk(conn, content="c", domain="proj_1042-fix")
    _mk(conn, content="d", domain="unrelated")
    corpus = db.optimization_corpus(conn)
    hints = corpus["domain_hints"]
    assert len(hints) == 1
    h = hints[0]
    assert h["total"] == 3
    assert {v["domain"] for v in h["variants"]} == {"PROJ-1042", "proj-1042", "proj_1042-fix"}
    assert h["canonical"] in {"PROJ-1042", "proj-1042"}  # counts tie -> shortest wins among them


def test_dedup_ranks_checkpoints_below(conn):
    n1 = _mk(conn, content="alpha beta gamma delta epsilon")
    n2 = _mk(conn, content="alpha beta gamma delta epsilon")
    _mk(conn, content="zeta eta theta iota kappa", type="checkpoint", domain="d1")
    _mk(conn, content="zeta eta theta iota kappa", type="checkpoint", domain="d2")
    pairs = db.dedup_candidates(conn, threshold=0.9)
    assert all(m == "lexical" for _a, _b, _s, m in pairs)
    assert len(pairs) == 2
    # equal scores (identical contents), but the note pair outranks the
    # cross-domain checkpoint pair
    a, b, _s, _m = pairs[0]
    assert {a["uid"], b["uid"]} == {n1, n2}


def test_optimize_runs_and_status_tools(tmp_path, monkeypatch):
    from memai import server

    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with db.connect() as c:
        uid = db.insert_memory(c, type="note", content="a fact")
        staged = db.stage_optimization(c, "visibility", [
            {"kind": "reword", "target_uid": uid, "payload": {"new_content": "better"}},
        ])

    runs = server.optimize_runs()
    assert runs[0]["id"] == staged["run_id"]
    assert (runs[0]["total"], runs[0]["pending"]) == (1, 1)

    st = server.optimize_status(staged["run_id"])
    assert st["run"]["id"] == staged["run_id"] and st["run"]["note"] == "visibility"
    s = st["suggestions"][0]
    assert s["kind"] == "reword" and s["status"] == "pending"
    assert s["payload"]["new_content"] == "better"

    assert "error" in server.optimize_status(99999)


# ------------------------------------------------------------------ admin API

def _stage_via_db(uid, kind, payload):
    """Stage directly through the db layer against the client's store."""
    with db.connect() as conn:
        return db.stage_optimization(conn, "api run", [
            {"kind": kind, "target_uid": uid, "payload": payload, "rationale": "r", "verified": "v"},
        ])


def _new_memory(client, **kw):
    res = client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "api fact", **kw})
    assert res.status_code == 200, res.text
    return res.json()["uid"]


def test_api_runs_and_suggestions(client):
    uid = _new_memory(client, domain="d")
    staged = _stage_via_db(uid, "redomain", {"domain": "d2"})
    runs = client.get("/api/optimization/runs").json()["runs"]
    assert runs and runs[0]["id"] == staged["run_id"] and runs[0]["pending"] == 1

    got = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()
    assert len(got["suggestions"]) == 1
    s = got["suggestions"][0]
    assert s["kind"] == "redomain" and s["target"]["domain"] == "d"


def test_api_crosslist_card_shows_both_sets(client):
    """Before is the whole current set, After the whole proposed one -- the
    card would otherwise read as an addition to something it replaces."""
    uid = _new_memory(client, domain="acme/x100/p200", also="omni/x900")
    staged = _stage_via_db(uid, "crosslist", {"also": ["omni/x900", "omni/x800"]})
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert s["target"]["also"] == ["omni/x900"]
    # sorted: the payload is a set, and staging shows what will hold
    assert s["payload"]["also"] == ["omni/x800", "omni/x900"]

    client.post("/api/optimization/apply", json={"id": s["id"]})
    assert client.get(f"/api/memories/{uid}").json()["also"] == ["omni/x800", "omni/x900"]
    client.post("/api/optimization/revert", json={"id": s["id"]})
    assert client.get(f"/api/memories/{uid}").json()["also"] == ["omni/x900"]


def test_api_apply_takes_backup_and_mutates(client, tmp_path):
    uid = _new_memory(client)
    staged = _stage_via_db(uid, "reword", {"new_content": "rewritten"})
    sug_id = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]["id"]

    res = client.post("/api/optimization/apply", json={"id": sug_id}).json()
    assert res["ok"] and res["backup"]
    assert (tmp_path / "backups").exists()
    assert list((tmp_path / "backups").glob("*.db"))
    assert client.get(f"/api/memories/{uid}").json()["content"] == "rewritten"

    # revert restores
    client.post("/api/optimization/revert", json={"id": sug_id})
    assert client.get(f"/api/memories/{uid}").json()["content"] == "api fact"


def test_api_distill_card_sources_and_new_uid(client):
    a = _new_memory(client, domain="d")
    b = _new_memory(client, domain="d")
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "api distill", [
            {"kind": "distill", "payload": {
                "source_uids": [a, b], "new_type": "note", "new_content": "distilled",
                "title": "What the sources taught",
            }, "rationale": "r", "verified": "v"},
        ])
    got = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()
    s = got["suggestions"][0]
    assert [x["uid"] for x in s["sources"]] == [a, b]
    assert "new_uid" not in s

    client.post("/api/optimization/apply", json={"id": s["id"]})
    s = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert s["new_uid"]
    assert client.get(f"/api/memories/{s['new_uid']}").json()["content"] == "distilled"
    # sources now render as archived in the card
    assert all(x["status"] == "archived" for x in s["sources"])


def test_api_runs_include_kind_breakdown(client):
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "kinds run", [
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "x"}, "rationale": "r", "verified": "v"},
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "y"}, "rationale": "r", "verified": "v"},
            {"kind": "set_confidence", "target_uid": uid, "payload": {"confidence": "confirmed"}, "rationale": "r", "verified": "v"},
        ])
    sugs = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]
    retag_id = next(s["id"] for s in sugs if s["kind"] == "retag")
    client.post("/api/optimization/apply", json={"id": retag_id})

    run = client.get("/api/optimization/runs").json()["runs"][0]
    kinds = {k["kind"]: k for k in run["kinds"]}
    assert kinds["retag"] == {"kind": "retag", "total": 2, "pending": 1, "rejected": 0}
    assert kinds["set_confidence"] == {
        "kind": "set_confidence", "total": 1, "pending": 1, "rejected": 0}


def test_api_kind_breakdown_counts_what_was_turned_down(client):
    """applied per kind is total minus pending minus rejected.

    Without `rejected` the day's summary reads a turned-down suggestion as
    applied and claims work nobody accepted.
    """
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "three retags", [
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "a"}, "rationale": "r", "verified": "v"},
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "b"}, "rationale": "r", "verified": "v"},
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "c"}, "rationale": "r", "verified": "v"},
        ])
    ids = [s["id"] for s in client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]]
    client.post("/api/optimization/apply", json={"id": ids[0]})
    client.post("/api/optimization/reject", json={"id": ids[1]})

    run = next(r for r in client.get("/api/optimization/runs").json()["runs"]
               if r["id"] == staged["run_id"])
    k = {x["kind"]: x for x in run["kinds"]}["retag"]
    assert k == {"kind": "retag", "total": 3, "pending": 1, "rejected": 1}
    assert k["total"] - k["pending"] - k["rejected"] == 1     # the applied one


def test_api_apply_all_filters_by_kind(client):
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "kind filter", [
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "x"}, "rationale": "r", "verified": "v"},
            {"kind": "set_confidence", "target_uid": uid, "payload": {"confidence": "confirmed"}, "rationale": "r", "verified": "v"},
        ])
    res = client.post("/api/optimization/apply-all",
                      json={"run": staged["run_id"], "kind": "retag"}).json()
    assert res["applied"] == 1 and not res["failed"]

    sugs = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]
    by_kind = {s["kind"]: s["status"] for s in sugs}
    assert by_kind == {"retag": "applied", "set_confidence": "pending"}


def test_api_suggestions_filter_by_kind(client):
    """A group is its own page, so it fetches its own kind and no other."""
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "one kind", [
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "x"}, "rationale": "r", "verified": "v"},
            {"kind": "retitle", "target_uid": uid, "payload": {"title": "Cache warmup"}, "rationale": "r", "verified": "v"},
        ])
    got = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}&kind=retag").json()["suggestions"]
    assert [s["kind"] for s in got] == ["retag"]


def test_api_suggestions_across_several_runs(client):
    """A day holds every run staged that day, and the rail decides across them.

    `runs` is a list of RUN IDS and not a date: created_at is UTC and the
    calendar's day is the reader's local one, so a date filtered on the
    server would disagree with the grid that offered it.
    """
    uid = _new_memory(client)
    first = _stage_via_db(uid, "retag", {"tags": "one"})
    second = _stage_via_db(uid, "retag", {"tags": "two"})
    third = _stage_via_db(uid, "retag", {"tags": "three"})

    got = client.get("/api/optimization/suggestions"
                     f"?runs={first['run_id']},{third['run_id']}").json()
    assert [s["run_id"] for s in got["suggestions"]] == [first["run_id"], third["run_id"]]
    assert [r["id"] for r in got["runs"]] == [first["run_id"], third["run_id"]]
    # the single-run key stays for every caller that has always read it
    assert got["run"]["id"] == first["run_id"]
    assert second["run_id"] not in [s["run_id"] for s in got["suggestions"]]


def test_api_suggestions_across_runs_still_filters_by_status(client):
    uid = _new_memory(client)
    a = _stage_via_db(uid, "retag", {"tags": "one"})
    b = _stage_via_db(uid, "retag", {"tags": "two"})
    only = client.get(f"/api/optimization/suggestions?runs={a['run_id']}").json()
    client.post("/api/optimization/apply", json={"id": only["suggestions"][0]["id"]})

    pend = client.get("/api/optimization/suggestions"
                      f"?runs={a['run_id']},{b['run_id']}&status=pending").json()
    assert [s["run_id"] for s in pend["suggestions"]] == [b["run_id"]]


@pytest.mark.parametrize("qs", ["", "?runs=", "?runs=nope", "?run=abc"])
def test_api_suggestions_rejects_a_malformed_scope(client, qs):
    assert client.get(f"/api/optimization/suggestions{qs}").status_code >= 400


def test_api_suggestions_rejects_an_unknown_run_in_the_list(client):
    """One bad id fails the whole request rather than silently returning less."""
    uid = _new_memory(client)
    good = _stage_via_db(uid, "retag", {"tags": "x"})
    assert client.get(
        f"/api/optimization/suggestions?runs={good['run_id']},9999").status_code >= 400


def test_api_summary_counts_verified_and_the_ledger(client):
    """The run head reports what is written on the rows, not a projected score."""
    keep, drop = _new_memory(client, domain="acme/x100"), _new_memory(client, domain="zeta/x200")
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "head", [
            # checked, and it rewrites a body: 'api fact' (8) -> 'short' (5)
            {"kind": "reword", "target_uid": keep, "payload": {"new_content": "short"},
             "rationale": "r", "verified": "v"},
            # unchecked, so the head has something to warn about
            {"kind": "retag", "target_uid": keep, "payload": {"tags": "cache, warmup"},
             "rationale": "r"},
            {"kind": "set_confidence", "target_uid": keep,
             "payload": {"confidence": "confirmed"}, "rationale": "r", "verified": "v"},
            {"kind": "merge", "payload": {"keep_uid": keep, "drop_uid": drop},
             "rationale": "r", "verified": "v"},
        ])
    got = client.get(f"/api/optimization/summary?run={staged['run_id']}").json()

    assert got["pending"] == 4 and got["verified"] == 3
    lg = got["ledger"]
    assert lg["memories"] == 2          # both, reached through the merge payload
    assert lg["relations"] == 1         # the supersedes the merge creates
    assert lg["confirmed"] == 1
    assert lg["archived"] == 1          # the dropped half of the merge
    assert lg["chars"] == len("short") - len("api fact")
    assert lg["domains"] == 2
    assert lg["active"] == 2

    by_kind = {g["kind"]: g for g in got["groups"]}
    assert by_kind["retag"]["verified"] == 0
    assert by_kind["reword"]["verified"] == 1
    # the numbers each kind's sentence is built from
    assert by_kind["reword"]["facts"]["chars"] == len("short") - len("api fact")
    assert by_kind["retag"]["facts"]["terms"] == 2
    assert by_kind["set_confidence"]["facts"]["conf"] == "confirmed"


def test_api_summary_leaves_the_field_empty_when_the_batch_disagrees(client):
    """One destination can be named in the sentence; four cannot."""
    a, b = _new_memory(client), _new_memory(client)
    with db.connect() as conn:
        one = db.stage_optimization(conn, "same", [
            {"kind": "redomain", "target_uid": a, "payload": {"domain": "acme/x100"}, "rationale": "r"},
            {"kind": "redomain", "target_uid": b, "payload": {"domain": "acme/x100"}, "rationale": "r"},
        ])
        many = db.stage_optimization(conn, "split", [
            {"kind": "redomain", "target_uid": a, "payload": {"domain": "acme/x100"}, "rationale": "r"},
            {"kind": "redomain", "target_uid": b, "payload": {"domain": "zeta/x200"}, "rationale": "r"},
        ])
    facts = lambda run: {g["kind"]: g["facts"] for g in client.get(
        f"/api/optimization/summary?run={run}").json()["groups"]}["redomain"]
    assert facts(one["run_id"]) == {"paths": 1, "to": "acme/x100"}
    assert facts(many["run_id"]) == {"paths": 2, "to": ""}


def test_api_summary_ledger_ignores_what_is_already_decided(client):
    """The ledger is what is STILL on the table -- an applied row is history."""
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "half done", [
            {"kind": "set_confidence", "target_uid": uid,
             "payload": {"confidence": "confirmed"}, "rationale": "r", "verified": "v"},
        ])
    sug = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    client.post("/api/optimization/apply", json={"id": sug["id"]})

    got = client.get(f"/api/optimization/summary?run={staged['run_id']}").json()
    assert got["pending"] == 0 and got["verified"] == 0
    assert got["ledger"]["confirmed"] == 0 and got["ledger"]["memories"] == 0
    assert got["groups"][0]["applied"] == 1


def test_api_summary_rejects_an_unknown_run(client):
    assert client.get("/api/optimization/summary?run=9999").status_code >= 400


def test_api_apply_all_takes_an_explicit_selection(client):
    """The level-2 footer acts on what is ticked, not on the whole kind."""
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "pick some", [
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "a"}, "rationale": "r", "verified": "v"},
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "b"}, "rationale": "r", "verified": "v"},
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "c"}, "rationale": "r", "verified": "v"},
        ])
    ids = [s["id"] for s in client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]]

    res = client.post("/api/optimization/apply-all",
                      json={"run": staged["run_id"], "ids": ids[:2]}).json()
    assert res["applied"] == 2 and not res["failed"]
    left = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]
    assert [s["status"] for s in left] == ["applied", "applied", "pending"]


def test_api_apply_all_drops_ids_from_another_run(client):
    """An id is intersected with this run's pending rows before anything runs."""
    uid = _new_memory(client)
    mine = _stage_via_db(uid, "retag", {"tags": "mine"})
    theirs = _stage_via_db(uid, "retag", {"tags": "theirs"})
    alien = client.get(
        f"/api/optimization/suggestions?run={theirs['run_id']}").json()["suggestions"][0]["id"]

    res = client.post("/api/optimization/apply-all",
                      json={"run": mine["run_id"], "ids": [alien]}).json()
    assert res["applied"] == 0
    assert client.get(
        f"/api/optimization/suggestions?run={theirs['run_id']}"
    ).json()["suggestions"][0]["status"] == "pending"


def test_api_apply_all_rejects_a_malformed_ids(client):
    uid = _new_memory(client)
    staged = _stage_via_db(uid, "retag", {"tags": "x"})
    bad = client.post("/api/optimization/apply-all",
                      json={"run": staged["run_id"], "ids": ["1"]})
    assert bad.status_code >= 400
    assert client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}"
    ).json()["suggestions"][0]["status"] == "pending"


def test_api_apply_all_across_a_set_of_runs(client):
    """A calendar day holds every run staged that day; deciding it is one call."""
    uid = _new_memory(client)
    a = _stage_via_db(uid, "retag", {"tags": "one"})
    b = _stage_via_db(uid, "retag", {"tags": "two"})
    c = _stage_via_db(uid, "retag", {"tags": "three"})

    res = client.post("/api/optimization/apply-all",
                      json={"runs": [a["run_id"], b["run_id"]]}).json()
    assert res["applied"] == 2 and not res["failed"]

    status = lambda run: client.get(
        f"/api/optimization/suggestions?run={run}").json()["suggestions"][0]["status"]
    assert status(a["run_id"]) == "applied"
    assert status(b["run_id"]) == "applied"
    assert status(c["run_id"]) == "pending"     # outside the scope, untouched


def test_a_decision_over_several_runs_takes_one_backup(client, tmp_path):
    """Thirteen runs on one day would otherwise copy the database thirteen times."""
    uid = _new_memory(client)
    a = _stage_via_db(uid, "retag", {"tags": "one"})
    b = _stage_via_db(uid, "retag", {"tags": "two"})

    client.post("/api/optimization/apply-all", json={"runs": [a["run_id"], b["run_id"]]})

    runs = {r["id"]: r for r in client.get("/api/optimization/runs").json()["runs"]}
    first, second = runs[a["run_id"]], runs[b["run_id"]]
    assert first["backup_path"] and first["backup_path"] == second["backup_path"]
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_a_run_that_already_has_a_backup_keeps_its_own(client):
    """That copy is the state before ITS first apply, possibly days ago --
    reusing today's would hand back a restore point that never existed."""
    uid = _new_memory(client)
    old = _stage_via_db(uid, "retag", {"tags": "old"})
    client.post("/api/optimization/apply-all", json={"run": old["run_id"]})
    first = next(r for r in client.get("/api/optimization/runs").json()["runs"]
                 if r["id"] == old["run_id"])["backup_path"]
    assert first

    fresh = _stage_via_db(uid, "retag", {"tags": "fresh"})
    client.post("/api/optimization/apply-all",
                json={"runs": [old["run_id"], fresh["run_id"]]})

    runs = {r["id"]: r for r in client.get("/api/optimization/runs").json()["runs"]}
    assert runs[old["run_id"]]["backup_path"] == first
    assert runs[fresh["run_id"]]["backup_path"]
    assert runs[fresh["run_id"]]["backup_path"] != first


def test_api_reject_all_across_a_set_of_runs(client):
    uid = _new_memory(client)
    a = _stage_via_db(uid, "retag", {"tags": "one"})
    b = _stage_via_db(uid, "retag", {"tags": "two"})
    res = client.post("/api/optimization/reject-all",
                      json={"runs": [a["run_id"], b["run_id"]]}).json()
    assert res["rejected"] == 2


@pytest.mark.parametrize("body", [
    {}, {"runs": []}, {"runs": ["1"]}, {"run": "1"}, {"runs": [1], "ids": ["2"]},
])
def test_a_bulk_decision_rejects_a_malformed_scope(client, body):
    for path in ("apply-all", "reject-all"):
        assert client.post(f"/api/optimization/{path}", json=body).status_code >= 400


def test_api_reject_all_by_selection_and_by_kind(client):
    uid = _new_memory(client)
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "reject some", [
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "a"}, "rationale": "r"},
            {"kind": "retag", "target_uid": uid, "payload": {"tags": "b"}, "rationale": "r"},
            {"kind": "retitle", "target_uid": uid, "payload": {"title": "Queue drain"}, "rationale": "r"},
        ])
    sugs = client.get(f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]
    first = next(s["id"] for s in sugs if s["kind"] == "retag")

    res = client.post("/api/optimization/reject-all",
                      json={"run": staged["run_id"], "ids": [first]}).json()
    assert res["rejected"] == 1

    res = client.post("/api/optimization/reject-all",
                      json={"run": staged["run_id"], "kind": "retag"}).json()
    assert res["rejected"] == 1        # the other retag; the retitle is untouched

    by_id = {s["id"]: s["status"] for s in client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"]}
    assert sorted(by_id.values()) == ["pending", "rejected", "rejected"]


def test_api_reject_all_writes_nothing_to_the_memory(client):
    """Rejecting answers a suggestion; it must not touch what it was about."""
    uid = _new_memory(client, tags="original")
    staged = _stage_via_db(uid, "retag", {"tags": "rewritten"})
    before = client.get(f"/api/memories/{uid}").json()

    client.post("/api/optimization/reject-all", json={"run": staged["run_id"]})

    after = client.get(f"/api/memories/{uid}").json()
    assert after["tags"] == before["tags"] == "original"
    assert after["updated_at"] == before["updated_at"]


def test_a_rewrite_carries_the_whole_before_and_both_lengths(client):
    """Before is read beside a complete After, so a preview will not do.

    `snippet` is cut to a fixed width; against the full new body that reads
    as text the suggestion removes, which is exactly the decision being
    asked about.
    """
    body = ("a body long enough to be worth shortening. " * 40).strip()
    uid = _new_memory(client, content=body)
    staged = _stage_via_db(uid, "reword", {"new_content": "shorter"})
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert s["content_before"] == body
    assert len(s["target"]["snippet"]) < len(body)      # the preview really is cut
    assert s["chars_before"] == len(body)
    assert s["chars_after"] == len("shorter")

    # a kind that rewrites no body carries none of the three
    other = _stage_via_db(uid, "retag", {"tags": "x"})
    s2 = client.get(
        f"/api/optimization/suggestions?run={other['run_id']}").json()["suggestions"][0]
    assert not {"content_before", "chars_before", "chars_after"} & set(s2)


def test_an_applied_suggestion_still_reports_what_it_replaced(client):
    """Before is the state the apply left behind, for every kind.

    Applying writes the proposal into the memory, so reading the memory
    afterwards puts the same value in both panes and the pair says nothing
    changed. prev_state is the Before once a suggestion is decided.
    """
    cases = {
        "retag": ({"tags": "queue, drain"}, "tags", "cache, warmup"),
        "retitle": ({"title": "the new name"}, "title", "the old name"),
        "redomain": ({"domain": "acme/x200"}, "domain", "acme/x100"),
        "set_confidence": ({"confidence": "confirmed"}, "confidence", "unverified"),
        "review": ({"review_after": "2027-01-01"}, "review_after", ""),
        "archive": ({}, "status", "active"),
    }
    for kind, (payload, field, before) in cases.items():
        uid = _new_memory(client, title="the old name", tags="cache, warmup",
                          domain="acme/x100", confidence="unverified")
        staged = _stage_via_db(uid, kind, payload)
        sug = client.get(
            f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
        assert client.post("/api/optimization/apply",
                           json={"id": sug["id"]}).status_code == 200
        after = client.get(
            f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
        assert after["status"] == "applied"
        assert after["target"][field] == before, kind


def test_a_peer_card_carries_the_name_the_memory_goes_by(client):
    """Every preview names a memory by its title and keeps the body behind it.

    The card had only `snippet`, so the relation rail, the diagram's links
    and these panes previewed a named memory by its opening line.
    """
    keep = _new_memory(client, content="the body of the one that stays")
    drop = _new_memory(client, content="the body of the one that goes")
    # merge names its pair in the payload and carries no target_uid
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "peer names", [
            {"kind": "merge", "payload": {"keep_uid": keep, "drop_uid": drop},
             "rationale": "r", "verified": "v"},
        ])
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]

    for role in ("keep_uid", "drop_uid"):
        card = s["peers"][role]
        assert card["title"] == "fixture title"
        assert card["snippet"]                       # the body is still there
        assert card["title"] != card["snippet"]


@pytest.mark.parametrize("body,shown", [
    ("The trigger fires **once per shard** and stops.",
     "The trigger fires once per shard and stops."),
    ("=== What to check first ===\n\n- `is_identity` on the table",
     "What to check first is_identity on the table"),
    ("See [[1790bbfd327c6d6c]] for the mechanism.",
     "See 1790bbfd327c6d6c for the mechanism."),
    ("Reproduce:\n\n```sql\nSELECT 1;\n```\n\nDone.", "Reproduce: SELECT 1; Done."),
    # an opener nothing closed is markup too
    ("a **run left open", "a run left open"),
    # and a lone asterisk is not markup at all
    ("SELECT * FROM t", "SELECT * FROM t"),
])
def test_a_preview_shows_prose_not_the_markup_around_it(body, shown):
    """A preview identifies a memory; it is not read as a document.

    Flattened BEFORE the cut, so a 160-character snippet cannot sever a
    `**` and leave the stray half on the screen.
    """
    assert admin._plain(body) == shown


def test_a_peer_snippet_is_flattened_before_it_is_cut(client):
    uid = _new_memory(client, content="**bold** " + ("filler word " * 40))
    staged = _stage_via_db(uid, "retag", {"tags": "x"})
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    snippet = s["target"]["snippet"]
    assert snippet.startswith("bold filler word")
    assert "*" not in snippet
    assert snippet.endswith("…")          # it really was long enough to cut


def test_a_peer_with_no_title_still_carries_the_field(client):
    """An empty string, not a missing key: the fallback is the UI's to make.

    /api/memories requires a title, but db.insert_memory does not and the
    `untitled` defect Health counts is exactly this row -- so a preview
    cannot assume a name is there.
    """
    with db.connect() as conn:
        uid = db.insert_memory(conn, type="note", title="",
                               content="a memory nobody named")
    staged = _stage_via_db(uid, "retag", {"tags": "x"})
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert s["target"]["title"] == ""
    assert s["target"]["snippet"].startswith("a memory nobody named")


def test_the_relation_rail_gets_the_peer_name_too(client):
    """Same card, reached through the memory endpoint rather than a run."""
    a = _new_memory(client, content="first")
    b = _new_memory(client, content="second")
    client.post("/api/relations",
                json={"from_uid": a, "to_uid": b, "relation_type": "relates_to"})
    rels = client.get(f"/api/memories/{a}").json()["relations"]
    assert rels and rels[0]["peer"]["title"] == "fixture title"


def test_a_suggestion_resolves_the_wikilinks_its_prose_carries(client):
    """The panes draw a body with the record's renderer, which needs targets.

    Without the map every `[[uid]]` is drawn as a dead reference, so the
    three places a card shows prose -- the rationale, the body it replaces
    and the body it proposes -- are resolved together.
    """
    peer = _new_memory(client, domain="acme/x100")
    other = _new_memory(client)
    uid = _new_memory(client, content=f"the body cites [[{peer}]] already")
    with db.connect() as conn:
        staged = db.stage_optimization(conn, "links", [
            {"kind": "reword", "target_uid": uid,
             "payload": {"new_content": f"shorter, still citing [[{other}]]"},
             "rationale": f"the reasoning behind it is in [[{peer}]]"},
        ])
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]

    links = s["body_links"]
    assert set(links) == {peer, other}
    assert links[peer]["domain"] == "acme/x100"
    assert links[other]["type"] == "note"
    assert not links[peer].get("missing")


def test_a_wikilink_pointing_nowhere_is_reported_as_missing(client):
    """The renderer draws a dead reference as dead rather than as a link."""
    uid = _new_memory(client)
    staged = _stage_via_db(uid, "reword", {"new_content": "cites [[deadbeefdeadbeef]]"})
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert s["body_links"]["deadbeefdeadbeef"] == {
        "uid": "deadbeefdeadbeef", "missing": True}


def test_prose_with_no_references_carries_no_map(client):
    """An empty map on every card would be payload for nothing."""
    uid = _new_memory(client)
    staged = _stage_via_db(uid, "reword", {"new_content": "no references at all"})
    s = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert "body_links" not in s


def test_an_applied_rewrite_shows_what_it_replaced(client):
    """Once applied, the memory holds the new body -- so Before is prev_state.

    Reading the memory instead puts the same string in both panes, and the
    pair says the rewrite changed nothing.
    """
    body = ("the paragraph that gets shortened. " * 20).strip()
    uid = _new_memory(client, content=body)
    staged = _stage_via_db(uid, "reword", {"new_content": "shorter"})
    sug = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    client.post("/api/optimization/apply", json={"id": sug["id"]})

    after = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert after["status"] == "applied"
    assert after["content_before"] == body
    assert after["chars_before"] == len(body)
    assert after["chars_after"] == len("shorter")

    # and reverting puts the pair back on the live body
    client.post("/api/optimization/revert", json={"id": sug["id"]})
    back = client.get(
        f"/api/optimization/suggestions?run={staged['run_id']}").json()["suggestions"][0]
    assert back["status"] == "pending" and back["content_before"] == body


def test_api_apply_all_and_discard(client):
    uid = _new_memory(client)
    staged = _stage_via_db(uid, "set_confidence", {"confidence": "confirmed"})
    res = client.post("/api/optimization/apply-all", json={"run": staged["run_id"]}).json()
    assert res["applied"] == 1 and not res["failed"]
    assert client.get(f"/api/memories/{uid}").json()["confidence"] == "confirmed"

    client.request("DELETE", f"/api/optimization/runs/{staged['run_id']}")
    assert client.get("/api/optimization/runs").json()["runs"] == []


# ------------------------------------- the dashboard renders every staged kind

OPTIMIZATION_JS = (Path(__file__).resolve().parents[1]
                   / "src" / "memai" / "webui" / "views" / "optimization.js")

# The kinds optCard routes away from the before/after pair, because they are
# about a pair of memories rather than a field: link and merge print two peer
# cards, distill prints its sources.
RELATIONAL = {"link", "merge", "distill"}


def _kind_set(name: str) -> set[str]:
    """One of optimization.js's kind sets, read from the file."""
    body = OPTIMIZATION_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", body, re.S)
    assert match, f"{name} is not where this test expects it"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _diff_kinds() -> set[str]:
    """The kinds optimization.js gives a before/after pair, read from the file."""
    return _kind_set("DIFF_KINDS")


def test_every_staged_kind_reaches_a_renderer():
    """A kind the staging accepts and the dashboard cannot draw is staged blind.

    optCard falls back to printing the raw payload, so the run lands, the
    Apply button works, and the reviewer decides against a JSON dump.
    """
    assert set(db.SUGGESTION_KINDS) == _diff_kinds() | RELATIONAL


def test_every_diff_kind_is_claimed_by_exactly_one_pane():
    """A kind's change has a SHAPE, and the four panes divide them up.

    Content is prose and gets two scrolling wells, a flag gets the two
    marks the rest of the UI draws it with, a set gets both collections as
    chips, and a single value gets one line each. A kind in DIFF_KINDS that
    no set claims falls through to the raw payload dump.
    """
    groups = {name: _kind_set(name) for name in
              ("CONTENT_KINDS", "FLAG_KINDS", "SET_KINDS", "LINE_KINDS")}
    union: set[str] = set()
    for name, kinds in groups.items():
        assert not (union & kinds), f"{name} claims a kind another pane already draws"
        union |= kinds
    assert union == _diff_kinds()


def _catalogs():
    i18n = OPTIMIZATION_JS.parents[1] / "public" / "i18n"
    return {loc: json.loads((i18n / f"{loc}.json").read_text(encoding="utf-8"))["strings"]
            for loc in ("en", "pt-BR")}


def test_every_kind_has_a_sentence_in_every_catalog():
    """`op.what.${kind}` is assembled at runtime, so a gap reaches the screen.

    t() falls back to the key itself, and the group row would then read
    "op.what.merge" where the sentence belongs. groupWhat's own fallback
    catches only a kind with no entry at all -- it cannot catch one locale
    missing what the other has.
    """
    for loc, strings in _catalogs().items():
        for kind in db.SUGGESTION_KINDS:
            assert f"op.what.{kind}" in strings, f"{loc} has no sentence for {kind}"
        assert "op.what.other" in strings


def test_every_kind_has_a_name_in_every_catalog():
    """`kind.${kind}` is what the reader sees instead of the identifier.

    kindLabel falls back to the raw string, so a gap does not break the
    screen -- it leaves `set_confidence` on it in one locale and "Definir
    confianca" in the other, which is the inconsistency the mask exists to
    remove.
    """
    for loc, strings in _catalogs().items():
        for kind in db.SUGGESTION_KINDS:
            assert f"kind.{kind}" in strings, f"{loc} has no name for {kind}"
        assert "kind.raw" in strings, f"{loc} cannot show the stored spelling"


def test_the_mixed_variants_exist_for_the_kinds_that_ask_for_one():
    """A batch with several destinations picks `<kind>Mixed`; it has to be there."""
    body = OPTIMIZATION_JS.read_text(encoding="utf-8")
    match = re.search(r"const WHAT_MIXED = \{(.*?)\}", body, re.S)
    assert match, "WHAT_MIXED is not where this test expects it"
    kinds = re.findall(r"(\w+):", match.group(1))
    assert kinds, "expected at least one kind with a mixed-batch sentence"
    for loc, strings in _catalogs().items():
        for kind in kinds:
            assert f"op.what.{kind}Mixed" in strings, f"{loc} has no mixed sentence for {kind}"


def test_the_placeholders_a_sentence_uses_are_ones_the_server_sends():
    """A {term} the payload never carries prints as literal braces."""
    known = {"n", "chars", "terms", "paths", "to", "conf", "rel", "sources", "kind"}
    for loc, strings in _catalogs().items():
        for key, value in strings.items():
            if not key.startswith("op.what."):
                continue
            unknown = set(re.findall(r"\{(\w+)\}", value)) - known
            assert not unknown, f"{loc}:{key} uses {unknown}"


def test_the_distilled_memory_is_born_with_its_name(conn):
    """distill is the only kind that authors a memory, so it carries the title.

    Nothing names the new memory afterwards: staged without one it would be
    listed by the opening line of its body for good.
    """
    a = _mk(conn, content="checkpoint one", type="checkpoint", domain="proj-1042")
    run = db.stage_optimization(conn, "distill", [
        {"kind": "distill", "verified": "checked repo", "payload": {
            "source_uids": [a], "new_type": "note",
            "new_content": "the retry loop lacked backoff",
            "title": "Why the retry loop stalled",
        }},
    ])
    sug = db.get_optimization_suggestions(conn, run["run_id"])[0]
    db.apply_suggestion(conn, sug["id"])
    new_uid = json.loads(db.get_suggestion(conn, sug["id"])["prev_state"])["new_uid"]
    assert db.get_memory(conn, new_uid)["title"] == "Why the retry loop stalled"
