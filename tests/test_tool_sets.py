"""What this process offers, and what it costs to offer it.

A tool's schema is sent with every request for the whole session, so the
full set is a fixed tax on every context window whether or not the session
ever documents a flow or runs a curation pass. MEMAI_TOOLS names the groups
to publish. What must not happen is a tool going quiet: help() documents
every one either way and says which are absent, because an agent told "set
MEMAI_TOOLS" can act, and one that just cannot see purge_memory concludes
memai has no such thing.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys

import pytest

from memai import db


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
                        "inputSchema": t.inputSchema}, ensure_ascii=False))
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
    assert counts == {"full": 37, "core,curation": 31, "core,diagrams": 29, "core": 23}


def test_core_leaves_out_authoring_and_curation(monkeypatch):
    module = _load(monkeypatch, "core")
    names = _published(module)
    assert {"note", "pulse", "search", "timeline", "get_memory", "get_diagram",
            "help"} <= names
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


# ------------------------------------------------------ nothing goes silent

def test_help_documents_a_tool_this_process_did_not_load(monkeypatch):
    module = _load(monkeypatch, "core")
    listing = module.help()
    assert "optimize_scan" in listing["tools"]
    assert "optimize_scan" in listing["not_loaded"]
    assert "MEMAI_TOOLS" in listing["not_loaded_hint"]


def test_help_on_an_absent_tool_still_explains_it(monkeypatch):
    module = _load(monkeypatch, "core")
    entry = module.help(command="purge_memory")
    assert entry["loaded"] is False
    assert "DELETE <uid>" in entry["doc"]


def test_nothing_is_reported_missing_when_nothing_is(monkeypatch):
    module = _load(monkeypatch, None)
    assert "not_loaded" not in module.help()
    assert "loaded" not in module.help(command="purge_memory")


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
    assert core < full * 0.7


def test_the_long_documentation_is_not_in_the_schema(monkeypatch):
    """It is paid for when help() is called, not on every request."""
    module = _load(monkeypatch, None)
    described = {t.name: t.description or "" for t in asyncio.run(module.mcp.list_tools())}
    for name, extra in module._LONG_DOC.items():
        assert extra.strip() not in described[name]
        assert extra.strip() in module.help(command=name)["doc"]


def test_help_names_every_suggestion_kind(monkeypatch):
    """A kind absent from the payload table is a kind nobody stages.

    help() is where a caller reads what optimize_stage accepts, so the table
    there is held to the tuple the validator works from.
    """
    module = _load(monkeypatch, None)
    doc = module.help(command="optimize_stage")["doc"]
    # The table itself, not the prose around it: `review` is a word a
    # paragraph about human review would satisfy on its own.
    table = re.search(r"Kinds and their payload:\n(.+?)\n\n", doc, re.S)
    assert table, "help() no longer documents the payload table"
    missing = [kind for kind in db.SUGGESTION_KINDS
               if not re.search(rf"\b{kind}\b", table.group(1))]
    assert not missing, missing
