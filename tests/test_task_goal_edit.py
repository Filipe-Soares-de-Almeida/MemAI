"""A task's goal rewritten through edit_memory."""

from __future__ import annotations

import asyncio

import pytest

from memai import server, tasks
from memai.store import connection, memories

ITEMS = ["Draft the grammar", "Write the lexer"]


@pytest.fixture
def uid(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        return tasks.create_task(c, title="Ship the config parser", goal="A parser for the config.",
                                 items=ITEMS, domain="acme/parser")


def _goal(uid):
    with connection.connect() as c:
        return tasks.get_task(c, uid)["goal"]


def test_edit_memory_rewrites_a_task_goal_and_regenerates_its_content(uid):
    out = server.edit_memory(uid, goal="A parser for the config and its includes.", note="scope grew")
    assert out == {"ok": True, "changed": ["goal"]}
    with connection.connect() as c:
        assert "A parser for the config and its includes." in memories.get_memory(c, uid)["content"]
        last = memories.get_edit_history(c, uid)[-1]
        assert "A parser for the config." in last["prev_content"] and last["note"] == "scope grew"


@pytest.mark.parametrize("goal, match", [
    ("Finish i2 first.", "goal cites i2"),
    ("g" * (tasks.GOAL_MAX + 1), "limit"),
], ids=["item-key", "length"])
def test_a_goal_is_held_to_the_task_rules(uid, goal, match):
    out = server.edit_memory(uid, goal=goal)
    assert out["ok"] is False and match in out["errors"][0]
    assert _goal(uid) == "A parser for the config."


def test_a_goal_on_a_memory_that_is_not_a_task_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        fact = memories.insert_memory(c, type="note", content="Lexers are greedy.", domain="acme")
    out = server.edit_memory(fact, goal="Something")
    assert out["ok"] is False and "not a task" in out["errors"][0]


def test_new_content_on_a_task_points_at_the_goal_parameter(uid):
    out = server.edit_memory(uid, new_content="A new body")
    assert out["ok"] is False and "goal=" in out["errors"][0]


def test_edit_memory_and_task_say_where_a_goal_is_rewritten():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert "task" in tools["edit_memory"].input_schema["properties"]["goal"]["description"]
    assert "edit_memory" in tools["task"].input_schema["properties"]["goal"]["description"]
