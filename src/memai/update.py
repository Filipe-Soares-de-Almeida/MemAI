"""Whether a newer memai has been released, and what to run to get it.

`refresh` asks the GitHub releases API of the repository memai is published
from and caches the answer -- one record per release, with its page and its
notes -- under MEMAI_HOME; `notice` turns the releases above this package's own
version into the note a session reads, with a `digest` of what each of them
changed. Setting MEMAI_UPDATE_CHECK to 0/false/no/off stops the request -- an
answer already cached is still read.

Two constraints decide who calls what. The request belongs to a hook process:
on Windows, loading a C extension once the stdio reader threads are up
deadlocks on the loader lock (see memai.autostart) and ssl is one, so an MCP
server reads the cache and never fills it. And the commands the note carries
are for a person to run with the host closed -- Windows locks an .exe while a
process is running it, so an install cannot rewrite `.venv\\Scripts` while a
session holds the console scripts in it.

Nothing here raises. An unreachable host, a payload of another shape, a cache
that cannot be written: each leaves the caller with what was known before,
which is usually nothing to report.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memai import __version__, changelog, db

REPO = "Filipe-Soares-de-Almeida/MemAI"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

# Releases asked for in one request, newest first. A machine that has skipped
# a few versions still gets every one of them above its own; beyond this many
# the note says how far behind it is and the page carries the rest.
PER_PAGE = 20
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page={PER_PAGE}"

CACHE_NAME = "update.json"

# Hours an answer is used before another request is made. The API allows 60
# unauthenticated requests an hour per address, shared with everything else
# running on it.
TTL_HOURS = 24

# Hours before a request that did not come back is tried again. A hiccup on
# one call must not decide what a whole day is told.
RETRY_HOURS = 1

# Seconds one request may take. It is paid at the end of a turn, and once by a
# session that has never checked.
TIMEOUT = 2.0

# Characters of notes the cache keeps per release, and characters a note shows
# across every release it covers. A session behind a release reads the digest
# every time it starts, so what it gets is the shape of what changed and not
# the changelog -- which the dashboard renders in full.
NOTES_STORED = 4000
NOTES_SHOWN = 700

# What MEMAI_UPDATE_CHECK has to say to stop the request. Unset means on.
_OFF = {"0", "false", "no", "off"}


def enabled() -> bool:
    """Whether MEMAI_UPDATE_CHECK allows the request."""
    return os.environ.get("MEMAI_UPDATE_CHECK", "").strip().lower() not in _OFF


def parse_version(text: object) -> tuple[int, ...]:
    """The leading numeric components of a version: 'v0.1.2' -> (0, 1, 2).

    Everything from the first non-numeric component on is dropped, so a
    pre-release tag reads as the release it names. () for text carrying no
    number at all.
    """
    match = re.match(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(candidate: object, current: str = __version__) -> bool:
    """Whether `candidate` names a release above `current`.

    The shorter of the two is padded with zeros, so 0.2 does not read as
    older than 0.2.0. Text with no number in it is never newer.
    """
    left, right = parse_version(candidate), parse_version(current)
    if not left:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def cache_path() -> Path:
    return db.home() / CACHE_NAME


def cached() -> dict:
    """The last answer written, or {} when there is none to read."""
    try:
        record = json.loads(cache_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _write(record: dict) -> None:
    try:
        cache_path().write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _fetch(timeout: float = TIMEOUT) -> dict:
    """The published releases as {latest, url, releases}, or {} for a request
    that did not come back with any.

    A release is {version, url, published_at, notes}, newest first, drafts and
    pre-releases left out. Each body is cut at NOTES_STORED characters: the
    cache is read at every session start and nothing reads the tail of a long
    one.

    urllib rather than a socket of our own because this request leaves the
    machine, and the proxy environment variables it honours are how it gets
    out of a network that has one.
    """
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"memai/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - literal https URL
            payload = json.loads(response.read(1 << 16).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - deliberate: see the module docstring
        return {}
    if not isinstance(payload, list):
        return {}
    releases = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("draft") or entry.get("prerelease"):
            continue
        tag = str(entry.get("tag_name", "") or "")
        if not tag:
            continue
        releases.append({
            "version": tag,
            "url": str(entry.get("html_url", "") or RELEASES_PAGE),
            "published_at": str(entry.get("published_at", "") or "")[:10],
            "notes": str(entry.get("body", "") or "")[:NOTES_STORED],
        })
    if not releases:
        return {}
    releases.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    return {"latest": releases[0]["version"], "url": releases[0]["url"],
            "releases": releases}


def _due(record: dict, *, hours: int, now: datetime) -> bool:
    """Whether the cached answer is old enough to ask again.

    A record with no readable stamp is due, which is what refills a cache
    somebody emptied or truncated.
    """
    try:
        checked = datetime.fromisoformat(str(record["checked_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return now - checked >= timedelta(hours=hours)


def refresh(*, unseen_only: bool = False, hours: int = TTL_HOURS,
            timeout: float = TIMEOUT, now: datetime | None = None) -> dict:
    """The cached answer, asking GitHub for a new one when one is due.

    `unseen_only` asks only when nothing has ever been cached. A request that
    fails stamps the cache anyway and keeps the release it already knew, so an
    unreachable host costs one attempt per window rather than one per call --
    RETRY_HOURS after a failure, `hours` after an answer.
    """
    record = cached()
    if not enabled():
        return record
    moment = now or datetime.now(timezone.utc)
    window = min(hours, RETRY_HOURS) if record.get("failed") else hours
    due = not record if unseen_only else _due(record, hours=window, now=moment)
    if not due:
        return record
    found = _fetch(timeout)
    stamped = {
        "checked_at": moment.isoformat(),
        "latest": found.get("latest") or str(record.get("latest", "")),
        "url": found.get("url") or str(record.get("url", "")) or RELEASES_PAGE,
        "releases": found.get("releases") if found else record.get("releases", []),
        "failed": not found,
    }
    _write(stamped)
    return stamped


def checkout_root() -> Path | None:
    """The git checkout this package is imported from, or None.

    None for an installation with no checkout above it -- a wheel in
    site-packages -- which nothing here knows how to update.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def commands(root: Path | None = None) -> list[str]:
    """What updates a checkout, in the order it has to run. [] without one.

    On Windows the second half is install.bat, which does the environment and
    the dashboard build; elsewhere both are spelled out, against this
    interpreter, so the pip that runs is the one memai is installed in.
    """
    root = checkout_root() if root is None else root
    if root is None:
        return []
    pull = f'git -C "{root}" pull --ff-only'
    if sys.platform == "win32":
        installer = root / "install.bat"
        return [pull, f'"{installer}"']
    return [pull, f'cd "{root}" && "{sys.executable}" -m pip install -e ".[dev]" '
                  f'&& npm ci && npm run build']


