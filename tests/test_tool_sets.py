"""What this process offers, and what it costs to offer it.

A tool's schema is sent with every request for the whole session, so the
full set is a fixed tax on every context window whether or not the session
ever documents a flow or runs a curation pass. MEMAI_TOOLS names the groups
to publish; core is always in.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import pathlib
import re
import sys

import pytest

from memai.store import optimizer


def _load(monkeypatch, setting: str | None):
    """Re-import the server with a given MEMAI_TOOLS, isolated per test."""
    if setting is None:
        monkeypatch.delenv("MEMAI_TOOLS", raising=False)
    else:
        monkeypatch.setenv("MEMAI_TOOLS", setting)
    sys.modules.pop("memai.server", None)
    module = importlib.import_module("memai.server")
    monkeypatch.setattr(sys.modules["memai"], "server", module, raising=False)
    return module


@pytest.fixture(autouse=True)
def _restore_server():
    """Leave the module registered as the rest of the suite expects it."""
    yield
    sys.modules.pop("memai.server", None)
    importlib.import_module("memai.server")


def _published(module) -> set[str]:
    return {t.name for t in asyncio.run(module.mcp.list_tools())}


def _schema_chars(module) -> int:
    return sum(
        len(json.dumps({"name": t.name, "description": t.description,
                        "inputSchema": t.input_schema}, ensure_ascii=False))
        for t in asyncio.run(module.mcp.list_tools()))


# ------------------------------------------------------------- what is offered

def test_the_default_offers_everything(monkeypatch):
    """Dropping a tool an existing setup calls is not done quietly."""
    module = _load(monkeypatch, None)
    assert _published(module) == set(module._TOOLS)


def test_the_published_counts_are_the_ones_documented(monkeypatch):
    """The four counts the wiki's MEMAI_TOOLS table quotes."""
    counts = {setting or "full": len(_published(_load(monkeypatch, setting)))
              for setting in (None, "core,curation", "core,diagrams", "core")}
    assert counts == {"full": 43, "core,curation": 37, "core,diagrams": 34, "core": 28}


def test_handoff_is_not_published(monkeypatch):
    module = _load(monkeypatch, None)
    assert "handoff" not in module._TOOLS
    assert "handoff" not in _published(module)
    assert "handoff" not in module._GROUP_OF


def test_core_leaves_out_authoring_and_curation(monkeypatch):
    module = _load(monkeypatch, "core")
    names = _published(module)
    assert {"note", "pulse", "search", "timeline", "get_memory", "get_diagram",
            "task", "task_item", "task_add", "task_comment", "must_read"} <= names
    assert not names & {"diagram", "diagram_node", "optimize_scan", "purge_memory"}


def test_a_group_can_be_added_back(monkeypatch):
    module = _load(monkeypatch, "core,diagrams")
    names = _published(module)
    assert "diagram_node" in names and "optimize_scan" not in names


def test_core_is_implied_by_any_group(monkeypatch):
    """Nobody wants curation tools and no way to read a memory."""
    module = _load(monkeypatch, "curation")
    assert {"pulse", "search", "optimize_scan"} <= _published(module)


def test_an_unknown_group_is_just_core(monkeypatch):
    module = _load(monkeypatch, "nonsense")
    assert "pulse" in _published(module) and "optimize_scan" not in _published(module)


def test_every_tool_belongs_to_a_group(monkeypatch):
    module = _load(monkeypatch, None)
    assert set(module._GROUP_OF) == set(module._TOOLS)
    assert set(module._GROUP_OF.values()) <= set(module.TOOL_SETS)


# ---------------------------------------------------------- off the schema

def test_an_unpublished_tool_is_still_callable(monkeypatch, tmp_path):
    """The decorator returns the plain function either way -- the admin
    surface and the tests reach these names directly."""
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    module = _load(monkeypatch, "core")
    assert module.get_domain_case() == {"mode": "preserve"}


# ------------------------------------------------------------------- the cost

def test_core_costs_meaningfully_less_per_request(monkeypatch):
    full = _schema_chars(_load(monkeypatch, None))
    core = _schema_chars(_load(monkeypatch, "core"))
    assert core < full * 0.75


def test_the_skill_names_every_suggestion_kind():
    """A kind absent from the payload table is a kind nobody stages.

    The maintenance skill is where a caller reads what optimize_stage
    accepts, so its table is held to the tuple the validator works from.
    """
    skill = (pathlib.Path(__file__).parents[1] / "src" / "memai" / "skills"
             / "memai-maintenance" / "SKILL.md").read_text(encoding="utf-8")
    table = re.search(r"## 1\. The \w+ suggestion kinds.*?\n\n(.+?)\n\n", skill, re.S)
    assert table, "the maintenance skill carries no payload table"
    missing = [kind for kind in optimizer.SUGGESTION_KINDS
               if not re.search(rf"`{kind}`", table.group(1))]
    assert not missing, missing
