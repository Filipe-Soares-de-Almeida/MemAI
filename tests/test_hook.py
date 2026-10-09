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
from datetime import UTC, datetime, timedelta

import pytest

from conftest import shaped
from memai import brief, hook, hook_install, server, tasks, warden
from memai.store import connection, memories
from memai.store import domains as store_domains
from memai.store import settings as store_settings

DOMAIN_CAP = brief.DOMAINS


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _seed(conn, *, created_at: str | None = None) -> dict:
    return {
        "note": memories.insert_memory(conn, type="note", domain="acme/x100",
                                 content="cache warmup runs before the first request",
                                 created_at=created_at),
        "pitfall": memories.insert_memory(conn, type="anti_pattern", domain="acme/x100",
                                    content=shaped("anti_pattern", "retry without backoff"),
                                    created_at=created_at),
        "hand": memories.insert_memory(conn, type="handoff", domain="omni/x900",
                                 content="pick up at the token refresh path",
                                 created_at=created_at),
        "cp": memories.insert_memory(conn, type="checkpoint", domain="acme/x100",
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

def _seed_task(conn, domain: str = "acme/x100") -> str:
    return tasks.create_task(conn, title="Ship the retry path", goal="Retries back off",
                             items=["add the backoff", "cover it with a test"],
                             domain=domain)


def test_the_brief_names_what_the_store_holds(conn):
    ids = _seed(conn)
    _seed_task(conn)
    text = brief.session_brief(conn)
    assert "5 memories" in text
    assert "acme/x100" in text
    assert "Pending in this project: 1 open task, 1 pitfall, 1 handoff, 1 note." in text
    assert ids["cp"] in text
    # the pending line counts; it never lists a memory
    assert ids["hand"] not in text and ids["pitfall"] not in text and ids["note"] not in text
    assert "pulse(domain)" in text and "must_read()" in text  # what to do next


def test_an_empty_store_has_nothing_to_say(conn):
    assert brief.session_brief(conn) == ""


def test_the_brief_can_be_scoped(conn):
    ids = _seed(conn)
    text = brief.session_brief(conn, domain="acme/x100")
    assert "Pending in acme/x100:" in text
    assert "1 pitfall, 1 note." in text
    assert "handoff" not in text.split("Pending in")[1].split("\n")[0]
    assert ids["hand"] not in text


def test_the_brief_pluralizes_the_counts(conn):
    for i in range(3):
        memories.insert_memory(conn, type="note", domain="acme/x100", content=f"fact {i}")
    _seed_task(conn)
    _seed_task(conn)
    text = brief.session_brief(conn)
    assert "Pending in this project: 2 open tasks, 3 notes." in text


def test_the_brief_calls_a_diagram_a_flow(conn):
    uid = memories.insert_memory(conn, type="diagram", domain="acme/x100",
                           content="export routine")
    text = brief.session_brief(conn)
    assert "1 flow." in text
    assert uid not in text


def test_the_brief_has_no_pending_line_when_nothing_is_pending(conn):
    memories.insert_memory(conn, type="checkpoint", domain="acme/x100",
                     content=shaped("checkpoint", "ship the retry path"))
    text = brief.session_brief(conn)
    assert "Pending in" not in text
    assert "Latest checkpoint" in text


def test_the_brief_puts_tasks_first(conn):
    _seed(conn)
    assert "must_read(type='task') first" not in brief.session_brief(conn)
    _seed_task(conn)
    assert "must_read(type='task') first" in brief.session_brief(conn)


def test_a_tight_budget_drops_whole_sections_and_says_so(conn):
    _seed(conn)
    text = brief.session_brief(conn, budget=200)
    assert "more section(s) omitted" in text
    # the instruction on what to do next survives a squeeze; it is the point
    assert "pulse(domain)" in text and "must_read()" in text


def test_a_long_section_cannot_starve_the_ones_after_it(conn):
    """The room is divided between the sections. Spent in order, the opening
    line and a long domains line would leave the pending line less than it
    needs; its even share keeps it."""
    for i in range(DOMAIN_CAP):
        memories.insert_memory(conn, type="note",
                         domain=f"acme/a-very-long-product-name-{i}/with/a/deep/path/to/walk",
                         content=f"fact {i}")
    memories.insert_memory(conn, type="checkpoint", domain="acme/x100",
                     content=shaped("checkpoint", "ship the retry path " + "spelled out at length " * 12))
    memories.insert_memory(conn, type="anti_pattern", domain="acme/x100",
                     content=shaped("anti_pattern", "retry without backoff"))
    whole = brief.session_brief(conn, budget=10_000).split("\n")
    opening = whole[0]
    domains = next(x for x in whole if x.startswith("Active domains"))
    pending = next(x for x in whole if x.startswith("Pending in"))
    assert len(domains) > 3 * len(pending)

    # Room for the opening and the domains line plus half the pending line:
    # in order, the pending line does not fit; split four ways, it does.
    room = len(opening) + len(domains) + len(pending) // 2 + 2
    assert room // 4 > len(pending)
    text = brief.session_brief(conn, budget=len(brief.call_to_action(conn)) + 1 + room)
    assert pending in text
    assert "more section(s) omitted" in text


def test_a_trimmed_domain_line_still_reports_its_total(conn):
    """The domains named are capped, and the line says how many it left out."""
    for i in range(DOMAIN_CAP + 1):  # plus the shared parent, two over the cap
        memories.insert_memory(conn, type="note", domain=f"acme/p{i}", content=f"fact {i}")
    text = brief.session_brief(conn)
    assert "+2 more" in text


def test_a_section_that_does_not_fit_is_dropped_whole():
    """A section is kept or dropped as one line, never cut mid-sentence."""
    part = "Latest checkpoint: " + "ship the retry path " * 5
    assert brief._cap(part, len(part)) == part
    assert brief._cap(part, len(part) - 1) == ""


@pytest.mark.parametrize("mode, said, not_said", [
    ("lower", "stored lowercase", "keeps the casing"),
    ("upper", "stored UPPERCASE", "lowercase"),
    ("preserve", "keeps the casing", "stored lowercase"),
])
def test_the_instruction_states_the_active_casing_policy(conn, mode, said, not_said):
    """Under lower/upper a path is folded on the way in and on the way
    through a read; under preserve two spellings are two domains."""
    _seed(conn)
    store_domains.set_domain_case(conn, mode)
    text = brief.session_brief(conn)
    assert said in text
    assert not_said not in text


def test_a_contradicted_pitfall_is_not_counted(conn):
    ids = _seed(conn)
    memories.insert_memory(conn, type="anti_pattern", domain="acme/x100",
                     content=shaped("anti_pattern", "sleep instead of waiting on a signal"))
    assert "2 pitfalls" in brief.session_brief(conn)
    memories.set_confidence(conn, ids["pitfall"], "contradicted")
    text = brief.session_brief(conn)
    assert "1 pitfall," in text and "2 pitfalls" not in text


# ------------------------------------------------------------------ the hooks

def test_session_start_emits_the_brief(store, capsysbinary):
    with connection.connect() as conn:
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
    with connection.connect() as conn:
        _seed(conn)
    out = _run("session-start", {}, capsysbinary)
    assert "pulse(domain)" in out["hookSpecificOutput"]["additionalContext"]


def test_pre_compact_asks_for_the_durable_part(store, capsysbinary):
    out = _run("pre-compact", {}, capsysbinary)
    assert "checkpoint()" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_is_quiet_when_the_session_wrote_something(store, capsysbinary):
    with connection.connect() as conn:
        _seed(conn)
    assert _run("stop", {}, capsysbinary) is None


def test_stop_asks_when_nothing_was_written(store, capsysbinary):
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with connection.connect() as conn:
        _seed(conn, created_at=old)
    out = _run("stop", {}, capsysbinary)
    assert "note()" in out["hookSpecificOutput"]["additionalContext"]
    assert out["systemMessage"]


def test_stop_does_not_answer_its_own_nudge(store, capsysbinary):
    """stop_hook_active means this run was triggered by the last one."""
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with connection.connect() as conn:
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
    with connection.connect() as conn:
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
    earlier = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({"started_at": earlier}), encoding="utf-8")
    hook_install.install_agents(hook_install.agents_dir(settings))

    with connection.connect() as conn:
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
    with connection.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=("--settings", str(settings))) is None


