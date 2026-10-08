"""Deciding when a session is owed a warden run, and remembering that it was.

The state is a file written by a process that lives for one hook event, so
the tests care about what a second process reads back -- and about the paths
where reading fails, because a hook that raises costs the session its whole
result.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from memai import lite, warden
from memai.store import connection, settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def test_a_session_never_asked_is_owed_a_run(store):
    """No state at all means the warden has not run here."""
    assert warden.due("session-1") is True


def test_marking_an_ask_satisfies_the_interval(store):
    """A session asked just now is not asked again on the next turn."""
    warden.mark("session-1")
    assert warden.due("session-1") is False


def test_the_ask_returns_once_the_interval_has_passed(store):
    """The interval is a floor on the cost, so it reopens after it elapses."""
    warden.mark("session-1")
    later = datetime.now(UTC) + timedelta(minutes=lite.WARDEN_MINUTES_DEFAULT + 1)
    assert warden.due("session-1", now=later) is True


def test_the_interval_is_the_caller_s_to_set(store):
    """--warden-minutes reaches here as `minutes`."""
    warden.mark("session-1")
    later = datetime.now(UTC) + timedelta(minutes=5)
    assert warden.due("session-1", 60, now=later) is False
    assert warden.due("session-1", 4, now=later) is True


def test_mark_keeps_the_fields_it_was_given(store):
    """The transcript recorded with one ask is there for the next one to read."""
    warden.mark("session-1", transcript="/tmp/a.jsonl")
    assert warden.read("session-1")["transcript"] == "/tmp/a.jsonl"
    assert warden.read("session-1")["asked_at"]


def test_a_second_mark_merges_rather_than_replaces(store):
    """A later ask that names no transcript does not erase the one on file."""
    warden.mark("session-1", transcript="/tmp/a.jsonl")
    warden.mark("session-1")
    assert warden.read("session-1")["transcript"] == "/tmp/a.jsonl"


def test_one_session_s_ask_leaves_another_owed_a_run(store):
    """The interval is per session: an ask in one does not satisfy the next."""
    warden.mark("session-1")
    assert warden.due("session-1") is False
    assert warden.due("session-2") is True


def test_two_sessions_keep_their_state_apart(store):
    """Each session's ask records its own transcript, in a file of its own."""
    warden.mark("session-1", transcript="/tmp/one.jsonl")
    warden.mark("session-2", transcript="/tmp/two.jsonl")
    assert warden.read("session-1")["transcript"] == "/tmp/one.jsonl"
    assert warden.read("session-2")["transcript"] == "/tmp/two.jsonl"
    assert {p.name for p in warden.state_dir().glob("*.json")} == {
        "session-1.json", "session-2.json"}


@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "a\\b", "../evil", "x" * 129])
def test_an_id_that_could_leave_the_directory_is_refused(store, bad):
    """A session id comes from the host and reaches a path."""
    assert warden.safe_id(bad) == ""
    assert warden.state_path(bad) is None
    assert warden.mark(bad) == {}
    assert warden.read(bad) == {}
    assert warden.due(bad) is False


def test_an_ordinary_host_id_is_accepted(store):
    """Hex and dashes, which is what a host writes."""
    ident = "30656c92-c544-4f89-9acc-e2f46bf1a402"
    assert warden.safe_id(ident) == ident


def test_an_unreadable_state_reads_as_absent(store):
    """Corrupt state makes the warden run again; it never raises."""
    warden.state_path("session-1").write_text("{not json", encoding="utf-8")
    assert warden.read("session-1") == {}
    assert warden.due("session-1") is True


def test_a_state_that_is_not_an_object_reads_as_absent(store):
    warden.state_path("session-1").write_text("[1, 2]", encoding="utf-8")
    assert warden.read("session-1") == {}


def test_an_unparseable_stamp_counts_as_never_asked(store):
    """Rather than pinning the session to an ask it can never satisfy."""
    warden.state_path("session-1").write_text(
        json.dumps({"asked_at": "yesterday"}), encoding="utf-8")
    assert warden.due("session-1") is True


