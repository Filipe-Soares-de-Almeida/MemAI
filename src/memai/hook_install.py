"""Registering memai's hooks, and installing the skills it ships.

`memai-hook install` merges the four hook entries into the user's settings,
`~/.claude/settings.json`. Hooks on the same event that memai did not write are
kept as they are; memai's own entries are replaced, so a second run registers
them once. An existing file is copied to `<name>.bak-<stamp>` before it is
overwritten.

The user's settings are the only scope memai maintains. `--settings <path>`
writes the same block into any other file -- a repository's
`.claude/settings.local.json` included -- and nothing here checks that file
afterwards: what it registers is whoever asked for it to keep current.

`memai-hook install --skills` copies the skill directories bundled under
`memai/skills/` into the `skills/` directory beside that settings file. Only
the names memai ships are read or written; a file it would overwrite is
copied to `<name>.bak-<stamp>` first. Each run leaves a `RECEIPT` there
holding the sha256 of every file it installed, which is what tells an update
waiting to be copied from a copy somebody edited.

`stale()` reports what an installation holds out of date: a host event it does
not register, a registration whose command is gone from disk, one that fires
this command through an entry `install` would rewrite, a bundled skill whose
installed copy is the one the receipt recorded while the bundle has moved on.
The MCP server appends it to its instructions, and `install --check` prints the
same states per event and per skill.

The command is written with forward slashes on every platform: a hook of
`type: command` is handed to a shell, and a POSIX shell reads the
backslashes of a Windows path as escapes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from memai import __version__, guard

# CLI event -> the host event that fires it.
EVENTS = {
    "session-start": "SessionStart",
    "pre-compact": "PreCompact",
    "stop": "Stop",
    "guard": "PreToolUse",
}

# Host event -> the tools its entry fires for. A PreToolUse entry without a
# matcher runs on every tool call in the session, so the one event that reads
# a call rather than the store names what it wants to see.
MATCHERS = {"PreToolUse": guard.matcher()}

# Written into a skills directory by an install, and read back to tell an
# update waiting to be copied from a copy somebody edited. A leading dot keeps
# it out of the skills a host reads from that directory.
RECEIPT = ".memai-skills.json"

# Seconds. A cold first run pays for the interpreter, the store and the
# embedder behind it.
TIMEOUT = 15

# The guard opens no store and reads one payload, and it runs in front of a
# call the session is waiting on -- so it is held to a shorter leash than the
# events that have the store to load.
TIMEOUTS = {"guard": 10}


def user_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def skills_source() -> Path:
    """The skill directories this package ships."""
    return Path(__file__).parent / "skills"


def skills_dir(settings: Path) -> Path:
    """Where the skills belonging to `settings` go: `skills/` beside the file.

    A host reads its skills from `.claude/skills`, which is where the user's
    `settings.json` lives, so this lands on that directory -- and beside the
    file for a `--settings` target.
    """
    return settings.parent / "skills"


def bundled_skills() -> list[Path]:
    """The bundled skill directories, by name. Empty when none are shipped.

    A directory whose name starts with `.` or `_` is not a skill.
    """
    try:
        found = [p for p in skills_source().iterdir()
                 if p.is_dir() and not p.name.startswith((".", "_"))]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name)


def hook_command() -> str:
    """This environment's `memai-hook`, as an absolute POSIX-separated path.

    Absolute because a host fires its hooks with the PATH the host itself
    inherited, which need not contain this environment's scripts.
    """
    exe = Path(sys.argv[0])
    if exe.name.startswith("memai-hook") and exe.exists():
        return exe.as_posix()
    found = shutil.which("memai-hook")
    if found:
        return Path(found).as_posix()
    suffix = ".exe" if sys.platform == "win32" else ""
    return (Path(sys.executable).parent / f"memai-hook{suffix}").as_posix()


def _entry(command: str, event: str) -> dict:
    entry = {"hooks": [{"type": "command", "command": f"{command} {event}",
                        "timeout": TIMEOUTS.get(event, TIMEOUT)}]}
    matcher = MATCHERS.get(EVENTS[event])
    return {"matcher": matcher, **entry} if matcher else entry


def _is_ours(group: dict) -> bool:
    return any("memai-hook" in str(h.get("command", "")) for h in group.get("hooks", []))


def read_settings(path: Path) -> dict:
    """The file as a dict; {} when it is absent, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def merge(settings: dict, command: str) -> tuple[dict, list[str]]:
    """A copy of `settings` with the hooks registered, and the host events
    whose registration it had to change."""
    merged = json.loads(json.dumps(settings))  # a copy, not the caller's dict
    hooks = merged.setdefault("hooks", {})
    changed: list[str] = []
    for event, host_event in EVENTS.items():
        wanted = _entry(command, event)
        groups = [g for g in hooks.get(host_event, []) if isinstance(g, dict)]
        theirs = [g for g in groups if not _is_ours(g)]
        ours = [g for g in groups if _is_ours(g)]
        if ours != [wanted]:
            changed.append(host_event)
        hooks[host_event] = theirs + [wanted]
    return merged, changed