def test_session_start_records_that_the_session_began(store, capsysbinary):
    """It is what a later Stop compares the agent's install time against."""
    with connection.connect() as conn:
        _seed(conn)
    _run("session-start", {"session_id": "session-1"}, capsysbinary)
    assert warden.read("session-1")["started_at"]


def test_recording_the_start_does_not_count_as_asking(store, capsysbinary):
    """A session that just began is still owed its first warden run."""
    with connection.connect() as conn:
        _seed(conn)
    _run("session-start", {"session_id": "session-1"}, capsysbinary)
    assert warden.due("session-1") is True


def test_stop_asks_for_the_warden_once_it_is_installed(store, capsysbinary, tmp_path):
    with connection.connect() as conn:
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
    with connection.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None


def test_a_second_session_is_asked_while_the_first_is_in_its_interval(
        store, capsysbinary, tmp_path):
    """Two sessions at once each get their own run: the interval is kept per
    session, and each request names the transcript of the session it is for."""
    argv = _with_agent(tmp_path)
    warden.began("session-2")
    with connection.connect() as conn:
        _seed(conn)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None
    out = _run("stop", {"session_id": "session-2", "transcript_path": "/tmp/two.jsonl"},
               capsysbinary, argv=argv)
    assert warden.AGENT in _context(out)
    assert "/tmp/two.jsonl" in _context(out)


