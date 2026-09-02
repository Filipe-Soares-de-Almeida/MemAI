"""Per-session state for the warden subagent, and the text that asks for it.

The warden is a subagent the session itself launches: it reads the turns
that happened since it last ran, searches the store, and reports back what
the memory has to say about them. Nothing here launches it -- a hook cannot
spawn a subagent, only the agent can -- so what this module does is decide
WHEN to ask and remember that it did.

The state is one file per session under `<MEMAI_HOME>/warden/`, holding the
timestamp of the last request and the transcript position the warden was
last pointed at. A hook process lives for one event, so a file is the only
place two runs can meet.

A session id comes from the host and reaches a path, so `safe_id` refuses
anything that could name a file outside the directory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memai import db

# How far an agent file may postdate a session start and still count as
# something the host had. Covers two clocks disagreeing by microseconds, and
# nothing longer: the case it must NOT swallow is an install into a session
# already under way, which is minutes old at the very least. See `loaded`.
GRACE = timedelta(seconds=2)

# The subagent this asks for: the name a host launches it by, and the file
# `install --agents` copies. Both spellings appear in text a session reads,
# so they are written once.
AGENT = "memai-warden"
AGENT_FILE = f"{AGENT}.md"

# Session ids the state directory will hold a file for. The host's own ids
# are hex and dashes; this also allows the underscores and dots a different
# host might use, and nothing else -- no separators, no `..`.
_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_DIRNAME = "warden"


def state_dir() -> Path:
    """`<MEMAI_HOME>/warden`, created if needed."""
    out = db.home() / _DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    return out


def safe_id(session_id: object) -> str:
    """`session_id` if it can name a file in the state directory, else "".

    A dot is allowed inside an id but `.` and `..` are not ids, and neither
    is anything carrying a separator.
    """
    text = str(session_id or "")
    if text in (".", ".."):
        return ""
    return text if _ID.match(text) else ""


def state_path(session_id: str) -> Path | None:
    """Where `session_id`'s state lives, or None for an id we will not write."""
    safe = safe_id(session_id)
    return state_dir() / f"{safe}.json" if safe else None


def read(session_id: str) -> dict:
    """`session_id`'s state, or {} when there is none to read.

    Unreadable is the same as absent on purpose: the caller is a hook whose
    failure costs the session its nudge, and a corrupt state file should
    make the warden run again, not raise.
    """
    path = state_path(session_id)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(session_id: str, fields: dict) -> dict:
    """Merge `fields` into `session_id`'s state and return it as written.

    {} when the id is one we will not write a file for, or when the write
    itself failed -- the caller decides what an unrecorded fact costs.
    """
    path = state_path(session_id)
    if path is None:
        return {}
    state = read(session_id)
    state.update(fields)
    try:
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return {}
    return state


def mark(session_id: str, **fields) -> dict:
    """Merge `fields` into `session_id`'s state, stamp `asked_at`, return it."""
    return _write(session_id, {**fields, "asked_at": db.now_iso()})


def began(session_id: str) -> dict:
    """Record that `session_id` has just started, without asking for anything.

    A host reads its agent definitions when it starts, so an agent installed
    afterwards is on disk without being launchable in the session that is
    already running. The start time is what `loaded` compares against.
    """
    return _write(session_id, {"started_at": db.now_iso()})


def loaded(session_id: str, agent: Path) -> bool:
    """Whether `session_id`'s host had `agent` when it read its definitions.

    False when the file is newer than the session, and false when the start
    was never recorded: an ask the host cannot serve costs the session a turn
    on a launch error, and one skipped session costs it nothing it can see.

    A file's mtime and `now()` come from different clocks, so an install and
    a start in the same breath can land microseconds out of order. GRACE is
    what keeps that from reading as "installed after" -- it separates two
    events that are simultaneous from two that are a session apart, which is
    minutes at the very least.
    """
    started = read(session_id).get("started_at")
    if not isinstance(started, str) or not started:
        return False
    try:
        at = datetime.fromisoformat(started)
        installed = datetime.fromtimestamp(agent.stat().st_mtime, timezone.utc)
    except (ValueError, OSError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return installed <= at + GRACE


def due(session_id: str, minutes: int = db.WARDEN_MINUTES_DEFAULT,
        *, now: datetime | None = None) -> bool:
    """Whether the warden is owed a run in `session_id`.

    True for a session that has never been asked, and for one whose last ask
    is older than `minutes`. An `asked_at` that cannot be parsed counts as
    never asked -- the same reasoning as `read`.
    """
    if not safe_id(session_id):
        return False
    asked = read(session_id).get("asked_at")
    if not isinstance(asked, str) or not asked:
        return True
    try:
        stamp = datetime.fromisoformat(asked)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    at = now or datetime.now(timezone.utc)
    return at - stamp >= timedelta(minutes=minutes)


def prune(days: int = 14) -> int:
    """Delete state files not modified in `days`, and say how many went.

    A session's state outlives the session, and nothing else removes it.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    gone = 0
    try:
        entries = list(state_dir().glob("*.json"))
    except OSError:
        return 0
    for path in entries:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                gone += 1
        except OSError:
            continue
    return gone


def request(session_id: str, transcript: str = "", since: str = "") -> str:
    """What to tell a session that owes the warden a run.

    Names the subagent, the transcript to read and where to start, because
    the agent launching it has none of that in front of it. `since` empty
    means the warden has not run in this session and should read the turns
    it can still see rather than the whole file.
    """
    where = f" Transcript: {transcript}." if transcript else ""
    start = (f" Read the turns after {since}." if since else
             " It has not run in this session yet; read the recent turns.")
    return (
        "MemAI: this session has not consulted the warden recently. Launch it "
        "now, in the background, and carry on with the work -- its findings "
        "arrive as a task notification, not as something to wait for: "
        f"Agent(subagent_type='{AGENT}')." + where + start +
        " If it reports a memory, READ that memory before acting on the part "
        "of the work it names."
    )
