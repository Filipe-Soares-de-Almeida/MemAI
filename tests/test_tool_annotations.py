"""What each published tool declares it does to the store, as MCP tool annotations."""

from __future__ import annotations

import asyncio

import pytest

from memai import server

HINTS = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}


@pytest.fixture(scope="module")
def published() -> dict[str, dict]:
    """Each published tool's annotations as a client receives them."""
    return {t.name: t.annotations.model_dump(by_alias=True, exclude_none=True) if t.annotations
            else {} for t in asyncio.run(server.mcp.list_tools())}


def test_every_tool_declares_all_four_hints(published):
    missing = {name: sorted(HINTS - set(hints)) for name, hints in published.items()
               if HINTS - set(hints)}
    assert missing == {}


def test_no_tool_reaches_beyond_the_local_store(published):
    assert [name for name, hints in published.items() if hints.get("openWorldHint")] == []


def test_no_read_only_tool_is_destructive(published):
    assert [name for name, hints in published.items()
            if hints.get("readOnlyHint") and hints.get("destructiveHint")] == []


@pytest.mark.parametrize("name, read_only, destructive, idempotent", [
    ("search", True, False, True),
    ("recall", True, False, True),
    ("get_diagram", True, False, True),
    ("note", False, False, False),
    ("forget", False, False, True),
    ("edit_memory", False, True, False),
    ("diagram_relayout", False, True, True),
    ("purge_memory", False, True, True),
])
def test_a_tool_declares_what_it_does(published, name, read_only, destructive, idempotent):
    hints = published[name]
    assert (hints["readOnlyHint"], hints["destructiveHint"], hints["idempotentHint"]) == (
        read_only, destructive, idempotent)


def test_a_tool_cannot_be_registered_without_its_hints():
    with pytest.raises(TypeError):
        server.tool("core")
