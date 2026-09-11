"""What t() gives back, held to the strings the catalogs actually carry.

The runtime is JavaScript, so these run it: tools/i18n-cases.mjs feeds
i18n.js a set of lookups under node and prints what came back. Same
arrangement as tests/test_richtext.py -- the browser's code is the code
under test, rather than a Python re-implementation of it standing in for it.

Skipped where node is absent; the runtime still ships.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tools" / "i18n-cases.mjs"
I18N = ROOT / "src" / "memai" / "webui" / "public" / "i18n"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(locale: str) -> dict[str, str]:
    out = subprocess.run(["node", str(CASES), locale], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def en() -> dict[str, str]:
    return _run("en")


@pytest.fixture(scope="module")
def pt() -> dict[str, str]:
    return _run("pt-BR")


# ------------------------------------------------------------------ plurals

def test_one_takes_the_singular_and_anything_else_the_plural(en):
    assert en["plural-one"] == "Archives 1 memory"
    assert en["plural-many"] == "Archives 3 memories"


def test_zero_takes_the_plural(en):
    """English and Portuguese both say "0 memories"."""
    assert en["plural-zero"] == "Archives 0 memories"


def test_the_branch_reads_the_number_not_the_text(en):
    """A caller passes fmtInt output, which is a string with a separator in it."""
    assert en["plural-formatted"] == "Archives 1 memory"
    assert en["plural-thousand"] == "Archives 1024 memories"


def test_a_grouped_count_agrees_with_the_count(en):
    """pt-BR groups thousands with a dot, which Number() reads as one."""
    assert en["plural-grouped-dot"] == "Archives 1.000 memories"
    assert en["plural-grouped-comma"] == "Archives 1,000 memories"


def test_a_count_the_caller_forgot_takes_the_plural(en):
    """Better a plural than a sentence that silently reads as singular."""
    assert en["plural-missing-var"] == "Archives {n} memories"


def test_each_count_in_a_sentence_is_decided_on_its_own(en):
    assert en["plural-two-counts-one"] == "Adds 1 search term across 1 memory"
    assert en["plural-two-counts-mixed"] == "Adds 4 search terms across 1 memory"
    assert en["plural-two-counts-many"] == "Adds 9 search terms across 3 memories"


def test_braces_arriving_in_a_VALUE_are_not_read_as_a_form(en):
    """A domain path is user data; it must not be able to rewrite the sentence."""
    assert en["plural-not-in-values"] == "Moves 1 memory to acme/{x}"


def test_a_lookup_with_no_vars_is_left_alone(en):
    """Nothing to decide with, so nothing is decided -- and nothing crashes."""
    assert en["no-vars-leaves-form"] == "Archives {n} {n?memory:memories}"


def test_a_missing_key_still_comes_back_as_the_key(en):
    assert en["missing-key"] == "op.what.nosuchkind"


def test_the_verb_agrees_too_not_only_the_noun(en):
    assert en["pt-plural-one"] == "Merges 1 pair that says the same thing"
    assert en["pt-plural-many"] == "Merges 2 pairs that say the same thing"


# ------------------------------------------------------- the other language

def test_portuguese_agrees_on_its_own_terms(pt):
    assert pt["plural-one"] == "Arquiva 1 memória"
    assert pt["plural-many"] == "Arquiva 3 memórias"
    assert pt["pt-plural-one"] == "Funde 1 par que diz a mesma coisa"
    assert pt["pt-plural-many"] == "Funde 2 pares que dizem a mesma coisa"


def test_a_whole_clause_can_be_the_thing_that_agrees(pt):
    """The form holds words, not just a suffix, which is what Portuguese needs."""
    assert pt["hint-some-one"].startswith("1 desta foi proposta")
    assert "abra-a" in pt["hint-some-one"]
    assert pt["hint-some-many"].startswith("5 destas foram propostas")
    assert "uma a uma" in pt["hint-some-many"]


def test_the_locale_actually_switched(en, pt):
    assert en["groups-aside"] != pt["groups-aside"]
    assert pt["groups-aside"] == "18 pendentes em 1 grupo"


# ------------------------------------------------------------ the catalogs

def _strings(locale: str) -> dict[str, str]:
    return json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))["strings"]


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_every_plural_form_is_well_formed(locale):
    """An unclosed or empty form prints its own braces on the screen."""
    for key, value in _strings(locale).items():
        for var, one, many in re.findall(r"\{(\w+)\?([^{}:]*):([^{}]*)\}", value):
            assert one.strip(), f"{locale}:{key} has an empty singular"
            assert many.strip(), f"{locale}:{key} has an empty plural"
            assert f"{{{var}}}" in value, (
                f"{locale}:{key} agrees with {var}, which the sentence never prints")


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_a_form_is_never_left_half_written(locale):
    """`{n?one}` with no colon is a literal, and would ship as one."""
    for key, value in _strings(locale).items():
        for stray in re.findall(r"\{\w+\?[^{}]*\}", value):
            assert ":" in stray, f"{locale}:{key} has a form with no plural: {stray}"


def test_a_known_relation_type_is_shown_by_its_name(en, pt):
    assert en["rel-known"] == "Supersedes"
    assert pt["rel-known"] == "Substitui"
    assert en["rel-known-two-words"] == "Relates to"
    assert pt["rel-diagram-only"] == "Explica"


def test_a_custom_relation_type_falls_back_to_what_was_stored(en):
    """db accepts any string, so a type this UI never offered has no name --
    and printing the key would be worse than printing the type."""
    assert en["rel-custom"] == "blocks_the_release"
    assert en["rel-empty"] == ""
    assert en["rel-padded"] == "Supersedes"


def test_the_stored_value_stays_reachable_on_a_translated_type(en, pt):
    """Queries and the MCP tools use the stored string, so it is not lost --
    and the tooltip is dropped where it would only repeat the label."""
    assert en["rel-title-known"] == "Stored as supersedes"
    assert pt["rel-title-known"] == "Gravado como supersedes"
    assert en["rel-title-custom"] == ""


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_no_offered_type_is_left_showing_its_identifier(lang, en, pt):
    line = {"en": en, "pt": pt}[lang]["rel-every-offered"]
    for pair in line.split(" · "):
        stored, shown = pair.split("=", 1)
        assert shown and shown != stored, f"{lang}: {stored} still shows as itself"


# ------------------------------------------------------- the calendar's day

def test_a_day_key_is_the_local_day(en):
    """The calendar groups runs by the day the reader was living.

    Timestamps are stored UTC, so 23:40 on the 8th belongs to the 8th no
    matter what Greenwich calls that instant -- and 00:20 on the 9th to the
    9th. Grouping by a slice of the ISO string files both under whichever
    day UTC happened to be on.
    """
    assert en["day-key"] == "2026-09-09"
    assert en["day-key-pads"] == "2026-01-05"       # zero-padded, sortable
    assert en["day-key-late"] == "2026-09-08"
    assert en["day-key-early"] == "2026-09-09"
    assert en["month-key"] == "2026-09"


def test_a_day_key_reads_back_as_the_same_day(en):
    """fromKey is the inverse, which `new Date(key)` is not.

    A bare date string parses as UTC, so it lands on the previous day for
    every reader west of Greenwich -- the same bug from the other side. This
    asserts the round trip rather than one machine's offset, so it holds in
    any zone.
    """
    assert en["from-key"] == "2026-09-09"
    assert en["from-key-roundtrip"] == "2026-03-01"
    assert en["from-key-month"] == "8"              # September, zero-based


RELS = ROOT / "src" / "memai" / "webui" / "core" / "shared.js"


def _suggested_rel_types() -> set[str]:
    """The relation types the two pickers offer, read from the module."""
    body = RELS.read_text(encoding="utf-8")
    found = set()
    for name in ("REL_SUGGEST", "DG_REL_SUGGEST"):
        match = re.search(rf"export const {name} = \[(.*?)\]", body, re.S)
        assert match, f"{name} is not where this test expects it"
        found |= set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert found, "expected the pickers to offer some relation types"
    return found


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_every_offered_relation_type_has_a_name(locale):
    """`rel.${type}` is assembled at runtime, so a gap reaches the screen.

    relLabel falls back to the stored string, which is the right answer for
    a custom type somebody typed and the wrong one for a type this UI
    offers in its own picker.
    """
    strings = _strings(locale)
    for rel in _suggested_rel_types():
        assert f"rel.{rel}" in strings, f"{locale} has no name for {rel}"


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_the_two_types_the_server_writes_by_itself_have_a_name(locale):
    """Neither reaches the picker: merge and distill write `supersedes`, a
    diagram step writes `explains`, and both are shown like any other."""
    strings = _strings(locale)
    for rel in ("supersedes", "explains"):
        assert f"rel.{rel}" in strings
    assert "rel.raw" in strings


def test_a_relation_name_is_not_just_the_identifier_echoed_back():
    """A mask equal to the stored string is not a mask -- relTypeTitle reads
    that as "no translation" and drops the tooltip, and the screen shows the
    identifier the user asked to stop seeing."""
    for locale in ("en", "pt-BR"):
        strings = _strings(locale)
        for rel in _suggested_rel_types() | {"supersedes", "explains"}:
            assert strings[f"rel.{rel}"] != rel, f"{locale}:rel.{rel} echoes the identifier"


def test_every_group_sentence_agrees_in_both_languages():
    """A group of one is the common case, so "1 memories" is what breaks.

    Scoped to `op.what.*` deliberately, and NOT asserted catalog-wide: the
    two languages do not inflect the same words. English "pending" is one
    word for any count and Portuguese has two, so a rule demanding the same
    keys carry a form in both would be a rule against translating well.
    What every one of these sentences does have is a counted noun.
    """
    for locale in ("en", "pt-BR"):
        for key, value in _strings(locale).items():
            if not key.startswith("op.what.") or key == "op.what.other":
                continue
            assert re.search(r"\{n\?[^{}:]*:[^{}]*\}", value), (
                f"{locale}:{key} counts memories without agreeing with the count")
