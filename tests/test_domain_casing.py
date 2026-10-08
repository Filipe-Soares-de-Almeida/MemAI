"""Domain-casing policy: db enforcement + MCP coerce-and-warn + admin
config/normalize. Every example domain is synthetic.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memai import lite, server
from memai.admin.app import app as admin_app
from memai.admin.routes.domains import _normalize_plan
from memai.store import connection, memories, optimizer, search, settings
from memai.store import domains as store_domains


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin_app) as c:
        yield c


# ---------------------------------------------------------------- db layer

def test_default_policy_is_preserve(conn):
    assert store_domains.get_domain_case(conn) == "preserve"
    uid = memories.insert_memory(conn, type="note", content="x", domain="MixedCase")
    assert memories.get_memory(conn, uid)["domain"] == "MixedCase"


@pytest.mark.parametrize("mode,given,expected", [
    ("lower", "Proj-A", "proj-a"),
    ("upper", "Proj-A", "PROJ-A"),
    ("preserve", "Proj-A", "Proj-A"),
])
def test_insert_coerces_to_policy(conn, mode, given, expected):
    store_domains.set_domain_case(conn, mode)
    uid = memories.insert_memory(conn, type="note", content="x", domain=given)
    assert memories.get_memory(conn, uid)["domain"] == expected


def test_empty_domain_untouched(conn):
    store_domains.set_domain_case(conn, "upper")
    uid = memories.insert_memory(conn, type="note", content="x", domain="")
    assert memories.get_memory(conn, uid)["domain"] == ""


def test_set_domain_case_rejects_unknown(conn):
    with pytest.raises(ValueError):
        store_domains.set_domain_case(conn, "weird")


def test_update_meta_field_coerces_domain(conn):
    store_domains.set_domain_case(conn, "upper")
    uid = memories.insert_memory(conn, type="note", content="x", domain="ALPHA")
    optimizer._update_meta_field(conn, uid, "domain", "beta")
    assert memories.get_memory(conn, uid)["domain"] == "BETA"


# ------------------------------------------------------------ reading it back

def test_filter_in_another_case_finds_the_path_itself(conn):
    """`LIKE` ignores case and `=` does not, so a filter in another case has
    to reach the rows filed at the path itself, not only its descendants."""
    own = memories.insert_memory(conn, type="note", content="a", domain="acme/x100")
    deep = memories.insert_memory(conn, type="note", content="b", domain="acme/x100/p200")
    assert {r["uid"] for r in search.list_by_domain(conn, "ACME/X100")} == {own, deep}


def test_filter_in_another_case_with_nothing_deeper(conn):
    uid = memories.insert_memory(conn, type="note", content="a", domain="acme/x100")
    assert [r["uid"] for r in search.list_by_domain(conn, "Acme/X100")] == [uid]


def test_a_resolved_scope_is_spelled_as_stored(conn):
    """Not as the caller wrote it -- a scope is reported back (pulse's
    `scope.paths`, the admin's `domain_scope`) as somewhere to look next,
    and the caller's spelling may name no path at all."""
    memories.insert_memory(conn, type="note", content="a", domain="acme/x100")
    assert store_domains.resolve_domain_scopes(conn, "ACME/X100") == ["acme/x100"]


def test_case_folding_reaches_a_deep_segment_too(conn):
    uid = memories.insert_memory(conn, type="note", content="a", domain="acme/x100/p200")
    assert store_domains.resolve_domain_scopes(conn, "P200") == ["acme/x100/p200"]
    assert [r["uid"] for r in search.list_by_domain(conn, "P200")] == [uid]


def test_case_folding_reaches_an_implicit_level(conn):
    """A level that exists only because something deeper is filed under it
    is still 'this path', not a name found inside one."""
    uid = memories.insert_memory(conn, type="note", content="a", domain="acme/x100/p200")
    assert store_domains.resolve_domain_scopes(conn, "ACME/X100") == ["acme/x100"]
    assert [r["uid"] for r in search.list_by_domain(conn, "ACME/X100")] == [uid]


def test_a_lower_policy_store_resolves_a_filter_written_upper(conn):
    store_domains.set_domain_case(conn, "lower")
    uid = memories.insert_memory(conn, type="note", content="a", domain="Proj-A")
    assert store_domains.resolve_domain_scopes(conn, "PROJ-A") == ["proj-a"]
    assert [r["uid"] for r in search.list_by_domain(conn, "PROJ-A")] == [uid]


def test_both_spellings_of_one_path_broaden_like_any_ambiguity(conn):
    """'preserve' keeps whatever each write used, so both are real paths and
    a filter covers both -- the same widening an ambiguous segment gets."""
    a = memories.insert_memory(conn, type="note", content="a", domain="acme/Cache")
    b = memories.insert_memory(conn, type="note", content="b", domain="acme/cache")
    assert store_domains.resolve_domain_scopes(conn, "acme/CACHE") == ["acme/Cache", "acme/cache"]
    assert {r["uid"] for r in search.list_by_domain(conn, "acme/CACHE")} == {a, b}


def test_case_folding_reaches_a_crosslisted_only_scope(conn):
    uid = memories.insert_memory(conn, type="note", content="a", domain="acme/x100",
                           also="omni/x900")
    assert store_domains.resolve_domain_scopes(conn, "OMNI/X900") == ["omni/x900"]
    assert [r["uid"] for r in search.list_by_domain(conn, "OMNI/X900")] == [uid]


def test_search_and_pulse_inherit_the_folding(conn):
    memories.insert_memory(conn, type="note", content="cache warmup", domain="acme/x100")
    assert len(search.search_memories(conn, "warmup", domain="ACME/X100")) == 1
    assert store_domains.domain_census(conn, "ACME/X100")["paths"] == ["acme/x100"]


# ---------------------------------------------------------------- MCP tools

def test_writer_reports_domain_adjustment(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        store_domains.set_domain_case(c, "upper")
    res = server.note("fixture title", content="x", domain="Proj-A")
    assert res["domain_adjusted"] == {"from": "Proj-A", "to": "PROJ-A", "policy": "upper"}


def test_writer_silent_when_conforming(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        store_domains.set_domain_case(c, "upper")
    assert "domain_adjusted" not in server.note("fixture title", content="x", domain="PROJ-A")


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
    plan = _normalize_plan("upper", counts)
    by_from = {e["from"]: e for e in plan}
    assert by_from["Proj-A"]["to"] == "PROJ-A"
    assert by_from["Proj-A"]["action"] == "merge"
    assert by_from["other"]["to"] == "OTHER"
    assert by_from["other"]["action"] == "rename"
    assert "PROJ-A" not in by_from  # already conforms, omitted


def test_normalize_plan_preserve_is_empty():
    assert _normalize_plan("preserve", {"Proj-A": 1, "other": 2}) == []


# ---------------------------------------------------------- admin endpoints

def test_config_roundtrip(client):
    assert client.get("/api/config").json()["domain_case"] == "preserve"
    assert client.post(
        "/api/config", json={"domain_case": "upper"}).json()["domain_case"] == "upper"
    assert client.get("/api/config").json()["domain_case"] == "upper"


def test_config_writes_one_setting_without_restating_the_other(client):
    """A partial POST must not disturb the settings it does not name."""
    client.post("/api/config", json={"domain_case": "upper"})
    body = client.post("/api/config", json={"svg_retention": "30d"}).json()
    assert body == {"domain_case": "upper", "svg_retention": "30d",
                    "warden_enabled": settings.WARDEN_ENABLED_DEFAULT,
                    "warden_minutes": lite.WARDEN_MINUTES_DEFAULT,
                    "task_ask_enabled": settings.TASK_ASK_ENABLED_DEFAULT,
                    "task_ask_minutes": lite.TASK_ASK_MINUTES_DEFAULT}


def test_config_rejects_an_empty_payload(client):
    assert client.post("/api/config", json={}).status_code == 400


def test_config_rejects_bad_value(client):
    res = client.post("/api/config", json={"domain_case": "weird"})
    assert res.status_code == 400


def test_normalize_dry_run_then_apply(client):
    client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "a", "domain": "Proj-A"})
    client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "b", "domain": "other"})
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
    res = client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "x", "domain": "MixedCase"})
    uid = res.json()["uid"]
    assert client.get(f"/api/memories/{uid}").json()["domain"] == "mixedcase"
