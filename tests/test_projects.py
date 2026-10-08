"""Several projects in one home.

A project is one SQLite file. Which one a connect() opens, how the active
project is switched, what a project may be called, and what follows the
switch without a restart: the dashboard, the MCP tools, the brief the
SessionStart hook emits, and the backups -- kept per project, named after
the project they hold.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memai import autostart, brief, server, warden
from memai.admin.app import app as admin_app
from memai.store import backups, connection, memories, paths, projects, renders


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(home):
    with TestClient(admin_app) as c:
        yield c


def _count(project: str) -> int:
    with connection.connect(project=project) as conn:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


# ── which project a connect() opens ─────────────────────────────────────

def test_a_home_with_no_pointer_opens_the_general_project(home):
    assert paths.active_project() == "General"
    assert paths.default_db_path() == home / "memai.db"
    assert projects.list_projects() == [{
        "name": "General", "path": str(home / "memai.db"), "size": 0,
        "active": True, "general": True}]


def test_a_new_project_is_its_own_file_with_the_schema_in_place(home):
    path = projects.create_project("Acme Billing")
    assert path == home / "projects" / "Acme Billing.db"
    with connection.connect(project="Acme Billing") as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"memories", "memories_fts", "relations", "diagrams"} <= tables
    assert _count("General") == 0


def test_a_project_is_found_whatever_the_casing(home):
    projects.create_project("Acme Billing")
    assert paths.find_project("acme billing") == "Acme Billing"
    assert paths.project_path("ACME BILLING") == home / "projects" / "Acme Billing.db"
    assert paths.project_exists("acme billing")
    assert paths.find_project("zeta") is None


@pytest.mark.parametrize("name", [
    "", "   ", "a<b", 'a"b', "a/b", "a\\b", "a:b", "a|b", "a?b", "a*b", "trailing.",
    " leading", "trailing ", "CON", "com1.db", "\x07bell", "a" * 81,
])
def test_a_name_that_cannot_be_a_file_is_refused(home, name):
    with pytest.raises(ValueError):
        projects.create_project(name)
    assert not list(home.rglob("*.db"))


def test_a_name_already_taken_in_any_casing_is_refused(home):
    projects.create_project("Acme")
    with pytest.raises(ValueError, match="already exists"):
        projects.create_project("acme")
    with pytest.raises(ValueError, match="already exists"):
        projects.create_project("general")


def test_connect_takes_a_path_or_a_project_but_not_both(home):
    projects.create_project("Acme")
    with pytest.raises(ValueError, match="not both"), connection.connect(home / "memai.db", project="Acme"):
        pass


def test_switching_redirects_every_connect_without_a_path(home):
    projects.create_project("Acme")
    assert paths.set_active_project("acme") == "Acme"
    assert (home / "active").read_text(encoding="utf-8").strip() == "Acme"
    assert paths.active_project() == "Acme"
    assert paths.default_db_path() == home / "projects" / "Acme.db"
    with connection.connect() as conn:
        memories.insert_memory(conn, type="note", content="filed in acme", domain="acme/x100")
    assert _count("Acme") == 1
    assert _count("General") == 0
    assert paths.set_active_project("general") == "General"
    with connection.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_the_pointer_has_to_name_an_existing_project(home):
    with pytest.raises(ValueError, match="no project named"):
        paths.set_active_project("zeta")
    assert paths.active_project() == "General"


def test_a_pointer_to_a_project_that_is_not_there_falls_back_to_general(home):
    (home / "active").write_text("Zeta\n", encoding="utf-8")
    assert paths.active_project() == "General"
    (home / "active").write_text("a*b\n", encoding="utf-8")
    assert paths.active_project() == "General"
    assert paths.default_db_path() == home / "memai.db"


def test_list_projects_puts_general_first_and_the_rest_by_name(home):
    projects.create_project("zeta")
    projects.create_project("Acme")
    paths.set_active_project("zeta")
    with connection.connect() as conn:
        memories.insert_memory(conn, type="note", content="one", domain="zeta")
    rows = projects.list_projects(counts=True)
    assert [r["name"] for r in rows] == ["General", "Acme", "zeta"]
    assert [r["active"] for r in rows] == [False, False, True]
    assert [r["general"] for r in rows] == [True, False, False]
    assert [r["memories"] for r in rows] == [0, 0, 1]


def test_deleting_a_project_needs_it_empty_and_inactive(home):
    projects.create_project("Acme")
    paths.set_active_project("Acme")
    with pytest.raises(ValueError, match="active"):
        projects.delete_project("Acme")
    with connection.connect() as conn:
        uid = memories.insert_memory(conn, type="note", content="kept", domain="acme")
    paths.set_active_project("General")
    with pytest.raises(ValueError, match="holds 1"):
        projects.delete_project("Acme")
    with connection.connect(project="Acme") as conn:
        memories.purge_memory(conn, uid)
    projects.delete_project("acme")
    assert not (home / "projects" / "Acme.db").exists()
    assert [r["name"] for r in projects.list_projects()] == ["General"]


def test_the_general_project_cannot_be_deleted(home):
    with pytest.raises(ValueError, match="General"):
        projects.delete_project("general")
    with pytest.raises(ValueError, match="no project named"):
        projects.delete_project("zeta")


def test_the_side_folders_stay_in_the_home_whichever_project_is_active(home):
    projects.create_project("Acme")
    paths.set_active_project("Acme")
    assert renders.renders_dir() == home / "renders"
    assert warden.state_dir() == home / "warden"
    assert autostart.home() == home


# ── backups ─────────────────────────────────────────────────────────────

def test_backups_live_in_the_root_for_general_and_in_a_folder_per_project(home):
    projects.create_project("Acme Billing")
    assert backups.backups_dir("General") == home / "backups"
    assert backups.backups_dir("acme billing") == home / "backups" / "Acme Billing"
    assert backups.backup_name("Acme Billing").startswith("Acme Billing-")
    assert backups.backup_name("Acme Billing", "move").startswith("Acme Billing-move-")


def test_each_project_lists_its_own_backups(home):
    projects.create_project("Acme")
    root, mine = backups.backups_dir("General"), backups.backups_dir("Acme")
    for folder, name in ((root, "memai-20260101-000000.db"), (root, "General-20260102-000000.db"),
                         (root, "notes.txt"), (mine, "Acme-20260103-000000.db")):
        (folder / name).write_bytes(b"")
    assert {p.name for p in backups.backup_files("General")} == {
        "memai-20260101-000000.db", "General-20260102-000000.db"}
    assert [p.name for p in backups.backup_files("acme")] == ["Acme-20260103-000000.db"]


def test_backup_to_copies_the_project_it_is_asked_for(home):
    projects.create_project("Acme")
    with connection.connect(project="Acme") as conn:
        memories.insert_memory(conn, type="note", content="in the copy", domain="acme")
    dest = backups.backup_to(home / "copy.db", project="acme")
    with connection.connect(dest) as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "in the copy"


# ── the dashboard ───────────────────────────────────────────────────────

def test_the_project_endpoints_create_switch_list_and_delete(client, home):
    data = client.get("/api/projects").json()
    assert data["active"] == "General"
    assert [p["name"] for p in data["projects"]] == ["General"]

    res = client.post("/api/projects", json={"name": " Acme Billing ", "activate": True})
    assert res.status_code == 200, res.text
    assert res.json()["active"] == "Acme Billing"
    assert [p["name"] for p in res.json()["projects"]] == ["General", "Acme Billing"]
    assert client.get("/api/overview").json()["db"]["project"] == "Acme Billing"
    assert client.get("/api/ping").json()["project"] == "Acme Billing"

    assert client.post("/api/projects", json={"name": "acme billing"}).status_code == 400
    assert client.post("/api/projects", json={"name": "a*b"}).status_code == 400
    assert client.post("/api/projects/active", json={"name": "zeta"}).status_code == 400

    assert client.delete("/api/projects/acme%20billing").status_code == 400   # still active
    assert client.post("/api/projects/active", json={"name": "GENERAL"}).json()["active"] == "General"
    gone = client.delete("/api/projects/acme%20billing").json()
    assert [p["name"] for p in gone["projects"]] == ["General"]
    assert client.delete("/api/projects/General").status_code == 400


def test_a_backup_lands_in_the_active_projects_folder_and_health_lists_only_its_own(client, home):
    first = client.post("/api/maintenance/backup", json={}).json()
    assert first["project"] == "General"
    assert Path(first["path"]).parent == home / "backups"
    client.post("/api/projects", json={"name": "Acme", "activate": True})
    bk = client.post("/api/maintenance/backup", json={}).json()
    assert bk["project"] == "Acme"
    assert Path(bk["path"]).parent == home / "backups" / "Acme"
    assert Path(bk["path"]).name.startswith("Acme-")
    h = client.get("/api/maintenance/health").json()
    assert h["project"] == "Acme"
    assert [b["name"] for b in h["backups"]] == [Path(bk["path"]).name]
    client.post("/api/projects/active", json={"name": "General"})
    names = [b["name"] for b in client.get("/api/maintenance/health").json()["backups"]]
    assert names == [Path(first["path"]).name]


# ── the MCP tools and the brief ─────────────────────────────────────────

def test_the_mcp_tools_follow_the_switch_without_a_restart(client, home):
    """Every tool opens connection.connect() per call, so a switch made in the
    dashboard reaches the very next call of a server already running."""
    first = server.note(title="filed before the switch", content="in general",
                        domain="acme/x100")
    assert first["project"] == "General"
    client.post("/api/projects", json={"name": "Zeta", "activate": True})
    second = server.note(title="filed after the switch", content="in zeta",
                         domain="acme/x100")
    assert second["project"] == "Zeta"
    assert server.pulse()["project"] == "Zeta"
    listed = server.list_projects()
    assert listed["active"] == "Zeta"
    assert {p["name"]: p["memories"] for p in listed["projects"]} == {"General": 1, "Zeta": 1}
    assert _count("General") == 1 and _count("Zeta") == 1


def test_the_brief_names_the_project_it_read(home):
    with connection.connect() as conn:
        memories.insert_memory(conn, type="note", content="a fact", domain="acme/x100")
        assert "1 memories in project 'Acme'." in brief.session_brief(conn, project="Acme")
        assert "1 memories in the whole store." in brief.session_brief(conn)