def test_a_naive_stamp_is_read_as_utc(store):
    """A stamp written without an offset still compares against `now`."""
    naive = (datetime.now(UTC) - timedelta(minutes=1)).replace(
        tzinfo=None).isoformat()
    warden.state_path("session-1").write_text(
        json.dumps({"asked_at": naive}), encoding="utf-8")
    assert warden.due("session-1") is False


def test_prune_removes_what_is_old_and_keeps_what_is_not(store):
    """A session's state outlives the session, and nothing else removes it."""
    warden.mark("fresh")
    stale = warden.state_path("stale")
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 20 * 86400
    os.utime(stale, (old, old))

    assert warden.prune(days=14) == 1
    assert not stale.exists()
    assert warden.state_path("fresh").exists()


def test_the_request_names_the_agent_the_transcript_and_the_start(store):
    """The session launching it has none of that in front of it."""
    text = warden.request("session-1", transcript="/tmp/a.jsonl",
                           since="2026-08-28T10:00:00+00:00")
    assert warden.AGENT in text
    assert "/tmp/a.jsonl" in text
    assert "2026-08-28T10:00:00+00:00" in text


def test_the_request_says_so_when_the_warden_has_not_run_yet(store):
    """An empty `since` is a first run, not a start of the epoch."""
    text = warden.request("session-1", transcript="/tmp/a.jsonl")
    assert "not run in this session yet" in text


def test_an_agent_that_predates_the_session_is_loaded(store, tmp_path):
    """The host read its definitions when it started, and the file was there."""
    agent = tmp_path / "memai-warden.md"
    agent.write_text("---\nname: memai-warden\n---\n", encoding="utf-8")
    old = time.time() - 60
    os.utime(agent, (old, old))
    warden.began("session-1")
    assert warden.loaded("session-1", agent) is True


def test_an_agent_newer_than_the_session_is_not_loaded(store, tmp_path):
    """Installed behind a running host's back: on disk, not launchable."""
    warden.began("session-1")
    agent = tmp_path / "memai-warden.md"
    agent.write_text("---\nname: memai-warden\n---\n", encoding="utf-8")
    later = time.time() + 60
    os.utime(agent, (later, later))
    assert warden.loaded("session-1", agent) is False


def test_an_install_in_the_same_breath_as_the_start_still_counts(store, tmp_path):
    """A file's mtime and now() come from different clocks; GRACE covers the
    microseconds they can land out of order by."""
    warden.began("session-1")
    agent = tmp_path / "memai-warden.md"
    agent.write_text("---\nname: memai-warden\n---\n", encoding="utf-8")
    assert warden.loaded("session-1", agent) is True


def test_a_session_that_never_began_is_not_loaded(store, tmp_path):
    """Nothing to compare against, so the conservative answer stands."""
    agent = tmp_path / "memai-warden.md"
    agent.write_text("---\nname: memai-warden\n---\n", encoding="utf-8")
    assert warden.loaded("session-1", agent) is False


def test_an_agent_that_is_not_there_is_not_loaded(store, tmp_path):
    warden.began("session-1")
    assert warden.loaded("session-1", tmp_path / "gone.md") is False


def test_began_does_not_stamp_an_ask(store):
    """Starting a session leaves it owed its first warden run."""
    warden.began("session-1")
    assert warden.read("session-1")["started_at"]
    assert "asked_at" not in warden.read("session-1")
    assert warden.due("session-1") is True


def test_an_ask_keeps_the_start_on_file(store):
    """`loaded` is asked again on every later turn."""
    warden.began("session-1")
    started = warden.read("session-1")["started_at"]
    warden.mark("session-1")
    assert warden.read("session-1")["started_at"] == started


# ------------------------------------------------------- the store-wide switch

