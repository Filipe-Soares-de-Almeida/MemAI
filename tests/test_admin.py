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

from conftest import unmigrated
from memai import admin, db, sections


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _create(client, **kw) -> str:
    """POST a memory. A type made of fields gets the text spread over them,
    since the endpoint builds those bodies rather than taking one."""
    body = {"type": "note", "content": "test fact", **kw}
    spec = sections.spec_for(body["type"])
    if spec:
        text = body.pop("content")
        body["sections"] = {s.key: f"{s.label.lower()}: {text}" for s in spec}
    res = client.post("/api/memories", json=body)
    assert res.status_code == 200, res.text
    return res.json()["uid"]


def _unread(type_: str, content: str, domain: str = "acme/x100") -> str:
    """Put a body in the store the way it stood before anything read it.

    The flag goes first: while it is set, a body that does not conform is
    refused rather than written, which is the whole point of it.
    """
    with db.connect() as conn:
        unmigrated(conn)
        uid = db.insert_memory(conn, type=type_, content=content, domain=domain)
        conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
        conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
    return uid


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
    assert res.json() == {"ok": True, "affected": 1, "also_affected": 0,
                          "domains": 1, "merged": True}
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


def test_fonts_css_never_names_a_missing_file(client):
    """webui/fonts/ is untracked and normally absent.

    A url() in a stylesheet is a request whether the file is there or not,
    so naming an unfetched face means a 404 in the console for something
    working exactly as designed. Every face still gets a local() src.
    """
    res = client.get("/fonts.css")
    assert res.status_code == 200
    assert "text/css" in res.headers["content-type"]
    body = res.text
    assert body.count("@font-face") == len(admin.WEBFONTS)
    assert "local('Roboto')" in body

    fonts_dir = admin.WEBUI_DIR / "fonts"
    for _family, _weight, filename, _locals in admin.WEBFONTS:
        if (fonts_dir / filename).is_file():
            assert f"url('/static/fonts/{filename}')" in body
        else:
            assert filename not in body


def test_fonts_css_uses_a_face_once_fetched(client, tmp_path, monkeypatch):
    webui = tmp_path / "webui"
    (webui / "fonts").mkdir(parents=True)
    (webui / "fonts" / "roboto-400.woff2").write_bytes(b"not really a font")
    monkeypatch.setattr(admin, "WEBUI_DIR", webui)

    body = client.get("/fonts.css").text
    assert "url('/static/fonts/roboto-400.woff2') format('woff2')" in body
    # the ones still absent stay out of it
    assert "roboto-mono-400.woff2" not in body
    assert "roboto-700.woff2" not in body


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
    """A write that does not declare application/json is refused.

    A cross-origin POST skips the CORS preflight only while it looks like a
    form, and request.json() parses any content type -- so the check is on
    the declared type, not on whether the body happens to parse.
    """
    for ctype in ("text/plain", "application/x-www-form-urlencoded",
                  "multipart/form-data"):
        res = client.post("/api/maintenance/vacuum", content=b"{}",
                          headers={"Content-Type": ctype})
        assert res.status_code == 415, ctype

    # DELETE needs no body, and cross-origin DELETE always preflights
    assert client.delete("/api/relations/999").status_code in (400, 404)


def test_lookup_carries_what_the_picker_renders(client):
    """A uid is not something a human recognizes, so the row needs the rest."""
    a = _create(client, content="source memory")
    b = _create(client, content="target memory", domain="parser-core", tags="loader,schema")

    items = client.get(f"/api/lookup?q=target&exclude={a}").json()["items"]
    assert [it["uid"] for it in items] == [b]
    row = items[0]
    assert row["domain"] == "parser-core"
    assert row["status"] == "active"
    assert row["snippet"]
    assert row["match_source"]          # why this row is in the list


def test_lookup_filters_narrow_the_candidate_set(client):
    _create(client, content="loader memory alpha", domain="parser-core", tags="loader,schema")
    _create(client, content="loader memory beta", domain="web-shell", tags="loader")
    _create(client, content="loader memory gamma", domain="parser-core", type="checkpoint",
            tags="batch")

    by_domain = client.get("/api/lookup?q=loader&domain=web-shell").json()["items"]
    assert [it["domain"] for it in by_domain] == ["web-shell"]

    by_type = client.get("/api/lookup?q=loader&type=checkpoint").json()["items"]
    assert [it["type"] for it in by_type] == ["checkpoint"]

    by_tag = client.get("/api/lookup?q=loader&tag=schema").json()["items"]
    assert len(by_tag) == 1
    assert by_tag[0]["domain"] == "parser-core"


