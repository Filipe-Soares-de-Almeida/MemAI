"""The release history, read from the file the release process writes.

CHANGELOG.md is generated, so the tests hold the reading of it rather than
its wording: what a version heading yields, what survives the flattening a
reader with no markdown needs, and what a file that is absent or shaped
differently comes back as -- never an exception, because the dashboard asks
for this on a machine that may have been installed without one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import memai
from memai import admin, changelog, update

ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """\
# Changelog

## [0.4.0](https://example.com/compare/v0.3.0...v0.4.0) (2026-03-02)


### Features

* **queue:** drain retries what timed out ([#1042](https://example.com/pull/1042))
* export a report without the browser ([a1b2c3d](https://example.com/commit/a1b2c3d))


### Bug Fixes

* the cache warmup runs once on a cold store ([b2c3d4e](https://example.com/commit/b2c3d4e))

## 0.3.0 (2026-02-01)

### Features

* rebuild the index in one pass
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


# ----------------------------------------------------------------- the file

def test_a_version_heading_yields_its_number_date_and_page():
    first = changelog.parse(SAMPLE)[0]
    assert first["version"] == "0.4.0"
    assert first["date"] == "2026-03-02"
    assert first["url"].endswith("v0.3.0...v0.4.0")


def test_a_heading_without_a_link_is_still_a_version():
    second = changelog.parse(SAMPLE)[1]
    assert (second["version"], second["date"], second["url"]) == ("0.3.0", "2026-02-01", "")


def test_versions_keep_the_order_the_file_writes_them_in():
    assert [r["version"] for r in changelog.parse(SAMPLE)] == ["0.4.0", "0.3.0"]


def test_entries_are_grouped_under_the_heading_they_sit_below():
    sections = changelog.parse(SAMPLE)[0]["sections"]
    assert [s["title"] for s in sections] == ["Features", "Bug Fixes"]
    assert len(sections[0]["entries"]) == 2
    assert sections[1]["entries"] == ["the cache warmup runs once on a cold store"]


def test_an_entry_is_flattened_for_a_reader_with_no_markdown():
    first = changelog.parse(SAMPLE)[0]["sections"][0]["entries"][0]
    assert first == "queue: drain retries what timed out"


def test_a_heading_with_no_entries_is_dropped():
    assert changelog.parse("## 1.0.0 (2026-01-01)\n\n### Features\n") == [
        {"version": "1.0.0", "date": "2026-01-01", "url": "", "sections": []}]


def test_text_that_is_not_a_changelog_reads_as_no_history():
    assert changelog.parse("") == []
    assert changelog.parse(None) == []
    assert changelog.parse("just a paragraph about nothing") == []


def test_notes_in_another_shape_keep_their_lines():
    """A release body nobody generated still says something, and dropping it
    for being unparseable would be the one case where the UI goes blank."""
    assert changelog.sections_of("first thing\nsecond thing") == [
        {"title": "", "entries": ["first thing", "second thing"]}]
    assert changelog.sections_of("") == []


def test_notes_shaped_like_the_history_read_as_its_groups():
    assert [s["title"] for s in changelog.sections_of(SAMPLE)] == ["Features", "Bug Fixes"]


# -------------------------------------------------------------- where it is

def test_the_packaged_copy_is_read_before_the_checkout(tmp_path, monkeypatch):
    packaged = tmp_path / changelog.FILENAME
    packaged.write_text("## 9.9.9 (2026-01-01)\n\n* packaged\n", encoding="utf-8")
    monkeypatch.setattr(changelog, "packaged", lambda: packaged)
    assert changelog.source() == packaged
    assert changelog.releases()[0]["version"] == "9.9.9"


def test_a_checkout_without_a_packaged_copy_reads_its_own(monkeypatch):
    monkeypatch.setattr(changelog, "packaged", lambda: Path("nowhere") / changelog.FILENAME)
    assert changelog.source() == ROOT / changelog.FILENAME


def test_an_install_with_no_history_reads_as_none(monkeypatch):
    monkeypatch.setattr(changelog, "source", lambda: None)
    assert changelog.releases() == []


def test_the_wheel_carries_the_history():
    """Without this the file is a checkout's, and every other install renders
    an empty changelog."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    forced = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert forced[changelog.FILENAME] == f"memai/{changelog.FILENAME}"


# ------------------------------------------------------------- what is served

def test_the_endpoint_marks_the_version_that_is_running(client, monkeypatch):
    """Three states, and the installed one is whatever this package says it
    is -- a release cut later moves the line without touching this."""
    history = (f"## {memai.__version__} (2026-03-02)\n\n### Features\n\n* a thing\n"
               "\n## 0.0.1 (2026-01-01)\n\n### Features\n\n* an older thing\n")
    monkeypatch.setattr(changelog, "releases", lambda: changelog.parse(history))
    body = client.get("/api/changelog").json()
    assert body["current"] == memai.__version__
    assert [r["state"] for r in body["releases"]] == ["installed", "past"]
    assert body["source"] is True


def test_a_release_above_this_version_is_marked_ahead(client, monkeypatch):
    monkeypatch.setattr(changelog, "releases", lambda: changelog.parse(SAMPLE))
    monkeypatch.setattr(update, "cached", lambda: {
        "latest": "v9.9.9", "url": "https://example.com/9",
        "releases": [{"version": "v9.9.9", "url": "https://example.com/9",
                      "published_at": "2026-04-01", "notes": "### Features\n\n* a thing"}]})
    body = client.get("/api/changelog").json()
    top = body["releases"][0]
    assert (top["version"], top["state"]) == ("9.9.9", "ahead")
    assert top["sections"] == [{"title": "Features", "entries": ["a thing"]}]
    assert body["update"]["behind"] == 1
    assert body["update"]["latest"] == "v9.9.9"


def test_the_shipped_file_wins_for_a_version_in_both(client, monkeypatch):
    """The cache and the file can both describe one version; what this
    installation actually holds is the file."""
    monkeypatch.setattr(changelog, "releases", lambda: changelog.parse(SAMPLE))
    monkeypatch.setattr(update, "cached", lambda: {
        "latest": "v0.4.0",
        "releases": [{"version": "v0.4.0", "url": "https://example.com/4",
                      "published_at": "2026-03-02", "notes": "* from the api"}]})
    body = client.get("/api/changelog").json()
    top = body["releases"][0]
    assert top["version"] == "0.4.0"
    assert top["sections"][0]["title"] == "Features"


def test_the_update_endpoint_answers_without_the_network(client, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _no_request)
    body = client.get("/api/update").json()
    assert body["current"] == memai.__version__
    assert body["latest"] == "" and body["behind"] == 0


def _no_request(timeout: float = update.TIMEOUT) -> dict:
    raise AssertionError("the dashboard asked GitHub for something")