def backup(path: Path) -> Path:
    """Copy `path` to `<name>.bak-<stamp>` and return the copy."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, target)
    return target


def _groups(path: Path) -> dict[str, dict]:
    """The hook group in `path` that fires memai, per host event.

    Matched on a command naming `memai-hook`, not on an exact entry: a
    registration written by an older install, or edited by hand, is still
    memai's.
    """
    hooks = read_settings(path).get("hooks", {})
    found = {}
    for host_event in EVENTS.values():
        for group in hooks.get(host_event, []):
            if isinstance(group, dict) and _is_ours(group):
                found[host_event] = group
                break
    return found


def registered(path: Path) -> dict[str, str]:
    """The host events in `path` that fire a memai hook, and with what command."""
    return {host_event: str(group["hooks"][0].get("command", ""))
            for host_event, group in _groups(path).items()}


def install(path: Path, *, command: str | None = None, write: bool = True) -> str:
    """Register the hooks in `path` and return a report to print.

    With write=False the file is left alone and the report is the hooks
    block that would have been written.
    """
    command = command or hook_command()
    settings = read_settings(path)
    merged, changed = merge(settings, command)

    if not write:
        return json.dumps(merged.get("hooks", {}), indent=2, ensure_ascii=False)
    if not changed:
        return f"{path}: already registers {', '.join(EVENTS.values())} -- nothing to do."

    lines = []
    if path.exists():
        lines.append(f"backed up {backup(path)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines.append(f"registered {', '.join(changed)} in {path}")
    lines.append(f"command: {command} <event>")
    lines.append("Restart the host (or open a new session) for a SessionStart hook to fire.")
    return "\n".join(lines)


# ---------------------------------------------------------------- the skills

def _files(skill: Path) -> list[Path]:
    """Every file anywhere under a skill directory, in a stable order."""
    return sorted(p for p in skill.rglob("*") if p.is_file())


def _same(a: Path, b: Path) -> bool:
    """Whether both paths are files holding the same bytes."""
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _copy_skill(source: Path, dest: Path) -> tuple[list[str], bool]:
    """Copy `source`'s files into `dest`; return the backups made and whether
    anything was written.

    A destination file already holding the source bytes is left alone, so a
    second run copies nothing and backs nothing up. Any other file in the way
    is copied to `<name>.bak-<stamp>` before it is replaced.
    """
    notes: list[str] = []
    written = False
    for path in _files(source):
        out = dest / path.relative_to(source)
        if out.is_file() and _same(out, path):
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_file():
            notes.append(f"backed up {backup(out)}")
        shutil.copy2(path, out)
        written = True
    return notes, written


def _names(skill: Path) -> list[str]:
    """A skill's files as relative POSIX names, in a stable order."""
    return [p.relative_to(skill).as_posix() for p in _files(skill)]


def hashes(root: Path, names: list[str]) -> dict[str, str]:
    """The sha256 of each of `names` read from under `root`, by name.

    The NAME SET is the caller's, not the directory's, so the same names
    weigh the bundled copy and the installed one, and any other file `root`
    holds -- a `.bak-<stamp>` among them -- counts for nothing. A name that is
    not a readable file there is left out, so comparing two of these maps
    catches a file that is gone as well as one that changed.
    """
    out = {}
    for name in names:
        try:
            out[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def receipt_path(target: Path) -> Path:
    """Where an install records what it copied into `target`."""
    return target / RECEIPT


def read_receipt(target: Path) -> dict:
    """`target`'s install receipt: {'memai': version, 'skills': {name: {file:
    sha256}}}.

    {} when it is absent, unreadable or not an object.
    """
    try:
        data = json.loads(receipt_path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _recorded(target: Path) -> dict[str, dict[str, str]]:
    """The file hashes per skill name in `target`'s receipt.

    A skill whose entry is not a name-to-string map is left out.
    """
    skills = read_receipt(target).get("skills")
    if not isinstance(skills, dict):
        return {}
    return {name: files for name, files in skills.items()
            if isinstance(files, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items())}


def write_receipt(target: Path) -> Path:
    """Record the file hashes of every bundled skill `target` now holds, and
    return the receipt.

    Read from `target`, not from the bundle: the receipt says what is on disk
    there, so a file a copy could not replace is recorded as it stands.
    """
    skills = {}
    for skill in bundled_skills():
        dest = target / skill.name
        if dest.is_dir():
            skills[skill.name] = hashes(dest, _names(skill))
    path = receipt_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"memai": __version__, "skills": skills}, indent=2) + "\n",
                    encoding="utf-8")
    return path


