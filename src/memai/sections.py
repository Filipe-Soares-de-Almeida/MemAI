"""The named fields some memory bodies are made of, and how to read them back.

Some types are written field by field and stored as one body: the first label
opens it, and each field after that opens a line of its own. Which types, and
which fields, is SECTION_SPEC below. This module owns that spec, renders a set
of values into a body, and reads a body back into values.

The body is the record. Sections are read out of it on every write, so no
caller keeps the two in step by hand.

A body CONFORMS when it opens with the first label, every required label
opens exactly one line, an optional label opens at most one, the labels appear
in spec order, no field is empty, and no field runs past the ceiling its spec
gives it. A label that opens two lines is not conforming: the second one reads
as the start of a field and nothing tells it apart from the one that is.

`read` and `read_spec` report what stops a body conforming instead of raising,
so a caller can index a body it is not ready to refuse.
"""

from __future__ import annotations

import functools
import re
from typing import NamedTuple

from memai import contract


class Section(NamedTuple):
    key: str          # the parameter the writing tool takes it as
    label: str        # how it is spelled at the head of its line
    max_len: int = 0  # characters this field holds; 0 for a field with no ceiling
    optional: bool = False


# Body order, and the writing tool's parameter order: tests/test_guard.py holds `key` to its signature.
# Only at-a-glance fields get a ceiling; capping ESTABLISHED would push substance out, not shorten it.
SECTION_SPEC: dict[str, tuple[Section, ...]] = {
    "checkpoint": (
        Section("intent", "INTENT", 800),
        Section("established", "ESTABLISHED"),
        Section("pursuing", "PURSUING", 1500),
        Section("open_questions", "OPEN QUESTIONS"),
    ),
    "anti_pattern": (
        Section("pattern", "TEMPTATION", 800),
        Section("why_wrong", "WHY WRONG"),
        Section("instead", "INSTEAD"),
    ),
    "reasoning": (
        Section("hypothesis", "HYPOTHESIS", 800),
        Section("reasoning", "REASONING"),
        Section("result", "RESULT"),
        Section("revised_belief", "REVISED BELIEF"),
        Section("next_time", "NEXT TIME", 1500),
    ),
}

# Labels no spec keeps; salvage() drops each one's block up to the next known label. DOMAIN repeats
# the domain column; CONFIDENCE is a 0-1 number that clashes with the `confidence` column.
LEGACY_LABELS: dict[str, tuple[str, ...]] = {
    "reasoning": ("DOMAIN", "CONFIDENCE"),
}


# The fields a task note on items is written in; a task note, not a memory type, so not in SECTION_SPEC.
BRIEF_SPEC: tuple[Section, ...] = tuple(
    Section(f["key"], f["label"], optional=bool(f.get("optional"))) for f in contract.TASK_BRIEF)


class DependsEntry(NamedTuple):
    item: str      # an `iN` key, or "" for a dependency on a deleted item
    deleted: str   # the deleted item's text, or "" for a key
    reason: str


_DEPENDS_ENTRY = re.compile(
    r'(?:(?P<key>[iI][1-9][0-9]*)|(?i:deleted)\s+"(?P<text>[^"]*)")\s*(?:\((?P<reason>[^()]*)\))?')
_DEPENDS_KEY_HEAD = re.compile(r"[iI][1-9][0-9]*")


