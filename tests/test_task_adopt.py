"""Turning memories that only make sense inside a task into that task's notes."""

from __future__ import annotations

import json

import pytest

from memai import db, portable, tasks


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with db.connect() as conn:
        uid = tasks.create_task(conn, title="Ship the parser", goal="g",
                                items=["read the spec", "write the lexer"], domain="acme")
        brief = db.insert_memory(conn, type="note", content="Lexer brief.",
                                 title="Parser P2: lexer", domain="acme")
        playbook = db.insert_memory(conn, type="note", content="Claim first.",
                                    title="Parser: pick-up rules", domain="acme")
        general = db.insert_memory(conn, type="note", content="Regex is greedy.",
                                   title="Regex greed", domain="acme")
        other = db.insert_memory(conn, type="note", content="x", title="x", domain="acme")
        big = db.insert_memory(conn, type="note", content="y" * (tasks.NOTE_MAX + 1),
                               title="Too long", domain="acme")
        db.add_relation(conn, general, other, "relates_to")
        tasks.link_item(conn, uid, "i2", [brief, general, big])
        tasks.link_item(conn, uid, "i1", [playbook])
        tasks.link_item(conn, uid, "i2", [playbook])
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
            "VALUES (?, ?, 'a', 'b', 'item i1: todo -> doing')", (uid, db.now_iso()))
    return {"task": uid, "brief": brief, "playbook": playbook, "general": general,
            "other": other, "big": big}


def test_dry_run_plans_and_writes_nothing(setup):
    out = portable.adopt(setup["task"], [setup["brief"], setup["playbook"]])
    assert out["dry_run"] is True and out["edits_dropped"] == 1
    plan = {p["uid"]: p for p in out["plan"]}
    assert plan[setup["brief"]]["items"] == ["i2"] and plan[setup["brief"]]["level"] == "item"
    assert plan[setup["playbook"]]["level"] == "task"
    with db.connect() as conn:
        assert db.get_memory(conn, setup["brief"]) is not None
        assert conn.execute("SELECT COUNT(*) FROM task_notes").fetchone()[0] == 0


def test_real_run_moves_notes_purges_memories_and_backs_up(setup):
    out = portable.adopt(setup["task"], [setup["brief"], setup["playbook"]], dry_run=False)
    assert out["backup"]
    with db.connect() as conn:
        assert db.get_memory(conn, setup["brief"]) is None
        assert db.get_memory(conn, setup["playbook"]) is None
        assert [n["title"] for n in tasks.notes(conn, setup["task"], "i2")] == ["Parser P2: lexer"]
        assert [n["title"] for n in tasks.notes(conn, setup["task"])] == ["Parser: pick-up rules"]
        assert tasks.notes(conn, setup["task"])[0]["body"] == "Claim first."
        assert db.get_edit_history(conn, setup["task"]) == []
        assert conn.execute("SELECT COUNT(*) FROM task_item_links WHERE target_uid IN (?, ?)",
                            (setup["brief"], setup["playbook"])).fetchone()[0] == 0


def test_memories_that_stand_alone_are_refused(setup):
    out = portable.adopt(setup["task"],
                         [setup["general"], setup["other"], setup["big"], "0000000000000000"],
                         dry_run=False)
    reasons = {r["uid"]: r["reason"] for r in out["refused"]}
    assert reasons[setup["general"]] == "has relations"
    assert reasons[setup["other"]] == "not linked to this task"
    assert reasons["0000000000000000"] == "not linked to this task"
    assert "4000" in reasons[setup["big"]]
    assert out["plan"] == [] and out["backup"] == ""
    with db.connect() as conn:
        assert db.get_memory(conn, setup["general"]) is not None
        assert db.edit_count(conn, setup["task"]) == 1


def test_a_non_task_is_a_value_error(setup):
    with pytest.raises(ValueError):
        portable.adopt(setup["brief"], [setup["playbook"]])


def test_cli_prints_the_plan(setup, capsys):
    assert portable.main(["task-adopt", setup["task"], setup["brief"], "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan"][0]["uid"] == setup["brief"]
