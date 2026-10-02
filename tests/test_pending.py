"""The type vocabulary and the reads that refuse a type outside it.

Every test runs against a store under tmp_path (MEMAI_HOME), never the real
~/.memai.
"""

from __future__ import annotations

import pytest

from memai import db, pending, server, tasks

VOCABULARY = "note, reasoning, anti_pattern, checkpoint, handoff, diagram, task"
UNKNOWN = f"unknown type 'pitfall'; valid types: {VOCABULARY}"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def test_type_error_lists_the_vocabulary():
    assert db.type_error("pitfall") == UNKNOWN


def test_type_error_is_none_for_known_and_empty_types():
    assert db.type_error("") is None
    for type_ in db.MEMORY_TYPES:
        assert db.type_error(type_) is None
    assert db.type_error("Task") == f"unknown type 'Task'; valid types: {VOCABULARY}"


@pytest.mark.parametrize("call", [
    lambda: server.search("x", type="pitfall"),
    lambda: server.list_by_domain("acme", type="pitfall"),
    lambda: server.list_recent(type="pitfall"),
    lambda: server.timeline(query="x", type="pitfall"),
])
def test_reads_refuse_an_unknown_type(store, call):
    result = call()
    assert result["errors"] == [UNKNOWN]
    assert "results" not in result


def test_reads_accept_the_new_type(store):
    uid = server.task(title="Ship the parser", goal="Parse every config file",
                      items="read the spec", domain="acme/parser")["uid"]
    assert [r["uid"] for r in server.list_recent(type="task")["results"]] == [uid]


# ------------------------------------------------------------------ pending

DOMAIN = "acme/x100"


def test_existing_handoffs_stay_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with db.connect() as conn:
        uid = db.insert_memory(conn, type="handoff", title="Parser handover",
                               content="The lexer is next.", domain=DOMAIN)
        assert pending.counts(conn, DOMAIN) == [{"type": "handoff", "count": 1}]
    assert server.get_memory(uid)["type"] == "handoff"
    found = server.search("lexer", type="handoff")
    assert [m["uid"] for m in found["results"]] == [uid]
    assert [i["uid"] for i in server.pending(domain=DOMAIN, type="handoff")["items"]] == [uid]


CATEGORY_LIST = "task, anti_pattern, handoff, note, diagram"


def _diagram(domain: str = DOMAIN) -> str:
    return server.diagram(
        title="Nightly export routine", domain=domain,
        nodes=[{"key": "a", "label": "Start", "shape": "start"},
               {"key": "b", "label": "Done", "shape": "end"}],
        edges=[{"from": "a", "to": "b"}])["uid"]


def _task(title: str, domain: str = DOMAIN) -> str:
    return server.task(title=title, goal="Parse every config file",
                       items="read the spec\nwrite the lexer", domain=domain)["uid"]


@pytest.fixture
def seeded(store):
    """One open and one completed task, two anti-patterns (one contradicted),
    one handoff, three notes and one diagram, all under acme/x100."""
    uids = {"open": _task("Ship the parser")}
    done = _task("Retire the legacy lexer")
    server.task_item(done, "i1", state="done")
    server.task_item(done, "i2", state="done")
    uids["done"] = done
    uids["ap_good"] = server.anti_pattern(
        title="Lexing twice", pattern="Lexing the input twice.",
        why_wrong="It doubles the work.", instead="Lex once.", domain=DOMAIN)["uid"]
    bad = server.anti_pattern(
        title="Eager flush", pattern="Flushing on every token.",
        why_wrong="It stalls the pipeline.", instead="Flush per line.", domain=DOMAIN)["uid"]
    server.set_confidence(bad, "contradicted")
    uids["ap_bad"] = bad
    with db.connect() as conn:
        uids["handoff"] = db.insert_memory(
            conn, type="handoff", title="Parser handover",
            content="The lexer is next.", domain=DOMAIN)
    for n in range(3):
        server.note(title=f"Parser fact {n}", content=f"Fact number {n}.", domain=DOMAIN)
    uids["diagram"] = _diagram()
    return uids


