"""Carrying memories from one project to another.

A move is an export with its edit history, an import into the target, a
check that every memory landed, and only then the purge of the originals --
with a backup of the source written first. What crosses the edge of the
slice is reported before anything moves, since the copy cannot carry it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memai import admin, db, portable, server


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    db.create_project("acme")
    return tmp_path


@pytest.fixture
def client(home):
    with TestClient(admin.app) as c:
        yield c


NODES = [{"key": "start", "shape": "start", "label": "begin the drain"},
         {"key": "done", "shape": "end", "label": "drained"}]
EDGES = [{"from": "start", "to": "done"}]


def _seed(conn) -> dict:
    """A slice under acme/x100 holding everything a move has to carry, and one
    memory outside it that the slice is related to."""
    ids = {
        "note": db.insert_memory(conn, type="note", domain="acme/x100", tags="queue, drain",
                                 title="Queue drain waits for the retry",
                                 content="the drain waits for the batch retry to settle",
                                 also="omni/x900"),
        "old": db.insert_memory(conn, type="note", domain="acme/x100/p200",
                                content="the drain used to run at once"),
        "outside": db.insert_memory(conn, type="note", domain="zeta/x100",
                                    content="token refresh happens on the hour"),
    }
    db.update_memory_content(conn, ids["note"],
                             "the drain waits for the retry to settle; measured", note="tightened")
    db.set_status(conn, ids["old"], "archived", superseded_by=ids["note"], note="superseded")
    db.add_relation(conn, ids["note"], ids["old"], "supersedes", "same fact, corrected")
    db.add_relation(conn, ids["note"], ids["outside"], "relates_to", "crosses the slice")
    ids["flow"], _ = db.insert_diagram(conn, title="Queue drain", domain="acme/x100",
                                       nodes=NODES, edges=EDGES, summary="how it drains")
    db.add_node_link(conn, ids["flow"], "start", ids["note"], "explains")
    db.record_recall(conn, [ids["note"]])
    return ids


def _count(store: str, sql: str = "SELECT COUNT(*) FROM memories") -> int:
    with db.connect(project=store) as conn:
        return conn.execute(sql).fetchone()[0]


# ── the report ──────────────────────────────────────────────────────────

def test_a_dry_run_reports_the_slice_and_moves_nothing(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
        edits = conn.execute(
            "SELECT COUNT(*) FROM edits WHERE memory_uid IN (?, ?, ?)",
            (ids["note"], ids["old"], ids["flow"])).fetchone()[0]
    plan = portable.move("General", "acme", domain="acme/x100")
    assert plan["dry_run"] is True
    assert (plan["memories"], plan["diagrams"], plan["relations"], plan["edits"]) == (3, 1, 1, edits)
    assert plan["conflicts"] == [] and plan["unknown"] == [] and plan["creates"] is False
    assert plan["outside"]["relations"]["count"] == 1
    assert plan["outside"]["relations"]["items"][0]["to_uid"] == ids["outside"]
    assert _count("General") == 4 and _count("acme") == 0
    assert not list(db.backups_dir().glob("*.db"))


def test_the_boundary_report_names_every_kind_of_crossing(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
        other, _ = db.insert_diagram(conn, title="Token refresh", domain="zeta/x100",
                                     nodes=NODES, edges=EDGES)
        db.add_diagram_jump(conn, ids["flow"], "done", other)
        db.update_memory_content(conn, ids["note"], f"see [[{ids['outside']}]] for the refresh")
        db.update_memory_content(conn, ids["outside"], f"see [[{ids['note']}]] for the drain")
        # a name nothing resolves is dangling already, and stays out of the report
        db.update_memory_content(conn, ids["old"], "see [[0123456789abcdef]] for nothing")
        report = portable.boundary(conn, [ids["note"], ids["flow"]])
    assert report["relations"]["count"] == 2            # note->old and note->outside
    assert report["diagram_links"]["count"] == 0        # flow->note stays inside
    assert report["diagram_jumps"]["count"] == 1
    assert report["diagram_jumps"]["items"][0]["to_uid"] == other
    assert report["superseded_by"]["count"] == 1        # old, outside, superseded by note
    assert {(b["uid"], b["target_uid"]) for b in report["body_links"]["items"]} == {
        (ids["note"], ids["outside"]), (ids["outside"], ids["note"])}


def test_moving_by_uid_leaves_out_what_the_store_does_not_hold(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
    plan = portable.move("General", "acme", uids=[ids["outside"], "0123456789abcdef"])
    assert plan["memories"] == 1 and plan["unknown"] == ["0123456789abcdef"]


def test_source_and_target_have_to_differ_and_the_target_has_to_exist(home):
    with pytest.raises(ValueError, match="same project"):
        portable.move("General", "general", domain="acme")
    with pytest.raises(ValueError, match="no project named"):
        portable.move("General", "zeta", domain="acme")
    with pytest.raises(ValueError, match="name what to move"):
        portable.move("General", "acme")


# ── the move ────────────────────────────────────────────────────────────

def test_a_move_carries_everything_and_then_removes_the_originals(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
        edits = conn.execute(
            "SELECT COUNT(*) FROM edits WHERE memory_uid IN (?, ?, ?)",
            (ids["note"], ids["old"], ids["flow"])).fetchone()[0]
    result = portable.move("General", "acme", domain="acme/x100", dry_run=False)
    assert result["moved"] == 3 and result["errors"] == []
    assert Path(result["backup"]).name.startswith("General-move-")
    assert Path(result["backup"]).exists()

    with db.connect(project="acme") as dst:
        note = db.get_memory(dst, ids["note"])
        assert note["content"].endswith("measured") and note["domain"] == "acme/x100"
        assert db.get_domain_links(dst, ids["note"]) == ["omni/x900"]
        old = db.get_memory(dst, ids["old"])
        assert old["status"] == "archived" and old["superseded_by"] == ids["note"]
        assert [r["to_uid"] for r in db.get_relations(dst, ids["note"])] == [ids["old"]]
        assert dst.execute("SELECT COUNT(*) FROM edits").fetchone()[0] == edits
        assert [n["key"] for n in db.get_diagram(dst, ids["flow"])["nodes"]] == ["start", "done"]
        assert [l["target_uid"] for l in db.get_node_links(dst, ids["flow"])] == [ids["note"]]
        assert db.usage_for(dst, [ids["note"]])[ids["note"]]["recalls"] == 1
        assert ids["note"] in {r["uid"] for r in db.search_memories(dst, "drain retry settle")}

    with db.connect(project="General") as src:
        assert [r["uid"] for r in src.execute("SELECT uid FROM memories")] == [ids["outside"]]
        assert src.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0
        assert src.execute("SELECT COUNT(*) FROM edits").fetchone()[0] == 0
        assert src.execute("SELECT COUNT(*) FROM diagram_nodes").fetchone()[0] == 0


def test_the_backup_holds_the_source_as_it_stood_before(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
    result = portable.move("General", "acme", uids=[ids["note"]], dry_run=False)
    with db.connect(Path(result["backup"])) as copy:
        assert db.get_memory(copy, ids["note"]) is not None
    with db.connect(project="General") as src:
        assert db.get_memory(src, ids["note"]) is None


def test_a_uid_the_target_already_holds_stays_in_both_places(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
    with db.connect(project="acme") as dst:
        db.restore_memory(dst, {"uid": ids["note"], "type": "note",
                                "content": "the copy acme already has"})
    result = portable.move("General", "acme", uids=[ids["note"], ids["old"]], dry_run=False)
    assert result["conflicts"] == [ids["note"]] and result["moved"] == 1
    with db.connect(project="acme") as dst:
        assert db.get_memory(dst, ids["note"])["content"] == "the copy acme already has"
    with db.connect(project="General") as src:
        assert db.get_memory(src, ids["note"]) is not None
        assert db.get_memory(src, ids["old"]) is None


def test_create_makes_the_target_on_the_real_run_only(home):
    with db.connect(project="General") as conn:
        _seed(conn)
    plan = portable.move("General", "zeta", domain="acme/x100", create=True)
    assert plan["creates"] is True and not db.project_exists("zeta")
    result = portable.move("General", "zeta", domain="acme/x100", create=True, dry_run=False)
    assert result["moved"] == 3 and db.project_exists("zeta")
    assert _count("zeta") == 3


def test_nothing_to_move_takes_no_backup(home):
    result = portable.move("General", "acme", domain="nowhere", dry_run=False)
    assert result["moved"] == 0 and result["backup"] == ""
    assert not list(db.backups_dir().glob("*.db"))


def test_two_moves_in_one_second_get_two_backups(home, monkeypatch):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
    monkeypatch.setattr(db, "backup_name", lambda store, kind="": f"{store}-{kind}-stamp.db")
    first = portable.move("General", "acme", uids=[ids["note"]], dry_run=False)
    second = portable.move("General", "acme", uids=[ids["outside"]], dry_run=False)
    assert Path(first["backup"]).name == "General-move-stamp.db"
    assert Path(second["backup"]).name == "General-move-stamp-2.db"


# ── the edit history in the format ──────────────────────────────────────

def test_the_edit_history_travels_only_when_asked_for(home):
    with db.connect(project="General") as conn:
        ids = _seed(conn)
        plain = [r["record"] for r in portable.export_records(conn, uids=[ids["note"]])]
        full = [r["record"] for r in portable.export_records(
            conn, uids=[ids["note"]], include_edits=True)]
    assert "edit" not in plain
    assert full.count("edit") == 1 and full.index("edit") > full.index("memory")


def test_an_imported_history_belongs_to_the_row_the_import_added(home):
    """A memory already in the target keeps the history it has."""
    with db.connect(project="General") as conn:
        ids = _seed(conn)
        records = list(portable.export_records(
            conn, uids=[ids["note"], ids["old"]], include_archived=True, include_edits=True))
    with db.connect(project="acme") as dst:
        db.restore_memory(dst, {"uid": ids["note"], "type": "note", "content": "already here"})
        result = portable.import_records(dst, records)
        assert result["added"] == 1 and result["skipped"] == 1
        per_uid = dict(dst.execute(
            "SELECT memory_uid, COUNT(*) FROM edits GROUP BY memory_uid").fetchall())
        assert ids["note"] not in per_uid
        assert result["edits"] == per_uid.get(ids["old"], 0) == 1


# ── the dashboard, the MCP tool and the CLI ────────────────────────────

def test_the_dashboard_moves_a_selection_after_a_dry_run(client, home):
    with db.connect() as conn:
        ids = _seed(conn)
    plan = client.post("/api/projects/move", json={"target": "acme", "uids": [ids["note"]]}).json()
    assert plan["dry_run"] is True and plan["memories"] == 1
    assert _count("acme") == 0
    done = client.post("/api/projects/move",
                       json={"target": "acme", "uids": [ids["note"]], "dry_run": False}).json()
    assert done["moved"] == 1
    assert _count("acme") == 1 and _count("General") == 3
    same = client.post("/api/projects/move", json={"target": "General", "uids": [ids["old"]]})
    assert same.status_code == 400


def test_the_mcp_tool_moves_out_of_the_active_project(home):
    with db.connect() as conn:
        ids = _seed(conn)
    plan = server.move_to_project("acme", uids=f"{ids['note']}, {ids['old']}")
    assert plan["dry_run"] is True and plan["source"] == "General" and plan["memories"] == 2
    result = server.move_to_project("acme", domain="acme/x100", dry_run=False)
    assert result["moved"] == 3
    # active rows: the archived `old` is not one of them
    assert {s["name"]: s["memories"] for s in server.list_projects()["projects"]} == {
        "General": 1, "acme": 2}


def test_the_cli_moves_too(home, capsys):
    with db.connect() as conn:
        _seed(conn)
    assert portable.main(["move", "--to", "acme", "--domain", "acme/x100", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True and plan["memories"] == 3
    assert portable.main(["move", "--to", "omni", "--domain", "acme/x100", "--create"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["moved"] == 3 and _count("omni") == 3
