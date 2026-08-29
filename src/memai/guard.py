"""The PreToolUse check that stops a write whose text never arrived.

A tool call is written by the model as tagged parameters. A tag opened
without the `antml:` prefix is dropped by the parser BEFORE the call reaches
the server: the parameter never arrives, and the text it held is gone -- not
truncated, not empty, gone. Nothing downstream can recover it, so the check
runs on the call rather than inside `server.py`.

The guard refuses such a call with exit 2, names that cause, and says what to
do about it: type the tags again, do not paste the block back. The server
names one missing field at a time, so a retry that fixes only the field it
named fails on the next one. A field the tool does not require raises nothing
at all on its own, so its absence is reported as a warning and the write goes
through.

`GUARDED` is read from the tool signatures in `memai.server`: a parameter
with no default is one the call cannot do without. A tool added here is
checked against its own signature, not against what its siblings take.
For a tool that writes a sectioned body, that list is the section spec, so
the fields the guard requires and the fields the store validates cannot
drift; `memai.sections` is imported for the spec alone and pulls in no
database.
"""

from __future__ import annotations

from memai import sections

# tool -> the parameters it has no default for.
GUARDED: dict[str, tuple[str, ...]] = {
    "note": ("content",),
    "reasoning": ("content",),
    "handoff": ("content",),
    **{tool: tuple(s.key for s in spec) for tool, spec in sections.SECTION_SPEC.items()},
}

# tool -> the optional parameters worth missing. Absence here is not an
# error, and that is the problem: a dropped `domain` or `source_ref` writes
# a memory nobody can file or check, with nothing on screen to say so.
WATCHED: dict[str, tuple[str, ...]] = {
    "note": ("domain", "tags", "source_ref"),
    "reasoning": ("domain", "source_ref"),
    "handoff": ("domain",),
    "checkpoint": ("domain",),
    "anti_pattern": ("domain", "source_ref"),
}

# What a dropped tag leaves behind when it lands inside the NEXT parameter's
# text instead of vanishing: the tag's own source, written into a memory's
# body. Warned about, never refused -- a memory documenting this defect
# quotes these marks on purpose.
DEBRIS = ("parameter name=", "</", "<parameter")


def matcher() -> str:
    """The tool names the guard's registration fires for, as a regex.

    The server's name in a host config is the user's to choose, so the middle
    segment is matched rather than spelled.
    """
    return f"mcp__[Mm]em[Aa][Ii]__({'|'.join(GUARDED)})"


def tool_of(name: str) -> str:
    """The memai tool a host's `tool_name` refers to, or "" for anything else.

    `mcp__MemAI__note` -> `note`. A tool of another server, or one this does
    not guard, is not ours to judge: the matcher is a regex in a file people
    edit, so the name is checked here as well.
    """
    parts = str(name).split("__")
    if len(parts) != 3 or parts[0] != "mcp" or parts[1].lower() != "memai":
        return ""
    return parts[2] if parts[2] in GUARDED else ""


def _blank(value: object) -> bool:
    """Whether a parameter arrived with nothing in it.

    A value that is not a string counts as present: the tools this guards
    take text, so anything else is the host's business, not the typo's.
    """
    if value is None:
        return True
    return not str(value).strip() if isinstance(value, str) else False


def check(tool: str, params: dict) -> tuple[list[str], list[str], list[str]]:
    """What is wrong with `params` for `tool`: (missing, warn, debris).

    `missing` are the required parameters that did not arrive -- the call
    cannot proceed. `warn` are the optional ones. `debris` are the parameters
    whose text carries a tag's own source, which means a dropped tag landed
    in the body of the one after it.
    """
    missing = [k for k in GUARDED.get(tool, ()) if _blank(params.get(k))]
    warn = [k for k in WATCHED.get(tool, ()) if _blank(params.get(k))]
    debris = sorted({k for k, v in params.items() if isinstance(v, str)
                     and any(mark in v for mark in DEBRIS)})
    return missing, warn, debris


def _table() -> str:
    return " | ".join(f"{tool}={','.join(fields)}" for tool, fields in GUARDED.items())


def refusal(tool: str, missing: list[str], call: str = "") -> str:
    """What to tell a caller whose required text never arrived.

    `call` is the tool name the host used, which carries the server's
    registered name. Without one the message falls back to the name the
    documentation registers.
    """
    return (
        f"BLOCKED ({call or f'mcp__memai__{tool}'}): required parameter(s) missing: "
        f"{', '.join(missing)}. MOST LIKELY CAUSE: a parameter tag opened "
        f"without the antml: prefix -- the parser drops the parameter and the "
        f"text is LOST before the call leaves the client, so nothing here can "
        f"recover it. REDO the call typing EVERY tag again with the prefix, "
        f"and do NOT reuse the text block from the attempt that failed, "
        f"because the typo comes with it. The server names one missing field "
        f"at a time, so a retry that fixes only the field named above will "
        f"fail again on the next one. Required per tool: {_table()}."
    )


def warning(tool: str, warn: list[str], debris: list[str], call: str = "") -> str:
    """What to tell a caller whose write goes through with something off.

    `call` names the tool the way the host does, as in `refusal`.
    """
    parts = []
    if warn:
        parts.append(f"optional parameter(s) missing ({', '.join(warn)}) -- if that "
                     f"was not deliberate it is the same dropped-tag typo")
    if debris:
        parts.append(f"a parameter tag's own source is inside the text of "
                     f"{', '.join(debris)} -- a dropped tag landed in the body "
                     f"of the parameter after it")
    if not parts:
        return ""
    return (f"MemAI {call or f'mcp__memai__{tool}'}: " + "; ".join(parts)
            + ". The write goes through; fix it afterwards with edit_memory.")
