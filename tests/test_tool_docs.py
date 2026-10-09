"""Tool descriptions and the parameter text their input schemas carry."""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from memai import sections, server

WRITERS = ("note", "checkpoint", "anti_pattern", "reasoning", "task", "diagram")
SHARED = ("title", "domain", "also", "tags", "session", "review_after", "source_ref")
OWN_TEXT = {"task": {"title", "tags"}, "diagram": {"also"}}


def _descriptions() -> dict[str, str]:
    return {t.name: t.description for t in asyncio.run(server.mcp.list_tools())}


def _schemas() -> dict[str, dict]:
    return {t.name: t.input_schema for t in asyncio.run(server.mcp.list_tools())}


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_no_published_description_keeps_a_marker():
    leftovers = [name for name, text in _descriptions().items() if "@param" in text]
    assert leftovers == []


def test_every_entry_is_used_by_some_tool():
    used = {m[1] for name in server._TOOLS
            for m in re.finditer(r"@param (\w+)", inspect.getsource(getattr(server, name)))}
    assert used == set(server.PARAM_DOCS)


def test_every_parameter_is_described():
    bare = {name: sorted(p for p, prop in schema["properties"].items() if not prop.get("description"))
            for name, schema in _schemas().items()}
    assert {name: params for name, params in bare.items() if params} == {}


def test_no_schema_carries_a_generated_title():
    titled = [name for name, schema in _schemas().items()
              if "title" in schema or any("title" in p for p in schema["properties"].values())]
    assert titled == []


def test_a_parameter_paragraph_moves_from_the_description_into_the_schema():
    def probe(foo: str, bar: int = 0) -> dict:
        """Summary.

        foo: what foo holds,
        across two lines.

        bar: a count.

        Tail.
        """
        return {}

    try:
        host = server._Server("probe-host")
        host.tool()(server.tool("_probe", server.READ)(probe))
        [published] = asyncio.run(host.list_tools())
    finally:
        server._GROUP_OF.pop("probe", None)
        server._PARAM_TEXT.pop("probe", None)
    assert published.description == "Summary.\n\nTail."
    assert published.input_schema["properties"]["foo"]["description"] == "what foo holds, across two lines."
    assert published.input_schema["properties"]["bar"]["description"] == "a count."


@pytest.mark.parametrize("name", WRITERS)
def test_a_writer_takes_its_shared_parameter_text_from_param_docs(name):
    props = _schemas()[name]["properties"]
    taken = sorted(set(props) & set(SHARED) - OWN_TEXT.get(name, set()))
    assert taken
    for p in taken:
        assert props[p]["description"] == _flat(server.PARAM_DOCS[p]).removeprefix(f"{p}: "), p


def test_an_entry_takes_the_indentation_of_its_marker():
    doc = 'Summary.\n\n        @param domain\n\n    Tail.\n'
    lines = server._expand_params(doc).splitlines()
    body = server.PARAM_DOCS["domain"].splitlines()
    assert lines[2:2 + len(body)] == ["        " + line for line in body]
    assert lines[-1] == "    Tail."


def test_an_unknown_marker_fails_at_import_time():
    with pytest.raises(KeyError):
        server._expand_params("    @param no_such_parameter\n")


def test_task_note_documents_the_brief_it_takes():
    params = inspect.signature(server.task_note).parameters
    for s in sections.BRIEF_SPEC:
        assert s.key in params and params[s.key].default == ""
        assert f"{s.label}:" in server.task_note.__doc__


def test_task_note_documents_the_depends_on_format():
    text = _schemas()["task_note"]["properties"]["depends_on"]["description"]
    assert "i3 (why), i9" in text and "renumbered" in text


def test_every_tool_docstring_carries_no_indentation():
    for name, fn in server._TOOLS.items():
        assert fn.__doc__ == inspect.cleandoc(fn.__doc__), name


def test_tool_strips_the_indentation_an_interpreter_leaves_in_a_docstring():
    def probe():
        pass

    probe.__doc__ = "Summary.\n\n    Body line.\n        deeper\n    "
    wrapped = server.tool("full", server.READ)(probe)
    assert wrapped.__doc__ == "Summary.\n\nBody line.\n    deeper"
    server._GROUP_OF.pop("probe", None)


def test_anti_pattern_says_when_a_note_or_a_reasoning_fits_instead():
    doc = _flat(_descriptions()["anti_pattern"])
    assert "note()" in doc and "reasoning()" in doc
    assert "See note()" not in doc


def test_anti_pattern_names_the_labels_its_body_reads_back_under():
    doc = _flat(_descriptions()["anti_pattern"])
    labels = [s.label for s in sections.SECTION_SPEC["anti_pattern"]]
    assert " / ".join(labels) in doc


def test_anti_pattern_says_what_it_returns():
    doc = _flat(_descriptions()["anti_pattern"])
    assert "Returns" in doc and "uid" in doc and "similar" in doc


def test_anti_pattern_states_the_pattern_ceiling():
    [pattern] = [s for s in sections.SECTION_SPEC["anti_pattern"] if s.key == "pattern"]
    text = _schemas()["anti_pattern"]["properties"]["pattern"]["description"]
    assert f"{pattern.max_len} characters" in text