FULL = [{"type": "task", "count": 1}, {"type": "anti_pattern", "count": 1},
        {"type": "handoff", "count": 1}, {"type": "note", "count": 3},
        {"type": "diagram", "count": 1}]


def test_counts_follow_the_category_order_and_skip_empty_ones(seeded):
    with db.connect() as conn:
        assert pending.counts(conn) == FULL
        conn.execute("UPDATE memories SET status = 'archived' WHERE uid = ?",
                     (seeded["handoff"],))
        assert [c["type"] for c in pending.counts(conn)] == [
            "task", "anti_pattern", "note", "diagram"]


def test_counts_are_scoped(seeded):
    with db.connect() as conn:
        assert pending.counts(conn, "other") == []
        assert pending.counts(conn, "acme") == FULL


def test_task_headers_carry_progress_and_doing(seeded):
    server.task_item(seeded["open"], "i1", state="doing")
    with db.connect() as conn:
        result = pending.headers(conn, "", "task")
    assert result["type"] == "task" and result["total"] == 1
    item = result["items"][0]
    assert item["uid"] == seeded["open"]
    assert item["progress"] == {"done": 0, "total": 2}
    assert item["doing"] == ["i1"]


def test_tasks_are_ordered_by_their_latest_item_update(store):
    older = _task("Older task")
    newer = _task("Newer task")
    with db.connect() as conn:
        assert [i["uid"] for i in pending.headers(conn, "", "task")["items"]] == [newer, older]
    server.task_item(older, "i1", state="doing")
    with db.connect() as conn:
        assert [i["uid"] for i in pending.headers(conn, "", "task")["items"]] == [older, newer]


def test_headers_exclude_what_is_not_pending(seeded):
    with db.connect() as conn:
        assert [i["uid"] for i in pending.headers(conn, "", "anti_pattern")["items"]] == [
            seeded["ap_good"]]
        assert [i["uid"] for i in pending.headers(conn, "", "task")["items"]] == [
            seeded["open"]]


def test_headers_page(store):
    for n in range(12):
        server.note(title=f"Parser fact {n}", content=f"Fact number {n}.", domain=DOMAIN)
    with db.connect() as conn:
        first = pending.headers(conn, "", "note")
        assert first["total"] == 12 and len(first["items"]) == 10
        assert first["next_offset"] == 10
        last = pending.headers(conn, "", "note", offset=10)
        assert len(last["items"]) == 2 and "next_offset" not in last
        assert len(pending.headers(conn, "", "note", limit=500)["items"]) == 12
        assert len(pending.headers(conn, "", "note", limit=0)["items"]) == 1


def test_headers_are_newest_first(store):
    uids = [server.note(title=f"Parser fact {n}", content=f"Fact {n}.",
                        domain=DOMAIN)["uid"] for n in range(3)]
    with db.connect() as conn:
        assert [i["uid"] for i in pending.headers(conn, "", "note")["items"]] == uids[::-1]


def test_headers_have_no_bodies(seeded):
    with db.connect() as conn:
        for type_ in pending.CATEGORIES:
            for item in pending.headers(conn, "", type_)["items"]:
                assert "content" not in item
                assert {"uid", "title", "domain", "est_tokens"} <= set(item)


def test_pending_tool_without_a_type_lists_the_counts(seeded):
    assert server.pending() == {"categories": FULL}
    assert server.pending(domain="acme/x100") == {"categories": FULL}


def test_pending_tool_with_a_type_lists_headers(seeded):
    result = server.pending(domain="acme", type="note")
    assert result["type"] == "note" and result["total"] == 3 and len(result["items"]) == 3


def test_pending_tool_counts_what_it_returns_as_read(seeded):
    server.pending(type="handoff")
    with db.connect() as conn:
        usage = db.usage_for(conn, [seeded["handoff"], seeded["ap_good"]])
    assert usage[seeded["handoff"]]["recalls"] == 1
    assert seeded["ap_good"] not in usage


def test_pending_tool_refuses_a_type_it_does_not_serve(store):
    result = server.pending(type="checkpoint")
    assert result["errors"] == [
        f"unknown type 'checkpoint'; valid types: {CATEGORY_LIST}"]


