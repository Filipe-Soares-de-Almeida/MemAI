"""Holding a briefed session's tools until it has read the store.

The session-start brief asks for pulse() or pending() before anything else;
the guard is what makes that hold. Only a session the hook actually briefed
is held, the memai tools and ToolSearch go through so the warm-up can
happen, and the hold gives way after a few refusals so a host without the
memai server is never locked out of its own tools.
"""

from __future__ import annotations

import io
import json
import re

import pytest

from conftest import shaped
from memai import db, guard, hook, hook_install, warden


SESSION = "session-1"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _seed() -> None:
    with db.connect() as conn:
        db.insert_memory(conn, type="note", domain="acme/x100",
                         content="the harbor crane lifts at dawn")
        db.insert_memory(conn, type="checkpoint", domain="acme/x100",
                         content=shaped("checkpoint", "rig the crane"))


def _start(capsysbinary, session: str = SESSION) -> dict | None:
    import sys
    sys.stdin = io.StringIO(json.dumps({"session_id": session}))
    try:
        assert hook.main(["session-start"]) == 0
    finally:
        sys.stdin = sys.__stdin__
    out = capsysbinary.readouterr().out
    return json.loads(out.decode("utf-8")) if out else None


def _guard(tool_name: str, monkeypatch, capsysbinary, session: str | None = SESSION,
           **params) -> tuple[int, str, str]:
    payload = {"tool_name": tool_name, "tool_input": params}
    if session is not None:
        payload["session_id"] = session
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = hook.main(["guard"])
    captured = capsysbinary.readouterr()
    return code, captured.out.decode("utf-8"), captured.err.decode("utf-8")


# ------------------------------------------------------------ the brief's ask

def test_the_brief_makes_the_warm_up_unconditional(store, capsysbinary):
    _seed()
    context = _start(capsysbinary)["hookSpecificOutput"]["additionalContext"]
    assert "pulse(domain)" in context and "pending()" in context
    assert "whatever the prompt asks" in context
    assert "refuses every other tool" in context


def test_session_start_tells_the_person_the_brief_was_loaded(store, capsysbinary):
    _seed()
    out = _start(capsysbinary)
    assert out["systemMessage"].startswith("MemAI: brief loaded")
    assert "2 memories" in out["systemMessage"]


def test_session_start_marks_the_session_as_briefed(store, capsysbinary):
    _seed()
    _start(capsysbinary)
    assert warden.read(SESSION)["briefed_at"]


def test_an_empty_store_briefs_nobody(store, capsysbinary):
    assert _start(capsysbinary) is None
    assert "briefed_at" not in warden.read(SESSION)


# ------------------------------------------------------------------ the hold

def test_a_briefed_session_is_refused_any_tool_before_the_warm_up(
        store, monkeypatch, capsysbinary):
    _seed()
    _start(capsysbinary)
    code, _, err = _guard("Bash", monkeypatch, capsysbinary, command="git status")
    assert code == 2
    assert "pulse(" in err and "pending(" in err


@pytest.mark.parametrize("tool_name", ["ToolSearch", "mcp__memai__list_domains",
                                       "mcp__MemAI__search"])
def test_what_the_warm_up_needs_goes_through(store, monkeypatch, capsysbinary, tool_name):
    _seed()
    _start(capsysbinary)
    assert _guard(tool_name, monkeypatch, capsysbinary)[0] == 0
    assert "warmed_at" not in warden.read(SESSION)


@pytest.mark.parametrize("tool", sorted(guard.WARM_UP))
def test_a_warm_up_call_opens_the_session_and_says_so(store, monkeypatch, capsysbinary,
                                                       tool):
    _seed()
    _start(capsysbinary)
    code, out, _ = _guard(f"mcp__memai__{tool}", monkeypatch, capsysbinary,
                          domain="acme/x100")
    assert code == 0
    assert json.loads(out)["systemMessage"].startswith(f"MemAI: session warmed up with {tool}")
    assert warden.read(SESSION)["warmed_by"] == tool
    assert _guard("Bash", monkeypatch, capsysbinary, command="git status") == (0, "", "")


def test_the_warm_up_message_is_said_once(store, monkeypatch, capsysbinary):
    _seed()
    _start(capsysbinary)
    _guard("mcp__memai__pulse", monkeypatch, capsysbinary, domain="acme/x100")
    assert _guard("mcp__memai__pending", monkeypatch, capsysbinary) == (0, "", "")


def test_a_session_the_hook_never_briefed_is_not_held(store, monkeypatch, capsysbinary):
    assert _guard("Bash", monkeypatch, capsysbinary, command="ls") == (0, "", "")
    warden.began(SESSION)
    assert _guard("Bash", monkeypatch, capsysbinary, command="ls") == (0, "", "")


def test_a_call_without_a_session_is_not_held(store, monkeypatch, capsysbinary):
    _seed()
    _start(capsysbinary)
    assert _guard("Bash", monkeypatch, capsysbinary, session=None, command="ls")[0] == 0


def test_the_hold_gives_way_after_its_limit(store, monkeypatch, capsysbinary):
    """A host without the memai server can never warm up; it keeps its tools."""
    _seed()
    _start(capsysbinary)
    codes = [_guard("Bash", monkeypatch, capsysbinary, command="ls")[0]
             for _ in range(guard.GATE_LIMIT + 2)]
    assert codes == [2] * guard.GATE_LIMIT + [0, 0]


def test_a_hold_that_raises_lets_the_call_through(store, monkeypatch, capsysbinary):
    _seed()
    _start(capsysbinary)

    def broken(*args, **kwargs):
        raise RuntimeError("cannot read")
    monkeypatch.setattr(warden, "read", broken)
    assert _guard("Bash", monkeypatch, capsysbinary, command="ls")[0] == 0


def test_a_held_memai_write_still_meets_its_own_checks(store, monkeypatch, capsysbinary):
    _seed()
    _start(capsysbinary)
    code, _, err = _guard("mcp__memai__note", monkeypatch, capsysbinary)
    assert code == 2 and "BLOCKED" in err


# ------------------------------------------------------------ its registration

def test_the_guard_fires_for_every_tool():
    pattern = re.compile(guard.matcher())
    for name in ("Bash", "Read", "ToolSearch", "mcp__memai__pulse", "mcp__other__x"):
        assert pattern.fullmatch(name)


def test_the_guard_is_registered_for_every_tool(tmp_path):
    settings = tmp_path / "settings.json"
    hook_install.install(settings, command="C:/x/memai-hook")
    groups = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups[0]["matcher"] == guard.matcher()
