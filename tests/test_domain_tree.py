"""Nested domains: a domain is a path, and a scope covers what is under it.

Covers the path helpers, the subtree filters every read shares, the
subtree-aware move, the nesting proposals the curation pass reads, and the
admin/MCP surfaces built on them. Hermetic FTS-only path like the rest of
the suite (the autouse fixture in conftest keeps the real embedder out);
every example domain is synthetic.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memai import admin, db, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _tree(conn):
    """One product, two modules, one routine under each -- the shape the
    flat domain could not express."""
    return {
        "root": db.insert_memory(conn, type="note", content="product wide", domain="acme"),
        "mod": db.insert_memory(conn, type="note", content="module wide", domain="acme/x100"),
        "proc": db.insert_memory(conn, type="note", content="routine detail", domain="acme/x100/p200"),
        "other": db.insert_memory(conn, type="note", content="other module", domain="acme/x200"),
        "outside": db.insert_memory(conn, type="note", content="unrelated", domain="zeta"),
    }


# ------------------------------------------------------------- path helpers

@pytest.mark.parametrize("given,expected", [
    ("acme/x100/p200", ["acme", "x100", "p200"]),
    (" acme / x100 ", ["acme", "x100"]),
    ("acme//x100", ["acme", "x100"]),
    ("/acme/", ["acme"]),
    ("", []),
])
def test_split_domain(given, expected):
    assert db.split_domain(given) == expected


def test_normalize_domain_is_idempotent():
    once = db.normalize_domain(" acme / /x100// ")
    assert once == "acme/x100"
    assert db.normalize_domain(once) == once


def test_parent_ancestors_depth():
    assert db.domain_parent("acme/x100/p200") == "acme/x100"
    assert db.domain_parent("acme") == ""
    assert db.domain_ancestors("acme/x100/p200") == ["acme", "acme/x100"]
    assert db.domain_ancestors("acme/x100", include_self=True) == ["acme", "acme/x100"]
    assert db.domain_depth("acme/x100/p200") == 3
    assert db.domain_depth("") == 0


def test_in_domain_compares_segments_not_strings():
    assert db.in_domain("acme/x100/p200", "acme/x100")
    assert db.in_domain("acme", "acme")
    assert db.in_domain("acme", "")           # no scope holds everything
    # the prefix of a NAME is not a level: x1000 is not inside x100
    assert not db.in_domain("acme/x1000", "acme/x100")
    assert not db.in_domain("acme", "acme/x100")


def test_domain_clause_escapes_like_wildcards(conn):
    """`_` is a LIKE wildcard and appears in real names."""
    keep = db.insert_memory(conn, type="note", content="under it", domain="acme/f_100/deep")
    decoy = db.insert_memory(conn, type="note", content="not under it", domain="acme/fx100/deep")
    uids = {r["uid"] for r in db.list_by_domain(conn, "acme/f_100")}
    assert uids == {keep}
    assert decoy not in uids


# ------------------------------------------------------------- write paths

def test_writes_normalize_the_path(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain=" acme / /x100 ")
    assert db.get_memory(conn, uid)["domain"] == "acme/x100"


def test_write_normalization_survives_the_casing_policy(conn):
    db.set_domain_case(conn, "upper")
    uid = db.insert_memory(conn, type="note", content="x", domain="acme / x100")
    assert db.get_memory(conn, uid)["domain"] == "ACME/X100"


def test_mcp_writer_reports_the_path_adjustment(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    res = server.note(content="x", domain="acme / x100")
    assert res["domain_adjusted"] == {"from": "acme / x100", "to": "acme/x100",
                                      "policy": "preserve"}


# ----------------------------------------------------------- subtree reads

def test_list_by_domain_covers_the_subtree(conn):
    ids = _tree(conn)
    got = {r["uid"] for r in db.list_by_domain(conn, "acme/x100")}
    assert got == {ids["mod"], ids["proc"]}
    assert {r["uid"] for r in db.list_by_domain(conn, "acme/x100", subtree=False)} == {ids["mod"]}
    assert ids["outside"] not in {r["uid"] for r in db.list_by_domain(conn, "acme")}


def test_list_recent_and_search_share_the_scope(conn):
    ids = _tree(conn)
    assert {r["uid"] for r in db.list_recent(conn, domain="acme")} == {
        ids["root"], ids["mod"], ids["proc"], ids["other"]}
    assert {r["uid"] for r in db.list_recent(conn, domain="acme", subtree=False)} == {ids["root"]}
    hits = db.search_memories(conn, "detail", domain="acme")
    assert [r["uid"] for r in hits] == [ids["proc"]]
    assert db.search_memories(conn, "detail", domain="acme", subtree=False) == []


def test_fts_indexes_the_ancestors_of_the_path(conn):
    """A path's levels are words in the index, so a module code still finds
    the routines filed under it without naming the whole path."""
    ids = _tree(conn)
    assert ids["proc"] in {r["uid"] for r in db.search_memories(conn, "x100")}


def test_diagram_overview_scopes_to_the_subtree(conn):
    db.insert_diagram(conn, title="module flow", nodes=[
        {"key": "s", "shape": "start", "label": "start"},
        {"key": "e", "shape": "end", "label": "end"}],
        edges=[{"from": "s", "to": "e"}], domain="acme/x100/p200")
    assert len(db.diagram_overview(conn, domain="acme")) == 1
    assert db.diagram_overview(conn, domain="acme", subtree=False) == []


def test_dedup_candidates_scope_to_the_subtree(conn):
    db.insert_memory(conn, type="note", content="warmup rule for counters", domain="acme/x100")
    db.insert_memory(conn, type="note", content="warmup rule for counters!", domain="acme/x100/p200")
    assert db.dedup_candidates(conn, domain="acme", threshold=0.6)
    assert not db.dedup_candidates(conn, domain="acme", threshold=0.6, subtree=False)


# ------------------------------------------------------------- the tree API

def test_list_domains_rolls_up_and_fills_implicit_levels(conn):
    db.insert_memory(conn, type="note", content="deep only", domain="acme/x100/p200")
    db.insert_memory(conn, type="note", content="second", domain="acme/x100/p300")
    by_path = {d["domain"]: d for d in db.list_domains(conn)}

    assert set(by_path) == {"acme", "acme/x100", "acme/x100/p200", "acme/x100/p300"}
    # nobody wrote to 'acme' or 'acme/x100', but both are levels of the tree
    assert by_path["acme"]["implicit"] is True
    assert by_path["acme"]["count"] == 0
    assert by_path["acme"]["subtree"] == 2
    assert by_path["acme"]["children"] == 1
    assert by_path["acme/x100"]["children"] == 2
    assert by_path["acme/x100/p200"]["implicit"] is False
    assert by_path["acme/x100/p200"]["parent"] == "acme/x100"
    assert by_path["acme/x100/p200"]["depth"] == 3


def test_list_domains_orders_parents_with_their_liveliest_child(conn):
    """An implicit parent has no activity of its own, so it sorts by the
    branch under it -- otherwise it would sink below every leaf."""
    old = db.insert_memory(conn, type="note", content="old", domain="zeta")
    db.insert_memory(conn, type="note", content="new", domain="acme/x100")
    # the two writes can land in the same clock tick on a coarse timer; the
    # ordering under test is about activity, so state it explicitly
    conn.execute("UPDATE memories SET created_at = '2020-01-01T00:00:00+00:00' WHERE uid = ?",
                 (old,))
    assert [d["domain"] for d in db.list_domains(conn)] == ["acme", "acme/x100", "zeta"]


def test_mcp_list_domains_returns_the_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    server.note(content="x", domain="acme/x100/p200")
    by_path = {d["domain"]: d for d in server.list_domains()}
    assert by_path["acme"]["subtree"] == 1 and by_path["acme"]["implicit"] is True


def test_mcp_reads_take_the_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    server.note(content="module wide", domain="acme/x100")
    server.note(content="routine detail", domain="acme/x100/p200")
    assert len(server.list_by_domain(domain="acme")["results"]) == 2
    assert len(server.list_by_domain(domain="acme", subtree=False)["results"]) == 0
    assert len(server.list_recent(domain="acme/x100", subtree=False)["results"]) == 1
    assert len(server.pulse(domain="acme")["recent_notes"]) == 2


# ------------------------------------------------- resolving a bare segment

def test_resolves_a_deep_segment_to_its_branch(conn):
    ids = _tree(conn)
    assert db.resolve_domain_scopes(conn, "x100") == ["acme/x100"]
    assert {r["uid"] for r in db.list_by_domain(conn, "x100")} == {ids["mod"], ids["proc"]}
    assert {r["uid"] for r in db.list_by_domain(conn, "p200")} == {ids["proc"]}


def test_resolves_a_segment_in_the_middle_of_a_path(conn):
    """The scope is the level asked for, so what hangs off it comes too."""
    uid = db.insert_memory(conn, type="note", content="deep", domain="acme/x100/p200/warmup")
    assert db.resolve_domain_scopes(conn, "p200") == ["acme/x100/p200"]
    assert [r["uid"] for r in db.list_by_domain(conn, "p200")] == [uid]


def test_resolution_takes_a_run_of_segments(conn):
    db.insert_memory(conn, type="note", content="a", domain="acme/x100/p200")
    db.insert_memory(conn, type="note", content="b", domain="zeta/x100/p300")
    assert db.resolve_domain_scopes(conn, "x100/p200") == ["acme/x100/p200"]


def test_the_literal_reading_wins(conn):
    """A name that IS a domain means that domain -- resolution never mixes
    a same-named level from another branch into it."""
    flat = db.insert_memory(conn, type="note", content="flat", domain="p200")
    db.insert_memory(conn, type="note", content="nested", domain="acme/x100/p200")
    assert db.resolve_domain_scopes(conn, "p200") == ["p200"]
    assert [r["uid"] for r in db.list_by_domain(conn, "p200")] == [flat]


def test_an_ambiguous_segment_broadens_to_every_branch(conn):
    a = db.insert_memory(conn, type="note", content="one", domain="acme/x100/p200")
    b = db.insert_memory(conn, type="note", content="two", domain="acme/x200/p200")
    assert db.resolve_domain_scopes(conn, "p200") == ["acme/x100/p200", "acme/x200/p200"]
    assert {r["uid"] for r in db.list_by_domain(conn, "p200")} == {a, b}


def test_a_repeated_segment_resolves_to_the_outer_level(conn):
    """Which is also what keeps the scope list free of nesting: a deeper
    match would mean the query already occurred at the shallower depth."""
    uid = db.insert_memory(conn, type="note", content="x", domain="zeta/p200/legacy/p200")
    assert db.resolve_domain_scopes(conn, "p200") == ["zeta/p200"]
    assert [r["uid"] for r in db.list_by_domain(conn, "p200")] == [uid]
    scopes = db.resolve_domain_scopes(conn, "p200")
    assert not [(a, b) for a in scopes for b in scopes if a != b and db.in_domain(a, b)]


def test_an_unknown_domain_still_means_no_rows(conn):
    _tree(conn)
    assert db.resolve_domain_scopes(conn, "nothing/here") == ["nothing/here"]
    assert db.list_by_domain(conn, "nothing/here") == []


def test_search_and_dedup_take_the_resolved_scope(conn):
    ids = _tree(conn)
    assert [r["uid"] for r in db.search_memories(conn, "detail", domain="x100")] == [ids["proc"]]
    db.insert_memory(conn, type="note", content="routine detail!", domain="acme/x100/p200")
    assert db.dedup_candidates(conn, domain="p200", threshold=0.6)


def test_pulse_reports_where_it_read(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    server.note(content="routine detail", domain="acme/x100/p200")
    resolved = server.pulse(domain="p200")
    assert resolved["scope"]["paths"] == ["acme/x100/p200"]
    assert len(resolved["recent_notes"]) == 1
    assert server.pulse(domain="acme/x100")["scope"]["paths"] == ["acme/x100"]
    assert server.pulse()["scope"]["paths"] == []       # whole store


def test_admin_echoes_the_resolved_scope(client):
    _create(client, domain="acme/x100/p200")
    body = client.get("/api/memories?domain=p200").json()
    assert body["total"] == 1 and body["domain_scope"] == ["acme/x100/p200"]
    assert client.get("/api/memories?q=x&domain=p200").json()["domain_scope"] == ["acme/x100/p200"]
    assert client.get("/api/graph?domain=p200").json()["domain_scope"] == ["acme/x100/p200"]
    # literal filters stay quiet
    assert "domain_scope" not in client.get("/api/memories?domain=acme").json()
    assert "domain_scope" not in client.get("/api/memories").json()


def test_a_move_targets_only_what_was_named(client):
    """Resolution is for READING. A rename that resolved a name would move
    a domain the operator never pointed at."""
    _create(client, domain="acme/x100/p200")
    assert client.post("/api/domains/rename",
                       json={"from": "p200", "to": "zeta/p200"}).status_code == 400


# ------------------------------------------------------- the scope's census

def test_census_counts_the_scope_and_splits_one_level_down(conn):
    db.insert_memory(conn, type="note", content="parent own", domain="acme")
    for i in range(4):
        db.insert_memory(conn, type="note", content=f"child {i}", domain="acme/x100")
    db.insert_memory(conn, type="note", content="deeper", domain="acme/x100/deep")
    db.insert_memory(conn, type="handoff", content="other child", domain="acme/x200")
    db.insert_memory(conn, type="note", content="outside", domain="zeta")

    census = db.domain_census(conn, "acme")
    assert census["paths"] == ["acme"]
    assert census["total"] == 7
    assert census["by_type"] == {"note": 6, "handoff": 1}
    # children only: 'acme/x100/deep' is counted INTO 'acme/x100', not listed
    assert census["children"] == [
        {"domain": "acme/x100", "own": 4, "subtree": 5},
        {"domain": "acme/x200", "own": 1, "subtree": 1},
    ]


def test_census_of_the_whole_store_lists_the_roots(conn):
    db.insert_memory(conn, type="note", content="a", domain="acme/x100")
    db.insert_memory(conn, type="note", content="b", domain="zeta")
    census = db.domain_census(conn)
    assert census["paths"] == []
    # equal subtree counts, so alphabetical -- and the implicit root is listed
    # like any other, holding nothing of its own
    assert census["children"] == [
        {"domain": "acme", "own": 0, "subtree": 1},
        {"domain": "zeta", "own": 1, "subtree": 1},
    ]


def test_pulse_says_what_the_caps_left_behind(monkeypatch, tmp_path):
    """The failure this exists for: a busy child fills the newest-five and
    the parent's own note never appears, with nothing saying so."""
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    server.note(content="parent convention", domain="acme")
    for i in range(12):
        server.note(content=f"child detail {i}", domain="acme/x100")
    server.handoff(content="one handoff", domain="acme/x100")

    p = server.pulse(domain="acme")
    assert len(p["recent_notes"]) == server.PULSE_NOTES
    assert p["scope"]["by_type"]["note"] == 13
    assert p["scope"]["not_shown"] == {"recent_notes": 13 - server.PULSE_NOTES}
    assert p["scope"]["subdomains"] == [{"domain": "acme/x100", "own": 13, "subtree": 13}]


