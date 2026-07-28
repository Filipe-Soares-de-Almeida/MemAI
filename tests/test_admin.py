"""API tests for the admin dashboard server (memai/admin.py).

Same hermetic setup as the rest of the suite: the autouse fixture in
conftest.py keeps the real embedding model out, so everything runs on
the FTS-only degradation path. MEMAI_HOME is pointed at a tmp dir per
test, which is all the isolation the app needs -- every endpoint opens
its own db.connect() against default_db_path().
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memai import admin


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _create(client, **kw) -> str:
    body = {"type": "note", "content": "test fact", **kw}
    res = client.post("/api/memories", json=body)
    assert res.status_code == 200, res.text
    return res.json()["uid"]


def test_overview_empty(client):
    data = client.get("/api/overview").json()
    assert data["totals"]["memories"] == 0
    assert data["db"]["path"]


def test_memory_lifecycle(client):
    uid = _create(client, domain="proj-x", tags="alpha, beta")

    listed = client.get("/api/memories").json()
    assert listed["total"] == 1
    assert listed["items"][0]["uid"] == uid

    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["content"] == "test fact"
    assert detail["edit_history"] == []

    res = client.post(f"/api/memories/{uid}/content",
                      json={"content": "corrected fact", "note": "typo"})
    assert res.json()["ok"] is True
    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["content"] == "corrected fact"
    assert len(detail["edit_history"]) == 1
    assert detail["edit_history"][0]["note"] == "typo"

    res = client.post(f"/api/memories/{uid}/meta", json={"domain": "proj-y"})
    assert res.json()["changed"] == ["domain"]
    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["domain"] == "proj-y"
    assert any(e["note"].startswith("meta:") for e in detail["edit_history"])

    assert client.post(f"/api/memories/{uid}/confidence",
                       json={"confidence": "confirmed"}).json()["ok"]
    assert client.post(f"/api/memories/{uid}/confidence",
                       json={"confidence": "invalida"}).status_code == 400

    assert client.post(f"/api/memories/{uid}/status",
                       json={"status": "archived", "reason": "obsolete"}).json()["ok"]
    assert client.get("/api/memories?status=active").json()["total"] == 0
    assert client.get("/api/memories?status=archived").json()["total"] == 1
    assert client.post(f"/api/memories/{uid}/status",
                       json={"status": "active"}).json()["ok"]


def test_search_and_filters(client):
    _create(client, content="database tuning guide", domain="db", tags="database")
    _create(client, content="car maintenance schedule", domain="car")

    hits = client.get("/api/memories?q=database tuning").json()
    assert hits["searched"] is True
    assert hits["total"] >= 1
    assert any("tuning" in i["content"] for i in hits["items"])

    only_db = client.get("/api/memories?domain=db").json()
    assert only_db["total"] == 1


def test_relations_graph_and_lookup(client):
    a = _create(client, content="source memory")
    b = _create(client, content="target memory")

    res = client.post("/api/relations", json={
        "from_uid": a, "to_uid": b, "relation_type": "relates_to", "note": "pair"})
    rel_id = res.json()["relation_id"]

    dup = client.post("/api/relations", json={
        "from_uid": a, "to_uid": b, "relation_type": "relates_to"})
    assert dup.status_code == 400

    self_link = client.post("/api/relations", json={
        "from_uid": a, "to_uid": a, "relation_type": "relates_to"})
    assert self_link.status_code == 400

    detail = client.get(f"/api/memories/{a}").json()
    assert detail["relations"][0]["direction"] == "out"
    assert detail["relations"][0]["peer"]["uid"] == b

    g = client.get("/api/graph").json()
    assert len(g["nodes"]) == 2
    assert len(g["edges"]) == 1
    assert {n["degree"] for n in g["nodes"]} == {1}

    found = client.get(f"/api/lookup?q={b}").json()["items"]
    assert found[0]["uid"] == b

    assert client.request("DELETE", f"/api/relations/{rel_id}").json()["ok"]
    assert client.get("/api/graph").json()["edges"] == []


def test_purge_guardrail(client):
    uid = _create(client)
    bad = client.post(f"/api/memories/{uid}/purge", json={"confirm": "yes"})
    assert bad.status_code == 400
    ok = client.post(f"/api/memories/{uid}/purge", json={"confirm": f"DELETE {uid}"})
    assert ok.json()["ok"] is True
    assert client.get(f"/api/memories/{uid}").status_code == 400


def test_bulk_operations(client):
    uids = [_create(client, content=f"note {i}") for i in range(3)]
    res = client.post("/api/bulk", json={
        "uids": uids, "action": "confidence", "value": "confirmed"})
    assert res.json()["affected"] == 3
    res = client.post("/api/bulk", json={"uids": uids, "action": "archive", "reason": "batch"})
    assert res.json()["affected"] == 3
    assert client.get("/api/memories?status=archived").json()["total"] == 3


def test_domains_rename_and_collision(client):
    _create(client, domain="PROJ-1")
    _create(client, domain="proj-1")
    doms = client.get("/api/domains").json()["domains"]
    assert len(doms) == 2
    assert all("collides_with" in d for d in doms)

    res = client.post("/api/domains/rename", json={"from": "PROJ-1", "to": "proj-1"})
    assert res.json() == {"ok": True, "affected": 1, "merged": True}
    doms = client.get("/api/domains").json()["domains"]
    assert len(doms) == 1
    assert doms[0]["active"] == 2

    missing = client.post("/api/domains/rename", json={"from": "nada", "to": "x"})
    assert missing.status_code == 400


def test_maintenance_suite(client):
    uid = _create(client, content="maintenance row content", domain="mnt")
    _create(client, content="maintenance row content nearly equal", domain="mnt")

    h = client.get("/api/maintenance/health").json()
    assert h["integrity"]["ok"] is True
    assert h["fts"]["ok"] is True
    assert h["relations"]["orphans"] == 0

    assert client.post("/api/maintenance/fts-rebuild", json={}).json()["ok"]
    assert client.post("/api/maintenance/clean-orphans", json={}).json()["ok"]
    assert client.post("/api/maintenance/vacuum", json={}).json()["ok"]

    bk = client.post("/api/maintenance/backup", json={}).json()
    assert bk["ok"] and bk["size"] > 0
    assert client.get("/api/maintenance/health").json()["backups"]

    pairs = client.get("/api/maintenance/dedup?threshold=0.5").json()["pairs"]
    assert pairs and pairs[0]["ratio"] >= 0.5

    client.post(f"/api/memories/{uid}/content", json={"content": "edited", "note": "audit"})
    entries = client.get("/api/audit").json()["entries"]
    assert entries[0]["memory_uid"] == uid
    assert entries[0]["content_changed"] == 1


def test_static_ui_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "MemAI" in res.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/admin.css").status_code == 200


def test_graph_is_capped_and_says_so(client):
    """An uncapped graph freezes the browser: the layout is O(n^2)."""
    for i in range(6):
        _create(client, content=f"graph node {i}", domain="proj-1042")

    full = client.get("/api/graph").json()
    assert full["total"] == 6
    assert full["truncated"] is False
    assert len(full["nodes"]) == 6

    capped = client.get("/api/graph?limit=2").json()
    assert len(capped["nodes"]) == 2
    assert capped["total"] == 6
    assert capped["truncated"] is True


def test_graph_cap_keeps_the_connected_nodes(client):
    """If it has to cut, cut the isolated dots -- they graph nothing."""
    lonely = [_create(client, content=f"isolated {i}") for i in range(4)]
    a = _create(client, content="linked one")
    b = _create(client, content="linked two")
    client.post("/api/relations", json={"from_uid": a, "to_uid": b,
                                        "relation_type": "relates_to"})

    kept = {n["uid"] for n in client.get("/api/graph?limit=2").json()["nodes"]}
    assert kept == {a, b}
    assert not kept & set(lonely)


# ------------------------------------------------------- same-origin guard

def test_cross_origin_write_is_refused(client):
    """A page you happen to be visiting must not be able to drive this.

    There is no login on the admin API, so the browser's own labelling is
    the guard: Sec-Fetch-Site on everything, Origin on anything
    cross-origin. See admin.SameOriginMiddleware.
    """
    uid = _create(client)

    res = client.post("/api/memories", json={"type": "note", "content": "x"},
                      headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403

    res = client.post(f"/api/memories/{uid}/status", json={"status": "archived"},
                      headers={"Origin": "https://evil.example.com"})
    assert res.status_code == 403

    # reads are refused the same way -- the store is not public either
    assert client.get("/api/overview",
                      headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403

    # and nothing happened to the record
    assert client.get(f"/api/memories/{uid}").json()["status"] == "active"


def test_same_origin_write_is_allowed(client):
    """The UI's own requests carry both headers and must sail through."""
    res = client.post("/api/memories",
                      json={"type": "note", "content": "from the real UI"},
                      headers={"Sec-Fetch-Site": "same-origin",
                               "Origin": "http://testserver"})
    assert res.status_code == 200, res.text


def test_form_content_type_write_is_refused(client):
    """The whole point of requiring JSON.

    A cross-origin POST skips the CORS preflight only while it looks like
    a form. request.json() parses any content type, so text/plain used to
    be a working way to reach a destructive endpoint from another page.
    """
    for ctype in ("text/plain", "application/x-www-form-urlencoded",
                  "multipart/form-data"):
        res = client.post("/api/maintenance/vacuum", content=b"{}",
                          headers={"Content-Type": ctype})
        assert res.status_code == 415, ctype

    # DELETE needs no body, and cross-origin DELETE always preflights
    assert client.delete("/api/relations/999").status_code in (400, 404)
