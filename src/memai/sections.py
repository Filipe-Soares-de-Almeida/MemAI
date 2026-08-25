"""The named fields some memory bodies are made of, and how to read them back.

Some types are written field by field and stored as one body: the first label
opens it, and each field after that opens a line of its own. Which types, and
which fields, is SECTION_SPEC below. This module owns that spec, renders a set
of values into a body, and reads a body back into values.

The body is the record. Sections are read out of it on every write, so no
caller keeps the two in step by hand.

A body CONFORMS when it opens with the first label, every label in the spec
opens exactly one line, the labels appear in spec order, no field is empty,
and no field runs past the ceiling its spec gives it. A label that opens two
lines is not conforming: the second one reads as the start of a field and
nothing tells it apart from the one that is.

`read` reports what stops a body conforming instead of raising, so a caller
can index a body it is not ready to refuse.
"""

from __future__ import annotations

import functools
import re
from typing import NamedTuple


class Section(NamedTuple):
    key: str          # the parameter the writing tool takes it as
    label: str        # how it is spelled at the head of its line
    max_len: int = 0  # characters this field holds; 0 for a field with no ceiling


# The order here is the order the labels appear in a body, and the order the
# writing tool takes them: tests/test_guard.py holds `key` against the tool
# signature, so a rename here is a rename there.
#
# A ceiling belongs on a field that is meant to be read at a glance -- what
# the work is for, what is being done next, what the mistake was. The fields
# that carry the substance have none: capping ESTABLISHED would push what it
# holds out of the memory rather than shorten it.
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

# Labels an older writer opened blocks with that no spec keeps. salvage()
# drops the block each one runs, from its own line to the next known label.
#
# DOMAIN held a flat slug from before domains were paths, which the memory's
# own domain column has since replaced. CONFIDENCE held a number between 0
# and 1, beside a `confidence` column holding one of three words: two
# different scales under one name, and a reader has no way to tell which
# answer was meant.
LEGACY_LABELS: dict[str, tuple[str, ...]] = {
    "reasoning": ("DOMAIN", "CONFIDENCE"),
}


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


@functools.lru_cache(maxsize=None)
def _opener(label: str) -> re.Pattern[str]:
    """Matches `label` at the head of a line, plus the space after the colon."""
    return re.compile(rf"^{re.escape(label)}:[ \t]?", re.M)


def render(type: str, values: dict[str, str]) -> str:
    """A body built from field values, keyed by Section.key.

    A key the spec does not name is ignored; one it names and `values`
    does not carry is written empty, which `read` then reports as a
    problem. Raises ValueError for a type with no spec.
    """
    spec = spec_for(type)
    if not spec:
        raise ValueError(f"type {type!r} has no sections")
    return "\n".join(f"{s.label}: {str(values.get(s.key, '')).strip()}" for s in spec)


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
    spec = spec_for(type)
    if not spec:
        return Reading({}, [])

    found = {s.label: [m.span() for m in _opener(s.label).finditer(content)] for s in spec}
    missing = [s.label for s in spec if not found[s.label]]
    doubled = [s.label for s in spec if len(found[s.label]) > 1]

    problems: list[str] = []
    if missing:
        problems.append(f"no line opens with {', '.join(missing)}")
    if doubled:
        problems.append(f"more than one line opens with {', '.join(doubled)}")
    if not missing and not content.startswith(f"{spec[0].label}:"):
        problems.append(f"the body does not open with {spec[0].label}:")
    if not missing and not doubled:
        heads = [found[s.label][0][0] for s in spec]
        if heads != sorted(heads):
            problems.append(
                "the sections are out of order; expected "
                + ", ".join(s.label for s in spec)
            )
    if problems:
        return Reading({}, problems)

    bounds = [found[s.label][0] for s in spec] + [(len(content), len(content))]
    values = {
        spec[i].key: content[bounds[i][1]:bounds[i + 1][0]].strip()
        for i in range(len(spec))
    }
    faults = [f"nothing under {s.label}" for s in spec if not values[s.key]]
    faults += [f"{s.label} runs to {len(values[s.key])} characters and holds {s.max_len}"
               for s in spec if s.max_len and len(values[s.key]) > s.max_len]
    return Reading(values, faults)