def test_pulse_stays_quiet_when_it_showed_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    server.note(content="the only note", domain="acme")
    p = server.pulse(domain="acme")
    assert p["scope"]["not_shown"] == {}
    assert p["scope"]["subdomains"] == []


def test_pulse_counts_the_checkpoints_it_did_not_return(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    for i in range(3):
        server.checkpoint(intent=f"i{i}", established="e", pursuing="p",
                          open_questions="q", domain="acme/x100")
    p = server.pulse(domain="acme")
    assert p["latest_checkpoint"]["domain"] == "acme/x100"
    assert p["scope"]["not_shown"]["latest_checkpoint"] == 2


# ------------------------------------------------------------------- moving

def test_move_domain_takes_the_subtree_along(conn):
    ids = _tree(conn)
    result = db.move_domain(conn, "acme/x100", "acme/legacy")
    assert result["moved"] == 2 and result["domains"] == 2 and result["merged"] is False
    assert db.get_memory(conn, ids["mod"])["domain"] == "acme/legacy"
    assert db.get_memory(conn, ids["proc"])["domain"] == "acme/legacy/p200"
    assert db.get_memory(conn, ids["root"])["domain"] == "acme"  # untouched


def test_move_domain_can_nest_a_flat_bucket(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="x100")
    db.move_domain(conn, "x100", "acme/x100")
    assert db.get_memory(conn, uid)["domain"] == "acme/x100"


def test_move_domain_reports_a_merge(conn):
    db.insert_memory(conn, type="note", content="a", domain="acme/x100")
    db.insert_memory(conn, type="note", content="b", domain="acme/x200")
    assert db.move_domain(conn, "acme/x100", "acme/x200")["merged"] is True


def test_move_domain_audits_every_moved_row(conn):
    ids = _tree(conn)
    db.move_domain(conn, "acme/x100", "acme/legacy")
    notes = [e["note"] for e in db.get_edit_history(conn, ids["proc"])]
    assert "meta: domain 'acme/x100/p200' → 'acme/legacy/p200'" in notes


def test_move_domain_refuses_its_own_subtree(conn):
    _tree(conn)
    with pytest.raises(ValueError, match="own subtree"):
        db.move_domain(conn, "acme", "acme/x100")


def test_move_domain_needs_rows_and_a_different_target(conn):
    _tree(conn)
    with pytest.raises(ValueError, match="no memories"):
        db.move_domain(conn, "nothing/here", "acme")
    with pytest.raises(ValueError, match="same"):
        db.move_domain(conn, "acme", "acme")


def test_move_domain_normalizes_both_ends(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="x100")
    db.move_domain(conn, " x100 ", " acme / x100 ")
    assert db.get_memory(conn, uid)["domain"] == "acme/x100"


# ------------------------------------------------- archiving a whole level

def test_archiving_a_domain_takes_the_subtree_and_nothing_else(conn):
    ids = _tree(conn)
    result = db.set_domain_status(conn, "acme/x100", "archived")
    assert set(result["uids"]) == {ids["mod"], ids["proc"]}
    assert result["domains"] == 2
    assert db.get_memory(conn, ids["proc"])["status"] == "archived"
    assert db.get_memory(conn, ids["root"])["status"] == "active"
    assert db.get_memory(conn, ids["other"])["status"] == "active"


def test_archiving_a_domain_reports_only_what_it_changed(conn):
    """What an Undo has to act on. Restoring the whole scope instead would
    revive a memory archived long before, for reasons of its own."""
    old = db.insert_memory(conn, type="note", content="stale", domain="acme/x100")
    db.set_status(conn, old, "archived")
    fresh = db.insert_memory(conn, type="note", content="live", domain="acme/x100")
    assert db.set_domain_status(conn, "acme/x100", "archived")["uids"] == [fresh]


def test_restoring_a_domain_is_the_same_call_the_other_way(conn):
    ids = _tree(conn)
    db.set_domain_status(conn, "acme/x100", "archived")
    assert len(db.set_domain_status(conn, "acme/x100", "active")["uids"]) == 2
    assert db.get_memory(conn, ids["proc"])["status"] == "active"


def test_archiving_a_domain_audits_the_reason(conn):
    ids = _tree(conn)
    db.set_domain_status(conn, "acme/x100", "archived", note="archived: superseded")
    assert "archived: superseded" in [e["note"] for e in db.get_edit_history(conn, ids["mod"])]


def test_archiving_a_domain_needs_a_path_and_a_known_status(conn):
    _tree(conn)
    with pytest.raises(ValueError, match="domain is required"):
        db.set_domain_status(conn, "  ", "archived")
    with pytest.raises(ValueError, match="status must be"):
        db.set_domain_status(conn, "acme", "deleted")


# --------------------------------------------------- deleting a whole level

def test_deleting_a_domain_purges_the_subtree(conn):
    ids = _tree(conn)
    result = db.purge_domain(conn, "acme/x100")
    assert result == {"purged": 2, "unlinked": 0, "domains": 2}
    assert db.get_memory(conn, ids["proc"]) is None
    assert db.get_memory(conn, ids["mod"]) is None
    assert db.get_memory(conn, ids["root"])["domain"] == "acme"
    assert db.get_memory(conn, ids["other"])["domain"] == "acme/x200"


def test_deleting_a_domain_takes_the_history_with_it(conn):
    uid = db.insert_memory(conn, type="note", content="cache warmup", domain="acme/x100")
    db.update_memory_content(conn, uid, "cache warmup, revised")
    db.purge_domain(conn, "acme/x100")
    assert db.get_edit_history(conn, uid) == []


def test_deleting_a_domain_with_nothing_in_it_raises(conn):
    _tree(conn)
    with pytest.raises(ValueError, match="no memories"):
        db.purge_domain(conn, "nothing/here")
    with pytest.raises(ValueError, match="domain is required"):
        db.purge_domain(conn, " ")


# --------------------------------------------------------- nesting proposals

def test_nesting_hints_lift_codes_and_roots_out_of_a_flat_name():
    # 'acme' heads three domains, so it reads as a root; x100/p200 are codes
    counts = {
        "acme-x100-p200-cache-warmup": 4,
        "acme-x100-counters": 2,
        "acme-x200": 1,
    }
    by_domain = {h["domain"]: h["proposed"] for h in db._nesting_hints(counts)}
    assert by_domain["acme-x100-p200-cache-warmup"] == "acme/x100/p200/cache-warmup"
    assert by_domain["acme-x100-counters"] == "acme/x100/counters"
    assert by_domain["acme-x200"] == "acme/x200"


def test_nesting_hints_leave_prose_and_paths_alone():
    counts = {"cache-warmup": 3, "acme/x100": 2, "x100": 1}
    assert db._nesting_hints(counts) == []


def test_corpus_carries_the_nesting_proposals(conn):
    db.insert_memory(conn, type="note", content="a", domain="acme-x100-p200-counters")
    for i in range(2):
        db.insert_memory(conn, type="note", content=f"b{i}", domain=f"acme-x{i}00-detail")
    proposals = {n["domain"]: n["proposed"] for n in
                 db.optimization_corpus(conn)["domain_nesting"]}
    assert proposals["acme-x100-p200-counters"] == "acme/x100/p200/counters"


def test_redomain_suggestion_is_staged_as_a_normalized_path(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="x100")
    staged = db.stage_optimization(conn, "nest it", [
        {"kind": "redomain", "target_uid": uid, "payload": {"domain": " acme / x100 "},
         "rationale": "the module owns this routine"}])
    sug = db.get_optimization_suggestions(conn, staged["run_id"])[0]
    assert '"domain": "acme/x100"' in sug["payload"]
    db.apply_suggestion(conn, sug["id"])
    assert db.get_memory(conn, uid)["domain"] == "acme/x100"


# --------------------------------------------------------- admin endpoints

def _create(client, **body) -> str:
    return client.post("/api/memories", json={"type": "note", "content": "x", **body}).json()["uid"]


def test_domains_endpoint_returns_the_tree(client):
    _create(client, domain="acme/x100/p200")
    _create(client, domain="acme/x100")
    by_path = {d["domain"]: d for d in client.get("/api/domains").json()["domains"]}
    assert by_path["acme"]["implicit"] is True
    assert by_path["acme"]["subtree_active"] == 2
    assert by_path["acme/x100"]["active"] == 1
    assert by_path["acme/x100"]["children"] == 1
    assert by_path["acme/x100"]["depth"] == 2


def test_domain_collisions_are_compared_between_siblings(client):
    _create(client, domain="acme/Cache")
    _create(client, domain="acme/cache")
    _create(client, domain="zeta/cache")       # same word, another parent
    by_path = {d["domain"]: d for d in client.get("/api/domains").json()["domains"]}
    assert by_path["acme/Cache"]["collides_with"] == ["acme/cache"]
    assert "collides_with" not in by_path["zeta/cache"]


def test_overview_counts_only_the_named_levels(client):
    _create(client, domain="acme/x100/p200")
    assert client.get("/api/overview").json()["totals"]["domains"] == 1


def test_rename_endpoint_nests_and_carries_descendants(client):
    _create(client, domain="x100")
    _create(client, domain="x100/p200")
    res = client.post("/api/domains/rename", json={"from": "x100", "to": "acme/x100"}).json()
    assert res["affected"] == 2
    paths = {d["domain"] for d in client.get("/api/domains").json()["domains"]}
    assert paths == {"acme", "acme/x100", "acme/x100/p200"}


def test_rename_endpoint_refuses_a_cycle(client):
    _create(client, domain="acme/x100")
    res = client.post("/api/domains/rename", json={"from": "acme", "to": "acme/x100"})
    assert res.status_code == 400


def test_memories_endpoint_scopes_to_the_subtree(client):
    _create(client, domain="acme/x100")
    _create(client, domain="acme/x100/p200")
    assert client.get("/api/memories?domain=acme").json()["total"] == 2
    assert client.get("/api/memories?domain=acme&subtree=0").json()["total"] == 0
    # the search path takes the same scope
    assert client.get("/api/memories?q=x&domain=acme").json()["total"] == 2
    assert client.get("/api/memories?q=x&domain=acme&subtree=0").json()["total"] == 0


def test_normalize_repairs_a_malformed_path(client):
    """A path written straight into the table (no writer to normalize it)
    still has a way back to a shape a prefix query can match."""
    uid = _create(client, domain="acme/x100")
    with db.connect() as conn:
        conn.execute("UPDATE memories SET domain = 'acme//x100 ' WHERE uid = ?", (uid,))
    plan = client.post("/api/domains/normalize", json={"dry_run": True}).json()["plan"]
    assert plan == [{"from": "acme//x100 ", "to": "acme/x100", "count": 1, "action": "rename"}]
    client.post("/api/domains/normalize", json={"dry_run": False})
    assert client.get(f"/api/memories/{uid}").json()["domain"] == "acme/x100"


def test_status_endpoint_archives_a_level_and_echoes_the_uids(client):
    kept = _create(client, domain="zeta")
    uid = _create(client, domain="acme/x100")
    res = client.post("/api/domains/status",
                      json={"domain": "acme", "status": "archived", "reason": "superseded"}).json()
    assert res["affected"] == 1 and res["uids"] == [uid]
    assert client.get(f"/api/memories/{uid}").json()["status"] == "archived"
    assert client.get(f"/api/memories/{kept}").json()["status"] == "active"
    # the echoed uids are what /api/bulk takes back, which is what makes Undo work
    client.post("/api/bulk", json={"action": "restore", "uids": res["uids"]})
    assert client.get(f"/api/memories/{uid}").json()["status"] == "active"


def test_status_endpoint_refuses_an_unknown_status(client):
    _create(client, domain="acme")
    assert client.post("/api/domains/status",
                       json={"domain": "acme", "status": "deleted"}).status_code == 400


def test_delete_endpoint_demands_the_exact_phrase(client):
    uid = _create(client, domain="acme/x100")
    assert client.post("/api/domains/delete",
                       json={"domain": "acme/x100"}).status_code == 400
    assert client.post("/api/domains/delete",
                       json={"domain": "acme/x100", "confirm": "DELETE acme"}).status_code == 400
    assert client.get(f"/api/memories/{uid}").status_code == 200

    res = client.post("/api/domains/delete",
                      json={"domain": "acme/x100", "confirm": "DELETE acme/x100"})
    assert res.json()["purged"] == 1
    assert client.get(f"/api/memories/{uid}").status_code == 400
    # nothing names the levels any more, so the tree loses them both
    assert client.get("/api/domains").json()["domains"] == []
