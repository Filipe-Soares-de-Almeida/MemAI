"""One active checkpoint per domain, and the block a checkpoint past its age limit puts on writes.

Hermetic: MEMAI_HOME is a tmp dir per test, and every memory is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from conftest import shaped
from memai import brief, server
from memai.admin.app import app as admin_app
from memai.store import connection, memories, relations, settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(store):
    with TestClient(admin_app) as c:
        yield c


def _ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _checkpoint(domain: str = "acme/x100", **over):
    args = {"title": "Retry path bearing", "intent": "ship the retry path",
            "established": "the queue drains", "pursuing": "the batch retry",
            "open_questions": "none", "domain": domain, **over}
    return server.checkpoint(**args)


def _old_checkpoint(domain: str = "acme/x100", days: int = 30) -> str:
    with connection.connect() as conn:
        return memories.insert_memory(
            conn, type="checkpoint", content=shaped("checkpoint", "ship the old path"),
            title="Old bearing", domain=domain, created_at=_ago(days))


def _active_checkpoints(domain: str) -> list[str]:
    with connection.connect() as conn:
        return [r["uid"] for r in conn.execute(
            "SELECT uid FROM memories WHERE type = 'checkpoint' AND status = 'active' "
            "AND domain = ?", (domain,))]


# ------------------------------------------------------------ one per domain

def test_a_second_checkpoint_in_a_domain_is_refused(store):
    first = _checkpoint()["uid"]
    res = _checkpoint(intent="a different plan")
    assert res["ok"] is False
    assert "one active checkpoint" in res["errors"][0]
    assert [c["uid"] for c in res["checkpoints"]] == [first]
    assert res["checkpoints"][0]["overdue"] is False
    assert _active_checkpoints("acme/x100") == [first]


def test_supersedes_archives_the_old_checkpoint_with_its_successor(store):
    first = _checkpoint()["uid"]
    res = _checkpoint(intent="the plan as it stands", supersedes=first)
    assert res["superseded"] == [first]
    assert _active_checkpoints("acme/x100") == [res["uid"]]
    with connection.connect() as conn:
        old = memories.get_memory(conn, first)
        edges = relations.get_relations(conn, res["uid"])
    assert old["status"] == "archived"
    assert old["superseded_by"] == res["uid"]
    assert [(e["from_uid"], e["to_uid"], e["relation_type"]) for e in edges] == [
        (res["uid"], first, "supersedes")]


def test_archiving_the_old_checkpoint_frees_the_domain(store):
    first = _checkpoint()["uid"]
    server.forget(first, reason="the retry path shipped")
    assert "uid" in _checkpoint(intent="the next plan")


def test_a_subdomain_keeps_its_own_checkpoint(store):
    _checkpoint("acme/x100")
    assert "uid" in _checkpoint("acme/x100/p200")
    assert "uid" in _checkpoint("acme")


def test_every_checkpoint_already_sharing_a_domain_has_to_be_named(store):
    """A domain can already hold several, written past checkpoint(); the next write settles all."""
    with connection.connect() as conn:
        older = [memories.insert_memory(conn, type="checkpoint", title="Old bearing",
                                        content=shaped("checkpoint", f"plan {i}"),
                                        domain="acme/x100") for i in range(2)]
    res = _checkpoint(supersedes=older[0])
    assert [c["uid"] for c in res["checkpoints"]] == [older[1]]
    res = _checkpoint(supersedes=",".join(older))
    assert sorted(res["superseded"]) == sorted(older)
    assert _active_checkpoints("acme/x100") == [res["uid"]]


@pytest.mark.parametrize("make, expected", [
    (lambda: "0123456789abcdef", "no memory"),
    (lambda: server.note("Queue fact", content="the queue drains", domain="acme")["uid"],
     "not a checkpoint"),
])
def test_supersedes_names_only_active_checkpoints(store, make, expected):
    res = _checkpoint(supersedes=make())
    assert res["ok"] is False
    assert expected in res["errors"][0]


def test_an_archived_checkpoint_cannot_be_superseded_again(store):
    first = _checkpoint()["uid"]
    server.forget(first)
    res = _checkpoint(supersedes=first)
    assert "already archived" in res["errors"][0]


def test_the_docstring_states_the_rule():
    doc = server.checkpoint.__doc__
    assert "ONE active checkpoint" in doc
    assert "supersedes='<uid>'" in doc


# ------------------------------------------------------------- overdue block

WRITERS = {
    "note": lambda d: server.note("Queue fact", content="the queue drains", domain=d),
    "anti_pattern": lambda d: server.anti_pattern("Retry pitfall", pattern="retry at once",
                                                  why_wrong="it drops rows",
                                                  instead="drain first", domain=d),
    "reasoning": lambda d: server.reasoning("Drain analysis", hypothesis="h", reasoning="r",
                                            result="res", revised_belief="b", next_time="n",
                                            domain=d),
    "task": lambda d: server.task("Drain the queue", "the queue is empty", "drain", domain=d),
    "diagram": lambda d: server.diagram("Drain flow", [
        {"key": "a", "label": "start", "shape": "start"},
        {"key": "b", "label": "done", "shape": "end"}], [{"from": "a", "to": "b"}], domain=d),
}


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_an_overdue_checkpoint_refuses_writes_under_its_domain(store, writer):
    old = _old_checkpoint("acme/x100")
    for domain in ("acme/x100", "acme/x100/p200"):
        res = WRITERS[writer](domain)
        assert res["ok"] is False, domain
        assert res["overdue"]["checkpoints"][0]["uid"] == old
        assert "tell the user" in res["overdue"]["alert"]
    assert "uid" in WRITERS[writer]("acme/x200")


def test_a_young_checkpoint_blocks_nothing(store):
    _old_checkpoint(days=settings.CHECKPOINT_DAYS_DEFAULT - 1)
    assert "uid" in WRITERS["note"]("acme/x100")


def test_settling_the_overdue_checkpoint_lifts_the_block(store):
    old = _old_checkpoint()
    res = _checkpoint(intent="the bearing that still holds")
    assert res["ok"] is False
    assert res["checkpoints"][0]["overdue"] is True
    renewed = _checkpoint(intent="the bearing that still holds", supersedes=old)
    assert renewed["superseded"] == [old]
    assert "uid" in WRITERS["note"]("acme/x100")


def test_forgetting_the_overdue_checkpoint_lifts_the_block(store):
    old = _old_checkpoint()
    server.forget(old, reason="its work shipped")
    assert "uid" in WRITERS["note"]("acme/x100")


def test_a_checkpoint_below_an_overdue_one_must_settle_it(store):
    old = _old_checkpoint("acme")
    res = _checkpoint("acme/x100")
    assert [(c["uid"], c["overdue"]) for c in res["checkpoints"]] == [(old, True)]
    assert _checkpoint("acme/x100", supersedes=old)["superseded"] == [old]


def test_an_overdue_checkpoint_with_no_domain_covers_the_project(store):
    _old_checkpoint("")
    assert WRITERS["note"]("zeta/anything")["ok"] is False


def test_the_block_switched_off_refuses_and_alerts_nothing(store):
    _old_checkpoint()
    with connection.connect() as conn:
        settings.set_checkpoint_block_enabled(conn, False)
    assert "uid" in WRITERS["note"]("acme/x100")
    assert "overdue" not in server.pulse("acme")
    assert "overdue" not in server.must_read("acme")


def test_the_limit_is_the_configured_one(store):
    _old_checkpoint(days=5)
    assert "uid" in WRITERS["note"]("acme/x100")
    with connection.connect() as conn:
        settings.set_checkpoint_days(conn, 3)
    assert WRITERS["note"]("acme/x100")["ok"] is False


def test_pulse_and_must_read_alert_and_put_settling_first(store):
    old = _old_checkpoint("acme/x100", days=20)
    p = server.pulse("acme")
    assert p["overdue"]["total"] == 1
    first = p["overdue"]["checkpoints"][0]
    assert (first["uid"], first["age_days"]) == (old, 20)
    assert p["overdue"]["days"] == settings.CHECKPOINT_DAYS_DEFAULT
    assert p["read_next"].startswith("Before acting, settle the checkpoints under `overdue`")
    assert server.must_read("acme")["overdue"]["checkpoints"][0]["uid"] == old
    assert server.must_read("acme", type="note")["overdue"]["total"] == 1
    assert "overdue" not in server.pulse("zeta")


def test_the_session_brief_names_the_overdue_checkpoint(store):
    old = _old_checkpoint()
    with connection.connect() as conn:
        text = brief.session_brief(conn)
    assert f"{old} [acme/x100]" in text
    assert "tell the user" in text


# ----------------------------------------------------------- the dashboard

def _symptom(data, key):
    return next(s for s in data["symptoms"] if s["key"] == key)


def test_the_overview_counts_overdue_checkpoints_and_lists_them(client):
    old = _old_checkpoint()
    _checkpoint("acme/x200")
    data = client.get("/api/overview").json()
    sym = _symptom(data, "overdue")
    assert (sym["count"], sym["severity"], sym["days"]) == (1, "bad", 14)
    listed = client.get("/api/memories", params=sym["params"]).json()
    assert [r["uid"] for r in listed["items"]] == [old]


def test_an_overdue_checkpoint_is_not_fresh(client):
    _old_checkpoint()
    _checkpoint("acme/x200")
    assert client.get("/api/overview").json()["health"]["axes"]["freshness"] == 50


def test_the_stale_symptom_follows_the_unverified_limit(client):
    with connection.connect() as conn:
        memories.insert_memory(conn, type="note", content="a fact", title="Queue fact",
                               domain="acme", created_at=_ago(10))
    assert _symptom(client.get("/api/overview").json(), "stale")["count"] == 0
    client.post("/api/config", json={"unverified_days": 7})
    sym = _symptom(client.get("/api/overview").json(), "stale")
    assert (sym["count"], sym["days"]) == (1, 7)


def test_config_round_trips_the_new_settings(client):
    body = {"checkpoint_block_enabled": False, "checkpoint_days": 30,
            "confidence_ask_enabled": False, "confidence_ask_minutes": 90,
            "unverified_days": 60}
    assert client.post("/api/config", json=body).status_code == 200
    got = client.get("/api/config").json()
    assert {k: got[k] for k in body} == body


@pytest.mark.parametrize("key, value", [
    ("checkpoint_days", 0), ("checkpoint_days", 366), ("unverified_days", "soon"),
    ("confidence_ask_minutes", 481),
])
def test_config_refuses_a_span_out_of_range(client, key, value):
    assert client.post("/api/config", json={key: value}).status_code == 400
