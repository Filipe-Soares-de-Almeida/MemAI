"""The named fields some memory bodies are made of, and how to read them back.

A checkpoint and an anti-pattern are written field by field and stored as one
body: the first label opens it, and each field after that opens a line of its
own. This module owns the spec that says which fields a type has, renders a
set of values into that body, and reads a body back into values.

The body is the record. Sections are read out of it on every write, so no
caller keeps the two in step by hand.

A body CONFORMS when it opens with the first label, every label in the spec
opens exactly one line, the labels appear in spec order, and no field is
empty. A label that opens two lines is not conforming: the second one reads
as the start of a field and nothing tells it apart from the one that is.

`read` reports what stops a body conforming instead of raising, so a caller
can index a body it is not ready to refuse.
"""

from __future__ import annotations

import functools
import re
from typing import NamedTuple


class Section(NamedTuple):
    key: str    # the parameter the writing tool takes it as
    label: str  # how it is spelled at the head of its line


# The order here is the order the labels appear in a body, and the order the
# writing tool takes them: tests/test_guard.py holds `key` against the tool
# signature, so a rename here is a rename there.
SECTION_SPEC: dict[str, tuple[Section, ...]] = {
    "checkpoint": (
        Section("intent", "INTENT"),
        Section("established", "ESTABLISHED"),
        Section("pursuing", "PURSUING"),
        Section("open_questions", "OPEN QUESTIONS"),
    ),
    "anti_pattern": (
        Section("pattern", "TEMPTATION"),
        Section("why_wrong", "WHY WRONG"),
        Section("instead", "INSTEAD"),
    ),
}


class Reading(NamedTuple):
    """What `read` made of a body.

    `sections` maps Section.key to text. It is empty when the labels
    themselves are wrong -- missing, doubled, out of order, or not opening
    the body -- and filled when they are right, even if a field they
    delimit came out empty. `problems` says what stops the body
    conforming, one entry per fault, and is empty when nothing does. Check
    `conforms`; a filled `sections` does not mean the body is good.
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
    empty = [s.label for s in spec if not values[s.key]]
    if empty:
        return Reading(values, [f"nothing under {', '.join(empty)}"])
    return Reading(values, [])
