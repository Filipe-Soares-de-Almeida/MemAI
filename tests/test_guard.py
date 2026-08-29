"""Refusing a write whose text never arrived.

The guard is the one hook that can stop the session it is attached to, so
these tests weigh the two failure directions against each other. A refusal
that should not have happened costs a session its memory; a refusal that
does not happen costs a memory its content, with no error anywhere to say
so. Everything the guard is unsure about therefore goes through, and what
it does refuse it refuses on a table read from the tools themselves.
"""

from __future__ import annotations

import inspect
import io
import json
import re

import pytest

from memai import guard, hook, hook_install, server


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
    code, out, err = _run(_call("note", content="a fact"), monkeypatch, capsys)
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