def test_the_warden_is_on_until_somebody_turns_it_off(store):
    with connection.connect() as conn:
        assert settings.get_warden_enabled(conn) is True
        assert settings.get_warden_minutes(conn) == lite.WARDEN_MINUTES_DEFAULT


@pytest.mark.parametrize("given, expected", [
    (False, False), (True, True),
    ("off", False), ("0", False), ("false", False), ("", False),
    ("on", True), ("1", True), ("true", True),
])
def test_the_switch_takes_a_bool_or_what_a_form_sends(store, given, expected):
    with connection.connect() as conn:
        assert settings.set_warden_enabled(conn, given) is expected
        assert settings.get_warden_enabled(conn) is expected


def test_the_interval_survives_the_switch(store):
    """Turning it off and on again does not lose the chosen interval."""
    with connection.connect() as conn:
        settings.set_warden_minutes(conn, 45)
        settings.set_warden_enabled(conn, False)
        settings.set_warden_enabled(conn, True)
        assert settings.get_warden_minutes(conn) == 45


@pytest.mark.parametrize("bad", [0, -5, 481, "", "soon", None, 3.7])
def test_an_interval_outside_the_range_is_refused(store, bad):
    with connection.connect() as conn, pytest.raises(ValueError):
        settings.set_warden_minutes(conn, bad)


def test_a_stored_interval_out_of_range_reads_as_the_default(store):
    """Hand-edited meta does not put the hook on a nonsense schedule."""
    with connection.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (settings.WARDEN_MINUTES_KEY, "99999"))
        assert settings.get_warden_minutes(conn) == lite.WARDEN_MINUTES_DEFAULT
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (settings.WARDEN_MINUTES_KEY, "not a number"))
        assert settings.get_warden_minutes(conn) == lite.WARDEN_MINUTES_DEFAULT


# ------------------------------------------------------- the task ask's stamp

def test_a_session_never_asked_about_tasks_is_owed_the_ask(store):
    assert warden.task_due("session-1", 30) is True


def test_marking_the_task_ask_satisfies_its_interval(store):
    warden.mark_tasks("session-1")
    assert warden.task_due("session-1", 30) is False
    later = datetime.now(UTC) + timedelta(minutes=31)
    assert warden.task_due("session-1", 30, now=later) is True


def test_the_task_ask_and_the_warden_ask_are_independent(store):
    """Stamping one leaves the other owed."""
    warden.mark_tasks("session-1")
    assert warden.due("session-1") is True
    warden.mark("session-1")
    assert warden.task_due("session-1", 30) is False
    other = "session-2"
    warden.mark(other)
    assert warden.task_due(other, 30) is True


def test_a_task_stamp_merges_with_what_is_on_file(store):
    warden.mark("session-1", transcript="/tmp/a.jsonl")
    state = warden.mark_tasks("session-1")
    assert state["tasks_asked_at"]
    assert warden.read("session-1")["transcript"] == "/tmp/a.jsonl"
    assert warden.read("session-1")["asked_at"]


def test_a_session_without_a_safe_id_is_never_owed_the_task_ask(store):
    assert warden.task_due("", 30) is False
    assert warden.task_due("../escape", 30) is False
    assert warden.mark_tasks("") == {}


def test_an_unreadable_task_stamp_counts_as_never_asked(store):
    warden.state_path("session-1").write_text(
        json.dumps({"tasks_asked_at": "not a timestamp"}), encoding="utf-8")
    assert warden.task_due("session-1", 30) is True


# --------------------------------------------------- the task ask's own settings

def test_the_task_ask_is_on_every_thirty_minutes_by_default(store):
    with connection.connect() as conn:
        assert settings.get_task_ask_enabled(conn) is True
        assert settings.get_task_ask_minutes(conn) == lite.TASK_ASK_MINUTES_DEFAULT == 30