def test_pending_on_an_unknown_domain_is_empty_not_an_error(seeded):
    assert server.pending(domain="nowhere/at/all") == {"categories": []}


# -------------------------------------------------------------------- pulse


def test_pulse_returns_counts_not_lists(seeded):
    result = server.pulse(DOMAIN)
    assert set(result) == {"project", "latest_checkpoint", "pending", "read_next", "scope"}
    for gone in ("handoffs", "anti_patterns", "recent_notes", "diagrams"):
        assert gone not in result
    assert result["pending"] == FULL
    assert "not_shown" not in result["scope"]


def test_pulse_still_returns_the_checkpoint_in_full(store):
    body = "Where the parser stands. " * 60
    uid = server.checkpoint(title="Parser bearing", intent="Finish the parser",
                            established=body, pursuing="The lexer",
                            open_questions="None", domain=DOMAIN)["uid"]
    server.link_memories(uid, server.note(title="Lexer fact", content="Lex once.",
                                          domain=DOMAIN)["uid"], "relates_to")
    checkpoint = server.pulse(DOMAIN)["latest_checkpoint"]
    assert checkpoint["uid"] == uid
    assert body.strip() in checkpoint["content"]
    assert "[+" not in checkpoint["content"]
    assert len(checkpoint["relations"]) == 1


def test_pulse_counts_do_not_include_contradicted_rows(seeded):
    by_type = {c["type"]: c["count"] for c in server.pulse(DOMAIN)["pending"]}
    assert by_type["anti_pattern"] == 1


def test_read_next_puts_tasks_first(seeded):
    assert server.pulse(DOMAIN)["read_next"] == (
        "Open tasks first: pending('acme/x100', type='task'), then get_memory(uid) for "
        "each task you will touch. Then pending('acme/x100', type=<t>) for each other "
        "category listed in `pending`, before acting.")


def test_read_next_without_a_domain_omits_the_argument(seeded):
    assert server.pulse()["read_next"] == (
        "Open tasks first: pending(type='task'), then get_memory(uid) for each task "
        "you will touch. Then pending(type=<t>) for each other category listed in "
        "`pending`, before acting.")


def test_read_next_without_tasks_and_when_empty(store):
    assert server.pulse(DOMAIN)["read_next"] == ""
    assert server.pulse(DOMAIN)["pending"] == []
    server.note(title="Parser fact", content="Fact.", domain=DOMAIN)
    assert server.pulse(DOMAIN)["read_next"] == (
        "Then pending('acme/x100', type=<t>) for each other category listed in "
        "`pending`, before acting.")


def test_instructions_name_pending_and_tasks():
    assert "pending(" in server.INSTRUCTIONS
    assert "task(" in server.INSTRUCTIONS
    assert "pulse(domain)" in server.INSTRUCTIONS


# ------------------------------------------- an archived task is never open

def _raw_archive(conn, uid: str) -> None:
    """Archive without touching tasks.state, as a writer that does not know tasks does."""
    conn.execute("UPDATE memories SET status = 'archived' WHERE uid = ?", (uid,))


def test_a_task_archived_without_syncing_its_state_is_not_counted(store):
    uid = _task("Ship the parser")
    with db.connect() as conn:
        assert pending.counts(conn, DOMAIN) == [{"type": "task", "count": 1}]
        _raw_archive(conn, uid)
        assert conn.execute("SELECT state FROM tasks WHERE memory_uid = ?",
                            (uid,)).fetchone()[0] == "open"
        assert pending.counts(conn, DOMAIN) == []
        assert pending.headers(conn, DOMAIN, "task")["items"] == []


# ------------------------------------------- open tasks in a set of domains

def _pier(conn, domain: str, also: str = "") -> str:
    return tasks.create_task(conn, title="Repair the pier", goal="The pier holds",
                             items=["replace the planks"], domain=domain, also=also)


