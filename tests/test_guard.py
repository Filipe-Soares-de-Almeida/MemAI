"""Refusing a write whose text never arrived.

The guard is the one hook that can stop the session it is attached to, so
these tests weigh the two failure directions against each other. A refusal
that should not have happened costs a session its memory; a refusal that
does not happen costs a memory its content, with no error anywhere to say
so. Everything the guard is unsure about therefore goes through, and what
it does refuse it refuses on a table read from the tools themselves.

The second refusal is for the shape of the typo that arrives: a parameter
holding the rest of the call as its text. That one is refused twice -- by the
hook, and by the store on the way in -- so both are exercised here, beside
the prose that quotes a tag on purpose and has to keep writing.
"""

from __future__ import annotations

import inspect
import io
import json
import re

import pytest
from starlette.testclient import TestClient

from memai import admin, db, guard, hook, hook_install, server, tasks, warden


# What a leaked call looks like once it is one parameter's text: the closing
# tag of the field it was written under, and the fields after it as prose.
LEAKED = ("a cache warmup runs twice on a cold queue</content>\n"
          "<domain>acme/x100/p200</domain>\n"
          "<tags>cache warmup, queue drain</tags>")

# The prefixed form of a closing tag, spelled in pieces. A literal one does
# not survive being typed: the parser it belongs to reads it.
PREFIXED = "</" + "antml:parameter>"


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _run(payload, monkeypatch, capsys) -> tuple[int, str, str]:
    """Drive `memai-hook guard` the way a host does."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    code = hook.main(["guard"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _call(tool: str, **params) -> dict:
    return {"tool_name": f"mcp__MemAI__{tool}", "tool_input": params}


def _full(tool: str) -> dict:
    """Every parameter the guard looks at, filled in."""
    fields = guard.GUARDED[tool] + guard.WATCHED.get(tool, ())
    return {name: f"the {name}" for name in fields}


# ------------------------------------------------- the table, and its source

@pytest.mark.parametrize("tool", sorted(guard.GUARDED))
def test_the_guarded_fields_are_what_the_tool_actually_requires(tool):
    """Read from the signature, not from what the siblings take.

    A table that over-reaches refuses correct calls, which is how a guard
    gets removed; one that under-reaches lets the defect through silently.
    """
    parameters = inspect.signature(getattr(server, tool)).parameters
    required = tuple(name for name, p in parameters.items()
                     if p.default is inspect.Parameter.empty)
    assert guard.GUARDED[tool] == required


@pytest.mark.parametrize("tool", sorted(guard.WATCHED))
def test_a_watched_field_is_one_its_tool_takes_and_does_not_require(tool):
    parameters = inspect.signature(getattr(server, tool)).parameters
    for name in guard.WATCHED[tool]:
        assert name in parameters
        assert parameters[name].default is not inspect.Parameter.empty


@pytest.mark.parametrize("tool", sorted(guard.OPTIONAL))
def test_the_optional_fields_are_the_rest_of_the_signature(tool):
    """The two tables together are the tool's parameter list, in its order.

    The closing tags a leak carries are read from that list, so a name
    missing here is a mark nothing looks for.
    """
    parameters = tuple(inspect.signature(getattr(server, tool)).parameters)
    assert guard.fields(tool) == parameters


@pytest.mark.parametrize("tool", sorted(guard.WATCHED))
def test_every_watched_field_is_one_of_the_optional_ones(tool):
    assert set(guard.WATCHED[tool]) <= set(guard.OPTIONAL[tool])


def test_a_tool_with_no_table_is_read_against_the_frame_alone():
    assert guard.fields("forget") == ()
    assert guard.leak_marks("forget", "a body with </content> in it") == []
    assert guard.leak_marks("forget", "a body with </invoke> in it") == ["</invoke>"]


def test_the_matcher_selects_every_memai_tool_and_no_other():
    pattern = re.compile(guard.matcher())
    for name in ("pulse", "note", "search", "forget", "task_item"):
        assert pattern.fullmatch(f"mcp__memai__{name}")  # the name is the user's
        assert pattern.fullmatch(f"mcp__MemAI__{name}")
    assert not pattern.fullmatch("mcp__other__note")
    assert not pattern.fullmatch("mcp__OtherServer__pulse")


def test_a_tool_of_ours_outside_guarded_is_not_judged():
    assert guard.tool_of("mcp__memai__pulse") == ""
    assert guard.tool_of("mcp__memai__note") == "note"
    assert guard.memai_tool("mcp__MemAI__pulse") == "pulse"
    assert guard.memai_tool("mcp__other__pulse") == ""
    assert guard.memai_tool("mcp__memai__a__b") == ""


def test_guard_matcher_has_no_handoff():
    assert "handoff" not in guard.matcher()
    for table in (guard.GUARDED, guard.WATCHED, guard.OPTIONAL):
        assert "handoff" not in table


# ------------------------------------------------------------ what it refuses

def test_a_write_missing_its_required_text_is_refused(monkeypatch, capsys):
    code, out, err = _run(_call("checkpoint", intent="ship it", open_questions="none"),
                          monkeypatch, capsys)
    assert code == 2
    assert out == ""
    assert "established, pursuing" in err
    assert "antml:" in err  # the cause, named where the model will read it


def test_the_refusal_names_every_missing_field_at_once(monkeypatch, capsys):
    """The server names one at a time, which is what makes each retry read
    as a new problem."""
    _, _, err = _run(_call("anti_pattern", pattern="p"), monkeypatch, capsys)
    assert "why_wrong, instead" in err


def test_the_refusal_names_the_tool_the_way_the_host_did(monkeypatch, capsys):
    """The middle segment is the server's registered name, which is the
    user's to choose. A message that spells another one sends the reader
    looking for a tool their host does not publish."""
    payload = {"tool_name": "mcp__memai__note", "tool_input": {}}
    code, _, err = _run(payload, monkeypatch, capsys)
    assert code == 2
    assert "BLOCKED (mcp__memai__note)" in err


def test_a_field_that_arrived_empty_counts_as_missing(monkeypatch, capsys):
    code, _, err = _run(_call("note", content="   "), monkeypatch, capsys)
    assert code == 2
    assert "content" in err


def test_a_complete_write_goes_through_in_silence(monkeypatch, capsys):
    for tool in guard.GUARDED:
        code, out, err = _run(_call(tool, **_full(tool)), monkeypatch, capsys)
        assert (code, out, err) == (0, "", ""), tool


# ----------------------------------------------------------- what it warns of

def test_an_optional_field_is_warned_about_not_refused(monkeypatch, capsys):
    code, out, err = _run(
        _call("note", title="a name for it", content="a fact"), monkeypatch, capsys)
    assert (code, err) == (0, "")
    message = json.loads(out)["systemMessage"]
    assert "domain, tags, source_ref" in message
    assert "edit_memory" in message  # what to do about it after the write


def test_a_tag_that_landed_in_the_body_is_warned_about(monkeypatch, capsys):
    """The other shape of the same typo: the dropped tag does not vanish,
    it ends up inside the text of the parameter after it."""
    params = _full("note")
    params["content"] = "a fact <parameter name=domain> acme/x100"
    code, out, _ = _run(_call("note", **params), monkeypatch, capsys)
    assert code == 0
    assert "content" in json.loads(out)["systemMessage"]


def test_debris_is_never_a_refusal(monkeypatch, capsys):
    """A memory documenting this defect quotes the tag on purpose; a guard
    that cannot be written about is one that gets taken out."""
    params = _full("anti_pattern")
    params["why_wrong"] = "a tag opened as <parameter name=...> is dropped"
    code, _, err = _run(_call("anti_pattern", **params), monkeypatch, capsys)
    assert (code, err) == (0, "")


# ------------------------------------ the call a parameter swallowed: marks

def test_a_closing_tag_of_the_call_frame_or_of_the_tools_own_field_is_a_mark():
    assert guard.leak_marks("note", f"{LEAKED}</invoke>") == [
        "</content>", "</domain>", "</invoke>", "</tags>"]


def test_the_prefixed_closing_tag_is_a_mark_too():
    """Either half of the typo writes the tag; only one prefixes it."""
    assert guard.leak_marks("note", f"a fact {PREFIXED}") == [PREFIXED]


def test_a_closing_tag_of_a_field_the_tool_does_not_take_is_not_a_mark():
    """`</result>` is a leak in the tool that has a result and prose in the
    ones that do not, which is what keeps a body free to quote markup."""
    assert guard.leak_marks("note", "the endpoint answers <result>0</result>") == []
    assert guard.leak_marks("reasoning", "<result>0</result>") == ["</result>"]
    assert guard.leak_marks("note", "<div>the row</div>") == []


def test_an_opening_tag_on_its_own_is_not_a_mark():
    """What a memory ABOUT this defect writes."""
    assert guard.leak_marks("note", "a tag opened as <parameter name=...> is dropped") == []


def test_a_broken_closing_tag_is_not_a_mark():
    """The escape the refusal offers has to actually work."""
    assert guard.leak_marks("note", "quote it as </ invoke> and </ content>") == []


# ---------------------------------- the call a parameter swallowed: refusals

def test_a_body_holding_the_rest_of_the_call_is_refused(monkeypatch, capsys):
    params = _full("note")
    params["content"] = LEAKED
    code, out, err = _run(_call("note", **params), monkeypatch, capsys)
    assert code == 2
    assert out == ""
    assert "content carries </content>, </domain>, </tags>" in err
    assert "antml:" in err          # the cause
    assert "note(title, content)" in err


def test_the_leak_refusal_names_the_tool_the_way_the_host_did(monkeypatch, capsys):
    payload = {"tool_name": "mcp__memai__note",
               "tool_input": {"title": "a name for it", "content": LEAKED}}
    code, _, err = _run(payload, monkeypatch, capsys)
    assert code == 2
    assert "BLOCKED (mcp__memai__note)" in err


def test_every_swallowing_parameter_is_named_not_only_the_first(monkeypatch, capsys):
    """And with the marks that tool can carry: `content` is not one of its
    fields, so `</content>` in an anti_pattern is text somebody wrote."""
    params = _full("anti_pattern")
    params["why_wrong"] = LEAKED
    params["instead"] = "</invoke>"
    _, _, err = _run(_call("anti_pattern", **params), monkeypatch, capsys)
    assert "instead carries </invoke>" in err
    assert "why_wrong carries </domain>, </tags>" in err


def test_prose_that_quotes_the_tag_still_writes(monkeypatch, capsys):
    """The same conviction as test_debris_is_never_a_refusal, one tier up: a
    guard that cannot be written about is one that gets taken out."""
    params = _full("anti_pattern")
    params["why_wrong"] = ("a tag opened as <parameter name=...> is dropped, and "
                           "quoting the closing half as </ parameter> is not a mark")
    code, _, err = _run(_call("anti_pattern", **params), monkeypatch, capsys)
    assert (code, err) == (0, "")


# ------------------------------------ the call a parameter swallowed: the store

def test_the_store_refuses_a_body_holding_the_rest_of_the_call(conn):
    """The backstop: a call that reaches the store with no hook in front of
    it -- an unregistered host, the dashboard, staged text."""
    with pytest.raises(ValueError, match="tool call's own source"):
        db.insert_memory(conn, type="note", title="a cache warmup", content=LEAKED)


def test_the_store_refuses_a_title_holding_it(conn):
    with pytest.raises(ValueError, match="tool call's own source"):
        db.insert_memory(conn, type="note", title=f"a warmup{PREFIXED}", content="a fact")


def test_the_store_refuses_leaked_tags_and_a_leaked_source_ref(conn):
    """Where a leak lands is wherever the typo was typed, and the tags are as
    common a landing place as the body."""
    tail = "</tags>\n<source_ref>src/acme/x100/warmup.py</source_ref>"
    with pytest.raises(ValueError, match="tool call's own source"):
        db.insert_memory(conn, type="note", title="a cache warmup",
                         content="a fact", tags=f"cache warmup{tail}")
    with pytest.raises(ValueError, match="tool call's own source"):
        db.insert_memory(conn, type="note", title="a cache warmup",
                         content="a fact", source_ref=f"src/acme{tail}")


def test_a_tag_edit_that_leaks_leaves_the_tags_it_had(conn):
    uid = db.insert_memory(conn, type="note", title="a cache warmup",
                           content="a fact", tags="cache warmup")
    with pytest.raises(ValueError, match="tool call's own source"):
        db.set_tags(conn, uid, "cache warmup</tags>")
    with pytest.raises(ValueError, match="tool call's own source"):
        db.set_source_ref(conn, uid, "src/acme/x100/warmup.py</source_ref>")
    row = db.get_memory(conn, uid)
    assert (row["tags"], row["source_ref"]) == ("cache warmup", "")


def test_an_edit_that_leaks_leaves_the_body_it_had(conn):
    uid = db.insert_memory(conn, type="note", title="a cache warmup",
                           content="the warmup drains the queue once")
    with pytest.raises(ValueError, match="tool call's own source"):
        db.update_memory_content(conn, uid, LEAKED)
    assert db.get_memory(conn, uid)["content"] == "the warmup drains the queue once"


def test_a_rename_that_leaks_leaves_the_name_it_had(conn):
    uid = db.insert_memory(conn, type="note", title="a cache warmup", content="a fact")
    with pytest.raises(ValueError, match="tool call's own source"):
        db.set_title(conn, uid, f"a cache warmup{PREFIXED}")
    assert db.get_memory(conn, uid)["title"] == "a cache warmup"


def test_the_tool_says_so_instead_of_writing(store):
    uid = server.note(title="a cache warmup", content="the warmup drains the queue",
                      domain="acme/x100")["uid"]
    res = server.edit_memory(uid, new_content=LEAKED)
    assert res["ok"] is False and "tool call's own source" in res["errors"][0]
    assert server.get_memory(uid)["content"] == "the warmup drains the queue"


def test_the_dashboard_refuses_it(client):
    res = client.post("/api/memories", json={
        "title": "a cache warmup", "type": "note", "content": LEAKED})
    assert res.status_code == 400
    assert "tool call's own source" in res.json()["error"]


def test_a_restore_reproduces_a_row_that_already_carries_it(conn):
    """A restore reproduces a row whose body carries a leak: a round trip
    reproduces rows, it does not re-judge them."""
    db.restore_memory(conn, {"uid": "a1b2c3d4e5f60718", "type": "note",
                             "title": "a cache warmup", "content": LEAKED})
    assert db.get_memory(conn, "a1b2c3d4e5f60718")["content"] == LEAKED


# ------------------------------------------------- what it will not judge

def test_a_tool_of_another_server_goes_through(monkeypatch, capsys):
    payload = {"tool_name": "mcp__Other__note", "tool_input": {}}
    assert _run(payload, monkeypatch, capsys) == (0, "", "")


def test_a_memai_tool_the_guard_does_not_own_goes_through(monkeypatch, capsys):
    assert _run(_call("forget", uid="deadbeef"), monkeypatch, capsys) == (0, "", "")


@pytest.mark.parametrize("payload", [
    "not json at all",
    "",
    {"tool_name": "mcp__MemAI__note"},                     # no input to read
    {"tool_name": "mcp__MemAI__note", "tool_input": "a"},  # not an object
    {"tool_input": {"content": ""}},                       # no tool named
])
def test_a_payload_it_cannot_read_is_not_a_refusal(payload, monkeypatch, capsys):
    code, _, _ = _run(payload, monkeypatch, capsys)
    assert code == 0


def test_a_failure_inside_the_guard_lets_the_call_through(monkeypatch, capsys):
    """Nothing this hook can hit is worth stopping a write over."""
    monkeypatch.setattr(guard, "check", lambda *a: 1 / 0)
    code, _, _ = _run(_call("note", content="a fact"), monkeypatch, capsys)
    assert code == 0


# --------------------------------------------------------- its registration

def test_the_guard_is_registered_with_a_matcher(tmp_path):
    settings = tmp_path / "settings.json"
    hook_install.install(settings, command="C:/x/memai-hook")
    groups = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups[0]["matcher"] == guard.matcher()
    assert groups[0]["hooks"][0]["command"] == "C:/x/memai-hook guard"


def test_another_pretooluse_hook_is_left_where_it_is(tmp_path):
    """A repository that already guards something of its own on this event
    keeps it -- an install adds memai's entry, it does not own the event."""
    settings = tmp_path / "settings.json"
    theirs = {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "check.ps1"}]}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [theirs]}}), encoding="utf-8")
    hook_install.install(settings, command="C:/x/memai-hook")
    groups = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups[0] == theirs
    assert len(groups) == 2