def test_lookup_defaults_to_active_but_can_include_archived(client):
    live = _create(client, content="live memory")
    gone = _create(client, content="retired memory")
    assert client.post(f"/api/memories/{gone}/status",
                       json={"status": "archived"}).status_code == 200

    default = client.get("/api/lookup").json()["items"]
    assert [it["uid"] for it in default] == [live]

    with_archived = client.get("/api/lookup?status=").json()["items"]
    assert {it["uid"] for it in with_archived} == {live, gone}
    assert [it["status"] for it in with_archived if it["uid"] == gone] == ["archived"]


def test_lookup_exact_uid_answers_past_every_filter(client):
    """Naming a row explicitly is not something a filter should overrule."""
    gone = _create(client, content="retired memory", domain="infra")
    assert client.post(f"/api/memories/{gone}/status",
                       json={"status": "archived"}).status_code == 200

    items = client.get(f"/api/lookup?q={gone}&domain=parser-core&type=checkpoint").json()["items"]
    assert [it["uid"] for it in items] == [gone]


def test_lookup_reports_more_without_a_count_query(client):
    for i in range(6):
        _create(client, content=f"batch memory number {i}")

    page = client.get("/api/lookup?limit=3").json()
    assert len(page["items"]) == 3
    assert page["has_more"] is True

    whole = client.get("/api/lookup?limit=50").json()
    assert len(whole["items"]) == 6
    assert whole["has_more"] is False


# ------------------------------------------------------------------ sections

CHECKPOINT_BODY = (
    "INTENT: drain the queue before the nightly export\n"
    "ESTABLISHED: the worker retries three times, then parks the row\n"
    "PURSUING: the parked rows from the last run\n"
    "OPEN QUESTIONS: whether a parked row should age out"
)


def test_sectionize_reads_the_store_and_keeps_a_backup(client, tmp_path):
    uid = _unread("checkpoint", "CHECKPOINT @ 2026-01-01\n" + CHECKPOINT_BODY)

    res = client.post("/api/maintenance/sectionize", json={}).json()

    assert res["rewritten"] == 1 and res["needs_review"] == 0
    assert (tmp_path / "backups").exists()
    assert client.get(f"/api/memories/{uid}").json()["content"] == CHECKPOINT_BODY


def test_the_queue_lists_what_could_not_be_read(client):
    uid = _unread("anti_pattern", "a refutation over the whole body")

    client.post("/api/maintenance/sectionize", json={})
    queue = client.get("/api/maintenance/sections-queue").json()

    assert queue["migrated"] is True
    assert [e["uid"] for e in queue["queue"]] == [uid]
    assert "no line opens with" in queue["queue"][0]["detail"]


def test_a_record_carries_its_fields_and_what_they_should_be(client):
    uid = _create(client, type="checkpoint", content="the queue drain")

    detail = client.get(f"/api/memories/{uid}").json()

    assert [s["label"] for s in detail["spec"]] == [
        "INTENT", "ESTABLISHED", "PURSUING", "OPEN QUESTIONS"]
    assert detail["sections"][0]["key"] == "intent"
    assert detail["section_problem"] == ""


def test_a_record_that_could_not_be_read_says_so(client):
    uid = _unread("checkpoint", "a body with no labels")
    client.post("/api/maintenance/sectionize", json={})

    detail = client.get(f"/api/memories/{uid}").json()

    assert detail["sections"] == []
    assert "no line opens with" in detail["section_problem"]


def test_the_config_serves_the_fields_a_form_needs(client):
    spec = client.get("/api/config").json()["sections"]
    assert [s["key"] for s in spec["anti_pattern"]] == ["pattern", "why_wrong", "instead"]


def test_creating_a_sectioned_memory_refuses_a_plain_body(client):
    res = client.post("/api/memories", json={"type": "checkpoint", "content": CHECKPOINT_BODY})
    assert res.status_code == 400
    assert "created from its sections" in res.text


