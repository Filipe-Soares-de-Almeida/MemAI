"""Registering memai's hooks, and installing the skills it ships.

`memai-hook install` merges the three hook entries into the user's settings
(`--project` for one repository's `.claude/settings.local.json`, `--settings`
for a named file). Hooks on the same event that memai did not write are kept
as they are; memai's own entries are replaced, so a second run registers
them once. An existing file is copied to `<name>.bak-<stamp>` before it is
overwritten.

`memai-hook install --skills` copies the skill directories bundled under
`memai/skills/` into the `skills/` directory beside that settings file. Only
the names memai ships are read or written; a file it would overwrite is
copied to `<name>.bak-<stamp>` first.

The command is written with forward slashes on every platform: a hook of
`type: command` is handed to a shell, and a POSIX shell reads the
backslashes of a Windows path as escapes.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# CLI event -> the host event that fires it.
EVENTS = {
    "session-start": "SessionStart",
    "pre-compact": "PreCompact",
    "stop": "Stop",
}

# Seconds. A cold first run pays for the interpreter, the store and the
# embedder behind it.
TIMEOUT = 15


def user_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def project_settings_path(root: Path | None = None) -> Path:
    return (root or Path.cwd()) / ".claude" / "settings.local.json"


def skills_source() -> Path:
    """The skill directories this package ships."""
    return Path(__file__).parent / "skills"


def skills_dir(settings: Path) -> Path:
    """Where the skills belonging to `settings` go: `skills/` beside the file.

    A host reads its skills from `.claude/skills`, and both settings files
    live in a `.claude` directory -- the user's `settings.json` and a
    project's `settings.local.json` -- so this lands on that directory for
    either of them, and beside the file for a `--settings` target.
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
    return {"hooks": [{"type": "command", "command": f"{command} {event}", "timeout": TIMEOUT}]}


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


def registered(path: Path) -> dict[str, str]:
    """The host events in `path` that fire a memai hook, and with what command.

    Matched on the command naming `memai-hook`, not on an exact entry: a
    registration written by an older install, or edited by hand, still
    counts as registered.
    """
    hooks = read_settings(path).get("hooks", {})
    found = {}
    for host_event in EVENTS.values():
        for group in hooks.get(host_event, []):
            if isinstance(group, dict) and _is_ours(group):
                found[host_event] = str(group["hooks"][0].get("command", ""))
                break
    return found


def anywhere(paths: list[Path] | None = None) -> dict[str, str]:
    """The memai hooks registered across `paths` (user and project by default)."""
    found: dict[str, str] = {}
    for path in paths if paths is not None else [user_settings_path(), project_settings_path()]:
        for host_event, command in registered(path).items():
            found.setdefault(host_event, command)
    return found


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


def skill_state(target: Path) -> dict[str, str]:
    """Each bundled skill name mapped to how `target` holds it.

    `installed` when every file is there with the bundled bytes, `outdated`
    when the directory exists but a file is missing or differs, `missing`
    when the directory is not there. A skill in `target` that memai does not
    ship is not reported.
    """
    state: dict[str, str] = {}
    for skill in bundled_skills():
        dest = target / skill.name
        if not dest.is_dir():
            state[skill.name] = "missing"
            continue
        files = _files(skill)
        current = all(_same(dest / p.relative_to(skill), p) for p in files)
        state[skill.name] = "installed" if current else "outdated"
    return state


def install_skills(target: Path, *, write: bool = True) -> str:
    """Copy the bundled skills into `target`, one directory per skill, and
    return a report to print.

    With write=False nothing is copied and the report names the directories
    it would write. Bundling no skills is not an error -- the report says
    nothing was installed.
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
    if not installed:
        names = ", ".join(s.name for s in skills)
        return f"{target}: already holds {names} -- nothing to do."
    lines.append(f"installed {', '.join(installed)} in {target}")
    return "\n".join(lines)