def test_the_refusal_spells_each_tool_as_a_signature(monkeypatch, capsys):
    """A parameter list reads as a set unless something gives it an order.

    The retry this asks for is the whole call retyped, so the order the tool
    takes its fields in has to be in the message the model reads.
    """
    _, _, err = _run(_call("note"), monkeypatch, capsys)
    assert "note(title, content)" in err
    assert "checkpoint(title, intent, established, pursuing, open_questions)" in err
    assert "POSITIONAL" in err


def test_the_missing_fields_are_named_in_signature_order():
    """The refusal claims the two orders agree; this is what makes that true."""
    for tool, fields in guard.GUARDED.items():
        missing, _, _ = guard.check(tool, {})
        assert missing == list(fields), tool


# ------------------------------------------- the domains a session names

def _seed_task(domain: str) -> str:
    with db.connect() as conn:
        return tasks.create_task(conn, title="Repair the pier", goal="The pier holds",
                                 items=["replace the planks"], domain=domain)


def _named(session: str = "session-1") -> list[str]:
    return warden.read(session).get("domains", [])


def _guarded(payload, monkeypatch, capsys, session: str | None = "session-1"):
    if session is not None and isinstance(payload, dict):
        payload = {**payload, "session_id": session}
    return _run(payload, monkeypatch, capsys)