def _lines(notes: object) -> list[str]:
    """One release's notes as plain lines.

    Markdown is flattened for a reader with nothing to render it (see
    changelog.flatten), a bullet becomes a dash, a section heading loses its
    hashes, and the version heading goes -- whoever shows these lines names
    the version itself.
    """
    out: list[str] = []
    for raw in str(notes or "").splitlines():
        if raw.lstrip().startswith("## "):
            continue
        line = changelog.flatten(raw)
        if not line:
            continue
        if line.startswith("#"):
            out.append(f"{line.lstrip('#').strip().rstrip(':')}:")
        elif line[:2] in ("* ", "- "):
            out.append(f"-{line[1:]}")
        else:
            out.append(line)
    return out


def _cut(lines: list[str], limit: int) -> str:
    """`lines` joined, stopping before `limit` characters. A cut ends on a line
    boundary and is marked with `...`; a single line over the limit is cut in
    place instead."""
    kept: list[str] = []
    room = limit
    for line in lines:
        if len(line) + 1 > room:
            kept.append("...")
            break
        kept.append(line)
        room -= len(line) + 1
    if kept == ["..."]:
        return lines[0][:max(limit - 3, 0)].rstrip() + "..."
    return "\n".join(kept)


def preview(notes: object, *, limit: int = NOTES_SHOWN) -> str:
    """One release's notes as plain lines, cut to `limit` characters."""
    return _cut(_lines(notes), limit)


def known(record: dict | None = None) -> list[dict]:
    """Every release the cache holds, newest first.

    A cache written before releases were kept one by one carries only the
    newest, in `latest`/`url`/`notes`; it reads as that single release.
    """
    record = cached() if record is None else record
    found = record.get("releases")
    if isinstance(found, list) and found:
        return [entry for entry in found if isinstance(entry, dict)]
    latest = str(record.get("latest", ""))
    if not latest:
        return []
    return [{"version": latest, "url": str(record.get("url") or RELEASES_PAGE),
             "published_at": "", "notes": str(record.get("notes", ""))}]


def ahead(record: dict | None = None, *, local: str = __version__) -> list[dict]:
    """The releases above `local`, newest first."""
    return [entry for entry in known(record) if is_newer(entry.get("version"), local)]