def test_open_task_uids_resolve_each_domain_like_pending(store):
    with db.connect() as conn:
        harbor = _pier(conn, "acme/harbor/pier")
        docks = _pier(conn, "acme/docks")
        _pier(conn, "zeta/other")
        assert set(pending.open_task_uids(conn, ["acme/harbor"])) == {harbor}
        assert set(pending.open_task_uids(conn, ["acme"])) == {harbor, docks}
        assert pending.open_task_uids(conn, ["pier"]) == [harbor]  # the deep end
        assert pending.open_task_uids(conn, ["nowhere"]) == []


def test_a_task_in_two_listed_domains_counts_once(store):
    with db.connect() as conn:
        uid = _pier(conn, "acme/harbor", also="acme/docks")
        assert pending.open_task_uids(conn, ["acme/docks"]) == [uid]
        assert pending.open_task_uids(conn, ["acme/harbor", "acme/docks", "acme"]) == [uid]


def test_a_closed_task_and_a_blank_domain_are_not_counted(store):
    with db.connect() as conn:
        done = _pier(conn, "acme/harbor")
        tasks.set_item_state(conn, done, "i1", "done")
        _pier(conn, "acme/docks")
        assert pending.open_task_uids(conn, ["acme/harbor"]) == []
        assert pending.open_task_uids(conn, ["", "  "]) == []
        assert pending.open_task_uids(conn, []) == []


# ----------------------------------------- scopes, paging and rows without tasks

def test_a_task_cross_listed_into_a_domain_is_counted_there(store):
    uid = server.task(title="Repair the pier", goal="The pier holds", items="replace the planks",
                      domain="acme/harbor", also="acme/docks")["uid"]
    with db.connect() as conn:
        assert pending.counts(conn, "acme/docks") == [{"type": "task", "count": 1}]
        assert [i["uid"] for i in pending.headers(conn, "acme/docks", "task")["items"]] == [uid]
        assert pending.counts(conn, "acme/elsewhere") == []


def test_an_archived_diagram_is_not_counted(store):
    uid = _diagram()
    with db.connect() as conn:
        assert pending.counts(conn, DOMAIN) == [{"type": "diagram", "count": 1}]
        conn.execute("UPDATE memories SET status = 'archived' WHERE uid = ?", (uid,))
        assert pending.counts(conn, DOMAIN) == []
        assert pending.headers(conn, DOMAIN, "diagram")["items"] == []


@pytest.mark.parametrize("kwargs", [{"limit": "many"}, {"offset": "later"}, {"limit": None}])
def test_pending_refuses_a_limit_or_offset_that_is_not_a_number(store, kwargs):
    server.note(title="Parser fact", content="Fact.", domain=DOMAIN)
    result = server.pending(domain=DOMAIN, type="note", **kwargs)
    assert result["ok"] is False
    assert len(result["errors"]) == 1 and "must be a whole number" in result["errors"][0]


def test_pending_accepts_a_limit_and_offset_written_as_digits(store):
    for n in range(3):
        server.note(title=f"Parser fact {n}", content=f"Fact {n}.", domain=DOMAIN)
    result = server.pending(domain=DOMAIN, type="note", limit="2", offset="1")
    assert len(result["items"]) == 2 and result["total"] == 3


@pytest.mark.parametrize("domain", ["/", " / ", "//"])
def test_a_domain_that_normalizes_to_nothing_is_the_whole_project(seeded, domain):
    assert server.pending(domain=domain) == {"categories": FULL}
    assert server.pending(domain=domain, type="note")["total"] == 3


def test_open_task_uids_skip_a_domain_that_normalizes_to_nothing(store):
    with db.connect() as conn:
        _pier(conn, "acme/harbor")
        assert pending.open_task_uids(conn, ["/", " / "]) == []


def test_list_by_domain_lists_a_task_memory_that_has_no_tasks_row(store):
    with db.connect() as conn:
        orphan = db.insert_memory(conn, type="task", title="Orphaned task",
                                  content="GOAL: nothing", domain=DOMAIN)
    good = _task("Ship the parser")
    found = {r["uid"]: r for r in server.list_by_domain(DOMAIN, type="task")["results"]}
    assert set(found) == {orphan, good}
    assert "state" not in found[orphan] and "progress" not in found[orphan]
    assert found[good]["state"] == "open" and found[good]["progress"] == {"done": 0, "total": 2}
