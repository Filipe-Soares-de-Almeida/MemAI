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

from conftest import shaped
from memai import brief, db, hook, hook_install, server, warden


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
                                    content=shaped("anti_pattern", "retry without backoff"),
                                    created_at=created_at),
        "hand": db.insert_memory(conn, type="handoff", domain="omni/x900",
                                 content="pick up at the token refresh path",
                                 created_at=created_at),
        "cp": db.insert_memory(conn, type="checkpoint", domain="acme/x100",
                               content=shaped("checkpoint", "ship the retry path"),
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


def _status(capsysbinary, argv=(), stdin: str = "{}") -> str:
    """Run `statusline` and return the bare text it wrote, without its newline."""
    import sys
    sys.stdin = io.StringIO(stdin)
    try:
        assert hook.main(["statusline", *argv]) == 0
    finally:
        sys.stdin = sys.__stdin__
    return capsysbinary.readouterr().out.decode("utf-8").rstrip("\n")


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
                         content=shaped("anti_pattern",
                                        f"pitfall {i} " + "spelled out at length " * 12))
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
                         content=shaped("anti_pattern",
                                        f"pitfall {i} " + "spelled out at length " * 12))
    text = brief.session_brief(conn, budget=1400)
    section = next(b for b in text.split("\n\n") if "Pitfalls on record" in b)
    shown = sum(1 for line in section.splitlines() if line.startswith("  - "))
    assert shown < 4
    assert f"... +{4 - shown} not shown" in text


@pytest.mark.parametrize("mode, said, not_said", [
    ("lower", "stored lowercase", "keeps the casing"),
    ("upper", "stored UPPERCASE", "lowercase"),
    ("preserve", "keeps the casing", "stored lowercase"),
])
def test_the_instruction_states_the_active_casing_policy(conn, mode, said, not_said):
    """Under lower/upper a path is folded on the way in and on the way
    through a read; under preserve two spellings are two domains."""
    _seed(conn)
    db.set_domain_case(conn, mode)
    text = brief.session_brief(conn)
    assert said in text
    assert not_said not in text


def test_a_contradicted_pitfall_is_not_in_the_warm_up(conn):
    ids = _seed(conn)
    db.set_confidence(conn, ids["pitfall"], "contradicted")
    assert ids["pitfall"] not in brief.session_brief(conn)


# ------------------------------------------------------------------ the hooks

def test_session_start_emits_the_brief(store, capsysbinary):
    with db.connect() as conn:
        _seed(conn)
    out = _run("session-start", {}, capsysbinary)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "acme/x100" in context
    # the brief names the store it was read from, and a home with no
    # pointer is on the general one
    assert "in project 'General'" in context


def test_session_start_on_an_empty_store_emits_nothing(store, capsysbinary):
    assert _run("session-start", {}, capsysbinary) is None


def test_session_start_also_carries_the_instruction(store, capsysbinary):
    """One hook emits the context and the instruction to act on it."""
    with db.connect() as conn:
        _seed(conn)
    out = _run("session-start", {}, capsysbinary)
    assert "pulse(domain)" in out["hookSpecificOutput"]["additionalContext"]


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


# -------------------------------------------------------- asking the warden

def _with_agent(tmp_path, session_id="session-1"):
    """A host that has the warden installed and started up afterwards.

    Returns the argv pointing the hook at it, so a test drives the same
    lookup the installed hook does rather than the real ~/.claude. The
    session is started AFTER the install, which is the order that makes the
    agent launchable.
    """
    settings = tmp_path / "host" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    hook_install.install_agents(hook_install.agents_dir(settings))
    warden.began(session_id)
    return ("--settings", str(settings))


def _context(out) -> str:
    return out["hookSpecificOutput"]["additionalContext"]


def test_stop_does_not_ask_for_an_agent_the_host_does_not_have(store, capsysbinary,
                                                               tmp_path):
    """An uninstalled warden would spend the next turn on a launch error."""
    settings = tmp_path / "host" / "settings.json"
    with db.connect() as conn:
        _seed(conn)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=("--settings", str(settings)))
    assert out is None


def test_an_agent_installed_after_the_session_started_is_not_asked_for(
        store, capsysbinary, tmp_path):
    """A host reads its agent definitions once, when it starts.

    Installing one into a session already running puts it on disk without
    making it launchable, and asking for it would spend the turn on an error.
    """
    settings = tmp_path / "host" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    warden.began("session-1")
    # A session already running when the install lands: minutes, not the
    # microseconds GRACE is there to absorb.
    earlier = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({"started_at": earlier}), encoding="utf-8")
    hook_install.install_agents(hook_install.agents_dir(settings))

    with db.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=("--settings", str(settings))) is None