def digest(record: dict | None = None, *, local: str = __version__,
           limit: int = NOTES_SHOWN) -> str:
    """What every release above `local` changed, newest first, cut to `limit`.

    Each release names itself, so an installation several versions behind
    reads the versions it skipped rather than one merged list.
    """
    lines: list[str] = []
    for release in ahead(record, local=local):
        said = _lines(release.get("notes"))
        if not said:
            continue
        date = str(release.get("published_at", ""))
        lines.append(f"{release.get('version', '')}{f' ({date})' if date else ''}:")
        lines.extend(said)
    return _cut(lines, limit)


# The three pieces of the note, in the order notice() joins them: what
# happened, what is in it when the release came with notes, and what to do
# about it -- one form for a checkout, one for an install no command here
# reaches. notice() indents the commands and the preview, which is what keeps
# each a block to read as one.
HEADLINE = ("NOTE: memai {latest} has been released and this session is "
            "running {local}.")

# The same, when more than one release was published since this version.
BEHIND = ("NOTE: this session is running memai {local}, and {n} releases have "
          "been published since -- {latest} is the newest.")

CHANGES = """\
What they change, newest first:

{changes}

The full notes are at {url}."""

UPDATE_AVAILABLE = """\
Say so in your first reply and hand these over, exactly as written -- do NOT
run them yourself. They rewrite the environment this server is running from,
and Windows locks an .exe while a process is running it, so they work only
once every Claude session and the dashboard are closed:

{commands}

The host is started again afterwards, and the next session is the one that
loads the new version. MEMAI_UPDATE_CHECK=0 turns this check off."""

UPDATE_ELSEWHERE = """\
This installation is not a git checkout, so there is no command to hand over:
say it is out of date and point at {url}, to be updated the way it was
installed. MEMAI_UPDATE_CHECK=0 turns this check off."""


def newest(record: dict | None = None) -> str:
    """The highest release the cache knows of, or "" when it knows none."""
    record = cached() if record is None else record
    latest = str(record.get("latest", ""))
    if latest:
        return latest
    found = known(record)
    return str(found[0].get("version", "")) if found else ""


def notice(record: dict | None = None, *, local: str = __version__) -> str:
    """The note a session reads, or "" when nothing newer is known.

    Reads the cache when no record is given, and never makes the request
    itself. The digest is dropped rather than replaced when the releases came
    with no notes.
    """
    record = cached() if record is None else record
    latest = newest(record)
    if not is_newer(latest, local):
        return ""
    url = str(record.get("url") or RELEASES_PAGE)
    count = len(ahead(record, local=local))
    parts = [BEHIND.format(latest=latest, local=local, n=count) if count > 1
             else HEADLINE.format(latest=latest, local=local)]
    shown = digest(record, local=local)
    if shown:
        parts.append(CHANGES.format(
            changes="\n".join(f"  {line}" for line in shown.splitlines()), url=url))
    lines = commands()
    if not lines:
        parts.append(UPDATE_ELSEWHERE.format(url=url))
        return "\n\n".join(parts)
    parts.append(UPDATE_AVAILABLE.format(
        commands="\n".join(f"  {line}" for line in lines)))
    # The page is named once. The preview carries it when there is one, and
    # this is the case where there is not.
    if not shown:
        parts.append(f"The release is at {url}.")
    return "\n\n".join(parts)


def banner(record: dict | None = None, *, local: str = __version__) -> str:
    """One line for the person, or "" when nothing newer is known."""
    record = cached() if record is None else record
    latest = newest(record)
    if not is_newer(latest, local):
        return ""
    count = len(ahead(record, local=local))
    if count > 1:
        return f"MemAI is {count} releases behind; {latest} is the newest."
    return f"MemAI {latest} has been released; this session runs {local}."


def state_line(record: dict | None = None, *, local: str = __version__) -> str:
    """What the cache says about this version, for a person reading a report."""
    record = cached() if record is None else record
    latest = newest(record)
    if not latest:
        off = "" if enabled() else " (MEMAI_UPDATE_CHECK is off)"
        return f"memai {local}: no release has been checked for{off}"
    if is_newer(latest, local):
        count = len(ahead(record, local=local))
        behind = f", {count} releases behind" if count > 1 else ""
        page = record.get("url") or RELEASES_PAGE
        return f"memai {local}: {latest} is out{behind} -- {page}"
    return f"memai {local}: up to date (latest release {latest})"