def test_a_call_naming_a_domain_records_it_and_goes_through(store, monkeypatch, capsys):
    code, out, err = _guarded(_call("pulse", domain="acme/harbor"), monkeypatch, capsys)
    assert (code, out, err) == (0, "", "")
    assert _named() == ["acme/harbor"]


def test_a_refused_write_records_its_domain_first(store, monkeypatch, capsys):
    code, _, err = _guarded(_call("note", domain="acme/harbor"), monkeypatch, capsys)
    assert code == 2 and "BLOCKED" in err
    assert _named() == ["acme/harbor"]


def test_the_domain_is_recorded_stripped_and_as_given(store, monkeypatch, capsys):
    _guarded(_call("search", query="x", domain="  p200 "), monkeypatch, capsys)
    assert _named() == ["p200"]


def test_a_task_call_records_the_domain_of_its_task(store, monkeypatch, capsys):
    uid = _seed_task("acme/docks")
    for tool, extra in (("task_item", {"item": "i1"}), ("task_add", {"items": "x"}),
                        ("task_comment", {"body": "x"}), ("get_memory", {})):
        warden.state_path("session-1").unlink(missing_ok=True)
        code, _, _ = _guarded(_call(tool, uid=uid, **extra), monkeypatch, capsys)
        assert code == 0
        assert _named() == ["acme/docks"], tool