def test_a_session_that_never_reported_starting_is_not_asked(store, capsysbinary,
                                                             tmp_path):
    """Without a start there is nothing to compare the install against, and a
    skipped session costs less than a launch error."""
    settings = tmp_path / "host" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    hook_install.install_agents(hook_install.agents_dir(settings))
    with db.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=("--settings", str(settings))) is None


def test_session_start_records_that_the_session_began(store, capsysbinary):
    """It is what a later Stop compares the agent's install time against."""
    with db.connect() as conn:
        _seed(conn)
    _run("session-start", {"session_id": "session-1"}, capsysbinary)
    assert warden.read("session-1")["started_at"]


def test_recording_the_start_does_not_count_as_asking(store, capsysbinary):
    """A session that just began is still owed its first warden run."""
    with db.connect() as conn:
        _seed(conn)
    _run("session-start", {"session_id": "session-1"}, capsysbinary)
    assert warden.due("session-1") is True


def test_stop_asks_for_the_warden_once_it_is_installed(store, capsysbinary, tmp_path):
    with db.connect() as conn:
        _seed(conn)
    out = _run("stop", {"session_id": "session-1",
                        "transcript_path": "/tmp/a.jsonl"},
               capsysbinary, argv=_with_agent(tmp_path))
    assert warden.AGENT in _context(out)
    assert "/tmp/a.jsonl" in _context(out)


def test_the_warden_is_not_asked_for_twice_in_one_interval(store, capsysbinary,
                                                            tmp_path):
    """The ask is recorded when it is made, so the next turn is silent."""
    argv = _with_agent(tmp_path)
    with db.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None


def test_a_second_session_is_asked_while_the_first_is_in_its_interval(
        store, capsysbinary, tmp_path):
    """Two sessions at once each get their own run: the interval is kept per
    session, and each request names the transcript of the session it is for."""
    argv = _with_agent(tmp_path)
    warden.began("session-2")
    with db.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None
    out = _run("stop", {"session_id": "session-2", "transcript_path": "/tmp/two.jsonl"},
               capsysbinary, argv=argv)
    assert warden.AGENT in _context(out)
    assert "/tmp/two.jsonl" in _context(out)


def test_a_session_without_an_id_is_never_asked(store, capsysbinary, tmp_path):
    """Nothing can record the ask, so making it would repeat it every turn."""
    with db.connect() as conn:
        _seed(conn)
    assert _run("stop", {}, capsysbinary, argv=_with_agent(tmp_path)) is None


def test_both_notes_travel_in_one_result(store, capsysbinary, tmp_path):
    """A turn that owes a checkpoint and a warden run emits one object."""
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with db.connect() as conn:
        _seed(conn, created_at=old)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=_with_agent(tmp_path))
    assert "note()" in _context(out)
    assert warden.AGENT in _context(out)
    assert "nothing recorded" in out["systemMessage"]
    assert "warden" in out["systemMessage"]


def test_the_second_ask_tells_the_warden_where_it_stopped(store, capsysbinary,
                                                           tmp_path):
    """The stamp of the previous ask is what bounds the turns to read."""
    argv = _with_agent(tmp_path)
    with db.connect() as conn:
        _seed(conn)
    _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    first = warden.read("session-1")["asked_at"]
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=(*argv, "--warden-minutes", "0"))
    assert first in _context(out)


# ---------------------------------------------------------------- statusline

def test_the_statusline_carries_the_count_the_domain_and_the_checkpoint_age(
        store, capsysbinary):
    with db.connect() as conn:
        _seed(conn)
    line = _status(capsysbinary)
    assert "4 mem" in line
    assert "acme/x100" in line
    assert "cp 0m ago" in line