def _depends_pieces(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """The text split at commas and newlines outside parentheses and quotes.

    Each piece comes with the separator before and after it ("" at an end).
    """
    pieces: list[tuple[str, str, str]] = []
    depth = 0
    quoted = False
    start = 0
    before = ""
    for at, ch in enumerate(text):
        if quoted:
            quoted = ch != '"'
        elif depth == 0 and ch == '"':
            quoted = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return [], ["unbalanced parentheses"]
        elif depth == 0 and ch in (",", "\n"):
            pieces.append((text[start:at], before, ch))
            start, before = at + 1, ch
    if depth:
        return [], ["unbalanced parentheses"]
    pieces.append((text[start:], before, ""))
    return pieces, []


def _depends_complaint(piece: str) -> str:
    shown = piece if len(piece) <= 40 else piece[:37] + "..."
    if piece.count("(") > 1:
        return f"nested or repeated parentheses in {shown!r}"
    head = _DEPENDS_KEY_HEAD.match(piece)
    if head and piece[head.end():].strip():
        return f"text outside parentheses after {head.group().lower()} in {shown!r}"
    if re.match(r"(?i)deleted\b", piece):
        return f'{shown!r} is not deleted "<item text>" with an optional (reason)'
    return f"{shown!r} is not an item key"


def parse_depends(text: str) -> tuple[list[DependsEntry], list[str]]:
    """The entries of a DEPENDS ON field and what stops it reading.

    The field is `none`, or entries separated by commas or newlines: an item
    key `iN`, or `deleted "<item text>"`, each with an optional `(reason)`.
    """
    text = str(text).strip()
    if text.lower() == "none":
        return [], []
    if not text:
        return [], ["the field is empty"]
    pieces, problems = _depends_pieces(text)
    entries: list[DependsEntry] = []
    for piece, before, after in pieces:
        piece = piece.strip()
        if not piece:
            if "\n" not in (before, after) and (before or after):
                problems.append("an empty entry")
            continue
        found = _DEPENDS_ENTRY.fullmatch(piece)
        if found is None:
            problems.append(_depends_complaint(piece))
            continue
        reason = (found.group("reason") or "").strip()
        if found.group("reason") is not None and not reason:
            problems.append(f"an empty reason in {piece!r}")
            continue
        if found.group("key"):
            key = found.group("key").lower()
            if any(e.item == key for e in entries):
                problems.append(f"{key} listed twice")
                continue
            entries.append(DependsEntry(key, "", reason))
        elif found.group("text"):
            entries.append(DependsEntry("", found.group("text"), reason))
        else:
            problems.append("a deleted entry with an empty item text")
    return entries, problems


def render_depends(entries: list[DependsEntry]) -> str:
    """The field text for `entries`: `none` for an empty list; parse_depends reads it back."""
    if not entries:
        return "none"
    parts = []
    for e in entries:
        text = " ".join(e.deleted.replace('"', "'").split())
        head = e.item or f'deleted "{text}"'
        parts.append(f"{head} ({e.reason})" if e.reason else head)
    return ", ".join(parts)


class Dependency(NamedTuple):
    """One DEPENDS ON entry: an item id, an `iN` position key from an older store, or a deleted item's text."""

    item: int | None
    key: str
    text: str
    reason: str


_DEPENDENCY = re.compile(
    r'(?:\[\[#(?P<token>[0-9]+)\]\]|#?(?P<id>[0-9]+)|(?P<key>[iI][1-9][0-9]*)'
    r'|(?i:deleted)\s+"(?P<text>[^"]*)")\s*(?:\((?P<reason>[^()]*)\))?')
_DEPENDENCY_HEAD = re.compile(r"\[\[#[0-9]+\]\]|#?[0-9]+|[iI][1-9][0-9]*")


def _dependency_complaint(piece: str) -> str:
    shown = piece if len(piece) <= 40 else piece[:37] + "..."
    if piece.count("(") > 1:
        return f"nested or repeated parentheses in {shown!r}"
    head = _DEPENDENCY_HEAD.match(piece)
    if head and piece[head.end():].strip():
        return f"text outside parentheses after {head.group().lower()} in {shown!r}"
    if re.match(r"(?i)deleted\b", piece):
        return f'{shown!r} is not deleted "<item text>" with an optional (reason)'
    return f"{shown!r} is not an item id"


def read_depends(text: str) -> tuple[list[Dependency], list[str]]:
    """The entries of a DEPENDS ON field and what stops it reading.

    The field is `none`, or entries separated by commas or newlines: an item
    id written `4812`, `#4812` or `[[#4812]]`, a position key `iN`, or
    `deleted "<item text>"`, each with an optional `(reason)`.
    """
    text = str(text).strip()
    if text.lower() == "none":
        return [], []
    if not text:
        return [], ["the field is empty"]
    pieces, problems = _depends_pieces(text)
    entries: list[Dependency] = []
    for piece, before, after in pieces:
        piece = piece.strip()
        if not piece:
            if "\n" not in (before, after) and (before or after):
                problems.append("an empty entry")
            continue
        found = _DEPENDENCY.fullmatch(piece)
        if found is None:
            problems.append(_dependency_complaint(piece))
            continue
        reason = (found.group("reason") or "").strip()
        if found.group("reason") is not None and not reason:
            problems.append(f"an empty reason in {piece!r}")
            continue
        number = found.group("token") or found.group("id")
        if number is not None:
            entry = Dependency(int(number), "", "", reason)
        elif found.group("key"):
            entry = Dependency(None, found.group("key").lower(), "", reason)
        elif found.group("text"):
            entry = Dependency(None, "", found.group("text"), reason)
        else:
            problems.append("a deleted entry with an empty item text")
            continue
        twice = ((entry.item is not None and any(e.item == entry.item for e in entries))
                 or (entry.key and any(e.key == entry.key for e in entries)))
        if twice:
            problems.append(f"{entry.key or entry.item} listed twice")
            continue
        entries.append(entry)
    return entries, problems


ITEM_TOKEN = re.compile(r"\[\[#([0-9]+)\]\]")


def item_token(item_id: int) -> str:
    return f"[[#{item_id}]]"


def item_tokens(text: str) -> list[int]:
    """The item ids `text` mentions, each once, in the order first mentioned."""
    return list(dict.fromkeys(int(m[1]) for m in ITEM_TOKEN.finditer(str(text))))


def map_item_tokens(text: str, mapping: dict[int, int]) -> str:
    """`text` with each mention moved through `mapping`; an id it lacks becomes [[#0]], the deleted item."""
    return ITEM_TOKEN.sub(lambda m: item_token(mapping.get(int(m[1]), 0)), str(text))


def write_depends(entries: list[Dependency]) -> str:
    """The field text for `entries`, `none` for an empty list; read_depends reads it back.

    A stored field never holds a position key, so an entry carrying one is a ValueError.
    """
    if not entries:
        return "none"
    parts = []
    for e in entries:
        if e.key:
            raise ValueError(f"{e.key} is a position key, not an item id")
        if e.item is not None:
            head = item_token(e.item)
        else:
            head = 'deleted "{}"'.format(" ".join(e.text.replace('"', "'").split()))
        parts.append(f"{head} ({e.reason})" if e.reason else head)
    return ", ".join(parts)


class Reading(NamedTuple):
    """What `read` made of a body.

    `sections` maps Section.key to text. It is empty when the labels
    themselves are wrong -- missing, doubled, out of order, or not opening
    the body -- and filled when they are right, even if a field they
    delimit came out empty or overlong. `problems` says what stops the
    body conforming, one entry per fault, and is empty when nothing does.
    Check `conforms`; a filled `sections` does not mean the body is good.
    """

    sections: dict[str, str]
    problems: list[str]

    @property
    def conforms(self) -> bool:
        return not self.problems


def spec_for(type: str) -> tuple[Section, ...]:
    """The sections a type is made of, empty for a type that has no spec."""
    return SECTION_SPEC.get(type, ())


def is_sectioned(type: str) -> bool:
    return type in SECTION_SPEC


@functools.cache
def _opener(label: str) -> re.Pattern[str]:
    """Matches `label` at the head of a line, plus the space after the colon."""
    return re.compile(rf"^{re.escape(label)}:[ \t]?", re.M)


def render(type: str, values: dict[str, str]) -> str:
    """A body built from field values, keyed by Section.key; ValueError for a type with no spec."""
    spec = spec_for(type)
    if not spec:
        raise ValueError(f"type {type!r} has no sections")
    return render_spec(spec, values)


def render_spec(spec: tuple[Section, ...], values: dict[str, str]) -> str:
    """A body built from field values, keyed by Section.key.

    A key the spec does not name is ignored. A required field `values` does
    not carry is written empty, which `read_spec` then reports; an optional
    one is left out.
    """
    lines = []
    for s in spec:
        text = str(values.get(s.key, "")).strip()
        if s.optional and not text:
            continue
        lines.append(f"{s.label}: {text}")
    return "\n".join(lines)


def _drop_legacy(type: str, content: str) -> str:
    """Remove the blocks opened by a label in LEGACY_LABELS for this type.

    A block runs from its own line to the next line opening with a label
    this type knows -- one of its own or another legacy one -- or to the end
    of the body.
    """
    legacy = LEGACY_LABELS.get(type, ())
    if not legacy:
        return content
    known = tuple(s.label for s in spec_for(type)) + legacy
    kept, dropping = [], False
    for line in content.split("\n"):
        opened = next((k for k in known if line.startswith(f"{k}:")), None)
        if opened is not None:
            dropping = opened in legacy
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


def salvage(type: str, content: str) -> Reading:
    """Read a body that does not conform, forgiving two shapes an older writer left.

    A preamble -- anything written above the first field -- is ignored, and
    so is a block opened by a label in LEGACY_LABELS. Everything else -- a
    label missing, doubled, out of order, or opening an empty or overlong
    field -- comes back as a problem the way `read` reports it.

    What comes back conforming can be re-rendered into a body `read`
    accepts, which is what the migration does with it. That re-render DROPS
    both of those shapes, so a caller keeps the body it replaced.
    """
    spec = spec_for(type)
    if not spec:
        return Reading({}, [])
    content = _drop_legacy(type, content)
    opening = _opener(spec[0].label).search(content)
    return read(type, content[opening.start():] if opening else content)


def read(type: str, content: str) -> Reading:
    """Read a body into its fields. A type with no spec reads as conforming."""
    return read_spec(spec_for(type), content)


def read_spec(spec: tuple[Section, ...], content: str) -> Reading:
    """Read a body into the fields of `spec`; an empty spec reads as conforming.

    An optional label may be absent; when present it obeys the same rules as
    a required one.
    """
    if not spec:
        return Reading({}, [])

    found = {s.label: [m.span() for m in _opener(s.label).finditer(content)] for s in spec}
    missing = [s.label for s in spec if not found[s.label] and not s.optional]
    doubled = [s.label for s in spec if len(found[s.label]) > 1]

    problems: list[str] = []
    if missing:
        problems.append(f"no line opens with {', '.join(missing)}")
    if doubled:
        problems.append(f"more than one line opens with {', '.join(doubled)}")
    if not missing and not content.startswith(f"{spec[0].label}:"):
        problems.append(f"the body does not open with {spec[0].label}:")
    present = [s for s in spec if found[s.label]]
    if not missing and not doubled:
        heads = [found[s.label][0][0] for s in present]
        if heads != sorted(heads):
            problems.append(
                "the sections are out of order; expected "
                + ", ".join(s.label for s in spec)
            )
    if problems:
        return Reading({}, problems)

    bounds = [found[s.label][0] for s in present] + [(len(content), len(content))]
    values = {
        present[i].key: content[bounds[i][1]:bounds[i + 1][0]].strip()
        for i in range(len(present))
    }
    faults = [f"nothing under {s.label}" for s in present if not values[s.key]]
    faults += [f"{s.label} runs to {len(values[s.key])} characters and holds {s.max_len}"
               for s in present if s.max_len and len(values[s.key]) > s.max_len]
    return Reading(values, faults)


_BRIEF_BODY = tuple(s for s in BRIEF_SPEC if s.key != "depends_on")


def without_depends(body: str) -> str | None:
    """A brief's body with its DEPENDS ON line taken out, or None when `body` is no brief."""
    reading = read_spec(BRIEF_SPEC, body)
    if not reading.conforms:
        return None
    return render_spec(_BRIEF_BODY, reading.sections)


def with_depends(body: str, field: str) -> str:
    """A body stored by without_depends, with its DEPENDS ON field put back as `field`."""
    reading = read_spec(_BRIEF_BODY, body)
    if not reading.conforms:
        return body
    return render_spec(BRIEF_SPEC, {**reading.sections, "depends_on": field})
