"""Domain-casing policy: db enforcement + MCP coerce-and-warn + admin
config/normalize. Hermetic FTS-only path like the rest of the suite
(autouse fixture in conftest keeps the real embedder out); every example
domain is synthetic.
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


# ---------------------------------------------------------------- db layer

def test_default_policy_is_preserve(conn):
    assert db.get_domain_case(conn) == "preserve"
    uid = db.insert_memory(conn, type="note", content="x", domain="MixedCase")
    assert db.get_memory(conn, uid)["domain"] == "MixedCase"


@pytest.mark.parametrize("mode,given,expected", [
    ("lower", "Proj-A", "proj-a"),
    ("upper", "Proj-A", "PROJ-A"),
    ("preserve", "Proj-A", "Proj-A"),
])
def test_insert_coerces_to_policy(conn, mode, given, expected):
    db.set_domain_case(conn, mode)
    uid = db.insert_memory(conn, type="note", content="x", domain=given)
    assert db.get_memory(conn, uid)["domain"] == expected


def test_empty_domain_untouched(conn):
    db.set_domain_case(conn, "upper")
    uid = db.insert_memory(conn, type="note", content="x", domain="")
    assert db.get_memory(conn, uid)["domain"] == ""


def test_set_domain_case_rejects_unknown(conn):
    with pytest.raises(ValueError):
        db.set_domain_case(conn, "weird")


def test_update_meta_field_coerces_domain(conn):
    db.set_domain_case(conn, "upper")
    uid = db.insert_memory(conn, type="note", content="x", domain="ALPHA")
    db._update_meta_field(conn, uid, "domain", "beta")
    assert db.get_memory(conn, uid)["domain"] == "BETA"


# ---------------------------------------------------------------- MCP tools

def test_writer_reports_domain_adjustment(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with db.connect() as c:
        db.set_domain_case(c, "upper")
    res = server.note(content="x", domain="Proj-A")
    assert res["domain_adjusted"] == {"from": "Proj-A", "to": "PROJ-A", "policy": "upper"}


def test_writer_silent_when_conforming(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with db.connect() as c:
        db.set_domain_case(c, "upper")
    assert "domain_adjusted" not in server.note(content="x", domain="PROJ-A")


def test_get_set_domain_case_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    assert server.get_domain_case() == {"mode": "preserve"}
    assert server.set_domain_case("lower") == {"mode": "lower"}
    assert server.get_domain_case() == {"mode": "lower"}


# ------------------------------------------------------ normalize planning

def test_normalize_plan_rename_and_merge():
    # 'proj-a' already conforms to upper's target 'PROJ-A'? no -- it renames;
    # 'Proj-A' and 'PROJ-A' both collapse onto 'PROJ-A' -> merge.
    counts = {"Proj-A": 2, "PROJ-A": 1, "other": 3}
    plan = admin._normalize_plan("upper", counts)
    by_from = {e["from"]: e for e in plan}
    assert by_from["Proj-A"]["to"] == "PROJ-A"
    assert by_from["Proj-A"]["action"] == "merge"
    assert by_from["other"]["to"] == "OTHER"
    assert by_from["other"]["action"] == "rename"
    assert "PROJ-A" not in by_from  # already conforms, omitted


def test_normalize_plan_preserve_is_empty():
    assert admin._normalize_plan("preserve", {"Proj-A": 1, "other": 2}) == []


# ---------------------------------------------------------- admin endpoints

def test_config_roundtrip(client):
    assert client.get("/api/config").json() == {"domain_case": "preserve"}
    assert client.post("/api/config", json={"domain_case": "upper"}).json() == {"domain_case": "upper"}
    assert client.get("/api/config").json() == {"domain_case": "upper"}


def test_config_rejects_bad_value(client):
    res = client.post("/api/config", json={"domain_case": "weird"})
    assert res.status_code == 400


def test_normalize_dry_run_then_apply(client):
    client.post("/api/memories", json={"type": "note", "content": "a", "domain": "Proj-A"})
    client.post("/api/memories", json={"type": "note", "content": "b", "domain": "other"})
    client.post("/api/config", json={"domain_case": "upper"})

    dry = client.post("/api/domains/normalize", json={"dry_run": True}).json()
    assert dry["dry_run"] is True
    assert {e["from"] for e in dry["plan"]} == {"Proj-A", "other"}

    applied = client.post("/api/domains/normalize", json={"dry_run": False}).json()
    assert applied["ok"] is True and applied["moved"] == 2

    domains = {d["domain"] for d in client.get("/api/domains").json()["domains"]}
    assert domains == {"PROJ-A", "OTHER"}


def test_create_memory_coerced_via_admin(client):
    client.post("/api/config", json={"domain_case": "lower"})
    res = client.post("/api/memories", json={"type": "note", "content": "x", "domain": "MixedCase"})
    uid = res.json()["uid"]
    assert client.get(f"/api/memories/{uid}").json()["domain"] == "mixedcase"