def skill_state(target: Path) -> dict[str, str]:
    """Each bundled skill name mapped to how `target` holds it.

    `installed` when every bundled file is there with the bundled bytes;
    `outdated` when what is there is the copy the receipt recorded and the
    bundle has moved since -- an update waiting to be copied; `edited` when
    it is neither, so somebody changed it after it was installed; `missing`
    when the directory is not there.

    Untouched is judged against the names the RECEIPT holds, not the bundle's,
    so a bundle that has gained or lost a file since can still tell an
    untouched copy from an edited one.

    Without a receipt for a skill, nothing separates an edit from an older
    copy, and a directory whose bytes differ reads as `outdated`.

    A skill in `target` that memai does not ship is not reported.
    """
    recorded = _recorded(target)
    state: dict[str, str] = {}
    for skill in bundled_skills():
        dest = target / skill.name
        if not dest.is_dir():
            state[skill.name] = "missing"
            continue
        names = _names(skill)
        if hashes(dest, names) == hashes(skill, names):
            state[skill.name] = "installed"
            continue
        was = recorded.get(skill.name)
        if was is None or hashes(dest, sorted(was)) == was:
            state[skill.name] = "outdated"
        else:
            state[skill.name] = "edited"
    return state


def skill_behind(target: Path) -> set[str]:
    """The bundled skills whose receipt in `target` records hashes other than
    the ones this version ships.

    True of an `outdated` skill by definition, and of an `edited` one whose
    local change is not the only difference.
    """
    recorded = _recorded(target)
    return {skill.name for skill in bundled_skills()
            if skill.name in recorded
            and recorded[skill.name] != hashes(skill, _names(skill))}


def install_skills(target: Path, *, write: bool = True) -> str:
    """Copy the bundled skills into `target`, one directory per skill, and
    return a report to print.

    With write=False nothing is copied and the report names the directories
    it would write. Bundling no skills is not an error -- the report says
    nothing was installed.

    A run that copies nothing still refreshes the receipt, so a directory
    that holds the bundled bytes without one gets its hashes recorded with no
    file touched.
    """
    skills = bundled_skills()
    if not skills:
        return f"{skills_source()}: no skills bundled -- nothing installed."
    if not write:
        return "\n".join(f"would install {target / s.name}" for s in skills)

    lines: list[str] = []
    installed: list[str] = []
    for skill in skills:
        notes, written = _copy_skill(skill, target / skill.name)
        lines.extend(notes)
        if written:
            installed.append(skill.name)
    write_receipt(target)
    if not installed:
        names = ", ".join(s.name for s in skills)
        return f"{target}: already holds {names} -- nothing to do."
    lines.append(f"installed {', '.join(installed)} in {target}")
    return "\n".join(lines)


# ------------------------------------------- what an installation is missing

def _exe(command: str, event: str) -> str:
    """The executable a hook command runs, without the event it passes it."""
    text = command.strip()
    suffix = f" {event}"
    if text.endswith(suffix):
        text = text[: -len(suffix)].strip()
    return text.strip('"')


def event_state(path: Path, *, command: str | None = None) -> dict[str, str]:
    """Each host event mapped to how `path` registers it.

    `current` for the entry an install would write; `outdated` when it fires
    that same command through an entry an install would rewrite -- another
    timeout, another type; `elsewhere` for another memai-hook that is still
    on disk; `broken` when the command it fires is not there any more;
    `missing` when nothing in the file fires memai for that event.
    """
    command = command or hook_command()
    groups = _groups(path)
    state: dict[str, str] = {}
    for event, host_event in EVENTS.items():
        group = groups.get(host_event)
        if group is None:
            state[host_event] = "missing"
            continue
        got = str(group["hooks"][0].get("command", ""))
        if got != f"{command} {event}":
            state[host_event] = "elsewhere" if Path(_exe(got, event)).exists() else "broken"
        else:
            state[host_event] = "current" if group == _entry(command, event) else "outdated"
    return state


def stale(path: Path | None = None, *, command: str | None = None) -> dict[str, list[str]]:
    """What the installation in `path` (the user's settings by default) holds
    out of date.

    `events` are the host events it does not register, `broken` the events
    whose registered command is gone from disk, `outdated` the events
    registered through an entry an install would rewrite, `skills` the
    bundled skills the directory beside it holds with an update waiting.

    A skill that is not installed is absent from `skills`, and so is one
    somebody edited: only an untouched copy with an update waiting is reported.
    """
    path = path if path is not None else user_settings_path()
    events = event_state(path, command=command or hook_command())
    skills = skill_state(skills_dir(path))
    return {
        "events": [e for e, how in events.items() if how == "missing"],
        "broken": [e for e, how in events.items() if how == "broken"],
        "outdated": [e for e, how in events.items() if how == "outdated"],
        "skills": sorted(name for name, how in skills.items() if how == "outdated"),
    }
