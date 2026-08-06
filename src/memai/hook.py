"""Host hooks: put the memory in front of the agent instead of asking for it.

An MCP server cannot make an agent call anything. The usual workaround is a
SessionStart hook whose text says "call pulse first", which costs a decision
the agent may not make, a tool round-trip, and a paragraph of contingency
for the MCP connection not being up yet. This is the other half: the same
hooks, emitting the memory ITSELF as context.

Nothing here touches MCP. It opens the SQLite store directly, so there is no
server to wait for, no tool to have been loaded, and no race with the host's
own startup -- which is the entire reason the instruction-shaped version
needed that paragraph.

Four events, each reading the host's hook payload on stdin and writing one
JSON object on stdout:

  session-start   the store's state, as context, before the first message
  user-prompt     what the store holds about what was just asked
  pre-compact     a reminder to checkpoint before the context is summarised
  stop            a nudge to checkpoint, only when nothing was written

It must never break the session it is attached to. Every failure path exits
0 with no output: no store yet, an unreadable one, a payload that is not
JSON, an unknown event. A memory server that stops a host from starting is
worse than one that stays quiet.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from memai import brief, db

# How long after the last write a Stop hook assumes the session already
# recorded what it learned. Long enough to cover a stretch of reading and
# discussion after the last note; short enough that a session which wrote
# nothing at all still gets asked.
QUIET_MINUTES = 45


def _payload() -> dict:
    """The host's hook JSON, or {} for anything we cannot read."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _emit(event: str, context: str, *, system: str = "") -> None:
    """One hook result on stdout. Silence when there is nothing to add.

    Written as UTF-8 bytes rather than through sys.stdout: a hook runs with
    whatever console encoding the host handed it, which on Windows is
    routinely cp1252, and a memory whose content has a character outside it
    would raise on the way out -- swallowed by main()'s catch, leaving a
    hook that silently emits nothing for exactly the sessions with the most
    interesting text in the store.
    """
    if not context:
        return
    out: dict = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
    if system:
        out["systemMessage"] = system
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def _session_start(args, payload) -> None:
    with db.connect() as conn:
        _emit("SessionStart", brief.session_brief(conn, domain=args.domain, budget=args.budget))


def _user_prompt(args, payload) -> None:
    """Recall against the user's own words.

    The session-start brief cannot know the subject -- nobody does yet. By
    the time a prompt arrives its text IS the query, which makes this the
    place domain-aware recall actually belongs, and it needs no one to
    guess a domain in advance.
    """
    with db.connect() as conn:
        _emit("UserPromptSubmit",
              brief.prompt_brief(conn, payload.get("prompt", ""),
                                 limit=args.limit, budget=args.budget))


def _pre_compact(args, payload) -> None:
    _emit("PreCompact",
          "The context is about to be summarised. Anything worth keeping past this "
          "point belongs in the memai store, not in the transcript: checkpoint() "
          "where the work stands, note() what was established, anti_pattern() what "
          "turned out to be a trap.")


def _wrote_recently(conn, minutes: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM memories WHERE created_at >= ? LIMIT 1", (cutoff,)).fetchone()
    return row is not None


def _stop(args, payload) -> None:
    """Ask for a checkpoint, but only from a session that recorded nothing.

    A timer-based nudge fires whether or not there is anything to record,
    which teaches the agent to skip it. This one reads the store: if
    something was written in the last stretch, the session already did the
    thing being asked for, and it stays quiet.
    """
    if payload.get("stop_hook_active"):
        return
    with db.connect() as conn:
        if _wrote_recently(conn, args.quiet_minutes):
            return
        empty = not conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
    note = ("Nothing has been written to the memai store" +
            ("" if empty else f" in the last {args.quiet_minutes} minutes") +
            ". If this session established anything worth having next time — a "
            "decision, a pitfall, where the work stands — write it now with "
            "note() / anti_pattern() / checkpoint(). If it did not, ignore this.")
    _emit("Stop", note, system="MemAI: nothing recorded this session.")


_EVENTS = {
    "session-start": _session_start,
    "user-prompt": _user_prompt,
    "pre-compact": _pre_compact,
    "stop": _stop,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memai-hook",
        description="Emit memai context for a host hook. Reads the hook payload on "
                    "stdin, writes one JSON object on stdout, never fails loudly.")
    parser.add_argument("event", choices=sorted(_EVENTS),
                        help="which hook is calling")
    parser.add_argument("--domain", default="",
                        help="narrow the session-start brief to one domain path")
    parser.add_argument("--budget", type=int, default=brief.DEFAULT_BUDGET,
                        help=f"characters of context to emit at most (default {brief.DEFAULT_BUDGET})")
    parser.add_argument("--limit", type=int, default=3,
                        help="memories to surface per prompt (user-prompt only)")
    parser.add_argument("--quiet-minutes", type=int, default=QUIET_MINUTES,
                        help=f"a write this recent counts as recorded (stop only, "
                             f"default {QUIET_MINUTES})")
    args = parser.parse_args(argv)

    payload = _payload()
    try:
        _EVENTS[args.event](args, payload)
    except Exception:
        # Deliberately silent, deliberately zero. A hook that cannot read the
        # store has nothing to say; a hook that says so on stderr and exits
        # non-zero interrupts a session over a memory server being absent.
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - console-script entry point
    raise SystemExit(main())
