"""The release history as data: CHANGELOG.md read into versions and entries.

`releases()` returns one record per version -- the number, the date, the page
it links to, and its entries grouped under the headings they sit below -- read
from the copy packaged beside this module, or from the one at the root of a
checkout. `flatten` is what turns a line of generated markdown into the text a
reader with nothing to render it sees, and the release-check note uses it too.

Nothing here raises. A file that is absent, unreadable or in another shape
reads as no history, which is what a dashboard shows when a wheel was built
without one.
"""

from __future__ import annotations

import re
from pathlib import Path

FILENAME = "CHANGELOG.md"

# `## [0.1.1](https://…/compare/v0.1.0...v0.1.1) (2026-09-12)`, and the same
# heading without a link. The date is optional: a hand-written entry can carry
# none, and a version with no date is still a version.
_HEADING = re.compile(
    r"^##\s+\[?v?(?P<version>\d[^\]\s]*)\]?"
    r"(?:\((?P<url>[^)]*)\))?"
    r"(?:\s*[-–—]?\s*\((?P<date>\d{4}-\d{2}-\d{2})\))?\s*$")
_SECTION = re.compile(r"^#{3,}\s+(?P<title>.+?)\s*$")
_ENTRY = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")

# A markdown link, and the issue and commit references a generated changelog
# appends to every entry.
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REFERENCE = re.compile(r"\s*\((?:#\d+|[0-9a-f]{7,40})\)")


def flatten(line: object) -> str:
    """One line of generated markdown as plain text.

    `[text](url)` reads as `text`, bold loses its asterisks, and the trailing
    `(#1042)` / `(a1b2c3d)` references go -- they name a page nothing here can
    open.
    """
    return _REFERENCE.sub("", _LINK.sub(r"\1", str(line or ""))).replace("**", "").strip()


def packaged() -> Path:
    """Where an installed copy of the history lives."""
    return Path(__file__).resolve().parent / FILENAME


def source() -> Path | None:
    """The history this installation can read, or None when it has none.

    The packaged copy first, then the root of the checkout this package is
    imported from -- an editable install has no packaged copy and the file is
    two directories up from here.
    """
    found = packaged()
    if found.is_file():
        return found
    for parent in Path(__file__).resolve().parents:
        candidate = parent / FILENAME
        if candidate.is_file() and (parent / "pyproject.toml").is_file():
            return candidate
    return None


def parse(text: object) -> list[dict]:
    """Markdown into [{version, date, url, sections: [{title, entries}]}].

    Versions keep the order they are written in, which is newest first.
    Entries before any `###` heading are kept under an empty title, and a
    heading that ends up with no entries is dropped.
    """
    releases: list[dict] = []
    section: dict | None = None
    for raw in str(text or "").splitlines():
        heading = _HEADING.match(raw)
        if heading:
            releases.append({"version": heading["version"],
                             "date": heading["date"] or "",
                             "url": heading["url"] or "",
                             "sections": []})
            section = None
            continue
        if not releases:
            continue
        titled = _SECTION.match(raw)
        if titled:
            section = {"title": flatten(titled["title"]), "entries": []}
            releases[-1]["sections"].append(section)
            continue
        entry = _ENTRY.match(raw)
        if not entry:
            continue
        if section is None:
            section = {"title": "", "entries": []}
            releases[-1]["sections"].append(section)
        text_ = flatten(entry["text"])
        if text_:
            section["entries"].append(text_)
    for release in releases:
        release["sections"] = [s for s in release["sections"] if s["entries"]]
    return releases


def sections_of(notes: object) -> list[dict]:
    """One release's notes as [{title, entries}].

    For a body written the way the history is -- a version heading, then
    headed groups of entries -- this is that version's groups. A body written
    any other way comes back as one untitled group holding its lines, so
    nothing a release actually said is dropped for being unparseable.
    """
    parsed = parse(notes)
    if parsed:
        return parsed[0]["sections"]
    # A body that opens straight on its groups, with no version heading above
    # them: give it one, so the same parse reads it.
    headed = parse(f"## 0.0.0\n{notes}")
    if headed and headed[0]["sections"]:
        return headed[0]["sections"]
    entries = [flatten(line).lstrip("*-").strip()
               for line in str(notes or "").splitlines()]
    kept = [entry for entry in entries if entry and not entry.startswith("#")]
    return [{"title": "", "entries": kept}] if kept else []


def releases() -> list[dict]:
    """The parsed history, or [] when this installation ships none."""
    path = source()
    if path is None:
        return []
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return []
