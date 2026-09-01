"""Several stores in one home.

Which file a connect() opens, how the active store is switched, and what
follows the switch without a restart: the dashboard, the MCP tools, the
brief the SessionStart hook emits, and the backups -- named after the store
they hold, listed for the store that is active.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memai import admin, autostart, brief, db, server, warden


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(home):
    with TestClient(admin.app) as c:
        yield c


def _count(store: str) -> int:
    with db.connect(store=store) as conn:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


# ── which store a connect() opens ───────────────────────────────────────

def test_a_home_with_no_pointer_opens_the_general_store(home):
    assert db.active_store() == "general"
    assert db.default_db_path() == home / "memai.db"
    assert db.list_stores() == [
        {"name": "general", "path": str(home / "memai.db"), "size": 0, "active": True}]


def test_a_new_store_is_its_own_file_with_the_schema_in_place(home):
    path = db.create_store("acme")
    assert path == home / "stores" / "acme.db"
    with db.connect(store="acme") as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"memories", "memories_fts", "relations", "diagrams"} <= tables
    assert _count("general") == 0


@pytest.mark.parametrize("name", ["", "Acme", "a b", "../x", "x/y", ".hidden", "memai",
                                  "sectionize", "a" * 41])
def test_a_name_that_cannot_be_a_store_is_refused(home, name):
    with pytest.raises(ValueError):
        db.create_store(name)
    assert not list(home.rglob("*.db"))


def test_creating_a_store_twice_is_refused(home):
    db.create_store("acme")
    with pytest.raises(ValueError, match="already exists"):
        db.create_store("acme")


def test_connect_takes_a_path_or_a_store_name_but_not_both(home):
    db.create_store("acme")
    with pytest.raises(ValueError, match="not both"):
        with db.connect(home / "memai.db", store="acme"):
            pass


def test_switching_redirects_every_connect_without_a_path(home):
    db.create_store("acme")
    db.set_active_store("acme")
    assert (home / "active").read_text(encoding="utf-8").strip() == "acme"
    assert db.default_db_path() == home / "stores" / "acme.db"
    with db.connect() as conn:
        db.insert_memory(conn, type="note", content="filed in acme", domain="acme/x100")
    assert _count("acme") == 1
    assert _count("general") == 0
    db.set_active_store("general")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_the_pointer_has_to_name_an_existing_store(home):
    with pytest.raises(ValueError, match="no store named"):
        db.set_active_store("zeta")
    assert db.active_store() == "general"


def test_a_pointer_that_cannot_name_a_store_falls_back_to_general(home):
    (home / "active").write_text("Not A Store\n", encoding="utf-8")
    assert db.active_store() == "general"
    assert db.default_db_path() == home / "memai.db"


def test_list_stores_puts_general_first_and_marks_the_active_one(home):
    db.create_store("zeta")
    db.create_store("acme")
    db.set_active_store("zeta")
    with db.connect() as conn:
        db.insert_memory(conn, type="note", content="one", domain="zeta")
    rows = db.list_stores(counts=True)
    assert [r["name"] for r in rows] == ["general", "acme", "zeta"]
    assert [r["active"] for r in rows] == [False, False, True]
    assert [r["memories"] for r in rows] == [0, 0, 1]


def test_deleting_a_store_needs_it_empty_and_inactive(home):
    db.create_store("acme")
    db.set_active_store("acme")
    with pytest.raises(ValueError, match="active"):
        db.delete_store("acme")
    with db.connect() as conn:
        uid = db.insert_memory(conn, type="note", content="kept", domain="acme")
    db.set_active_store("general")
    with pytest.raises(ValueError, match="holds 1"):
        db.delete_store("acme")
    with db.connect(store="acme") as conn:
        db.purge_memory(conn, uid)
    db.delete_store("acme")
    assert not (home / "stores" / "acme.db").exists()
    assert [r["name"] for r in db.list_stores()] == ["general"]


def test_the_general_store_cannot_be_deleted(home):
    with pytest.raises(ValueError, match="general"):
        db.delete_store("general")
    with pytest.raises(ValueError, match="no store named"):
        db.delete_store("zeta")


def test_the_side_folders_stay_in_the_home_whichever_store_is_active(home):
    db.create_store("acme")
    db.set_active_store("acme")
    assert db.renders_dir() == home / "renders"
    assert db.backups_dir() == home / "backups"
    assert warden.state_dir() == home / "warden"
    assert autostart.home() == home


# ── backups ─────────────────────────────────────────────────────────────

def test_a_backup_is_named_after_its_store(home):
    plain, kind = db.backup_name("acme"), db.backup_name("acme", "sectionize")
    assert plain.startswith("acme-") and plain.endswith(".db")
    assert kind.startswith("acme-sectionize-") and kind.endswith(".db")


def test_general_also_owns_the_backups_written_without_a_store_name(home):
    folder = db.backups_dir()
    for name in ("memai-20260101-000000.db", "optimize-run3-20260101-000000.db",
                 "sectionize-20260101-000000.db", "general-20260102-000000.db",
                 "acme-20260101-000000.db", "notes.txt"):
        (folder / name).write_bytes(b"")
    assert [p.name for p in db.backup_files("general")] == [
        "sectionize-20260101-000000.db", "optimize-run3-20260101-000000.db",
        "memai-20260101-000000.db", "general-20260102-000000.db"]
    assert [p.name for p in db.backup_files("acme")] == ["acme-20260101-000000.db"]


def test_backup_to_copies_the_store_it_is_asked_for(home):
    db.create_store("acme")
    with db.connect(store="acme") as conn:
        db.insert_memory(conn, type="note", content="in the copy", domain="acme")
    dest = db.backup_to(home / "copy.db", store="acme")
    with db.connect(dest) as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "in the copy"


# ── the dashboard ───────────────────────────────────────────────────────

def test_the_stores_endpoints_create_switch_list_and_delete(client, home):
    data = client.get("/api/stores").json()
    assert data["active"] == "general"
    assert [s["name"] for s in data["stores"]] == ["general"]

    res = client.post("/api/stores", json={"name": " Acme ", "activate": True})
    assert res.status_code == 200, res.text
    assert res.json()["active"] == "acme"
    assert [s["name"] for s in res.json()["stores"]] == ["general", "acme"]
    assert client.get("/api/overview").json()["db"]["store"] == "acme"
    assert client.get("/api/ping").json()["store"] == "acme"

    assert client.post("/api/stores", json={"name": "acme"}).status_code == 400
    assert client.post("/api/stores", json={"name": "no spaces"}).status_code == 400
    assert client.post("/api/stores/active", json={"name": "zeta"}).status_code == 400

    assert client.delete("/api/stores/acme").status_code == 400   # still active
    assert client.post("/api/stores/active", json={"name": "general"}).json()["active"] == "general"
    gone = client.delete("/api/stores/acme").json()
    assert [s["name"] for s in gone["stores"]] == ["general"]
    assert client.delete("/api/stores/general").status_code == 400


def test_a_backup_carries_the_active_store_and_health_lists_only_its_own(client, home):
    assert client.post("/api/maintenance/backup", json={}).json()["store"] == "general"
    client.post("/api/stores", json={"name": "acme", "activate": True})
    bk = client.post("/api/maintenance/backup", json={}).json()
    assert bk["store"] == "acme"
    assert Path(bk["path"]).name.startswith("acme-")
    h = client.get("/api/maintenance/health").json()
    assert h["store"] == "acme"
    assert [b["name"] for b in h["backups"]] == [Path(bk["path"]).name]
    client.post("/api/stores/active", json={"name": "general"})
    names = [b["name"] for b in client.get("/api/maintenance/health").json()["backups"]]
    assert len(names) == 1 and names[0].startswith("general-")


# ── the MCP tools and the brief ─────────────────────────────────────────

def test_the_mcp_tools_follow_the_switch_without_a_restart(client, home):
    """Every tool opens db.connect() per call, so a switch made in the
    dashboard reaches the very next call of a server already running."""
    first = server.note(title="filed before the switch", content="in general",
                        domain="acme/x100")
    assert first["store"] == "general"
    client.post("/api/stores", json={"name": "zeta", "activate": True})
    second = server.note(title="filed after the switch", content="in zeta",
                         domain="acme/x100")
    assert second["store"] == "zeta"
    assert server.pulse()["store"] == "zeta"
    listed = server.list_stores()
    assert listed["active"] == "zeta"
    assert {s["name"]: s["memories"] for s in listed["stores"]} == {"general": 1, "zeta": 1}
    assert _count("general") == 1 and _count("zeta") == 1


def test_the_brief_names_the_store_it_read(home):
    with db.connect() as conn:
        db.insert_memory(conn, type="note", content="a fact", domain="acme/x100")
        assert "1 memories in store 'acme'." in brief.session_brief(conn, store="acme")
        assert "1 memories in the whole store." in brief.session_brief(conn)