def test_a_session_without_an_id_is_never_asked(store, capsysbinary, tmp_path):
    """Nothing can record the ask, so making it would repeat it every turn."""
    with connection.connect() as conn:
        _seed(conn)
    assert _run("stop", {}, capsysbinary, argv=_with_agent(tmp_path)) is None


def test_both_notes_travel_in_one_result(store, capsysbinary, tmp_path):
    """A turn that owes a checkpoint and a warden run emits one object."""
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with connection.connect() as conn:
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
    with connection.connect() as conn:
        _seed(conn)
    _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    first = warden.read("session-1")["asked_at"]
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=(*argv, "--warden-minutes", "0"))
    assert first in _context(out)


# ---------------------------------------------------------------- statusline

def test_the_statusline_carries_the_count_the_domain_and_the_checkpoint_age(
        store, capsysbinary):
    with connection.connect() as conn:
        _seed(conn)
    line = _status(capsysbinary)
    assert "4 mem" in line
    assert "acme/x100" in line
    assert "cp 0m ago" in line


def test_the_statusline_is_one_bare_line_under_eighty_characters(store, capsysbinary):
    """Plain text closed by a single newline, not the JSON a hook event emits."""
    import sys
    with connection.connect() as conn:
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
    with connection.connect() as conn:
        memories.insert_memory(conn, type="note", domain="acme/" + "x100/" * 20 + "p200",
                         content="the index rebuild runs after the row merge")
    line = _status(capsysbinary)
    assert len(line) < 80
    assert "..." in line


def test_the_busiest_domain_wins_over_the_parent_holding_nothing(store, capsysbinary):
    """A path is ranked on the memories naming it, not on its subtree."""
    with connection.connect() as conn:
        for i in range(3):
            memories.insert_memory(conn, type="note", domain="acme/x100/p200",
                             content=f"queue drain step {i}")
    assert "acme/x100/p200" in _status(capsysbinary)


def test_a_store_with_no_checkpoint_says_so(store, capsysbinary):
    with connection.connect() as conn:
        memories.insert_memory(conn, type="note", domain="acme/x100",
                         content="the report export window is inclusive")
    assert "no checkpoint" in _status(capsysbinary)


def test_the_statusline_can_be_scoped(store, capsysbinary):
    with connection.connect() as conn:
        _seed(conn)
    line = _status(capsysbinary, ["--domain", "omni/x900"])
    assert "1 mem" in line and "omni/x900" in line
    assert "acme/x100" not in line


def test_an_empty_store_has_no_statusline(store, capsysbinary):
    assert _status(capsysbinary) == ""


def test_an_empty_scope_has_no_statusline(store, capsysbinary):
    with connection.connect() as conn:
        _seed(conn)
    assert _status(capsysbinary, ["--domain", "zeta/x200"]) == ""


