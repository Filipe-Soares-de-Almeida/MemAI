"""Cross-listed domains: a memory is filed at one path and can belong to more.

The path a memory is filed at is its one parent chain. A cross-listing says
it is ALSO part of a subject that cuts across that tree -- several routines
each being a step of one end-to-end process, none of them the parent of the
others. Covers the write policy, the scope arm every read shares, what the
tree and the census report, what a re-home does to a membership, and the
admin/MCP surfaces on top. Hermetic FTS-only path like the rest of the suite;
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


def _crossing(conn):
    """Two routines under different modules, both steps of one flow.

    This is the shape a single path cannot express: 'omni/x900' is not an
    ancestor of either routine, and neither routine is an ancestor of it.
    """
    return {
        "queue": db.insert_memory(conn, type="note", content="queue drain step",
                                  domain="acme/x100/p200", also="omni/x900"),
        "token": db.insert_memory(conn, type="note", content="token refresh step",
                                  domain="zeta/x200/p300", also="omni/x900/p910"),
        "plain": db.insert_memory(conn, type="note", content="unrelated",
                                  domain="acme/x100/p400"),
    }


# --------------------------------------------------------------- write policy

def test_parse_domains_splits_on_commas_semicolons_and_newlines():
    assert db.parse_domains("acme/x100, zeta/x200;omni\nacme/x100") == [
        "acme/x100", "zeta/x200", "omni"]
    assert db.parse_domains(["  acme // x100 ", ""]) == ["acme/x100"]
    assert db.parse_domains("") == []
    assert db.parse_domains(None) == []


def test_a_cross_listing_the_filed_path_already_covers_is_dropped(conn):
    """The prefix arm of a domain filter matches 'acme' for a memory filed at
    'acme/x100/p200' already; recording it would count the memory twice."""
    uid = db.insert_memory(conn, type="note", content="x", domain="acme/x100/p200",
                           also="acme, acme/x100, acme/x100/p200, omni/x900")
    assert db.get_domain_links(conn, uid) == ["omni/x900"]


def test_a_cross_listing_below_the_filed_path_is_kept(conn):
    """A narrower scope is a real thing to say, unlike a broader one."""
    uid = db.insert_memory(conn, type="note", content="x", domain="acme",
                           also="acme/x100/p200")
    assert db.get_domain_links(conn, uid) == ["acme/x100/p200"]


def test_cross_listings_follow_the_store_casing_policy(conn):
    db.set_domain_case(conn, "lower")
    uid = db.insert_memory(conn, type="note", content="x", domain="ACME",
                           also="OMNI/X900")
    assert db.get_memory(conn, uid)["domain"] == "acme"
    assert db.get_domain_links(conn, uid) == ["omni/x900"]


def test_add_and_remove_one_membership(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="acme/x100")
    assert db.add_domain_link(conn, uid, "omni/x900") == ["omni/x900"]
    assert db.add_domain_link(conn, uid, "omni/x900/p910") == [
        "omni/x900", "omni/x900/p910"]
    # exact-path removal: dropping the parent leaves the child membership
    assert db.remove_domain_link(conn, uid, "omni/x900") == ["omni/x900/p910"]
    with pytest.raises(ValueError):
        db.add_domain_link(conn, uid, "  ")


def test_setting_the_links_is_audited(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="acme/x100")
    db.add_domain_link(conn, uid, "omni/x900")
    db.remove_domain_link(conn, uid, "omni/x900")
    notes = [e["note"] for e in db.get_edit_history(conn, uid)]
    assert notes == ["meta: also '' → 'omni/x900'",
                     "meta: also 'omni/x900' → ''"]


def test_both_readers_agree_on_the_order(conn):
    """The rows come back ORDER BY domain and the mirror comes back in stored
    order, so the stored order has to BE sorted or one memory's memberships
    read two different ways depending on which reader asked."""
    uid = db.insert_memory(conn, type="note", content="x", domain="acme",
                           also="omni/x900, omni/x100, zeta")
    assert db.get_domain_links(conn, uid) == ["omni/x100", "omni/x900", "zeta"]
    assert db.parse_domains(db.get_memory(conn, uid)["also_domains"]) == \
        db.get_domain_links(conn, uid)
    assert db.domain_links_for(conn, [uid])[uid] == db.get_domain_links(conn, uid)


def test_an_unchanged_set_writes_nothing(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="acme/x100",
                           also="omni/x900")
    db.set_domain_links(conn, uid, "omni/x900")
    assert db.get_edit_history(conn, uid) == []


# ------------------------------------------------------------------- the scope

def test_a_scope_holds_what_is_cross_listed_into_it(conn):
    _crossing(conn)
    assert {r["content"] for r in db.list_by_domain(conn, "omni/x900")} == {
        "queue drain step", "token refresh step"}
    # subtree, like any path: the flow's own parent covers the whole thing
    assert len(db.list_by_domain(conn, "omni")) == 2
    # and the narrower membership is only in the narrower scope
    assert [r["content"] for r in db.list_by_domain(conn, "omni/x900/p910")] == [
        "token refresh step"]


def test_a_cross_listing_does_not_move_the_memory(conn):
    _crossing(conn)
    assert {r["content"] for r in db.list_by_domain(conn, "acme")} == {
        "queue drain step", "unrelated"}
    assert db.list_by_domain(conn, "acme", subtree=False) == []


def test_subtree_false_still_narrows_to_the_exact_path(conn):
    _crossing(conn)
    assert [r["content"] for r in
            db.list_by_domain(conn, "omni/x900", subtree=False)] == ["queue drain step"]


def test_search_and_recent_share_the_scope_arm(conn):
    _crossing(conn)
    hits = db.search_memories(conn, "step", domain="omni/x900")
    assert {r["content"] for r in hits} == {"queue drain step", "token refresh step"}
    assert len(db.list_recent(conn, domain="omni/x900")) == 2


def test_the_cross_listing_is_indexed_as_text(conn):
    """FTS and the embedder read one field (memories.also_domains), which is
    why a query naming the subject finds a memory filed elsewhere."""
    _crossing(conn)
    assert {r["content"] for r in db.search_memories(conn, "x900")} == {
        "queue drain step", "token refresh step"}


def test_a_path_that_exists_only_as_a_cross_listing_resolves(conn):
    _crossing(conn)
    assert db.resolve_domain_scopes(conn, "omni/x900") == ["omni/x900"]
    # and by its deep end alone, like any other path
    assert db.resolve_domain_scopes(conn, "p910") == ["omni/x900/p910"]


# -------------------------------------------------------------- tree + census

def test_the_tree_counts_filed_and_cross_listed_apart(conn):
    _crossing(conn)
    nodes = {n["domain"]: n for n in db.list_domains(conn)}
    flow = nodes["omni/x900"]
    assert (flow["count"], flow["subtree"]) == (0, 0)
    assert (flow["also"], flow["subtree_also"]) == (1, 2)
    # nobody named 'omni' itself, so it stays implicit -- but it rolls up
    assert nodes["omni"]["implicit"] is True
    assert nodes["omni"]["subtree_also"] == 2
    # being cross-listed at a path names it as surely as being filed there
    assert flow["implicit"] is False
    # the memory still counts once where it lives
    assert nodes["acme/x100/p200"]["count"] == 1


def test_the_tree_dates_a_purely_cross_cutting_subject(conn):
    """Or it would sort last in a recency-ordered tree it organizes."""
    _crossing(conn)
    nodes = {n["domain"]: n for n in db.list_domains(conn)}
    assert nodes["omni/x900"]["latest_at"]
    assert nodes["omni"]["subtree_latest_at"]


def test_the_census_places_a_cross_listed_memory_by_its_membership(conn):
    _crossing(conn)
    census = db.domain_census(conn, "omni/x900")
    assert census["total"] == 2
    # both are in scope only because of a cross-listing
    assert census["also"] == 2
    # placed by the path the membership names, not by where they live
    assert census["children"] == [
        {"domain": "omni/x900/p910", "own": 0, "subtree": 0,
         "also": 1, "subtree_also": 1}]


def test_the_census_omits_the_cross_listing_counts_when_there_are_none(conn):
    db.insert_memory(conn, type="note", content="x", domain="acme/x100")
    census = db.domain_census(conn, "acme")
    assert "also" not in census
    assert census["children"] == [{"domain": "acme/x100", "own": 1, "subtree": 1}]


def test_pulse_warms_up_a_flow_that_owns_nothing(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "test.db")
    _crossing(conn)
    conn.commit()
    p = server.pulse(domain="omni/x900")
    assert {n["content"].split("\n")[0] for n in p["recent_notes"]} == {
        "queue drain step", "token refresh step"}
    assert p["scope"]["also"] == 2


# ------------------------------------------------------------------- re-homing

def test_a_re_home_takes_the_memberships_with_it(conn):
    links = _crossing(conn)
    moved = db.move_domain(conn, "omni/x900", "omni/x800")
    # nothing is FILED there, so nothing moved -- the memberships did
    assert moved["moved"] == 0
    assert moved["also_moved"] == 2
    assert db.get_domain_links(conn, links["queue"]) == ["omni/x800"]
    assert db.get_domain_links(conn, links["token"]) == ["omni/x800/p910"]
    assert len(db.list_by_domain(conn, "omni/x800")) == 2
    assert db.list_by_domain(conn, "omni/x900") == []


def test_a_re_home_does_not_move_a_memory_that_only_belongs(conn):
    links = _crossing(conn)
    db.move_domain(conn, "omni/x900", "omni/x800")
    assert db.get_memory(conn, links["queue"])["domain"] == "acme/x100/p200"


def test_moving_a_memory_under_a_subject_it_belonged_to_drops_the_membership(conn):
    """The filed path now covers it, and keeping the row would double count."""
    uid = db.insert_memory(conn, type="note", content="x", domain="zeta/x200",
                           also="omni/x900")
    db.move_domain(conn, "zeta/x200", "omni/x900/x200")
    assert db.get_domain_links(conn, uid) == []
    assert len(db.list_by_domain(conn, "omni/x900")) == 1


def test_re_homing_a_subject_onto_a_memorys_own_path_drops_the_membership(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="acme/x100",
                           also="omni/x900")
    db.move_domain(conn, "omni/x900", "acme/x100")
    assert db.get_domain_links(conn, uid) == []


def test_a_domain_change_re_runs_the_link_policy(conn):
    uid = db.insert_memory(conn, type="note", content="x", domain="zeta/x200",
                           also="omni/x900")
    db._update_meta_field(conn, uid, "domain", "omni/x900/x200")
    assert db.get_domain_links(conn, uid) == []


def test_an_exact_path_move_leaves_a_descendant_membership_alone(conn):
    """What the normalize pass needs: it moves one exact stored string at a
    time, and a descendant is its own entry in that plan."""
    uid = db.insert_memory(conn, type="note", content="x", domain="acme",
                           also="omni/x900, omni/x900/p910")
    db.move_domain(conn, "omni/x900", "omni/x800", subtree=False)
    assert db.get_domain_links(conn, uid) == ["omni/x800", "omni/x900/p910"]


def test_a_move_with_nothing_at_all_at_the_source_still_raises(conn):
    db.insert_memory(conn, type="note", content="x", domain="acme")
    with pytest.raises(ValueError, match="no memories in domain"):
        db.move_domain(conn, "nowhere", "acme")


# ------------------------------------------------------------------- surfaces

def test_mcp_writers_take_also_and_echo_what_was_stored(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "test.db")
    r = server.note(content="queue drain step", domain="acme/x100/p200",
                    also="omni/x900, acme")
    # 'acme' is already covered by the filed path, so it is not stored
    assert r["also"] == ["omni/x900"]
    assert server.list_by_domain(domain="omni/x900")["results"][0]["uid"] == r["uid"]
    # a writer without `also` does not grow the field
    assert "also" not in server.note(content="plain", domain="acme")


def test_mcp_also_domain_and_unfile_domain(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "test.db")
    uid = server.note(content="queue drain step", domain="acme/x100/p200")["uid"]
    assert server.also_domain(uid, "omni/x900")["also"] == ["omni/x900"]
    assert server.get_memory(uid)["also"] == ["omni/x900"]
    assert server.unfile_domain(uid, "omni/x900")["also"] == []
    assert "also" not in server.get_memory(uid)
    assert server.also_domain("nope", "omni")["ok"] is False


def test_mcp_never_hands_back_the_indexing_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "test.db")
    uid = server.note(content="x", domain="acme", also="omni/x900")["uid"]
    assert "also_domains" not in server.get_memory(uid)
    assert "also_domains" not in server.search("x")["results"][0]


def _create(client, **kw):
    body = {"type": "note", "content": "queue drain step", **kw}
    res = client.post("/api/memories", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_admin_creates_and_edits_the_memberships(client):
    made = _create(client, domain="acme/x100/p200", also="omni/x900, acme")
    assert made["also"] == ["omni/x900"]
    uid = made["uid"]

    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["also"] == ["omni/x900"]
    assert "also_domains" not in detail

    res = client.post(f"/api/memories/{uid}/meta",
                      json={"also": "omni/x900/p910"}).json()
    assert res["changed"] == ["also"]
    assert res["also"] == ["omni/x900/p910"]

    # unchanged means unchanged, same as every other meta field
    again = client.post(f"/api/memories/{uid}/meta",
                        json={"also": "omni/x900/p910"}).json()
    assert again["changed"] == []


def test_admin_lists_and_scopes_by_a_membership(client):
    _create(client, domain="acme/x100/p200", also="omni/x900")
    _create(client, content="unrelated", domain="acme/x100/p400")
    items = client.get("/api/memories?domain=omni/x900&status=").json()["items"]
    assert [m["content"] for m in items] == ["queue drain step"]
    assert items[0]["also"] == ["omni/x900"]


def test_admin_tree_reports_the_cross_cutting_subject(client):
    _create(client, domain="acme/x100/p200", also="omni/x900")
    doms = {d["domain"]: d for d in client.get("/api/domains").json()["domains"]}
    assert (doms["omni/x900"]["active"], doms["omni/x900"]["also"]) == (0, 1)
    assert doms["omni/x900"]["implicit"] is False
    assert doms["omni"]["subtree_also"] == 1
    assert doms["acme/x100/p200"]["active"] == 1


def test_admin_rename_reports_the_memberships_it_moved(client):
    _create(client, domain="acme/x100/p200", also="omni/x900")
    res = client.post("/api/domains/rename",
                      json={"from": "omni/x900", "to": "omni/x800"}).json()
    assert (res["affected"], res["also_affected"]) == (0, 1)
    doms = {d["domain"] for d in client.get("/api/domains").json()["domains"]}
    assert "omni/x800" in doms and "omni/x900" not in doms


def test_admin_normalize_repairs_a_membership_only_path(client):
    """It is a stored domain string like any other; skipping it would leave
    the one spelling no prefix query can match."""
    _create(client, domain="acme", also="OMNI/X900, OMNI/X900/P910")
    client.post("/api/config", json={"domain_case": "lower"})
    plan = client.post("/api/domains/normalize", json={"dry_run": True}).json()
    assert [e["from"] for e in plan["plan"]] == ["OMNI/X900", "OMNI/X900/P910"]
    applied = client.post("/api/domains/normalize", json={"dry_run": False}).json()
    assert applied["also_affected"] == 2
    doms = {d["domain"] for d in client.get("/api/domains").json()["domains"]}
    assert {"omni/x900", "omni/x900/p910"} <= doms


def test_admin_diagram_cards_carry_their_memberships(client):
    made = client.post("/api/diagrams", json={
        "title": "Queue drain", "domain": "acme/x100/p200", "also": "omni/x900",
        "nodes": [{"key": "s", "shape": "start", "label": "Begin"},
                  {"key": "e", "shape": "end", "label": "Done"}],
        "edges": [{"from": "s", "to": "e"}]})
    assert made.json()["also"] == ["omni/x900"]
    cards = client.get("/api/diagrams?domain=omni/x900").json()["items"]
    assert [c["also"] for c in cards] == [["omni/x900"]]


# ------------------------------------------------------------------ migration

def test_a_store_written_before_the_column_migrates(tmp_path):
    """The FTS index has no ALTER, so a new indexed field means dropping it
    and rebuilding from the content table -- triggers included."""
    path = tmp_path / "old.db"
    with db.connect(path) as conn:
        uid = db.insert_memory(conn, type="note", content="queue drain step",
                              domain="acme/x100")
    # roll the store back to the pre-cross-listing shape
    import sqlite3
    raw = sqlite3.connect(str(path))
    for trigger in ("memories_ai", "memories_ad", "memories_au"):
        raw.execute(f"DROP TRIGGER {trigger}")
    raw.executescript("""
        DROP TABLE memories_fts;
        DROP TABLE memory_domains;
        ALTER TABLE memories DROP COLUMN also_domains;
        CREATE VIRTUAL TABLE memories_fts USING fts5(
            content, tags, domain, content='memories', content_rowid='rowid_pk',
            tokenize='porter unicode61');
        INSERT INTO memories_fts(memories_fts) VALUES ('rebuild');
    """)
    raw.commit()
    raw.close()

    with db.connect(path) as conn:
        # the index came back with the new column, and kept what it held
        assert [r["name"] for r in conn.execute("PRAGMA table_info(memories_fts)")] \
            == list(db._FTS_COLUMNS)
        assert [r["uid"] for r in db.search_memories(conn, "queue")] == [uid]
        # and the new field works on a row that predates it
        db.add_domain_link(conn, uid, "omni/x900")
        assert [r["uid"] for r in db.list_by_domain(conn, "omni/x900")] == [uid]
        assert [r["uid"] for r in db.search_memories(conn, "x900")] == [uid]


# ------------------------------------------------- archiving/deleting a scope

def test_archiving_a_scope_leaves_a_memory_that_only_belongs(conn):
    """The same reading as a re-home: a cross-listing says a memory belongs
    to a subject, not that it lives there, so archiving the subject cannot
    reach into the branch it actually lives in."""
    ids = _crossing(conn)
    result = db.set_domain_status(conn, "omni/x900", "archived")
    assert result["uids"] == []
    assert db.get_memory(conn, ids["queue"])["status"] == "active"


def test_purging_a_memory_takes_its_memberships(conn):
    """The FK on memory_domains refuses the DELETE while a membership names
    the row, so a purge has to clear the memberships first."""
    ids = _crossing(conn)
    assert db.purge_memory(conn, ids["queue"]) is True
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM memory_domains WHERE memory_uid = ?",
        (ids["queue"],)).fetchone()["n"] == 0


def test_deleting_a_domain_drops_the_cross_listings_pointing_into_it(conn):
    """The path stops existing, so a membership naming it would dangle. The
    memory holding it is filed in another branch and stays."""
    ids = _crossing(conn)
    result = db.purge_domain(conn, "omni/x900")
    assert result["purged"] == 0 and result["unlinked"] == 2
    assert db.get_memory(conn, ids["queue"])["domain"] == "acme/x100/p200"
    assert db.get_domain_links(conn, ids["queue"]) == []
    assert db.get_memory(conn, ids["queue"])["also_domains"] == ""
    # ...and the index the mirror feeds no longer answers for the dead subject
    assert db.search_memories(conn, "x900") == []


def test_deleting_a_domain_purges_what_is_filed_there_and_unlinks_the_rest(conn):
    ids = _crossing(conn)
    filed = db.insert_memory(conn, type="note", content="flow overview",
                             domain="omni/x900")
    result = db.purge_domain(conn, "omni/x900")
    assert result["purged"] == 1 and result["unlinked"] == 2
    assert db.get_memory(conn, filed) is None
    assert db.get_memory(conn, ids["token"]) is not None
