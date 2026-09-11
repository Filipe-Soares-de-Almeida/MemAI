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

The same typo has a second shape, and this one arrives. The tag's source
lands in the text of the parameter before it, bringing the fields it opened
with it: the body holds `</content>` and `<domain>acme/x100`, and the domain
column is empty. Nothing was lost on the way, so this is refused rather than
warned about -- here, and again in the store (`db.leak_error`) for a call that
reaches it some other way. A CLOSING tag is the mark: a tag the parser
accepted never reaches a parameter's text, and a memory written ABOUT this
defect quotes the opening half.

`GUARDED` is read from the tool signatures in `memai.server`: a parameter
with no default is one the call cannot do without. A tool added here is
checked against its own signature, not against what its siblings take.
For a tool that writes a sectioned body, that list is the section spec, so
the fields the guard requires and the fields the store validates cannot
drift; `memai.sections` is imported for the spec alone and pulls in no
database.
"""

from __future__ import annotations

import functools
import re

from memai import sections

# tool -> the parameters it has no default for. `title` opens every writer's
# signature, so it opens every tuple here: the order is compared against the
# signature itself (tests/test_guard.py).
GUARDED: dict[str, tuple[str, ...]] = {
    "note": ("title", "content"),
    "reasoning": ("title", "content"),
    "handoff": ("title", "content"),
    **{tool: ("title", *(s.key for s in spec))
       for tool, spec in sections.SECTION_SPEC.items()},
}

# tool -> the optional parameters worth missing. Absence here is not an
# error, and that is the problem: a dropped `domain` or `source_ref` writes
# a memory nobody can file or check, with nothing on screen to say so.
WATCHED: dict[str, tuple[str, ...]] = {
    "note": ("domain", "tags", "source_ref"),
    "reasoning": ("domain", "tags", "source_ref"),
    "handoff": ("domain", "tags"),
    "checkpoint": ("domain", "tags"),
    "anti_pattern": ("domain", "tags", "source_ref"),
}

# tool -> the parameters it takes that have a default, in signature order.
# GUARDED holds the ones that have none, so the two together are the whole
# signature (tests/test_guard.py). WATCHED is the subset an absence is worth
# warning about; a leak names the fields that never arrived, and most of a
# call's fields are optional, so the closing tags are read from all of them.
OPTIONAL: dict[str, tuple[str, ...]] = {
    "note": ("domain", "also", "tags", "session", "review_after", "source_ref"),
    "reasoning": ("domain", "also", "tags", "session", "review_after", "source_ref"),
    "handoff": ("domain", "also", "tags", "session"),
    "checkpoint": ("session", "domain", "also", "tags"),
    "anti_pattern": ("domain", "also", "tags", "session", "review_after", "source_ref"),
}

# The frame of a tool call. A closing tag naming one of these, inside the text
# of a parameter, is that parameter holding the rest of the call.
FRAME: tuple[str, ...] = ("invoke", "parameter", "function_calls")

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


def fields(tool: str) -> tuple[str, ...]:
    """Every parameter `tool` takes, in the order of its signature.

    Empty for a tool this does not know, which leaves its text checked
    against the call frame alone.
    """
    return GUARDED.get(tool, ()) + OPTIONAL.get(tool, ())


@functools.lru_cache(maxsize=None)
def _closing(names: tuple[str, ...]) -> re.Pattern[str]:
    """Matches a closing tag naming one of `names`, with or without the prefix."""
    return re.compile(rf"</(?:antml:)?(?:{'|'.join(names)})>")


def leak_marks(tool: str, text: str) -> list[str]:
    """The closing tags in `text` that can only be a tool call's own source.

    A tag naming the call frame, or naming a parameter of `tool` itself.
    Returned as they are written, deduplicated and sorted. A closing tag of
    anything else -- `</div>`, `</result>` in a tool that has no `result` --
    is text somebody wrote, and is not one of these.
    """
    if not isinstance(text, str) or "</" not in text:
        return []
    return sorted({m.group(0) for m in _closing(FRAME + fields(tool)).finditer(text)})


def leaked(tool: str, params: dict) -> dict[str, list[str]]:
    """The parameters of a call whose text carries the call's own source.

    Maps the parameter's name to the marks found in it, and is empty for a
    call with none.
    """
    found = {k: leak_marks(tool, v) for k, v in params.items() if isinstance(v, str)}
    return {k: marks for k, marks in found.items() if marks}


# A line that is nothing but a tag: the frame of a call, a parameter opener,
# or a closing tag on its own. `strip_leak` drops the whole line, because a
# line like this carries no text of the memory's own.
_TAG_LINE = re.compile(
    r"^\s*(?:</?(?:antml:)?(?:invoke|parameter|function_calls)\b[^>]*>?"
    r"|<(?:antml:)?parameter\s+name=.*"
    r"|</?[a-z_]+>\s*)$", re.I)

# A line opening a field as a shorthand tag -- `<domain>acme/x100` -- with or
# without its closing half. The name is checked against the tool's own
# parameters, so a line opening `<div>` is text.
_FIELD_LINE = re.compile(r"^\s*<([a-z_]+)>", re.I)

# What such a line declares, in either spelling: `<domain>acme/x100</domain>`
# and a parameter opener carrying the same value.
_DECLARES = (re.compile(r'^<([a-z_]+)>(.*?)(?:</\1>)?$', re.I),
             re.compile(r'^<(?:antml:)?parameter\s+name="?([a-z_]+)"?>(.*)$', re.I))


def strip_leak(tool: str, text: str) -> tuple[str, list[str]]:
    """`text` without the call's own source, and the lines that were dropped.

    Two things go: a line that is nothing but a tag, and a closing mark at
    the END of a line that carries real text. Nothing else moves -- a body
    whose remaining sections come AFTER the debris keeps them, which is what
    a truncation at the first mark would destroy.

    A mark in the MIDDLE of a line stays. That is where a memory quoting the
    defect writes one, and cutting it would take a hole out of the sentence
    around it -- so the result can still carry a mark, and a caller writing
    it back checks it with `leak_marks` rather than assuming this cleared it.
    """
    names = tuple(n.lower() for n in fields(tool)) + FRAME
    kept, dropped = [], []
    for line in str(text).split("\n"):
        opened = _FIELD_LINE.match(line)
        if _TAG_LINE.match(line) or (opened and opened.group(1).lower() in names):
            if line.strip():
                dropped.append(line.strip())
            continue
        line = line.rstrip()
        cutting = True
        while cutting:
            cutting = False
            for mark in leak_marks(tool, line):
                if line.endswith(mark):
                    line, cutting = line[:-len(mark)].rstrip(), True
        kept.append(line)
    return "\n".join(kept).rstrip(), dropped


def declared(dropped: list[str]) -> dict[str, str]:
    """The fields the dropped lines name, as {field: value}.

    What the leaked call was TRYING to write: the domain it meant to file
    under, the tags it meant to index by. The first reading of a field wins,
    and a field with an empty value is not reported.
    """
    out: dict[str, str] = {}
    for line in dropped:
        for pattern in _DECLARES:
            m = pattern.match(line)
            if m and m.group(2).strip():
                out.setdefault(m.group(1).lower(), m.group(2).strip())
                break
    return out


def _table() -> str:
    """Every guarded tool as a signature, so the parameters read as an order.

    The fields are the tool's positional parameters, and the refusal around
    this asks for the whole call to be retyped: a list with no order in it
    invites a retry carrying only the field the message named, which fails
    again on the next one.
    """
    return " | ".join(f"{tool}({', '.join(fields)})" for tool, fields in GUARDED.items())


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
        f"because the typo comes with it. These are the tool's POSITIONAL "
        f"parameters: the signature below is the order it takes them, the "
        f"fields named above are listed in that same order, and EVERY one of "
        f"them has to be in the retry, each under its own name=. The server "
        f"names one missing field at a time, so a retry that fixes only the "
        f"field named above will fail again on the next one. "
        f"Signatures: {_table()}."
    )


def leak_refusal(tool: str, leaks: dict[str, list[str]], call: str = "") -> str:
    """What to tell a caller whose parameter is holding the rest of the call.

    Names every parameter that carries a mark and the marks it carries, so
    the retry knows which field ate the others. `call` is the tool name the
    host used, as in `refusal`.
    """
    where = "; ".join(f"{name} carries {', '.join(marks)}"
                      for name, marks in sorted(leaks.items()))
    return (
        f"BLOCKED ({call or f'mcp__memai__{tool}'}): a tool call's own source is "
        f"inside the text of a parameter -- {where}. MOST LIKELY CAUSE: a "
        f"parameter tag opened without the antml: prefix is not a tag, so the "
        f"parser leaves it in the text of the parameter BEFORE it, and every "
        f"field that tag opened never arrives: the domain, the tags and the "
        f"source_ref are written into the body while their own columns stay "
        f"empty. REDO the call typing EVERY tag again with the prefix, one per "
        f"field, and do NOT reuse the text block from the attempt that failed, "
        f"because the typo comes with it. If this memory is ABOUT this defect, "
        f"put a space inside the closing tag (`</ invoke>`) so the quote is not "
        f"a mark. These are the tool's POSITIONAL parameters, in the order it "
        f"takes them, and every one of them has to be in the retry under its "
        f"own name=. Signatures: {_table()}."
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
