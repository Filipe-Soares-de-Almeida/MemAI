"""Putting the store in front of an agent that did not ask for it.

The brief text and the four host hooks that emit it. A hook is attached to
somebody's working session, so the tests care as much about the failure
paths -- no store, unreadable payload, nothing worth saying -- as about
the happy one: silence is the correct output far more often than not, and
a traceback is never one.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from memai import brief, db, hook, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _seed(conn, *, created_at: str | None = None) -> dict:
    return {
        "note": db.insert_memory(conn, type="note", domain="acme/x100",
                                 content="cache warmup runs before the first request",
                                 created_at=created_at),
        "pitfall": db.insert_memory(conn, type="anti_pattern", domain="acme/x100",
                                    content="TEMPTATION: retry without backoff",
                                    created_at=created_at),
        "hand": db.insert_memory(conn, type="handoff", domain="omni/x900",
                                 content="pick up at the token refresh path",
                                 created_at=created_at),
        "cp": db.insert_memory(conn, type="checkpoint", domain="acme/x100",
                               content="INTENT: ship the retry path",
                               created_at=created_at),
    }


def _run(event: str, payload: dict, capsysbinary, argv=()) -> dict | None:
    """Drive the CLI the way a host does, and parse what it wrote."""
    import sys
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        assert hook.main([event, *argv]) == 0
    finally:
        sys.stdin = sys.__stdin__
    out = capsysbinary.readouterr().out
    return json.loads(out.decode("utf-8")) if out else None


# ------------------------------------------------------------ the brief text

def test_the_brief_names_what_the_store_holds(conn):
    ids = _seed(conn)
    text = brief.session_brief(conn)
    assert "4 memories" in text
    assert "acme/x100" in text
    assert ids["hand"] in text and ids["pitfall"] in text and ids["cp"] in text
    assert "pulse(domain)" in text  # what to do next


def test_an_empty_store_has_nothing_to_say(conn):
    assert brief.session_brief(conn) == ""


def test_the_brief_can_be_scoped(conn):
    ids = _seed(conn)
    text = brief.session_brief(conn, domain="omni/x900")
    assert ids["hand"] in text and ids["note"] not in text


def test_a_tight_budget_drops_whole_sections_and_says_so(conn):
    _seed(conn)
    text = brief.session_brief(conn, budget=200)
    assert "more section(s) omitted" in text
    # the instruction on what to do next survives a squeeze; it is the point
    assert "pulse(domain)" in text


def test_a_long_section_cannot_starve_the_ones_after_it(conn):
    """Found against a real store: four pitfalls at full length took half
    the warm-up and the recent notes fell off the end entirely."""
    for i in range(4):
        db.insert_memory(conn, type="anti_pattern", domain="acme/x100",
                         content=f"TEMPTATION: pitfall {i} " + "spelled out at length " * 12)
    note = db.insert_memory(conn, type="note", domain="acme/x100",
                            content="the export window is inclusive on both ends")
    text = brief.session_brief(conn)
    assert "Pitfalls on record" in text
    assert "Recent notes" in text and note in text


def test_a_trimmed_section_still_reports_its_total(conn):
    """Items are dropped, never cut mid-sentence, and the heading says how
    many there really were."""
    for i in range(4):
        db.insert_memory(conn, type="anti_pattern", domain="acme/x100",
                         content=f"TEMPTATION: pitfall {i} " + "spelled out at length " * 12)
    text = brief.session_brief(conn, budget=900)
    section = next(b for b in text.split("\n\n") if "Pitfalls on record" in b)
    shown = sum(1 for line in section.splitlines() if line.startswith("  - "))
    assert shown < 4
    assert f"... +{4 - shown} not shown" in text


def test_a_contradicted_pitfall_is_not_in_the_warm_up(conn):
    ids = _seed(conn)
    db.set_confidence(conn, ids["pitfall"], "contradicted")
    assert ids["pitfall"] not in brief.session_brief(conn)


# ------------------------------------------------------- recall from a prompt

def test_a_prompt_brings_back_what_it_touches(conn):
    ids = _seed(conn)
    text = brief.prompt_brief(conn, "why does the cache warmup run when it does?")
    assert ids["note"] in text


def test_a_prompt_that_touches_nothing_says_nothing(conn):
    _seed(conn)
    assert brief.prompt_brief(conn, "quarterly invoicing thresholds") == ""


def test_a_one_word_prompt_is_not_worth_a_search(conn):
    _seed(conn)
    assert brief.prompt_brief(conn, "hi") == ""


def test_the_prompt_brief_marks_what_not_to_trust(conn):
    ids = _seed(conn)
    newer = db.insert_memory(conn, type="note", domain="acme/x100",
                             content="cache warmup was moved to first request of the hour")
    db.add_relation(conn, newer, ids["note"], "supersedes")
    db.set_confidence(conn, ids["note"], "contradicted")
    # limit past the default: contradicted sorts last by design, so in a
    # busier store it is the row a three-line brief drops first
    text = brief.prompt_brief(conn, "when does the cache warmup run", limit=5)
    assert "CONTRADICTED" in text and f"superseded by {newer}" in text


# ------------------------------------------------------------------ the hooks

def test_session_start_emits_the_brief(store, capsysbinary):
    with db.connect() as conn:
        _seed(conn)
    out = _run("session-start", {}, capsysbinary)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "acme/x100" in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_on_an_empty_store_emits_nothing(store, capsysbinary):
    assert _run("session-start", {}, capsysbinary) is None


def test_user_prompt_searches_the_prompt(store, capsysbinary):
    with db.connect() as conn:
        ids = _seed(conn)
    out = _run("user-prompt", {"prompt": "when does the cache warmup run?"}, capsysbinary)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert ids["note"] in out["hookSpecificOutput"]["additionalContext"]


def test_pre_compact_asks_for_the_durable_part(store, capsysbinary):
    out = _run("pre-compact", {}, capsysbinary)
    assert "checkpoint()" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_is_quiet_when_the_session_wrote_something(store, capsysbinary):
    with db.connect() as conn:
        _seed(conn)
    assert _run("stop", {}, capsysbinary) is None


def test_stop_asks_when_nothing_was_written(store, capsysbinary):
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with db.connect() as conn:
        _seed(conn, created_at=old)
    out = _run("stop", {}, capsysbinary)
    assert "note()" in out["hookSpecificOutput"]["additionalContext"]
    assert out["systemMessage"]


def test_stop_does_not_answer_its_own_nudge(store, capsysbinary):
    """stop_hook_active means this run was triggered by the last one."""
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with db.connect() as conn:
        _seed(conn, created_at=old)
    assert _run("stop", {"stop_hook_active": True}, capsysbinary) is None


# -------------------------------------------------------------- failure paths

def test_an_unreadable_payload_is_not_an_error(store, capsysbinary):
    import sys
    sys.stdin = io.StringIO("this is not json")
    try:
        assert hook.main(["user-prompt"]) == 0
    finally:
        sys.stdin = sys.__stdin__
    assert capsysbinary.readouterr().out == b""


def test_a_store_that_cannot_be_opened_is_not_an_error(monkeypatch, capsysbinary):
    monkeypatch.setattr(db, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert _run("session-start", {}, capsysbinary) is None


# ------------------------------------------------------------ the MCP surface

def test_the_warm_up_prompt_returns_the_same_brief(store):
    with db.connect() as conn:
        ids = _seed(conn)
    assert ids["hand"] in server.warm_up()


def test_the_warm_up_prompt_says_so_when_there_is_nothing(store):
    assert "empty" in server.warm_up()


def test_the_server_declares_instructions():
    """Some hosts inject these; an empty string buys nothing."""
    assert "pulse(domain)" in server.INSTRUCTIONS


# ------------------------------------------------------------ session stamping

def test_writes_carry_a_session_without_being_told(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    assert server.get_memory(uid)["session"] == server.SESSION


def test_an_explicit_session_still_wins(store):
    uid = server.note(content="cache warmup runs nightly", session="proj-1042")["uid"]
    assert server.get_memory(uid)["session"] == "proj-1042"
