"""Every MCP tool and hook event stays inside its ceiling on a store filled to every limit.

A tool or hook event without a scenario here fails: a new surface proves its budget before
it ships.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from memai import budget, hook, sections, server, tasks
from memai.store import connection, memories, relations, search

FILLER = "Lorem ipsum dolor sit amet, acao util e rapida. "
MEMORIES = 600
DOMAINS = 300
NODES = 400


def _text(n: int, salt: str = "") -> str:
    base = salt + FILLER
    return (base * (n // len(base) + 1))[:n]


def _memory(conn, n: int, domain: str) -> str:
    return memories.insert_memory(conn, type="note", content=_text(1800, f"m{n} "),
                            title=_text(120, f"m{n} "), domain=domain,
                            tags=", ".join(f"tag{n}-{k}" for k in range(25)))


@pytest.fixture(scope="module")
def worst(tmp_path_factory):
    home = tmp_path_factory.mktemp("worst")
    mp = pytest.MonkeyPatch()
    mp.setenv("MEMAI_HOME", str(home))
    ids: dict = {}
    with connection.connect() as conn:
        domains = [f"acme/m{d:03d}/p{d % 7}" for d in range(DOMAINS)]
        uids = [_memory(conn, n, domains[n % DOMAINS]) for n in range(MEMORIES)]
        ids["note"], ids["spare"] = uids[0], uids[-1]
        for n in range(1, 120):
            relations.add_relation(conn, uids[0], uids[n], "relates_to")
        for n in range(60):
            memories.update_memory_content(conn, uids[1], _text(20_000, f"e{n} "), note=f"edit {n}")
        memories.update_memory_content(conn, uids[2], _text(120_000, "long "), note="long body")
        ids["edited"], ids["long"] = uids[1], uids[2]
        task = tasks.create_task(conn, title=_text(120), goal=_text(tasks.GOAL_MAX),
                                 items=[_text(tasks.ITEM_MAX, f"{n} ") for n in range(tasks.ITEMS_MAX)],
                                 domain="acme/m000")
        keys = [f"i{n}" for n in range(1, tasks.ITEMS_MAX + 1)]
        for n in range(300):
            tasks.add_note(conn, task, title=_text(120, f"n{n} "), body=_text(tasks.NOTE_MAX),
                           items=[] if n % 5 == 0 else [keys[n % 50], keys[(n + 1) % 50]])
            tasks.add_comment(conn, task, _text(tasks.COMMENT_MAX, f"c{n} "),
                              item="" if n % 2 else keys[n % 50])
        for key in keys[:5]:
            tasks.link_item(conn, task, key, uids[3:203])
        ids["task"] = task
        for n in range(40):
            memories.insert_memory(conn, type="note", content=_text(1500, f"dup{n % 3} "),
                             title=_text(120, f"d{n} "), domain="acme/dups")
        for n in range(40):
            tasks.create_task(conn, title=_text(120, f"t{n} "), goal=_text(500), items=["a", "b"],
                              domain=domains[n])
    nodes = [{"key": f"n{k}", "label": _text(120, f"n{k} "), "note": _text(400),
              "shape": "start" if k == 0 else "end" if k == NODES - 1 else "step"}
             for k in range(NODES)]
    edges = [{"from": f"n{k}", "to": f"n{k + 1}", "label": _text(60)} for k in range(NODES - 1)]
    made = server.diagram(_text(120), nodes, edges, summary=_text(1000), domain="acme/m001")
    ids["diagram"] = made["uid"]
    for k in range(50):
        server.diagram_link(made["uid"], f"n{k}", uids[10 + k])
    staged = server.optimize_stage([
        {"kind": "retag", "target_uid": u, "payload": {"tags": _text(200)}, "rationale": _text(250)}
        for u in uids[300:500]], note="worst")
    ids["run"] = staged.get("run_id", 0)
    for n in range(120):
        server.optimize_stage([{"kind": "retag", "target_uid": uids[500 + n % 50],
                                "payload": {"tags": "x"}, "rationale": "r"}], note=_text(200))
    yield ids
    mp.undo()


def _walk(call) -> list:
    pages, offset = [], 0
    while offset is not None:
        page = call(offset)
        pages.append(page)
        offset = page.get("next_offset") if isinstance(page, dict) else None
    return pages


def _parts(w):
    calls = [("items", ""), ("notes", ""), ("notes", "i1"), ("comments", ""),
             ("comments", "i2"), ("links", "i3")]
    return [p for part, item in calls
            for p in _walk(lambda o, part=part, item=item: server.task_read(w["task"], part, item, o))]


def _sections(type_: str) -> list[str]:
    """Each field of a sectioned body at its own ceiling, or 6000 characters where it has none."""
    return [_text(f.max_len or 6000, f"{f.key} ") for f in sections.SECTION_SPEC[type_]]


QUERY = "lorem ipsum dolor sit amet acao util rapida"

SCENARIOS = {
    "also_domain": lambda w: [server.also_domain(w["spare"], "acme/m002")],
    "anti_pattern": lambda w: [server.anti_pattern(_text(120), *_sections("anti_pattern"),
                                                   domain="acme/m003")],
    "checkpoint": lambda w: [server.checkpoint(_text(120), *_sections("checkpoint"),
                                               domain="acme/m004")],
    "diagram": lambda w: [server.diagram(_text(120), [{"key": "a", "label": "a", "shape": "start"},
                                                      {"key": "b", "label": "b", "shape": "end"}],
                                         [{"from": "a", "to": "b"}], domain="acme/m005")],
    "diagram_edge": lambda w: [server.diagram_edge(w["diagram"], "n0", "n5", label="x")],
    "diagram_jump": lambda w: [server.diagram_jump(w["diagram"], "n1", w["diagram"], "n9")],
    "diagram_link": lambda w: [server.diagram_link(w["diagram"], "n60", w["spare"])],
    "diagram_node": lambda w: [server.diagram_node(w["diagram"], "n399", label=_text(120))],
    "diagram_relayout": lambda w: [server.diagram_relayout(w["diagram"])],
    "dedup_scan": lambda w: [server.dedup_scan(threshold=0.0, limit=500)],
    "edit_memory": lambda w: [server.edit_memory(w["spare"], _text(1800, "edited "), note="x")],
    "forget": lambda w: [server.forget(server.note(_text(120), _text(100))["uid"], reason="x")],
    "get_diagram": lambda w: [server.get_diagram(w["diagram"], format=f)
                              for f in ("mermaid", "text", "json", "svg", "svg-interactive")],
    "get_domain_case": lambda w: [server.get_domain_case()],
    "get_memory": lambda w: [server.get_memory(w["task"]), server.get_memory(w["note"]),
                             server.get_memory(w["diagram"]), server.get_memory(w["long"]),
                             server.get_memory(w["edited"]),
                             *_walk(lambda o: server.get_memory(w["edited"], edits_offset=o)),
                             *_walk(lambda o: server.get_memory(w["long"], content_offset=o))],
    "get_relations": lambda w: [server.get_relations(w["note"])],
    "link_memories": lambda w: [server.link_memories(w["spare"], w["note"], "relates_to")],
    "list_by_domain": lambda w: [server.list_by_domain("acme", limit=5000)],
    "list_domains": lambda w: [server.list_domains()],
    "list_projects": lambda w: [server.list_projects()],
    "list_recent": lambda w: [server.list_recent(limit=5000)],
    "move_to_project": lambda w: [server.move_to_project("other", domain="acme", dry_run=True,
                                                         create=True)],
    "must_read": lambda w: [server.must_read(type=t, limit=50)
                            for t in ("task", "anti_pattern", "note", "diagram")],
    "note": lambda w: [server.note(_text(120), _text(1800), domain="acme/m006")],
    "optimize_runs": lambda w: _walk(lambda o: server.optimize_runs(offset=o)),
    "optimize_scan": lambda w: [server.optimize_scan(limit=5000, full=True),
                                server.optimize_scan(domain="acme/m002", limit=5000, full=True)],
    "optimize_stage": lambda w: [server.optimize_stage(
        [{"kind": "retag", "target_uid": w["spare"], "payload": {"tags": _text(200)},
          "rationale": _text(250)}], note=_text(200))],
    "optimize_status": lambda w: [server.optimize_status(w["run"])],
    "pulse": lambda w: [server.pulse("acme"), server.pulse("")],
    "purge_memory": lambda w: [server.purge_memory(w["spare"], "wrong phrase")],
    "reasoning": lambda w: [server.reasoning(_text(120), *_sections("reasoning"),
                                             domain="acme/m007")],
    "recall": lambda w: [server.recall(QUERY, limit=5000)],
    "search": lambda w: [server.search(QUERY, limit=5000)],
    "set_confidence": lambda w: [server.set_confidence(w["spare"], "confirmed")],
    "set_domain_case": lambda w: [server.set_domain_case(server.get_domain_case().get("mode",
                                                                                      "preserve"))],
    "task": lambda w: [server.task(_text(120), _text(tasks.GOAL_MAX),
                                   "\n".join(_text(tasks.ITEM_MAX, f"{n} ") for n in range(50)))],
    "task_add": lambda w: [server.task_add(server.task("t", "g", "a")["uid"],
                                           "\n".join(_text(tasks.ITEM_MAX) for _ in range(49)))],
    "task_comment": lambda w: [server.task_comment(w["task"], _text(tasks.COMMENT_MAX))],
    "task_item": lambda w: [server.task_item(w["task"], "i4", state="doing",
                                             comment=_text(tasks.COMMENT_MAX))],
    "task_note": lambda w: [server.task_note(w["task"], title=_text(120), body=_text(4000),
                                             items=",".join(f"i{n}" for n in range(1, 51)))],
    "task_read": _parts,
    "timeline": lambda w: [server.timeline(w["note"], before=5000, after=5000),
                           server.timeline(query=QUERY, before=5000, after=5000)],
    "unfile_domain": lambda w: [server.unfile_domain(w["spare"], "acme/m002")],
}


def test_every_tool_has_a_scenario():
    missing = sorted(set(server._GROUP_OF) - set(SCENARIOS))
    assert not missing, f"tools without a budget scenario: {missing}"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_tool_fits_the_result_ceiling(worst, name):
    for result in SCENARIOS[name](worst):
        size = budget.result_chars(result)
        assert size <= budget.MCP_RESULT_MAX_CHARS, f"{name}: {size} characters"
        errors = " ".join(result.get("errors", [])) if isinstance(result, dict) else ""
        assert "budget" not in errors, f"{name}: {errors[:200]}"


HOOK_SCENARIOS = {
    "session-start": {"session_id": "s1", "cwd": ".", "source": "startup"},
    "pre-compact": {"session_id": "s1"},
    "stop": {"session_id": "s1", "stop_hook_active": False},
    "statusline": {"session_id": "s1"},
}


def test_every_hook_event_has_a_scenario():
    assert set(hook._EVENTS) == set(HOOK_SCENARIOS)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)


@pytest.mark.parametrize("event", sorted(HOOK_SCENARIOS))
def test_hook_fits_its_ceiling(worst, event, monkeypatch, capsysbinary):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(HOOK_SCENARIOS[event])))
    hook.main([event])
    raw = capsysbinary.readouterr().out.decode("utf-8")
    if not raw.strip():
        return
    try:
        fields = json.loads(raw)
    except json.JSONDecodeError:
        assert len(raw) <= budget.HOOK_MAX_CHARS
        return
    for value in _strings(fields):
        assert len(value) <= budget.HOOK_MAX_CHARS + 200


def test_search_pages_cover_every_hit_once(worst):
    seen = []
    for page in _walk(lambda o: server.search(QUERY, limit=400, offset=o)):
        assert budget.result_chars(page) <= budget.PAGE_MAX_CHARS + 2000
        seen += [r["uid"] for r in page["results"]]
    with connection.connect() as conn:
        ranked = [r["uid"] for r in search.search_ranked(conn, QUERY, limit=400, collapse=True)]
    assert seen == ranked and len(set(seen)) == len(seen)




WALKS = {
    "list_domains": (lambda w, o: server.list_domains(offset=o), "domains", "domain"),
    "get_relations": (lambda w, o: server.get_relations(w["note"], offset=o), "records", "id"),
    "dedup_scan": (lambda w, o: server.dedup_scan(domain="acme/dups", threshold=0.0, limit=200, offset=o),
                   "pairs", None),
    "optimize_status": (lambda w, o: server.optimize_status(w["run"], offset=o), "suggestions", "id"),
    "optimize_runs": (lambda w, o: server.optimize_runs(offset=o), "runs", "id"),
    "task_read": (lambda w, o: server.task_read(w["task"], "notes", "i2", o), "records", "id"),
}


@pytest.mark.parametrize("name", sorted(WALKS))
def test_paged_tools_return_every_record_once(worst, name):
    call, key, ident = WALKS[name]
    pages = _walk(lambda o: call(worst, o))
    rows = [r for p in pages for r in p[key]]
    assert len(pages) > 1 or "next_offset" not in pages[0]
    assert len(rows) == pages[0]["total"]
    if ident:
        assert len({r[ident] for r in rows}) == len(rows)
    for page in pages:
        assert budget.result_chars(page) <= budget.PAGE_MAX_CHARS + 2000