def test_an_unknown_uid_records_nothing_and_returns_as_before(store, monkeypatch, capsys):
    code, out, err = _guarded(_call("task_item", uid="deadbeefdeadbeef", item="i1"),
                              monkeypatch, capsys)
    assert (code, out, err) == (0, "", "")
    assert _named() == []
    code, _, err = _guarded(_call("task_item", uid="deadbeefdeadbeef"), monkeypatch, capsys)
    assert code == 2 and "BLOCKED" in err


@pytest.mark.parametrize("params", [{"domain": ""}, {"domain": "   "}, {}, {"domain": 3},
                                    {"domain": "/"}, {"domain": " / "}])
def test_no_domain_records_nothing(store, monkeypatch, capsys, params):
    assert _guarded(_call("pulse", **params), monkeypatch, capsys)[0] == 0
    assert _named() == []


def test_a_call_without_a_session_id_records_nothing(store, monkeypatch, capsys):
    assert _guarded(_call("pulse", domain="acme/harbor"), monkeypatch, capsys,
                    session=None)[0] == 0
    assert list((store / "warden").glob("*")) == []


def test_an_unsafe_session_id_records_nothing(store, monkeypatch, capsys):
    assert _guarded(_call("pulse", domain="acme/harbor"), monkeypatch, capsys,
                    session="../escape")[0] == 0
    assert list(store.rglob("escape*")) == []


