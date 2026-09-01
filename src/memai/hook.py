"""What memai emits when a host fires one of its hooks.

Three events, each reading the host's hook payload on stdin and writing one
JSON object on stdout:

  session-start   the store's state, and the instruction to open the subject
  pre-compact     a reminder to checkpoint before the context is summarised
  stop            a nudge to checkpoint when nothing was written, and a
                  request for the warden subagent when one is owed

A fourth reads the call the host is about to make instead of the store, and
is the one exception to everything the last paragraph of this docstring says:

  guard           refuses a memai write whose required text never arrived

One more subcommand reads the store the same way and writes plain text
instead:

  statusline      the store in a single line, for a host's status line

`memai-hook install` registers the four events in the user's settings, and
`install --skills` / `install --agents` copy the bundled skills and subagent
definitions into place, rather than emitting anything -- see
memai.hook_install.

No MCP: the SQLite store is opened directly, so a hook needs no server to be
running and no tool to have been loaded.

Every failure path exits 0 with no output -- no store, an unreadable one, a
payload that is not JSON, an unknown event -- so a hook cannot stop the
session it is attached to. `guard` is the deliberate exception: stopping the
call IS what it is for, and it exits 2 to do it. It never reads the store,
so the only way it can fail is by refusing, and it refuses only on a payload
it read and understood.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memai import brief, db, guard, hook_install, warden

# How long after the last write a Stop hook assumes the session already
# recorded what it learned. Long enough to cover a stretch of reading and
# discussion after the last note; short enough that a session which wrote
# nothing at all still gets asked.
QUIET_MINUTES = 45

# Characters the statusline may occupy. A host renders it on one row beside
# whatever else it shows, so the line has to fit without wrapping.
STATUS_WIDTH = 79


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
    # Both before the brief and never at its expense. `began` is what later
    # tells an agent the host loaded from one installed behind its back, and
    # a warden state left by a session that ended is nobody else's to clean up.
    try:
        warden.began(payload.get("session_id", ""))
        warden.prune()
    except Exception:
        pass
    with db.connect() as conn:
        _emit("SessionStart", brief.session_brief(
            conn, domain=args.domain, budget=args.budget, store=db.active_store()))


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


def _checkpoint_nudge(args) -> str:
    """Ask for a checkpoint, but only from a session that recorded nothing.

    A timer-based nudge fires whether or not there is anything to record,
    which teaches the agent to skip it. This one reads the store: if
    something was written in the last stretch, the session already did the
    thing being asked for, and it returns "".
    """
    with db.connect() as conn:
        if _wrote_recently(conn, args.quiet_minutes):
            return ""
        empty = not conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
    return ("Nothing has been written to the memai store" +
            ("" if empty else f" in the last {args.quiet_minutes} minutes") +
            ". If this session established anything worth having next time -- a "
            "decision, a pitfall, where the work stands -- write it now with "
            "note() / anti_pattern() / checkpoint(). If it did not, ignore this.")


def _agent_path(args) -> Path:
    """Where the warden definition belonging to this install lives."""
    settings = Path(args.settings) if args.settings else hook_install.user_settings_path()
    return hook_install.agents_dir(settings) / warden.AGENT_FILE


def _warden_launchable(args, session_id: str) -> bool:
    """Whether this session's host can actually launch the warden.

    Two conditions, and the second is not implied by the first: the
    definition has to be on disk, AND it has to have been there when the host
    read its agents, which it does once at startup. A definition somebody
    edited still counts -- the prompt is theirs to change.
    """
    agent = _agent_path(args)
    state = hook_install.agent_state(agent.parent)
    if state.get(warden.AGENT_FILE) not in ("installed", "edited"):
        return False
    return warden.loaded(session_id, agent)


def _warden_ask(args, payload) -> str:
    """Ask the session to launch the warden, or "" when it is not owed one.

    The ask is recorded BEFORE it is returned, and a state that could not be
    written cancels it: a request the interval cannot see is a request that
    repeats every turn, which is the noise this whole mechanism dies of.
    """
    session_id = payload.get("session_id", "")
    with db.connect() as conn:
        if not db.get_warden_enabled(conn):
            return ""
        # The flag is an override for one run; the store holds the standing
        # answer, which is what the dashboard writes.
        minutes = (args.warden_minutes if args.warden_minutes is not None
                   else db.get_warden_minutes(conn))
    if not warden.due(session_id, minutes):
        return ""
    if not _warden_launchable(args, session_id):
        return ""
    since = warden.read(session_id).get("asked_at", "")
    transcript = str(payload.get("transcript_path", "") or "")
    if not warden.mark(session_id, transcript=transcript):
        return ""
    return warden.request(session_id, transcript=transcript, since=since)


def _stop(args, payload) -> None:
    """Whatever the store has to say at the end of a turn, as one result.

    Both notes read state before they speak, and either can be silent, so a
    turn with nothing to say emits nothing at all.
    """
    if payload.get("stop_hook_active"):
        return
    notes, systems = [], []
    for note, system in ((_checkpoint_nudge(args), "nothing recorded this session"),
                         (_warden_ask(args, payload), "warden is owed a run")):
        if note:
            notes.append(note)
            systems.append(system)
    if notes:
        _emit("Stop", "\n\n".join(notes), system="MemAI: " + "; ".join(systems) + ".")


def _line(text: str) -> None:
    """One plain line on stdout, as UTF-8 bytes. Silence when empty.

    A status line is rendered verbatim, so this writes the text itself and
    not the {"hookSpecificOutput": ...} object the hook events emit. Bytes
    rather than sys.stdout for the reason given in _emit.
    """
    if not text:
        return
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _age(iso: str, *, now: datetime | None = None) -> str:
    """An ISO-8601 timestamp as a coarse age: `7m`, `3h`, `9d`.

    A timestamp carrying no offset is read as UTC, which is what the store
    writes. A timestamp in the future reads as `0m`.
    """
    stamp = datetime.fromisoformat(iso)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    seconds = max((now or datetime.now(timezone.utc)) - stamp, timedelta(0)).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _status_text(total: int, domain: str, age: str) -> str:
    """The status line: how much is stored, the busiest domain, how old the
    latest checkpoint is, joined by ` | ` and at most STATUS_WIDTH characters.

    The domain path is the field that gives way when the line is too long,
    cut to its tail behind a leading `...`; below the room that leaves
    anything readable it is dropped instead.
    """
    head = f"memai {total} mem"
    tail = f"cp {age} ago" if age else "no checkpoint"
    room = STATUS_WIDTH - len(head) - len(tail) - 2 * len(" | ")
    if domain and len(domain) > room:
        domain = "..." + domain[-(room - 3):] if room > 8 else ""
    return " | ".join(p for p in (head, domain, tail) if p)


def _statusline(args, payload) -> None:
    """The store in one line for a host's status line, or nothing at all.

    An empty store -- or an empty `--domain` scope -- emits no line.
    """
    with db.connect() as conn:
        census = db.domain_census(conn, args.domain)
        if not census["total"]:
            return
        tree = [d for d in db.list_domains(conn)
                if not args.domain or db.in_domain(d["domain"], args.domain)]
        checkpoint = db.latest_by_type(conn, "checkpoint", domain=args.domain,
                                       exclude_contradicted=True)
    # Ranked on the memories naming the path itself -- filed there plus
    # cross-listed there -- not on the subtree, so an implicit parent never
    # outranks its child. list_domains orders by recency, so max() takes the
    # most recent of equal scores.
    busiest = max(tree, key=lambda d: d["count"] + d["also"])["domain"] if tree else ""
    age = _age(checkpoint["created_at"]) if checkpoint is not None else ""
    _line(_status_text(census["total"], busiest, age))


def _guard(payload: dict) -> int:
    """Read the call the host is about to make; 2 to refuse it, 0 to allow.

    Refusing writes the reason on stderr, which is where a host shows the
    model what it did wrong. A call that goes through with an optional
    parameter missing gets a systemMessage instead, so a write is never
    interrupted over something that is not an error.

    Anything unrecognised is allowed: a tool of another server, one memai
    does not guard, a payload without an input, and anything at all that
    raises on the way. The matcher this hook is registered with lives in a
    file people edit, and the cost of a wrong refusal is a session that
    cannot write to its memory -- so everything this is not sure about goes
    through.
    """
    try:
        call = str(payload.get("tool_name", ""))
        tool = guard.tool_of(call)
        params = payload.get("tool_input")
        if not tool or not isinstance(params, dict):
            return 0

        missing, warn, debris = guard.check(tool, params)
        if missing:
            sys.stderr.write(guard.refusal(tool, missing, call) + "\n")
            return 2
        text = guard.warning(tool, warn, debris, call)
        if text:
            sys.stdout.write(json.dumps({"systemMessage": text}, ensure_ascii=True))
    except Exception:
        return 0
    return 0


_EVENTS = {
    "session-start": _session_start,
    "pre-compact": _pre_compact,
    "stop": _stop,
    "statusline": _statusline,
}


def _check(path, *, skills: bool = False, agents: bool = False) -> int:
    """Report the hooks registered in `path` and the skills and agents
    installed beside it, and exit non-zero for what the flags select: the
    skills and agents they name, or the hooks when they name neither.

    All three are always reported; only the exit code narrows. A hook that is
    missing breaks the store's own reachability, while an uninstalled skill
    or agent is a choice, so they cannot share one gate.

    A registration that is neither missing nor the current entry is reported
    as the command it fires: this version's through an older entry, another
    one, or another one whose file is gone.

    A skill and an agent are each reported against the install receipt beside
    them -- installed, outdated, edited or missing. Anything but `installed`
    fails the gate, an edited copy included.
    """
    found = hook_install.registered(path)
    events = hook_install.event_state(path)
    print(f"{path}:")
    for host_event, how in events.items():
        if how == "missing":
            print(f"  {host_event}: not registered")
        elif how == "current":
            print(f"  {host_event}: {found[host_event]}")
        elif how == "outdated":
            print(f"  {host_event}: {found[host_event]} -- through an entry this "
                  "version would write differently")
        elif how == "broken":
            print(f"  {host_event}: registers another command, and it is not on "
                  f"disk -- {found[host_event]}")
        else:
            print(f"  {host_event}: registers another command -- {found[host_event]}")

    target = hook_install.skills_dir(path)
    state = hook_install.skill_state(target)
    installed_by = hook_install.read_receipt(target).get("memai")
    behind = hook_install.skill_behind(target)
    print(f"{target}:" if not installed_by else f"{target}: installed by memai {installed_by}")
    if not state:
        print("  no skills bundled")
    for name, how in sorted(state.items()):
        also = " -- and an update shipped since" if how == "edited" and name in behind else ""
        print(f"  {name}: {how}{also}")

    agent_target = hook_install.agents_dir(path)
    agents_state = hook_install.agent_state(agent_target)
    agents_by = hook_install.read_agent_receipt(agent_target).get("memai")
    agents_behind = hook_install.agent_behind(agent_target)
    print(f"{agent_target}:" if not agents_by
          else f"{agent_target}: installed by memai {agents_by}")
    if not agents_state:
        print("  no agents bundled")
    for name, how in sorted(agents_state.items()):
        also = (" -- and an update shipped since"
                if how == "edited" and name in agents_behind else "")
        print(f"  {name}: {how}{also}")

    if skills or agents:
        gated = (list(state.values()) if skills else []) + \
                (list(agents_state.values()) if agents else [])
        return 1 if any(how != "installed" for how in gated) else 0
    return 1 if any(how != "current" for how in events.values()) else 0


def _install(args) -> int:
    """Register the hooks, or install the skills or the agents, and print the
    report.

    Unlike the hook events, this writes to stdout for a person and lets an
    error surface. --check reports all three, and exits 1 for whichever
    --skills or --agents selects.
    """
    path = Path(args.settings) if args.settings else hook_install.user_settings_path()
    if args.check:
        return _check(path, skills=args.skills, agents=args.agents)
    if args.skills:
        print(hook_install.install_skills(hook_install.skills_dir(path),
                                          write=not args.print_only))
    if args.agents:
        print(hook_install.install_agents(hook_install.agents_dir(path),
                                          write=not args.print_only))
    if args.skills or args.agents:
        return 0
    print(hook_install.install(path, write=not args.print_only))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memai-hook",
        description="Emit memai context for a host hook. Reads the hook payload on "
                    "stdin, writes one JSON object on stdout, never fails loudly. "
                    "`statusline` writes one plain line instead, and `install` "
                    "registers the hooks with the host.")
    parser.add_argument("event", nargs="?", choices=[*sorted(_EVENTS), "guard", "install"],
                        help="which hook is calling, `guard` to vet a memai write "
                             "the host is about to make, `statusline` for one plain "
                             "line, or `install` to register them; may be omitted "
                             "when an install flag is given")
    parser.add_argument("--domain", default="",
                        help="narrow the session-start brief, or the statusline, to "
                             "one domain path")
    parser.add_argument("--budget", type=int, default=brief.DEFAULT_BUDGET,
                        help=f"characters of context to emit at most (default {brief.DEFAULT_BUDGET})")
    parser.add_argument("--quiet-minutes", type=int, default=QUIET_MINUTES,
                        help=f"a write this recent counts as recorded (stop only, "
                             f"default {QUIET_MINUTES})")
    parser.add_argument("--warden-minutes", type=int, default=None,
                        help="how long a session goes before the warden subagent "
                             "is asked for again (stop only); overrides the "
                             "interval the dashboard writes, which defaults to "
                             f"{db.WARDEN_MINUTES_DEFAULT}")
    parser.add_argument("--settings", default="",
                        help="install into this settings file instead of the user's; "
                             "memai maintains the user's settings and checks nothing "
                             "else, so what this registers is yours to keep current")
    parser.add_argument("--skills", action="store_true",
                        help="install: copy the bundled skills into the `skills/` "
                             "directory beside the settings file, instead of "
                             "registering the hooks")
    parser.add_argument("--agents", action="store_true",
                        help="install: copy the bundled subagent definitions into "
                             "the `agents/` directory beside the settings file, "
                             "instead of registering the hooks")
    parser.add_argument("--check", action="store_true",
                        help="install: report the hooks registered and the skills "
                             "and agents installed; exit non-zero if a hook is "
                             "missing, or a skill or agent is when --skills or "
                             "--agents is given too")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="install: print what it would write, write nothing")
    args = parser.parse_args(argv)

    # `--check` and friends only mean anything to install, so they name it:
    # the flags are visible in --help before the positional is, and reaching
    # for them without it is the obvious reading.
    if args.event is None:
        if not (args.check or args.print_only or args.settings or args.skills
                or args.agents):
            parser.error("an event is required (or an install flag)")
        args.event = "install"
    if args.event == "install":
        return _install(args)

    payload = _payload()
    # Outside the catch below. The guard's non-zero exit is the point of it,
    # which the generic handler would swallow.
    if args.event == "guard":
        return _guard(payload)

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