def test_a_store_that_cannot_be_opened_has_no_statusline(monkeypatch, capsysbinary):
    monkeypatch.setattr(connection, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert _status(capsysbinary) == ""


def test_a_payload_that_is_not_json_does_not_stop_the_statusline(store, capsysbinary):
    """The status line does not depend on stdin, so junk on it changes nothing."""
    with connection.connect() as conn:
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
    now = datetime.now(UTC)
    assert hook._age((now - delta).isoformat(), now=now) == expected


def test_a_timestamp_without_an_offset_is_read_as_utc():
    now = datetime.now(UTC)
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
    monkeypatch.setattr(connection, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert _run("session-start", {}, capsysbinary) is None


# ------------------------------------------------------------ the MCP surface

def test_the_warm_up_prompt_returns_the_same_brief(store):
    with connection.connect() as conn:
        _seed(conn)
    text = server.warm_up()
    assert "Pending in this project: 1 pitfall, 1 handoff, 1 note." in text
    assert "must_read()" in text


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
    with connection.connect() as conn:
        _seed(conn)
        store_settings.set_warden_enabled(conn, False)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None


def test_the_switch_does_not_silence_the_checkpoint_nudge(store, capsysbinary, tmp_path):
    """It turns off one note, not the hook."""
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with connection.connect() as conn:
        _seed(conn, created_at=old)
        store_settings.set_warden_enabled(conn, False)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=_with_agent(tmp_path))
    assert "note()" in _context(out)
    assert warden.AGENT not in _context(out)


def test_the_stored_interval_is_what_the_hook_uses(store, capsysbinary, tmp_path):
    """The dashboard writes it; the hook reads it without a flag."""
    argv = _with_agent(tmp_path)
    with connection.connect() as conn:
        _seed(conn)
        store_settings.set_warden_minutes(conn, 60)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    # 30 minutes on, the stored 60 has not elapsed, so nothing is asked
    later = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({**warden.read("session-1"), "asked_at": later}), encoding="utf-8")
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv) is None


def test_the_flag_overrides_the_stored_interval(store, capsysbinary, tmp_path):
    """`--warden-minutes` is for one run, the store holds the standing answer."""
    argv = _with_agent(tmp_path)
    with connection.connect() as conn:
        _seed(conn)
        store_settings.set_warden_minutes(conn, 480)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary, argv=argv)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=(*argv, "--warden-minutes", "1")) is None
    hours = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({**warden.read("session-1"), "asked_at": hours}), encoding="utf-8")
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=(*argv, "--warden-minutes", "1"))


# ------------------------------------------------------- asking about tasks

SESSION = "session-1"


def _open_tasks(count: int = 2, *, backdate: bool = False, worked: bool = True) -> None:
    """`count` open tasks in `acme/x100`, and a session that worked there;
    `backdate` ages every memory so the checkpoint nudge is owed too."""
    with connection.connect() as conn:
        for _ in range(count):
            _seed_task(conn)
        if backdate:
            old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
            conn.execute("UPDATE memories SET created_at = ?", (old,))
    if worked:
        warden.record_domain(SESSION, "acme/x100")


def _age_task_ask(session_id: str, minutes: int) -> None:
    """Move the task ask's stamp back, as if that long had passed."""
    earlier = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    warden.state_path(session_id).write_text(
        json.dumps({**warden.read(session_id), "tasks_asked_at": earlier}),
        encoding="utf-8")


ASK = ("MemAI: {count} in the domains this session worked in ({domains}). Call "
       "must_read(type='task', domain=...) for each of them. A task on the same "
       "subject can be filed under another path: call list_domains() and check "
       "any similar domain before deciding that none applies -- an empty "
       "must_read() in one domain does not mean the subject has no task. For each "
       "task this session worked on, update its items with task_item(uid, item, "
       "state, comment, related) -- a task closes itself once every item is done "
       "or dropped. If none of them is this session's work, say so in one line "
       "and stop.")


def test_stop_blocks_when_tasks_are_open(store, capsysbinary):
    _open_tasks(2)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary)
    assert out["decision"] == "block"
    assert out["reason"] == ASK.format(count="2 open tasks", domains="acme/x100")
    assert set(out) == {"decision", "reason"}
    assert warden.read(SESSION)["tasks_asked_at"]


def test_the_stop_that_follows_the_block_is_silent(store, capsysbinary):
    _open_tasks(2)
    assert _run("stop", {"session_id": SESSION}, capsysbinary)["decision"] == "block"
    assert _run("stop", {"session_id": SESSION, "stop_hook_active": True},
                capsysbinary) is None


def test_the_block_says_one_task_in_the_singular(store, capsysbinary):
    _open_tasks(1)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary)
    assert out["reason"] == ASK.format(count="1 open task", domains="acme/x100")


def _two_domains() -> None:
    with connection.connect() as conn:
        _seed_task(conn, "acme/harbor")
        _seed_task(conn, "acme/docks")


def _reason(capsysbinary, session: str = SESSION) -> str | None:
    out = _run("stop", {"session_id": session}, capsysbinary)
    return out["reason"] if out else None


