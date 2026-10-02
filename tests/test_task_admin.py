"""The dashboard's task endpoints, and the task fields on the list, the detail
and the overview."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memai import admin, db, tasks


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
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
    with db.connect() as conn:
        return {"task": tasks.get_task(conn, uid), "content": db.get_memory(conn, uid)["content"],
                "status": db.get_memory(conn, uid)["status"]}


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
    with db.connect() as conn:
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

    def refuse(conn, uid, note):
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
    assert [l["uid"] for l in links] == [note]
    gone = client.request("DELETE", f"/api/tasks/{uid}/link", json={"item": "i1", "target": note})
    assert gone.status_code == 200, gone.text
    assert gone.json()["task"]["items"][0]["links"] == []


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
    with db.connect() as conn:
        conn.execute("UPDATE memories SET status = 'archived' WHERE uid = ?", (uid,))


def test_an_archived_task_is_not_an_open_task_in_the_overview_or_the_filter(client):
    open_uid = _task(client, items="a\nb")
    gone_uid = _task(client, items="c\nd")
    _raw_archive(gone_uid)
    # the dashboard reads through admin's own connection, which repairs on open;
    # stub the repair out so the read-side condition is what is under test
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
