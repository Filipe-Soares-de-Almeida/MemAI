"""Tests for the memory-optimization workflow.

Covers the db-layer staging/apply/revert dispatchers per suggestion kind
plus the admin API (backup-before-apply, revert, reject). Same hermetic
setup as the rest of the suite: conftest keeps the real embedder out, so
everything runs FTS-only; the admin client points MEMAI_HOME at a tmp dir.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

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
    return db.insert_memory(conn, type=kw.pop("type", "note"), content=content, **kw)


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


def test_stage_no_valid_suggestions_creates_no_run(conn):
    res = db.stage_optimization(conn, "", [{"kind": "bogus", "payload": {}}])
    assert res["run_id"] is None and res["staged"] == 0
    assert db.list_optimization_runs(conn) == []


@pytest.mark.parametrize("kind,payload,check", [
    ("reword", {"new_content": "reworded"}, lambda r: r["content"] == "reworded"),
    ("compact", {"new_content": "short"}, lambda r: r["content"] == "short"),
    ("retag", {"tags": "x, y"}, lambda r: r["tags"] == "x, y"),
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
    assert after["domain"] == before["domain"]
    assert after["confidence"] == before["confidence"]
    assert after["status"] == before["status"]
    assert db.get_suggestion(conn, sug["id"])["status"] == "pending"


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
        {"kind": "merge", "payload": {"keep_uid": keep, "drop_uid": drop}},
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


def test_link_merge_reject_mismatched_target_uid(conn):
    a, b = _mk(conn, content="one"), _mk(conn, content="two")
    res = db.stage_optimization(conn, "targets", [
        {"kind": "link", "target_uid": b,  # mismatch: derived is from_uid
         "payload": {"from_uid": a, "to_uid": b, "relation_type": "relates_to"}},
        {"kind": "merge", "target_uid": a,  # mismatch: derived is drop_uid
         "payload": {"keep_uid": a, "drop_uid": b}},
        {"kind": "link", "target_uid": a,  # matching is fine
         "payload": {"from_uid": a, "to_uid": b, "relation_type": "relates_to"}},
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0, 1}
    sug = db.get_optimization_suggestions(conn, res["run_id"])[0]
    assert sug["target_uid"] == a


def test_distill_validation(conn):
    a, b = _mk(conn, content="one"), _mk(conn, content="two")
    ok = {"source_uids": [a, b], "new_type": "note", "new_content": "the durable fact"}
    res = db.stage_optimization(conn, "distill guards", [
        {"kind": "distill", "payload": ok},                                     # no verified
        {"kind": "distill", "target_uid": a, "payload": ok, "verified": "v"},   # target_uid forbidden
        {"kind": "distill", "payload": {**ok, "source_uids": []}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "source_uids": [a, a]}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "source_uids": [a, "deadbeef"]}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "new_type": "checkpoint"}, "verified": "v"},
        {"kind": "distill", "payload": {**ok, "new_content": "  "}, "verified": "v"},
        {"kind": "distill", "payload": ok, "verified": "checked repo"},         # valid
    ])
    assert res["staged"] == 1
    assert {e["index"] for e in res["errors"]} == {0, 1, 2, 3, 4, 5, 6}


def test_distill_apply_and_revert(conn):
    a = _mk(conn, content="checkpoint one", type="checkpoint", domain="proj-1042")
    b = _mk(conn, content="checkpoint two", type="checkpoint", domain="proj-1042")
    run = db.stage_optimization(conn, "distill", [
        {"kind": "distill", "payload": {
            "source_uids": [a, b], "new_type": "note",
            "new_content": "root cause: retry loop lacked backoff",
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
    for absent in ("domain", "session", "tags", "superseded_by", "status",
                   "updated_at", "confidence"):     # confidence: unverified is the default
        assert absent not in m
    assert len(m["created_at"]) == 19             # sub-second precision dropped


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
    # lexical path (no embedder); identical contents guarantee a hit
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


def test_dedup_lexical_fallback_ranks_checkpoints_below(conn):
    # no fake_embedder here: the autouse fixture keeps vectors off -> lexical path
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
    res = client.post("/api/memories", json={"type": "note", "content": "api fact", **kw})
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
    assert kinds["retag"] == {"kind": "retag", "total": 2, "pending": 1}
    assert kinds["set_confidence"] == {"kind": "set_confidence", "total": 1, "pending": 1}


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


def test_api_apply_all_and_discard(client):
    uid = _new_memory(client)
    staged = _stage_via_db(uid, "set_confidence", {"confidence": "confirmed"})
    res = client.post("/api/optimization/apply-all", json={"run": staged["run_id"]}).json()
    assert res["applied"] == 1 and not res["failed"]
    assert client.get(f"/api/memories/{uid}").json()["confidence"] == "confirmed"

    client.request("DELETE", f"/api/optimization/runs/{staged['run_id']}")
    assert client.get("/api/optimization/runs").json()["runs"] == []