def test_the_ask_covers_only_the_domains_the_session_named(store, capsysbinary):
    _two_domains()
    warden.record_domain(SESSION, "acme/harbor")
    reason = _reason(capsysbinary)
    assert reason == ASK.format(count="1 open task", domains="acme/harbor")


def test_a_parent_domain_covers_the_tasks_below_it(store, capsysbinary):
    _two_domains()
    warden.record_domain(SESSION, "acme")
    assert _reason(capsysbinary) == ASK.format(count="2 open tasks", domains="acme")


def test_the_ask_names_the_domains_that_hold_a_task_in_recorded_order(store, capsysbinary):
    _two_domains()
    for domain in ("acme/docks", "zeta/other", "acme/harbor"):
        warden.record_domain(SESSION, domain)
    reason = _reason(capsysbinary)
    assert reason == ASK.format(count="2 open tasks", domains="acme/docks, acme/harbor")


def test_a_session_that_worked_elsewhere_is_not_asked(store, capsysbinary):
    _two_domains()
    warden.record_domain(SESSION, "zeta/other")
    assert _reason(capsysbinary) is None
    assert "tasks_asked_at" not in warden.read(SESSION)


def test_a_session_that_named_no_domain_is_not_asked(store, capsysbinary):
    _two_domains()
    assert _reason(capsysbinary) is None
    assert "tasks_asked_at" not in warden.read(SESSION)


def test_a_task_listed_into_a_recorded_domain_is_counted(store, capsysbinary):
    with connection.connect() as conn:
        tasks.create_task(conn, title="Repair the pier", goal="The pier holds",
                          items=["replace the planks"], domain="acme/harbor",
                          also="acme/docks")
    warden.record_domain(SESSION, "acme/docks")
    assert _reason(capsysbinary) == ASK.format(count="1 open task", domains="acme/docks")


def test_a_task_in_two_recorded_domains_counts_once(store, capsysbinary):
    with connection.connect() as conn:
        tasks.create_task(conn, title="Repair the pier", goal="The pier holds",
                          items=["replace the planks"], domain="acme/harbor",
                          also="acme/docks")
    warden.record_domain(SESSION, "acme/harbor")
    warden.record_domain(SESSION, "acme/docks")
    reason = _reason(capsysbinary)
    assert reason == ASK.format(count="1 open task", domains="acme/harbor, acme/docks")


def test_stop_does_not_block_twice_in_one_interval(store, capsysbinary):
    _open_tasks(2)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary)["decision"] == "block"
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None


def test_stop_blocks_again_after_the_interval(store, capsysbinary):
    _open_tasks(2)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary)
    _age_task_ask("session-1", 2)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=("--task-minutes", "1"))
    assert out["decision"] == "block"


def test_the_task_flag_overrides_the_stored_interval(store, capsysbinary):
    _open_tasks(2)
    with connection.connect() as conn:
        store_settings.set_task_ask_minutes(conn, 480)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary)
    _age_task_ask("session-1", 2)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None
    assert _run("stop", {"session_id": "session-1"}, capsysbinary,
                argv=("--task-minutes", "1"))["decision"] == "block"


def test_the_block_carries_the_other_notes(store, capsysbinary, tmp_path):
    """The reason leads with the task ask, then the checkpoint nudge and the
    warden ask, joined by a blank line."""
    _open_tasks(2, backdate=True)
    out = _run("stop", {"session_id": "session-1"}, capsysbinary,
               argv=_with_agent(tmp_path))
    reason = out["reason"]
    assert reason.startswith("MemAI: 2 open tasks in the domains this session worked in")
    assert reason.index("2 open tasks") < reason.index("note()") < reason.index("memai-warden")
    assert "\n\n" in reason
    assert "hookSpecificOutput" not in out
    state = warden.read("session-1")
    assert state["tasks_asked_at"] and state["asked_at"]
    assert _run("stop", {"session_id": "session-1", "stop_hook_active": True},
                capsysbinary, argv=_with_agent(tmp_path)) is None


def test_no_block_without_open_tasks(store, capsysbinary):
    """A completed task is not open, so the output is what it was before."""
    with connection.connect() as conn:
        uid = tasks.create_task(conn, title="Ship the retry path", goal="Retries back off",
                                items=["add the backoff"], domain="acme/x100")
        tasks.set_item_state(conn, uid, tasks.item_ids(conn, uid)[0], "done")
    warden.record_domain("session-1", "acme/x100")
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None