def test_setting_the_fields_empties_the_queue(client):
    uid = _unread("checkpoint", "a note that lost its shape")

    res = client.post(f"/api/memories/{uid}/sections", json={"sections": {
        "intent": "drain the queue",
        "established": "the worker parks a row after three tries",
        "pursuing": "the parked rows",
        "open_questions": "whether a parked row should age out"}})

    assert res.status_code == 200, res.text
    assert client.get("/api/maintenance/sections-queue").json()["queue"] == []
    assert client.get(f"/api/memories/{uid}").json()["content"].startswith("INTENT: drain")


def test_setting_the_fields_refuses_an_empty_one(client):
    uid = _create(client, type="checkpoint", content="the queue drain")

    res = client.post(f"/api/memories/{uid}/sections", json={"sections": {
        "intent": "drain the queue", "established": "", "pursuing": "x", "open_questions": "y"}})

    assert res.status_code == 400
    assert "nothing under ESTABLISHED" in res.text


def test_reclassifying_a_stuck_body_empties_the_queue(client):
    uid = _unread("checkpoint", "a refutation over the whole body")
    client.post("/api/maintenance/sectionize", json={})
    assert client.get("/api/maintenance/sections-queue").json()["queue"] != []

    res = client.post(f"/api/memories/{uid}/meta", json={"type": "note"})

    assert res.status_code == 200, res.text
    assert client.get("/api/maintenance/sections-queue").json()["queue"] == []


def test_claiming_a_type_made_of_fields_needs_a_body_that_reads_that_way(client):
    uid = _create(client, type="note", content="the cache warms on boot")

    res = client.post(f"/api/memories/{uid}/meta", json={"type": "checkpoint"})

    assert res.status_code == 400
    assert "INTENT, ESTABLISHED, PURSUING, OPEN QUESTIONS" in res.text
    assert client.get(f"/api/memories/{uid}").json()["type"] == "note"


def test_a_body_that_already_reads_that_way_may_claim_the_type(client):
    uid = _create(client, type="note", content=CHECKPOINT_BODY)

    res = client.post(f"/api/memories/{uid}/meta", json={"type": "checkpoint"})

    assert res.status_code == 200, res.text
    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["type"] == "checkpoint"
    assert detail["sections"][0]["text"] == "drain the queue before the nightly export"


def test_leaving_a_type_made_of_fields_drops_the_fields(client):
    uid = _create(client, type="checkpoint", content="the queue drain")
    assert client.get(f"/api/memories/{uid}").json()["sections"]

    res = client.post(f"/api/memories/{uid}/meta", json={"type": "note"})

    assert res.status_code == 200, res.text
    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["sections"] == [] and detail["spec"] == []


# ------------------------------------------------------- links inside a body

def test_a_record_resolves_the_wikilinks_written_in_its_body(client):
    target = _create(client, content="the cache warms on boot", domain="acme/x100")
    holder = _create(client, content=f"as established in [[{target}]]")

    links = client.get(f"/api/memories/{holder}").json()["body_links"]

    assert links[target]["type"] == "note"
    assert links[target]["domain"] == "acme/x100"
    assert links[target]["snippet"].startswith("the cache warms")


def test_a_wikilink_says_whether_the_graph_knows_about_it(client):
    """The reference is in the text; memai-link is what turns it into an edge,
    and until it does the record is the only place the gap shows."""
    target = _create(client, content="the cache warms on boot")
    holder = _create(client, content=f"as established in [[{target}]]")
    assert client.get(f"/api/memories/{holder}").json()["body_links"][target]["linked"] is False

    client.post("/api/relations", json={
        "from_uid": holder, "to_uid": target, "relation_type": "relates_to"})

    assert client.get(f"/api/memories/{holder}").json()["body_links"][target]["linked"] is True


def test_a_wikilink_pointing_at_nothing_says_so(client):
    holder = _create(client, content="as established in [[ffffffffffffffff]]")
    links = client.get(f"/api/memories/{holder}").json()["body_links"]
    assert links["ffffffffffffffff"] == {"uid": "ffffffffffffffff", "missing": True}


def test_a_body_with_no_wikilinks_resolves_nothing(client):
    holder = _create(client, content="a fact with no references")
    assert client.get(f"/api/memories/{holder}").json()["body_links"] == {}


def test_a_body_linking_to_itself_is_not_a_link(client):
    holder = _create(client, content="placeholder")
    client.post(f"/api/memories/{holder}/content", json={"content": f"see [[{holder}]]"})
    assert client.get(f"/api/memories/{holder}").json()["body_links"] == {}
