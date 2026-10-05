"""Tool descriptions assembled from the shared parameter text in server.PARAM_DOCS."""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from memai import server

WRITERS = ("note", "checkpoint", "anti_pattern", "reasoning", "task", "diagram")
SHARED = ("title", "domain", "also", "tags", "review_after", "source_ref")


def _descriptions() -> dict[str, str]:
    return {t.name: t.description for t in asyncio.run(server.mcp.list_tools())}


def test_no_published_description_keeps_a_marker():
    leftovers = [name for name, text in _descriptions().items() if "@param" in text]
    assert leftovers == []


def test_every_entry_is_used_by_some_tool():
    used = {m[1] for name in server._TOOLS
            for m in re.finditer(r"@param (\w+)", inspect.getsource(getattr(server, name)))}
    assert used == set(server.PARAM_DOCS)


@pytest.mark.parametrize("name", WRITERS)
def test_a_writer_documents_every_shared_parameter_it_takes(name):
    fn = getattr(server, name)
    taken = set(inspect.signature(fn).parameters) & set(SHARED)
    doc = _descriptions()[name]
    missing = [p for p in sorted(taken) if not re.search(rf"`{p}`|^\s*{p}:", doc, re.M)]
    assert missing == []


def test_an_entry_takes_the_indentation_of_its_marker():
    doc = 'Summary.\n\n        @param domain_brief\n\n    Tail.\n'
    lines = server._expand_params(doc).splitlines()
    body = server.PARAM_DOCS["domain_brief"].splitlines()
    assert lines[2:2 + len(body)] == ["        " + line for line in body]
    assert lines[-1] == "    Tail."


def test_an_unknown_marker_fails_at_import_time():
    with pytest.raises(KeyError):
        server._expand_params("    @param no_such_parameter\n")
