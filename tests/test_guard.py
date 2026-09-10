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

from memai import admin, db, guard, hook, hook_install, server


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
    fields = guard.GUARDED[tool] + guard.WATCHED[tool]
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


def test_the_matcher_selects_every_guarded_tool_and_nothing_else():
    pattern = re.compile(guard.matcher())
    for tool in guard.GUARDED:
        assert pattern.fullmatch(f"mcp__MemAI__{tool}")
        assert pattern.fullmatch(f"mcp__memai__{tool}")  # the name is the user's
    assert not pattern.fullmatch("mcp__MemAI__forget")
    assert not pattern.fullmatch("mcp__OtherServer__note")


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
    """A store holding one from before this refusal still exports and imports:
    a round trip reproduces rows, it does not re-judge them."""
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
