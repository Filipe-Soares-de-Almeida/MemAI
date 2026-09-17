"""Telling a session that a newer memai has been released.

Three things, and the middle one is what keeps the first two cheap: the
comparison that decides what counts as newer, the cache in MEMAI_HOME that
decides when to ask again, and the note -- which hands commands to a person
and says not to run them in the session that read it.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

import memai
from memai import db, hook, server, update

NEWER = "v9.9.9"
PAGE = "https://example.com/memai/releases/v9.9.9"

# A release body in the shape a generated changelog writes one.
NOTES = """\
## [9.9.9](https://example.com/memai/compare/v9.9.8...v9.9.9) (2026-01-09)

### Features

* the queue drain reports what it could not retry ([#1042](https://example.com/pull/1042))

### Bug Fixes

* the cache warmup runs once on a cold store ([a1b2c3d](https://example.com/commit/a1b2c3d))
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _release(version: str, notes: str = NOTES, date: str = "2026-01-09") -> dict:
    return {"version": version, "url": f"https://example.com/memai/releases/{version}",
            "published_at": date, "notes": notes}


def _answers(tag: str = NEWER, url: str = PAGE, notes: str = NOTES):
    """A stub `_fetch` that always comes back with one release."""
    return lambda timeout=update.TIMEOUT: {
        "latest": tag, "url": url,
        "releases": [{"version": tag, "url": url, "published_at": "2026-01-09",
                      "notes": notes}]}


def _several(*versions: str):
    """A stub `_fetch` for a run of releases, given newest first."""
    found = [_release(v) for v in versions]
    return lambda timeout=update.TIMEOUT: {
        "latest": found[0]["version"], "url": found[0]["url"], "releases": found}


def _nothing(timeout: float = update.TIMEOUT) -> dict:
    """A stub `_fetch` for a request that did not come back with a release."""
    return {}


def _never(timeout: float = update.TIMEOUT):
    raise AssertionError("the cached answer was there to be reused")


# Bound at import, before the autouse fixture replaces the module's `_fetch`
# with a stub: the two tests below are the ones that exercise the real request
# path, against a response of their own.
_REAL_FETCH = update._fetch


class _Response:
    """What urlopen hands back, as much of it as _fetch touches."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, limit: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _github(monkeypatch, payload) -> None:
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda request, timeout=None: _Response(payload))


def _hours_ago(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


# ------------------------------------------------------------ what is newer

def test_a_tag_reads_as_its_numbers():
    assert update.parse_version("v0.1.2") == (0, 1, 2)
    assert update.parse_version("0.2") == (0, 2)


def test_a_pre_release_reads_as_the_release_it_names():
    assert update.parse_version("1.2.0-rc1") == (1, 2, 0)


def test_text_carrying_no_number_is_never_newer():
    assert update.parse_version("nightly") == ()
    assert update.is_newer("nightly", "0.1.1") is False


def test_a_shorter_version_is_padded_rather_than_ranked_short():
    assert update.is_newer("0.2", "0.2.0") is False
    assert update.is_newer("0.2.1", "0.2") is True


def test_a_local_version_above_the_release_is_not_an_update():
    """A checkout of the branch a release is cut from sits above the tag it
    was cut at, for as long as it takes to cut the next one."""
    assert update.is_newer("0.1.0", "0.1.1") is False


# ----------------------------------------------------------- when it asks

def test_an_answer_is_cached_with_its_stamp(store, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _answers())
    record = update.refresh()
    assert record["latest"] == NEWER
    assert record["url"] == PAGE
    assert record["releases"][0]["notes"] == NOTES
    assert json.loads((store / update.CACHE_NAME).read_text("utf-8")) == record
    datetime.fromisoformat(record["checked_at"])


def test_a_fresh_answer_is_not_asked_for_again(store, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _answers())
    update.refresh()
    monkeypatch.setattr(update, "_fetch", _never)
    assert update.refresh()["latest"] == NEWER


def test_an_answer_older_than_the_window_is_asked_for_again(store, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _answers("v1.0.0"))
    update.refresh(now=_hours_ago(update.TTL_HOURS + 1))
    monkeypatch.setattr(update, "_fetch", _answers("v2.0.0"))
    assert update.refresh()["latest"] == "v2.0.0"


def test_unseen_only_asks_once_and_never_again(store, monkeypatch):
    """What a session start makes: the one request nobody has an answer for
    yet. Every refresh after it belongs to the end of a turn."""
    monkeypatch.setattr(update, "_fetch", _answers())
    assert update.refresh(unseen_only=True)["latest"] == NEWER
    monkeypatch.setattr(update, "_fetch", _never)
    assert update.refresh(unseen_only=True, now=_hours_ago(72))["latest"] == NEWER


def test_the_request_can_be_turned_off(store, monkeypatch):
    monkeypatch.setenv("MEMAI_UPDATE_CHECK", "0")
    monkeypatch.setattr(update, "_fetch", _never)
    assert update.refresh() == {}
    assert not (store / update.CACHE_NAME).exists()


def test_a_cached_answer_is_still_read_with_the_request_off(store, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _answers())
    update.refresh()
    monkeypatch.setenv("MEMAI_UPDATE_CHECK", "0")
    monkeypatch.setattr(update, "_fetch", _never)
    assert update.refresh()["latest"] == NEWER


def test_a_failed_request_keeps_the_release_it_knew_and_stamps_the_cache(store, monkeypatch):
    """Otherwise an unreachable host costs a request per call, and forgets
    the release it had already been told about."""
    monkeypatch.setattr(update, "_fetch", _answers())
    first = update.refresh(now=_hours_ago(update.TTL_HOURS + 1))
    monkeypatch.setattr(update, "_fetch", _nothing)
    second = update.refresh()
    assert second["latest"] == NEWER
    assert second["checked_at"] > first["checked_at"]


def test_a_failed_request_is_retried_long_before_an_answer_would_be(store, monkeypatch):
    """A hiccup on one call decides what one hour is told, not one day."""
    monkeypatch.setattr(update, "_fetch", _nothing)
    update.refresh(now=_hours_ago(update.RETRY_HOURS + 1))
    monkeypatch.setattr(update, "_fetch", _answers())
    assert update.refresh()["latest"] == NEWER


def test_a_cache_that_cannot_be_read_is_asked_again(store, monkeypatch):
    (store / update.CACHE_NAME).write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(update, "_fetch", _answers())
    assert update.refresh()["latest"] == NEWER


# --------------------------------------------------------------- what it says

def test_nothing_is_said_about_the_version_that_is_running(store):
    assert update.notice({"latest": memai.__version__}) == ""
    assert update.notice({}) == ""


def test_the_note_names_both_versions_and_refuses_to_be_run(store):
    note = update.notice({"latest": NEWER, "url": PAGE}, local="0.1.1")
    assert NEWER in note and "0.1.1" in note
    # The template wraps, so the sentence is read with its line breaks folded.
    assert "do NOT run them yourself" in " ".join(note.split())
    assert "git -C" in note and "pull --ff-only" in note
    assert "MEMAI_UPDATE_CHECK=0" in note


# ------------------------------------------------------------ what is in it

def test_the_preview_keeps_the_entries_and_drops_their_links():
    shown = update.preview(NOTES)
    assert "Features:" in shown and "Bug Fixes:" in shown
    assert "- the queue drain reports what it could not retry" in shown
    assert "https://" not in shown
    assert "#1042" not in shown and "a1b2c3d" not in shown


def test_the_preview_flattens_what_nothing_renders_markdown():
    """A note is read as text, so an entry scoped in bold is read as one."""
    assert update.preview("* **ci:** the queue drain retries once") == \
        "- ci: the queue drain retries once"


def test_the_preview_drops_the_version_heading():
    """The note names the version in its first sentence."""
    assert "9.9.8" not in update.preview(NOTES)


def test_the_preview_is_cut_on_a_line_boundary():
    shown = update.preview(NOTES, limit=40)
    assert shown.endswith("...")
    assert "Features:" in shown
    assert "queue drain" not in shown


def test_a_first_line_over_the_limit_is_cut_in_place():
    shown = update.preview("* " + "a queue drain " * 20, limit=30)
    assert len(shown) <= 30
    assert shown.endswith("...")


def test_notes_that_are_only_a_heading_preview_as_nothing():
    assert update.preview("## [9.9.9](https://example.com) (2026-01-09)") == ""
    assert update.preview("") == ""
    assert update.preview(None) == ""


def test_the_note_carries_the_digest_and_the_page_it_came_from(store):
    note = update.notice({"latest": NEWER, "url": PAGE, "notes": NOTES}, local="0.1.1")
    assert "What they change" in note
    assert "the cache warmup runs once on a cold store" in note
    assert f"The full notes are at {PAGE}" in note


def test_a_release_with_no_notes_leaves_the_digest_out(store):
    note = update.notice({"latest": NEWER, "url": PAGE, "notes": ""}, local="0.1.1")
    assert "What they change" not in note
    assert NEWER in note and "pull --ff-only" in note


# --------------------------------------------------------- more than one release

def test_every_release_above_this_version_is_named(store):
    """An installation that skipped versions reads the ones it skipped, not
    one merged list."""
    record = {"latest": "v0.4.0",
              "releases": [_release("v0.4.0"), _release("v0.3.0"),
                           _release("v0.2.0"), _release("v0.1.0")]}
    shown = update.digest(record, local="0.2.0")
    assert shown.index("v0.4.0") < shown.index("v0.3.0")
    assert "v0.2.0" not in shown and "v0.1.0" not in shown


def test_the_headline_counts_the_releases_that_were_skipped(store):
    record = {"latest": "v0.4.0",
              "releases": [_release("v0.4.0"), _release("v0.3.0"), _release("v0.2.0")]}
    note = update.notice(record, local="0.1.1")
    assert "3 releases have been published since" in note
    assert update.banner(record, local="0.1.1") == \
        "MemAI is 3 releases behind; v0.4.0 is the newest."


def test_one_release_ahead_reads_as_one_release(store):
    record = {"latest": NEWER, "releases": [_release(NEWER)]}
    assert "releases have been published" not in update.notice(record, local="0.1.1")
    assert update.banner(record, local="0.1.1").startswith(f"MemAI {NEWER}")


def test_the_digest_is_cut_across_every_release_it_covers(store):
    record = {"latest": "v0.4.0",
              "releases": [_release("v0.4.0"), _release("v0.3.0"), _release("v0.2.0")]}
    shown = update.digest(record, local="0.1.0", limit=60)
    assert len(shown) <= 60
    assert shown.endswith("...")


def test_a_cache_from_before_releases_were_kept_still_reads(store):
    """The one written by the version that asked for the latest release
    alone."""
    legacy = {"latest": NEWER, "url": PAGE, "notes": NOTES}
    assert [r["version"] for r in update.known(legacy)] == [NEWER]
    assert "the queue drain reports what it could not retry" in update.digest(
        legacy, local="0.1.1")


def test_the_payload_is_read_by_version_without_drafts_or_pre_releases(monkeypatch):
    """Ranked on the version, not on the order the API happened to list them,
    and 0.10.0 is above 0.9.0."""
    _github(monkeypatch, [
        {"tag_name": "v0.9.0", "html_url": "https://example.com/9",
         "published_at": "2026-02-01T10:00:00Z", "body": "notes"},
        {"tag_name": "v0.10.0", "html_url": "https://example.com/10",
         "published_at": "2026-03-01T10:00:00Z", "body": "notes"},
        {"tag_name": "v0.11.0", "draft": True, "body": "notes"},
        {"tag_name": "v0.12.0-rc1", "prerelease": True, "body": "notes"},
    ])
    found = _REAL_FETCH()
    assert [r["version"] for r in found["releases"]] == ["v0.10.0", "v0.9.0"]
    assert found["latest"] == "v0.10.0"
    assert found["releases"][0]["published_at"] == "2026-03-01"


def test_a_payload_of_another_shape_reads_as_no_answer(monkeypatch):
    _github(monkeypatch, {"message": "Not Found"})
    assert _REAL_FETCH() == {}


def test_an_install_with_no_checkout_is_pointed_at_the_release_page(store, monkeypatch):
    monkeypatch.setattr(update, "checkout_root", lambda: None)
    note = update.notice({"latest": NEWER, "url": PAGE}, local="0.1.1")
    assert PAGE in note
    assert "git -C" not in note


def test_the_commands_pull_before_they_install(tmp_path):
    first, second = update.commands(tmp_path)
    assert first.startswith(f'git -C "{tmp_path}"') and first.endswith("pull --ff-only")
    if sys.platform == "win32":
        assert "install.bat" in second
    else:
        assert "pip install -e" in second and "npm run build" in second


def test_the_banner_is_one_line_for_the_person(store):
    assert update.banner({"latest": NEWER}, local="0.1.1") == \
        f"MemAI {NEWER} has been released; this session runs 0.1.1."
    assert update.banner({"latest": "0.0.1"}, local="0.1.1") == ""


def test_the_state_line_separates_unchecked_from_up_to_date(store):
    assert "no release has been checked for" in update.state_line({})
    assert "up to date" in update.state_line({"latest": "0.1.1"}, local="0.1.1")
    assert "is out" in update.state_line({"latest": NEWER, "url": PAGE}, local="0.1.1")


# ------------------------------------------------------------- who reads it

def _session_start(payload: dict, capsysbinary) -> dict | None:
    """Drive the session-start hook the way a host does."""
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        assert hook.main(["session-start"]) == 0
    finally:
        sys.stdin = sys.__stdin__
    out = capsysbinary.readouterr().out
    return json.loads(out.decode("utf-8")) if out else None


def test_the_session_start_hook_appends_the_note(store, monkeypatch, capsysbinary):
    with db.connect() as conn:
        db.insert_memory(conn, type="note", domain="acme/x100",
                         content="cache warmup runs before the first request")
    monkeypatch.setattr(update, "_fetch", _answers())
    result = _session_start({"session_id": "s1"}, capsysbinary)
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "pulse(domain)" in context
    assert f"memai {NEWER} has been released" in context
    assert result["systemMessage"].startswith(f"MemAI {NEWER}")


def test_the_session_start_hook_says_nothing_about_a_version_that_is_current(
        store, monkeypatch, capsysbinary):
    with db.connect() as conn:
        db.insert_memory(conn, type="note", domain="acme/x100",
                         content="cache warmup runs before the first request")
    monkeypatch.setattr(update, "_fetch", _answers(f"v{memai.__version__}"))
    result = _session_start({"session_id": "s1"}, capsysbinary)
    assert "has been released" not in result["hookSpecificOutput"]["additionalContext"]
    assert "systemMessage" not in result


def test_the_stop_hook_fills_the_cache_the_next_session_reads(store, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _answers())
    sys.stdin = io.StringIO(json.dumps({"session_id": "s1"}))
    try:
        assert hook.main(["stop"]) == 0
    finally:
        sys.stdin = sys.__stdin__
    assert update.cached()["latest"] == NEWER


def test_the_server_instructions_carry_the_note(store, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _answers())
    update.refresh()
    assert f"memai {NEWER} has been released" in server._instructions()


def test_the_server_instructions_ask_for_nothing_over_the_network(store, monkeypatch):
    """An MCP server reads the cache a hook wrote: on Windows, the ssl import
    a request needs deadlocks once the stdio reader threads are up."""
    monkeypatch.setattr(update, "_fetch", _never)
    assert "has been released" not in server._instructions()