def test_the_statusline_is_one_bare_line_under_eighty_characters(store, capsysbinary):
    """Plain text closed by a single newline, not the JSON a hook event emits."""
    import sys
    with db.connect() as conn:
        _seed(conn)
    sys.stdin = io.StringIO("{}")
    try:
        assert hook.main(["statusline"]) == 0
    finally:
        sys.stdin = sys.__stdin__
    raw = capsysbinary.readouterr().out.decode("utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 1
    assert len(raw) - 1 < 80
    assert "hookSpecificOutput" not in raw and not raw.startswith("{")


def test_a_long_domain_path_does_not_push_the_line_over_the_limit(store, capsysbinary):
    with db.connect() as conn:
        db.insert_memory(conn, type="note", domain="acme/" + "x100/" * 20 + "p200",
                         content="the index rebuild runs after the row merge")
    line = _status(capsysbinary)
    assert len(line) < 80
    assert "..." in line


def test_the_busiest_domain_wins_over_the_parent_holding_nothing(store, capsysbinary):
    """A path is ranked on the memories naming it, not on its subtree."""
    with db.connect() as conn:
        for i in range(3):
            db.insert_memory(conn, type="note", domain="acme/x100/p200",
                             content=f"queue drain step {i}")
    assert "acme/x100/p200" in _status(capsysbinary)


def test_a_store_with_no_checkpoint_says_so(store, capsysbinary):
    with db.connect() as conn:
        db.insert_memory(conn, type="note", domain="acme/x100",
                         content="the report export window is inclusive")
    assert "no checkpoint" in _status(capsysbinary)


def test_the_statusline_can_be_scoped(store, capsysbinary):
    with db.connect() as conn:
        _seed(conn)
    line = _status(capsysbinary, ["--domain", "omni/x900"])
    assert "1 mem" in line and "omni/x900" in line
    assert "acme/x100" not in line


def test_an_empty_store_has_no_statusline(store, capsysbinary):
    assert _status(capsysbinary) == ""


def test_an_empty_scope_has_no_statusline(store, capsysbinary):
    with db.connect() as conn:
        _seed(conn)
    assert _status(capsysbinary, ["--domain", "zeta/x200"]) == ""


def test_a_store_that_cannot_be_opened_has_no_statusline(monkeypatch, capsysbinary):
    monkeypatch.setattr(db, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert _status(capsysbinary) == ""


def test_a_payload_that_is_not_json_does_not_stop_the_statusline(store, capsysbinary):
    """The status line does not depend on stdin, so junk on it changes nothing."""
    with db.connect() as conn:
        _seed(conn)
    assert "4 mem" in _status(capsysbinary, stdin="this is not json")


@pytest.mark.parametrize("delta, expected", [
    (timedelta(seconds=20), "0m"),
    (timedelta(minutes=7), "7m"),
    (timedelta(minutes=59), "59m"),
    (timedelta(hours=3, minutes=30), "3h"),
    (timedelta(days=9, hours=2), "9d"),
    (timedelta(minutes=-5), "0m"),
])
def test_an_age_reads_in_the_largest_unit_that_fits(delta, expected):
    now = datetime.now(timezone.utc)
    assert hook._age((now - delta).isoformat(), now=now) == expected


def test_a_timestamp_without_an_offset_is_read_as_utc():
    now = datetime.now(timezone.utc)
    naive = (now - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert hook._age(naive, now=now) == "2h"


# -------------------------------------------------------------- failure paths

def test_an_unreadable_payload_is_not_an_error(store, capsysbinary):
    """A payload that will not parse exits 0 with no output."""
    import sys
    sys.stdin = io.StringIO("this is not json")
    try:
        assert hook.main(["session-start"]) == 0
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
    uid = server.note("fixture title", content="cache warmup runs nightly")["uid"]
    assert server.get_memory(uid)["session"] == server.SESSION


def test_an_explicit_session_still_wins(store):
    uid = server.note("fixture title", content="cache warmup runs nightly", session="proj-1042")["uid"]
    assert server.get_memory(uid)["session"] == "proj-1042"


def test_the_switch_silences_the_ask(store, capsysbinary, tmp_path):
    """Off means the Stop hook never asks, which is what makes it cost nothing."""
    argv = _with_agent(tmp_path)
    with db.connect() as conn:
        _seed(conn)
        db.set_warden_enabled(conn, False)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None


def test_the_switch_does_not_silence_the_checkpoint_nudge(store, capsysbinary, tmp_path):
    """It turns off one note, not the hook."""
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with db.connect() as conn:
        _seed(conn, created_at=old)
        db.set_warden_enabled(conn, False)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=_with_agent(tmp_path))
    assert "note()" in _context(out)
    assert warden.AGENT not in _context(out)


def test_the_stored_interval_is_what_the_hook_uses(store, capsysbinary, tmp_path):
    """The dashboard writes it; the hook reads it without a flag."""
    argv = _with_agent(tmp_path)
    with db.connect() as conn:
        _seed(conn)
        db.set_warden_minutes(conn, 60)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    # 30 minutes on, the stored 60 has not elapsed, so nothing is asked
    later = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({**warden.read("session-1"), "asked_at": later}), encoding="utf-8")
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None


def test_the_flag_overrides_the_stored_interval(store, capsysbinary, tmp_path):
    """`--warden-minutes` is for one run, the store holds the standing answer."""
    argv = _with_agent(tmp_path)
    with db.connect() as conn:
        _seed(conn)
        db.set_warden_minutes(conn, 480)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=(*argv, "--warden-minutes", "1")) is None
    hours = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({**warden.read("session-1"), "asked_at": hours}), encoding="utf-8")
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=(*argv, "--warden-minutes", "1"))
