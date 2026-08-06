"""What memai emits when a host fires one of its hooks.

Three events, each reading the host's hook payload on stdin and writing one
JSON object on stdout:

  session-start   the store's state, and the instruction to open the subject
  pre-compact     a reminder to checkpoint before the context is summarised
  stop            a nudge to checkpoint, only when nothing was written

`memai-hook install` registers all three with the host rather than emitting
anything -- see memai.hook_install.

No MCP: the SQLite store is opened directly, so a hook needs no server to be
running and no tool to have been loaded.

Every failure path exits 0 with no output -- no store, an unreadable one, a
payload that is not JSON, an unknown event -- so a hook cannot stop the
session it is attached to.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memai import brief, db, hook_install

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
            ". If this session established anything worth having next time -- a "
            "decision, a pitfall, where the work stands -- write it now with "
            "note() / anti_pattern() / checkpoint(). If it did not, ignore this.")
    _emit("Stop", note, system="MemAI: nothing recorded this session.")


_EVENTS = {
    "session-start": _session_start,
    "pre-compact": _pre_compact,
    "stop": _stop,
}


def _install(args) -> int:
    """Register the hooks and print the report.

    Unlike the hook events, this writes to stdout for a person and lets an
    error surface. --check exits 1 when any hook is missing.
    """
    path = (Path(args.settings) if args.settings else
            hook_install.project_settings_path() if args.project else
            hook_install.user_settings_path())
    if args.check:
        command = hook_install.hook_command()
        found = hook_install.registered(path)
        print(f"{path}:")
        stale = False
        for host_event, event in zip(hook_install.EVENTS.values(), hook_install.EVENTS):
            got = found.get(host_event)
            if got is None:
                print(f"  {host_event}: not registered")
            elif got == f"{command} {event}":
                print(f"  {host_event}: {got}")
            else:
                stale = True
                print(f"  {host_event}: registers another command -- {got}")
        return 1 if (len(found) < len(hook_install.EVENTS) or stale) else 0
    print(hook_install.install(path, write=not args.print_only))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memai-hook",
        description="Emit memai context for a host hook. Reads the hook payload on "
                    "stdin, writes one JSON object on stdout, never fails loudly. "
                    "`install` registers all of them with the host instead.")
    parser.add_argument("event", nargs="?", choices=[*sorted(_EVENTS), "install"],
                        help="which hook is calling, or `install` to register them; "
                             "may be omitted when an install flag is given")
    parser.add_argument("--domain", default="",
                        help="narrow the session-start brief to one domain path")
    parser.add_argument("--budget", type=int, default=brief.DEFAULT_BUDGET,
                        help=f"characters of context to emit at most (default {brief.DEFAULT_BUDGET})")
    parser.add_argument("--quiet-minutes", type=int, default=QUIET_MINUTES,
                        help=f"a write this recent counts as recorded (stop only, "
                             f"default {QUIET_MINUTES})")
    parser.add_argument("--project", action="store_true",
                        help="install into this project's .claude/settings.local.json "
                             "instead of the user's settings")
    parser.add_argument("--settings", default="",
                        help="install into this settings file, whatever it is")
    parser.add_argument("--check", action="store_true",
                        help="install: report whether the hooks are registered, "
                             "exit non-zero if any is missing")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="install: print the hooks block it would write, write nothing")
    args = parser.parse_args(argv)

    # `--check` and friends only mean anything to install, so they name it:
    # the flags are visible in --help before the positional is, and reaching
    # for them without it is the obvious reading.
    if args.event is None:
        if not (args.check or args.print_only or args.project or args.settings):
            parser.error("an event is required (or an install flag)")
        args.event = "install"
    if args.event == "install":
        return _install(args)

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
