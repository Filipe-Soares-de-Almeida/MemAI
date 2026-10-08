"""The dashboard's task endpoints, and the task fields on the list, the detail
and the overview."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from conftest import brief
from memai import tasks
from memai.admin.app import app as admin_app
from memai.store import connection, memories


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin_app) as c:
        yield c


def _task(client, items="draft the plan\nreview the plan", **kw) -> str:
    body = {"title": "Ship the harbor map", "goal": "the map is live", "items": items,
            "domain": "acme/harbor", **kw}
    res = client.post("/api/tasks", json=body)
    assert res.status_code == 200, res.text
    return res.json()["uid"]


def _note(client) -> str:
    res = client.post("/api/memories", json={"title": "a harbor fact", "type": "note",
                                             "content": "the harbor has two docks"})
    assert res.status_code == 200, res.text
    return res.json()["uid"]


def _snapshot(uid: str) -> dict:
    with connection.connect() as conn:
        return {"task": tasks.get_task(conn, uid), "content": memories.get_memory(conn, uid)["content"],
                "status": memories.get_memory(conn, uid)["status"]}


def test_create_task_from_text(client):
    res = client.post("/api/tasks", json={
        "title": "Ship the harbor map", "goal": "the map is live",
        "items": "draft the plan\n\n  review the plan  \n", "domain": "acme/harbor",
        "tags": "map"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "active"
    assert [i["text"] for i in body["task"]["items"]] == ["draft the plan", "review the plan"]
    assert body["task"]["goal"] == "the map is live"
    assert body["task"]["state"] == "open"
    detail = client.get(f"/api/memories/{body['uid']}").json()
    assert detail["type"] == "task" and detail["domain"] == "acme/harbor"


def test_create_task_from_list(client):
    uid = _task(client, items=["one", "two", "three"])
    with connection.connect() as conn:
        assert [i["key"] for i in tasks.get_task(conn, uid)["items"]] == ["i1", "i2", "i3"]


def test_create_task_refusals(client):
    base = {"title": "t", "goal": "g", "items": "a"}
    assert client.post("/api/tasks", json={**base, "title": ""}).status_code == 400
    assert client.post("/api/tasks", json={**base, "goal": ""}).status_code == 400
    assert client.post("/api/tasks", json={**base, "items": ""}).status_code == 400
    over = client.post("/api/tasks", json={**base, "goal": "g" * (tasks.GOAL_MAX + 1)})
    assert over.status_code == 400
    assert client.get("/api/memories?type=task").json()["total"] == 0


def test_item_state_happy_path(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/item", json={"item": "i1", "state": "doing"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["task"]["items"][0]["state"] == "doing"
    assert body["status"] == "active"


def test_item_state_refuses_an_unknown_item(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/item", json={"item": "i9", "state": "done"})
    assert res.status_code == 400
    assert "i9" in res.json()["error"]


def test_closing_the_last_item_from_the_dashboard_archives(client):
    uid = _task(client, items="only step")
    res = client.post(f"/api/tasks/{uid}/item", json={"item": "1", "state": "done"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "archived"
    assert body["task"]["state"] == "completed"
    back = client.post(f"/api/tasks/{uid}/item", json={"item": "i1", "state": "todo"}).json()
    assert back["status"] == "active" and back["task"]["state"] == "open"


def test_add_items_happy_path(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/items", json={"items": "third step\nfourth step"})
    assert res.status_code == 200, res.text
    keys = [i["key"] for i in res.json()["task"]["items"]]
    assert keys == ["i1", "i2", "i3", "i4"]
    res = client.post(f"/api/tasks/{uid}/items", json={"items": ["fifth step"]})
    assert res.json()["task"]["items"][-1]["key"] == "i5"


def test_add_items_refuses_nothing_to_add(client):
    uid = _task(client)
    assert client.post(f"/api/tasks/{uid}/items", json={"items": "  \n "}).status_code == 400
    assert client.post(f"/api/tasks/{uid}/items", json={}).status_code == 400


def test_a_refused_item_batch_leaves_the_task_unchanged(client):
    uid = _task(client)
    before = _snapshot(uid)
    too_long = "x" * (tasks.ITEM_MAX + 1)
    res = client.post(f"/api/tasks/{uid}/items", json={"items": ["fine step", too_long]})
    assert res.status_code == 400
    assert _snapshot(uid) == before


def test_a_refused_link_leaves_the_task_unchanged(client):
    uid = _task(client)
    real = _note(client)
    before = _snapshot(uid)
    res = client.post(f"/api/tasks/{uid}/link", json={"item": "i1", "target": "nope0000"})
    assert res.status_code == 400
    assert "nope0000" in res.json()["error"]
    assert _snapshot(uid) == before
    res = client.post(f"/api/tasks/{uid}/link", json={"item": "i1", "target": [real, "nope0000"]})
    assert res.status_code == 400
    assert _snapshot(uid) == before


def test_a_refusal_after_rows_changed_rolls_them_back(client, monkeypatch):
    uid = _task(client)
    before = _snapshot(uid)

    def refuse(conn, uid, note, *, record_edit):
        raise ValueError("refused after the item row changed")

    monkeypatch.setattr(tasks, "_regenerate", refuse)
    res = client.post(f"/api/tasks/{uid}/item", json={"item": "i1", "state": "done"})
    assert res.status_code == 400
    assert _snapshot(uid) == before


def test_goal_happy_path(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/goal", json={"goal": "the map ships twice"})
    assert res.status_code == 200, res.text
    assert res.json()["task"]["goal"] == "the map ships twice"
    detail = client.get(f"/api/memories/{uid}").json()
    assert "the map ships twice" in detail["content"]


def test_goal_refuses_one_over_the_limit(client):
    uid = _task(client)
    before = _snapshot(uid)
    res = client.post(f"/api/tasks/{uid}/goal", json={"goal": "g" * (tasks.GOAL_MAX + 1)})
    assert res.status_code == 400
    assert _snapshot(uid) == before


def test_comment_happy_path_and_target_item(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/comment", json={"body": "looks good"})
    assert res.status_code == 200, res.text
    on_item = client.post(f"/api/tasks/{uid}/comment", json={"body": "needs a pass", "item": "i2"})
    comments = on_item.json()["task"]["comments"]
    assert [(c["item"], c["body"]) for c in comments] == [("", "looks good"), ("i2", "needs a pass")]


def test_person_comments_are_marked(client):
    uid = _task(client)
    body = client.post(f"/api/tasks/{uid}/comment", json={"body": "my note"}).json()
    assert body["task"]["comments"][0]["author"] == "person"


def test_comment_refuses_an_empty_body_and_an_unknown_item(client):
    uid = _task(client)
    assert client.post(f"/api/tasks/{uid}/comment", json={"body": "  "}).status_code == 400
    bad = client.post(f"/api/tasks/{uid}/comment", json={"body": "hi", "item": "i9"})
    assert bad.status_code == 400
    assert client.get(f"/api/memories/{uid}").json()["task"]["comments"] == []


def test_link_and_unlink_happy_path(client):
    uid = _task(client)
    note = _note(client)
    res = client.post(f"/api/tasks/{uid}/link", json={"item": "i1", "target": note})
    assert res.status_code == 200, res.text
    links = res.json()["task"]["items"][0]["links"]
    assert [link["uid"] for link in links] == [note]
    gone = client.request("DELETE", f"/api/tasks/{uid}/link", json={"item": "i1", "target": note})
    assert gone.status_code == 200, gone.text
    assert gone.json()["task"]["items"][0]["links"] == []


def test_unlink_accepts_a_list_of_uids(client):
    uid = _task(client)
    first, second, kept = _note(client), _note(client), _note(client)
    res = client.post(f"/api/tasks/{uid}/link", json={"item": "i1", "target": [first, second, kept]})
    assert res.status_code == 200, res.text
    gone = client.request("DELETE", f"/api/tasks/{uid}/link",
                          json={"item": "i1", "target": [first, second]})
    assert gone.status_code == 200, gone.text
    assert [link["uid"] for link in gone.json()["task"]["items"][0]["links"]] == [kept]


def test_unlink_refuses_an_unknown_item(client):
    uid = _task(client)
    res = client.request("DELETE", f"/api/tasks/{uid}/link", json={"item": "i9", "target": "x"})
    assert res.status_code == 400


@pytest.mark.parametrize("route,body", [
    ("item", {"item": "i1", "state": "done"}),
    ("items", {"items": "more"}),
    ("goal", {"goal": "new goal"}),
    ("comment", {"body": "hello"}),
    ("link", {"item": "i1", "target": "abc"}),
])
def test_a_post_to_a_non_task_is_refused(client, route, body):
    note = _note(client)
    res = client.post(f"/api/tasks/{note}/{route}", json=body)
    assert res.status_code == 400
    assert "no task" in res.json()["error"]
    res = client.post(f"/api/tasks/ghost0000/{route}", json=body)
    assert res.status_code == 400


def test_delete_link_on_a_non_task_is_refused(client):
    note = _note(client)
    res = client.request("DELETE", f"/api/tasks/{note}/link", json={"item": "i1", "target": "x"})
    assert res.status_code == 400
    assert "no task" in res.json()["error"]


def test_detail_carries_the_task_block(client):
    uid = _task(client)
    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["task"]["goal"] == "the map is live"
    assert len(detail["task"]["items"]) == 2
    note = client.get(f"/api/memories/{_note(client)}").json()
    assert "task" not in note


def test_list_rows_carry_progress(client):
    uid = _task(client, items="a\nb\nc")
    client.post(f"/api/tasks/{uid}/item", json={"item": "i1", "state": "done"})
    note = _note(client)
    rows = {r["uid"]: r for r in client.get("/api/memories").json()["items"]}
    assert rows[uid]["progress"] == {"done": 1, "total": 3}
    assert rows[uid]["task_state"] == "open"
    assert "progress" not in rows[note] and "task_state" not in rows[note]
    found = client.get("/api/memories?q=harbor").json()["items"]
    task_row = next(r for r in found if r["uid"] == uid)
    assert task_row["progress"] == {"done": 1, "total": 3}


def test_list_filters_by_task_state(client):
    open_uid = _task(client, items="a\nb")
    done_uid = _task(client, items="only")
    gone_uid = _task(client, items="dropped one")
    client.post(f"/api/tasks/{done_uid}/item", json={"item": "i1", "state": "done"})
    client.post(f"/api/tasks/{gone_uid}/item", json={"item": "i1", "state": "dropped"})
    note = _note(client)

    def uids(state: str) -> set[str]:
        res = client.get(f"/api/memories?task_state={state}")
        assert res.status_code == 200, res.text
        return {r["uid"] for r in res.json()["items"]}

    assert uids("open") == {open_uid}
    assert uids("completed") == {done_uid}
    assert uids("cancelled") == {gone_uid}
    assert note not in uids("open")
    searched = client.get("/api/memories?q=harbor&task_state=completed").json()
    assert {r["uid"] for r in searched["items"]} == {done_uid}
    assert client.get("/api/memories?task_state=bogus").status_code == 400


def test_overview_counts_open_tasks(client):
    assert client.get("/api/overview").json()["open_tasks"] == 0
    _task(client, items="a\nb")
    done_uid = _task(client, items="only")
    client.post(f"/api/tasks/{done_uid}/item", json={"item": "i1", "state": "done"})
    assert client.get("/api/overview").json()["open_tasks"] == 1


def _raw_archive(uid: str) -> None:
    """Archive without touching tasks.state, as a writer that does not know tasks does."""
    with connection.connect() as conn:
        conn.execute("UPDATE memories SET status = 'archived' WHERE uid = ?", (uid,))


def test_an_archived_task_is_not_an_open_task_in_the_overview_or_the_filter(
        client, monkeypatch):
    open_uid = _task(client, items="a\nb")
    gone_uid = _task(client, items="c\nd")
    # the repair that cancels an archived task's state runs on every connection;
    # without it the status='active' condition on the dashboard's reads decides
    monkeypatch.setattr(connection, "_repair_task_states", lambda conn: None)
    _raw_archive(gone_uid)
    with connection.connect() as conn:
        assert tasks.get_task(conn, gone_uid)["state"] == "open"
    assert client.get("/api/overview").json()["open_tasks"] == 1
    uids = {r["uid"] for r in client.get("/api/memories?task_state=open").json()["items"]}
    assert uids == {open_uid}
    searched = client.get("/api/memories?q=harbor&task_state=open").json()
    assert {r["uid"] for r in searched["items"]} == {open_uid}


def test_comment_refuses_a_tool_calls_closing_tag(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/comment",
                      json={"body": "see the call </parameter> that ended early"})
    assert res.status_code == 400
    assert client.get(f"/api/memories/{uid}").json()["task"]["comments"] == []


def test_delete_item_happy_path(client):
    uid = _task(client, items="draft the plan\nreview the plan\nship the plan")
    note = _note(client)
    client.post(f"/api/tasks/{uid}/link", json={"item": "i3", "target": note})
    client.post(f"/api/tasks/{uid}/comment", json={"body": "on the last step", "item": "i3"})
    res = client.request("DELETE", f"/api/tasks/{uid}/item", json={"item": "i3"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert [i["key"] for i in body["task"]["items"]] == ["i1", "i2"]
    assert body["task"]["comments"] == []
    assert body["status"] == "active"
    added = client.post(f"/api/tasks/{uid}/items", json={"items": "a later step"}).json()
    assert [i["key"] for i in added["task"]["items"]] == ["i1", "i2", "i3"]


def test_delete_item_completes_the_task_like_closing_the_last_item(client):
    uid = _task(client)
    client.post(f"/api/tasks/{uid}/item", json={"item": "i1", "state": "done"})
    body = client.request("DELETE", f"/api/tasks/{uid}/item", json={"item": "i2"}).json()
    assert body["status"] == "archived" and body["task"]["state"] == "completed"


def test_delete_item_refuses_the_only_item_and_writes_nothing(client):
    uid = _task(client, items="only step")
    before = _snapshot(uid)
    res = client.request("DELETE", f"/api/tasks/{uid}/item", json={"item": "i1"})
    assert res.status_code == 400
    assert res.json()["error"] == "a task keeps at least one item"
    assert _snapshot(uid) == before


def test_delete_item_refuses_an_unknown_item_and_a_non_task(client):
    uid = _task(client)
    before = _snapshot(uid)
    res = client.request("DELETE", f"/api/tasks/{uid}/item", json={"item": "i9"})
    assert res.status_code == 400 and "i9" in res.json()["error"]
    assert client.request("DELETE", f"/api/tasks/{uid}/item", json={}).status_code == 400
    assert _snapshot(uid) == before
    res = client.request("DELETE", f"/api/tasks/{_note(client)}/item", json={"item": "i1"})
    assert res.status_code == 400 and "no task" in res.json()["error"]
    assert client.request("DELETE", "/api/tasks/ghost0000/item", json={"item": "i1"}).status_code == 400


def test_a_delete_item_refusal_after_rows_changed_rolls_them_back(client, monkeypatch):
    uid = _task(client)
    before = _snapshot(uid)

    def refuse(conn, uid, note, *, record_edit):
        raise ValueError("refused after the item row was deleted")

    monkeypatch.setattr(tasks, "_regenerate", refuse)
    res = client.request("DELETE", f"/api/tasks/{uid}/item", json={"item": "i1"})
    assert res.status_code == 400
    assert _snapshot(uid) == before


def test_delete_item_refuses_a_foreign_origin(client):
    uid = _task(client)
    before = _snapshot(uid)
    for headers in ({"Origin": "https://evil.example.com"}, {"Sec-Fetch-Site": "cross-site"}):
        res = client.request("DELETE", f"/api/tasks/{uid}/item", json={"item": "i1"},
                             headers=headers)
        assert res.status_code == 403
    assert _snapshot(uid) == before


# ------------------------------------------------- the same-origin middleware

POST_ROUTES = [
    ("/api/tasks", {"title": "t", "goal": "g", "items": "a"}),
    ("/api/tasks/{uid}/item", {"item": "i1", "state": "done"}),
    ("/api/tasks/{uid}/items", {"items": "more"}),
    ("/api/tasks/{uid}/goal", {"goal": "new goal"}),
    ("/api/tasks/{uid}/comment", {"body": "hello"}),
    ("/api/tasks/{uid}/link", {"item": "i1", "target": "abc"}),
]
DELETE_ROUTES = [
    ("/api/tasks/{uid}/item", {"item": "i1"}),
    ("/api/tasks/{uid}/link", {"item": "i1", "target": "abc"}),
]
FOREIGN = [{"Origin": "https://evil.example.com"}, {"Sec-Fetch-Site": "cross-site"}]


@pytest.mark.parametrize("path,body", POST_ROUTES)
@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data"])
def test_a_task_post_that_is_not_json_is_refused(client, path, body, ctype):
    uid = _task(client)
    before = _snapshot(uid)
    res = client.post(path.format(uid=uid), content=json.dumps(body).encode(),
                      headers={"Content-Type": ctype})
    assert res.status_code == 415
    assert _snapshot(uid) == before
    assert client.get("/api/memories?type=task").json()["total"] == 1


@pytest.mark.parametrize("headers", FOREIGN, ids=["origin", "fetch-site"])
@pytest.mark.parametrize("method,path,body",
                         [("POST", p, b) for p, b in POST_ROUTES]
                         + [("DELETE", p, b) for p, b in DELETE_ROUTES])
def test_a_task_write_from_a_foreign_origin_is_refused(client, method, path, body, headers):
    uid = _task(client)
    before = _snapshot(uid)
    res = client.request(method, path.format(uid=uid), json=body, headers=headers)
    assert res.status_code == 403
    assert _snapshot(uid) == before
    assert client.get("/api/memories?type=task").json()["total"] == 1


def test_task_note_routes_round_trip(client):
    uid = _task(client)
    made = client.post(f"/api/tasks/{uid}/note",
                       json={"title": "Chart rules", "body": brief("chart the depths"), "items": ["i2"]})
    assert made.status_code == 200, made.text
    note = made.json()["task"]["notes"][0]
    assert (note["title"], note["items"]) == ("Chart rules", ["i2"])
    edited = client.post(f"/api/tasks/{uid}/note",
                         json={"id": note["id"], "body": "Depths in fathoms.", "items": []}).json()
    assert edited["task"]["notes"][0]["body"] == "Depths in fathoms."
    assert edited["task"]["notes"][0]["items"] == []
    gone = client.request("DELETE", f"/api/tasks/{uid}/note", json={"id": note["id"]}).json()
    assert gone["task"]["notes"] == []


def test_a_bad_task_note_is_refused(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/note", json={"title": "T", "body": "", "items": []})
    assert res.status_code == 400
    res = client.post(f"/api/tasks/{uid}/note", json={"title": "T", "body": brief("b"), "items": ["i9"]})
    assert res.status_code == 400


def test_the_record_carries_every_task_note(client):
    uid = _task(client)
    client.post(f"/api/tasks/{uid}/note", json={"title": "Top", "body": "b", "items": []})
    client.post(f"/api/tasks/{uid}/note", json={"title": "On i1", "body": brief("b"), "items": ["i1"]})
    record = client.get(f"/api/memories/{uid}").json()
    assert [n["title"] for n in record["task"]["notes"]] == ["Top", "On i1"]


def test_task_notes_carry_what_their_wikilinks_point_at(client):
    uid = _task(client)
    fact = _note(client)
    answer = client.post(f"/api/tasks/{uid}/note",
                         json={"title": "Chart rules", "body": f"See [[{fact}]].", "items": []}).json()
    assert answer["task"]["notes"][0]["body_links"][fact]["type"] == "note"
    shown = client.get(f"/api/memories/{uid}").json()
    assert fact in shown["task"]["notes"][0]["body_links"]


def test_a_note_carries_its_brief_fields_or_null(client):
    uid = _task(client)
    client.post(f"/api/tasks/{uid}/note", json={"title": "Top", "body": "free", "items": []})
    client.post(f"/api/tasks/{uid}/note",
                json={"title": "On i1", "body": brief("solder it", extra_info="flux first"), "items": ["i1"]})
    notes = client.get(f"/api/memories/{uid}").json()["task"]["notes"]
    assert notes[0]["brief"] is None
    assert notes[1]["brief"]["goal"] == "solder it" and notes[1]["brief"]["extra_info"] == "flux first"


def test_a_free_body_on_items_is_a_400_and_writes_nothing(client):
    uid = _task(client)
    res = client.post(f"/api/tasks/{uid}/note", json={"title": "T", "body": "free", "items": ["i1"]})
    assert res.status_code == 400 and "a note on items is a brief" in res.text
    assert client.get(f"/api/memories/{uid}").json()["task"]["notes"] == []