@pytest.mark.parametrize("given, expected", [
    (False, False), (True, True), ("off", False), ("0", False), ("on", True),
])
def test_the_task_switch_takes_a_bool_or_what_a_form_sends(store, given, expected):
    with connection.connect() as conn:
        assert settings.set_task_ask_enabled(conn, given) is expected
        assert settings.get_task_ask_enabled(conn) is expected


@pytest.mark.parametrize("bad", [0, -5, 481, "", "soon", None, 3.7])
def test_a_task_interval_outside_the_range_is_refused(store, bad):
    with connection.connect() as conn, pytest.raises(ValueError):
        settings.set_task_ask_minutes(conn, bad)


def test_the_task_interval_round_trips_apart_from_the_warden_s(store):
    with connection.connect() as conn:
        assert settings.set_task_ask_minutes(conn, 45) == 45
        assert settings.get_task_ask_minutes(conn) == 45
        assert settings.get_warden_minutes(conn) == lite.WARDEN_MINUTES_DEFAULT


def test_a_stored_task_interval_out_of_range_reads_as_the_default(store):
    with connection.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (settings.TASK_ASK_MINUTES_KEY, "99999"))
        assert settings.get_task_ask_minutes(conn) == lite.TASK_ASK_MINUTES_DEFAULT


# ------------------------------------------------ the domains a session names

def test_the_domains_keep_the_most_recent_last_without_repeats(store):
    for domain in ("acme/harbor", "acme/docks", "acme/harbor"):
        warden.record_domain("session-1", domain)
    assert warden.read("session-1")["domains"] == ["acme/docks", "acme/harbor"]


def test_the_domains_keep_the_twenty_most_recent(store):
    for n in range(25):
        warden.record_domain("session-1", f"acme/p{n}")
    domains = warden.read("session-1")["domains"]
    assert domains == [f"acme/p{n}" for n in range(5, 25)]


def test_recording_a_domain_keeps_the_rest_of_the_state(store):
    warden.mark_tasks("session-1")
    warden.record_domain("session-1", "acme/harbor")
    state = warden.read("session-1")
    assert state["tasks_asked_at"] and state["domains"] == ["acme/harbor"]


def test_a_blank_domain_or_an_unsafe_id_records_nothing(store):
    assert warden.record_domain("session-1", "  ") == {}
    assert warden.record_domain("session-1", "/") == {}
    assert warden.record_domain("session-1", " / ") == {}
    assert warden.record_domain("../escape", "acme/harbor") == {}
    assert warden.read("session-1") == {}


def test_a_state_that_holds_junk_for_domains_is_started_over(store):
    warden.state_path("session-1").write_text(
        json.dumps({"domains": "acme"}), encoding="utf-8")
    warden.record_domain("session-1", "acme/harbor")
    assert warden.read("session-1")["domains"] == ["acme/harbor"]


def test_the_state_is_written_through_a_temp_file_and_replaced(store, monkeypatch):
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((a, b)), real(a, b))[1])
    warden.mark_tasks("session-1")
    (source, target), = replaced
    assert Path(target) == warden.state_path("session-1")
    assert Path(source).parent == Path(target).parent and not Path(source).exists()
    assert list(warden.state_dir().glob("*.tmp")) == []


def test_a_failed_replace_leaves_the_old_state_and_no_temp_file(store, monkeypatch):
    warden.mark_tasks("session-1")
    before = warden.read("session-1")

    def refuse(a, b):
        raise OSError("locked")
    monkeypatch.setattr(os, "replace", refuse)
    assert warden.record_domain("session-1", "acme/harbor") == {}
    assert warden.read("session-1") == before
    assert list(warden.state_dir().glob("*.tmp")) == []


def test_prune_removes_an_old_temp_file_left_by_an_interrupted_write(store):
    warden.mark("fresh")
    leftover = warden.state_dir() / "stale.abc123.tmp"
    leftover.write_text("{", encoding="utf-8")
    old = time.time() - 20 * 86400
    os.utime(leftover, (old, old))

    assert warden.prune(days=14) == 1
    assert not leftover.exists()
