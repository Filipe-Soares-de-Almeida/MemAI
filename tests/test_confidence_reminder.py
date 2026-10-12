"""The sporadic reminder an MCP result carries about memories left unverified too long.

Hermetic: MEMAI_HOME is a tmp dir per test, and every memory is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from conftest import shaped
from memai import server
from memai.store import connection, memories, settings

# conftest leaves the reminder on for this module
REMINDERS = True


@pytest.fixture
def clock(tmp_path, monkeypatch):
    """A store of its own, a fresh reminder schedule, and the monotonic clock in hand."""
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    monkeypatch.setattr(server, "_NEXT_REMINDER", {})
    now = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: now[0])
    return now


def _old(domain: str = "acme/x100", days: int = settings.UNVERIFIED_DAYS_DEFAULT + 1,
         **kw) -> str:
    stamp = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with connection.connect() as conn:
        return memories.insert_memory(conn, type="note", content="the queue drains",
                                      title="Queue fact", domain=domain, created_at=stamp, **kw)


def _reminded(result: dict) -> list[str]:
    return [m["uid"] for m in result.get("confidence_reminder", {}).get("memories", [])]


def test_a_result_names_what_stayed_unverified(clock):
    old = _old()
    res = server.list_domains()
    assert _reminded(res) == [old]
    assert f"over {settings.UNVERIFIED_DAYS_DEFAULT} days" in res["confidence_reminder"]["hint"]
    assert res["confidence_reminder"]["memories"][0]["age_days"] == \
        settings.UNVERIFIED_DAYS_DEFAULT + 1


def test_it_comes_back_only_after_the_interval(clock):
    _old()
    assert _reminded(server.list_domains())
    assert not _reminded(server.list_domains())
    clock[0] += settings.CONFIDENCE_ASK_MINUTES_DEFAULT * 60 - 1
    assert not _reminded(server.list_domains())
    clock[0] += 1
    assert _reminded(server.list_domains())


def test_a_one_day_span_names_a_day(clock):
    with connection.connect() as conn:
        settings.set_unverified_days(conn, 1)
    old = _old(days=2)
    res = server.list_domains()
    assert _reminded(res) == [old]
    assert "over 1 day (1 in all)" in res["confidence_reminder"]["hint"]


def test_the_interval_is_the_configured_one(clock):
    _old()
    with connection.connect() as conn:
        settings.set_confidence_ask_minutes(conn, 5)
    server.list_domains()
    clock[0] += 5 * 60
    assert _reminded(server.list_domains())


def test_young_vetted_or_archived_memories_are_left_out(clock):
    _old(days=settings.UNVERIFIED_DAYS_DEFAULT - 1)
    _old(confidence="confirmed")
    server.forget(_old(), reason="wrong subject")
    assert "confidence_reminder" not in server.list_domains()


def test_the_call_domain_comes_first(clock):
    _old("zeta")
    near = _old("acme/x100/p200")
    assert _reminded(server.pulse("acme/x100")) == [near]


def test_a_domain_with_none_falls_back_to_the_project(clock):
    far = _old("zeta")
    assert _reminded(server.pulse("acme")) == [far]


def test_switched_off_it_never_shows(clock):
    _old()
    with connection.connect() as conn:
        settings.set_confidence_ask_enabled(conn, False)
    assert "confidence_reminder" not in server.list_domains()


def test_a_refusal_carries_none_and_keeps_the_turn(clock):
    old = _old()
    with connection.connect() as conn:
        memories.insert_memory(
            conn, type="checkpoint", content=shaped("checkpoint", "an old plan"),
            title="Old bearing", domain="acme",
            created_at=(datetime.now(UTC) - timedelta(days=40)).isoformat())
    refused = server.note("Queue fact", content="the batch retries", domain="acme")
    assert refused["ok"] is False
    assert "confidence_reminder" not in refused
    assert old in _reminded(server.list_domains())


def test_set_confidence_documents_the_reminder():
    assert "confidence_reminder" in server.set_confidence.__doc__