def test_a_call_naming_a_domain_does_not_open_the_store(store, monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise AssertionError("the store was opened")
    monkeypatch.setattr(db, "connect", refuse)
    assert _guarded(_call("pulse", domain="acme/harbor"), monkeypatch, capsys)[0] == 0
    assert _named() == ["acme/harbor"]


def test_a_recording_that_raises_leaves_the_result_alone(store, monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise RuntimeError("cannot write")
    monkeypatch.setattr(warden, "record_domain", refuse)
    assert _guarded(_call("pulse", domain="acme/harbor"), monkeypatch, capsys) == (0, "", "")
    code, _, err = _guarded(_call("note", domain="acme/harbor"), monkeypatch, capsys)
    assert code == 2 and "BLOCKED" in err


# ------------------------------------- the uid lookup is a bare read of the store

def test_the_uid_lookup_runs_no_migration(store, monkeypatch, capsys):
    """`db.connect` migrates and writes on open; the lookup must not go through it."""
    uid = _seed_task("acme/docks")

    def refuse(*args, **kwargs):
        raise AssertionError("db.connect was used")
    monkeypatch.setattr(db, "connect", refuse)
    assert _guarded(_call("get_memory", uid=uid), monkeypatch, capsys) == (0, "", "")
    assert _named() == ["acme/docks"]


def test_the_uid_lookup_does_not_wait_for_a_writer(store, monkeypatch, capsys):
    import sqlite3
    import time
    uid = _seed_task("acme/docks")
    writer = sqlite3.connect(str(db.default_db_path()), timeout=1)
    writer.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        code, _, _ = _guarded(_call("get_memory", uid=uid), monkeypatch, capsys)
        assert code == 0 and time.monotonic() - started < 5
    finally:
        writer.rollback()
        writer.close()
    assert _named() == ["acme/docks"]


def test_a_store_that_does_not_exist_records_nothing_and_is_not_created(
        store, monkeypatch, capsys):
    code, out, err = _guarded(_call("get_memory", uid="deadbeefdeadbeef"),
                              monkeypatch, capsys)
    assert (code, out, err) == (0, "", "")
    assert _named() == []
    assert not db.default_db_path().exists()
    code, _, err = _guarded(_call("task_item", uid="deadbeefdeadbeef"), monkeypatch, capsys)
    assert code == 2 and "BLOCKED" in err


def test_a_store_path_with_a_space_still_opens_for_the_lookup(
        tmp_path, monkeypatch, capsys):
    home = tmp_path / "a home with spaces"
    home.mkdir()
    monkeypatch.setenv("MEMAI_HOME", str(home))
    uid = _seed_task("acme/docks")
    assert _guarded(_call("task_comment", uid=uid, body="x"), monkeypatch, capsys)[0] == 0
    assert _named() == ["acme/docks"]


# ------------------------------------------------- the guard starts without the store

_HEAVY = ("memai.db", "memai.brief", "memai.update", "memai.pending")

_RUNNER = (
    "import json, sys\n"
    "from memai import hook\n"
    "code = hook.main(['guard'])\n"
    "open(sys.argv[1], 'w').write(json.dumps(sorted(m for m in sys.modules if m.startswith('memai'))))\n"
    "sys.exit(code)\n"
)


def _guard_process(tmp_path, payload: dict) -> tuple[int, str, list[str]]:
    """Run `memai-hook guard` in a fresh interpreter: the exit code, stderr, and
    the memai modules loaded when it finished."""
    import os
    import subprocess
    import sys
    seen = tmp_path / "modules.json"
    env = {**os.environ, "MEMAI_HOME": str(tmp_path / "home")}
    done = subprocess.run([sys.executable, "-c", _RUNNER, str(seen)],
                          input=json.dumps(payload), text=True, env=env,
                          capture_output=True, timeout=60)
    return done.returncode, done.stderr, json.loads(seen.read_text(encoding="utf-8"))


def test_a_call_naming_a_domain_loads_neither_the_database_nor_the_release_check(tmp_path):
    code, err, loaded = _guard_process(tmp_path, {
        "session_id": "s-light", **_call("pulse", domain="acme/harbor")})
    assert code == 0 and err == ""
    assert [m for m in _HEAVY if m in loaded] == []
    state = json.loads((tmp_path / "home" / "warden" / "s-light.json").read_text("utf-8"))
    assert state["domains"] == ["acme/harbor"]


def test_a_refused_write_loads_neither_the_database_nor_the_release_check(tmp_path):
    code, err, loaded = _guard_process(tmp_path, {
        "session_id": "s-light", **_call("note", domain="acme/harbor")})
    assert code == 2 and "BLOCKED" in err
    assert [m for m in _HEAVY if m in loaded] == []
    state = json.loads((tmp_path / "home" / "warden" / "s-light.json").read_text("utf-8"))
    assert state["domains"] == ["acme/harbor"]


def test_a_uid_lookup_still_records_the_domain_of_the_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path / "home"))
    uid = _seed_task("acme/docks")
    code, err, _ = _guard_process(tmp_path, {
        "session_id": "s-uid", **_call("get_memory", uid=uid)})
    assert (code, err) == (0, "")
    state = json.loads((tmp_path / "home" / "warden" / "s-uid.json").read_text("utf-8"))
    assert state["domains"] == ["acme/docks"]


def test_the_light_home_resolves_the_way_the_store_does(tmp_path, monkeypatch):
    from pathlib import Path

    from memai import lite
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path / "set"))
    assert lite.home() == db.home() == tmp_path / "set"
    monkeypatch.delenv("MEMAI_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    assert lite.home() == db.home() == tmp_path / "user" / ".memai"