def test_no_block_when_switched_off(store, capsysbinary):
    _open_tasks(2)
    with connection.connect() as conn:
        store_settings.set_task_ask_enabled(conn, False)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None


def test_the_task_switch_does_not_touch_the_warden_switch(store):
    with connection.connect() as conn:
        store_settings.set_task_ask_enabled(conn, False)
        assert store_settings.get_warden_enabled(conn) is True


def test_no_block_without_a_session_id(store, capsysbinary):
    """Nothing can record the ask, so making it would repeat it every turn."""
    _open_tasks(2)
    assert _run("stop", {}, capsysbinary) is None


def test_no_block_for_an_unsafe_session_id(store, capsysbinary):
    _open_tasks(2)
    assert _run("stop", {"session_id": "../escape"}, capsysbinary) is None


def test_stop_hook_active_still_returns_first(store, capsysbinary):
    _open_tasks(2)
    assert _run("stop", {"session_id": "session-1", "stop_hook_active": True},
                capsysbinary) is None
    assert "tasks_asked_at" not in warden.read("session-1")


def test_a_failed_stamp_cancels_the_block(store, capsysbinary, monkeypatch):
    """An ask the interval cannot see would repeat on every turn."""
    _open_tasks(2)
    monkeypatch.setattr(warden, "mark_tasks", lambda session_id: {})
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None


def test_a_store_that_cannot_be_opened_does_not_block(store, capsysbinary, monkeypatch):
    """The hook is attached to somebody's session: no store, no ask, exit 0."""
    def refuse(*args, **kwargs):
        raise OSError("store is not readable")
    monkeypatch.setattr(connection, "connect", refuse)
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None


def test_a_failing_task_read_asks_nothing_and_leaves_the_other_notes(
        store, capsysbinary, monkeypatch):
    """Only the task ask is lost when counting the open tasks raises."""
    import argparse

    from memai import pending
    _open_tasks(2, backdate=True)

    def refuse(*args, **kwargs):
        raise RuntimeError("cannot count")
    monkeypatch.setattr(pending, "open_task_uids", refuse)
    args = argparse.Namespace(task_minutes=None)
    assert hook._task_ask(args, {"session_id": "session-1"}) == ""
    out = _run("stop", {"session_id": "session-1"}, capsysbinary)
    assert "decision" not in out
    assert "note()" in _context(out)


def test_stop_does_not_block_over_a_task_archived_without_syncing_its_state(
        store, capsysbinary, monkeypatch):
    monkeypatch.setattr(connection, "_repair_task_states", lambda conn: None)
    with connection.connect() as conn:
        uid = _seed_task(conn)
        conn.execute("UPDATE memories SET status = 'archived' WHERE uid = ?", (uid,))
    warden.record_domain("session-1", "acme/x100")
    assert _run("stop", {"session_id": "session-1"}, capsysbinary) is None


@pytest.mark.parametrize("failing", ["_checkpoint_nudge", "_warden_ask"])
def test_a_note_that_raises_does_not_suppress_the_task_ask(
        store, capsysbinary, monkeypatch, failing):
    _open_tasks(2, backdate=True)

    def refuse(*args, **kwargs):
        raise RuntimeError("cannot compute")
    monkeypatch.setattr(hook, failing, refuse)
    out = _run("stop", {"session_id": SESSION}, capsysbinary)
    assert out["decision"] == "block"
    assert out["reason"].startswith("MemAI: 2 open tasks in the domains")


def test_a_stop_inside_the_interval_does_not_query_the_task_domains(
        store, capsysbinary, monkeypatch):
    from memai import pending
    _open_tasks(2)
    assert _run("stop", {"session_id": SESSION}, capsysbinary)["decision"] == "block"
    calls = []
    monkeypatch.setattr(pending, "open_task_uids",
                        lambda conn, domains: calls.append(domains) or [])
    assert _run("stop", {"session_id": SESSION}, capsysbinary) is None
    assert calls == []


def test_a_recorded_domain_that_names_no_path_does_not_disable_the_ask(store, capsysbinary):
    _open_tasks(2)
    state = warden.read(SESSION)
    warden.state_path(SESSION).write_text(
        json.dumps({**state, "domains": ["/", " / ", *state["domains"]]}), encoding="utf-8")
    assert _reason(capsysbinary) == ASK.format(count="2 open tasks", domains="acme/x100")
