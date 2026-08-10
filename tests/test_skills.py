"""What the bundled skills are held to.

A SKILL.md is what an agent reads instead of the code, so it goes stale the
way documentation goes stale: silently, and only for the reader. These tests
hold every shipped skill to the names the code actually publishes, and to the
repository's language rule.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from memai import db, hook_install, server

# The non-ASCII characters a skill may spell: typographic punctuation, the
# section sign, and the two mathematical signs its tables carry. No accented
# letter is here, which is what makes this list the language gate -- the
# repository is English only, and a translated paragraph fails on its first
# accent.
ALLOWED_NON_ASCII = set("—→§…·×∈✓")

# The `type` a memory can carry, taken from the writers' own constants.
MEMORY_TYPES = {v for k, v in vars(server).items()
                if k.startswith("TYPE_") and isinstance(v, str)}

# A backticked call: the one unambiguous way a skill names a tool. A bare
# backticked word is a field, a group or a value just as often.
CALLS = re.compile(r"`([a-z_][a-z0-9_]*)\(")
# `type='note'` -- the quoted form, which asserts a value. The lookbehind
# keeps `new_type=` out of it.
TYPED = re.compile(r"(?<!\w)type=['\"]([a-z_]+)['\"]")
# A wikilink naming a sibling skill. The same brackets are also how a memory
# body cites another memory, which is what memai-link is about, so only the
# family's own prefix is held to resolving.
WIKILINK = re.compile(r"\[\[(memai-[a-z0-9-]+)\]\]")
# A drive letter followed by a separator. The lookbehind spares a URL scheme,
# where the letter before the colon is part of `https`.
DRIVE_PATH = re.compile(r"(?<![a-z])[A-Za-z]:[\\/]")

SKILLS = hook_install.bundled_skills()
BY_NAME = {p.name for p in SKILLS}


def _text(skill: Path) -> str:
    return (skill / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter(text: str) -> str:
    """The YAML block between the opening `---` and the next one."""
    assert text.startswith("---\n"), "a skill opens with its frontmatter"
    end = text.index("\n---\n", 3)
    return text[4:end]


pytestmark = pytest.mark.skipif(not SKILLS, reason="no skills bundled")


def test_the_package_ships_its_skills():
    """An empty skills directory would leave every test below unparametrised
    and silently green."""
    assert "memai-memory" in BY_NAME
    assert len(SKILLS) >= 2


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_a_skill_names_itself_and_says_when_to_fire(skill):
    """The frontmatter `name` is what a host registers the skill as, and the
    `description` is the only text always in context -- an empty one means the
    skill never triggers."""
    front = _frontmatter(_text(skill))
    name = re.search(r"^name:\s*(\S+)\s*$", front, re.M)
    assert name and name.group(1) == skill.name
    description = re.search(r"^description:", front, re.M)
    assert description, f"{skill.name} has no description to trigger on"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_a_skill_is_written_in_english(skill):
    """Everything in this repository is English but the locale catalogues.

    An accent is the cheap signal that a paragraph came back in another
    language, which is how these files were written before they were ported.
    """
    for number, line in enumerate(_text(skill).splitlines(), start=1):
        for char in line:
            assert ord(char) < 128 or char in ALLOWED_NON_ASCII, (
                f"{skill.name}/SKILL.md:{number} spells U+{ord(char):04X}")


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_every_tool_a_skill_calls_is_published(skill):
    """A skill that names a tool the server does not have sends the agent
    after something it cannot call, and reads as a missing feature."""
    named = set(CALLS.findall(_text(skill)))
    assert named, f"{skill.name} names no tool at all"
    assert named <= set(server._TOOLS), named - set(server._TOOLS)


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_every_type_a_skill_filters_on_is_real(skill):
    """Filtering on a `type` that does not exist returns empty in silence,
    so a wrong string in a skill is invisible to whoever follows it."""
    named = set(TYPED.findall(_text(skill)))
    assert named <= MEMORY_TYPES, named - MEMORY_TYPES


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_every_sibling_wikilink_points_at_a_shipped_skill(skill):
    """The family cross-references itself by name. A link to a skill that is
    not installed alongside is a dead end."""
    named = set(WIKILINK.findall(_text(skill)))
    assert named <= BY_NAME, named - BY_NAME


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_a_skill_carries_no_machine_specific_path(skill):
    """These files ship to other people's machines. A drive letter is one
    author's filesystem, and the store's own location is `$MEMAI_HOME`."""
    found = DRIVE_PATH.search(_text(skill))
    assert not found, f"{skill.name}/SKILL.md: {found.group(0)!r}"


def test_the_curation_skill_lists_the_kinds_that_demand_a_verified():
    """A skill that names the wrong set tells a pass to work around a guard.

    Every kind in the list is spelled in the skill, and no kind outside it is
    -- naming one that does not demand `verified` is the same error the other
    way round. `set_confidence` is deliberately absent from the mapping: it
    demands one only when its payload says `contradicted`.
    """
    text = _text(hook_install.skills_source() / "memai-maintenance")
    # The claim's own sentence, stopping at the next bullet: a later bullet
    # naming a kind would otherwise count as part of the list.
    claim = re.search(r"destructive kinds:\*\*(.+?)\n- ", text, re.S)
    assert claim, "memai-maintenance no longer states which kinds demand a verified"
    said = {kind for kind in db.SUGGESTION_KINDS if f"`{kind}`" in claim.group(1)}
    assert said == set(db.VERIFIED_REQUIRED), said ^ set(db.VERIFIED_REQUIRED)


def test_the_distill_skill_lists_the_payload_keys_distill_applies():
    """The skill tells a caller which keys travel with a distill, and names
    the rejected ones elsewhere -- so this reads the enumeration itself, not
    the file. A key present in one and not the other fails either way.
    """
    text = _text(hook_install.skills_source() / "memai-distill")
    claim = re.search(r"payload accepts(.+?)and nothing else", text, re.S)
    assert claim, "memai-distill no longer enumerates the accepted payload keys"
    said = set(re.findall(r"`([a-z_]+)`", claim.group(1)))
    assert said == set(db.DISTILL_PAYLOAD_KEYS), said ^ set(db.DISTILL_PAYLOAD_KEYS)


def test_the_curation_skill_documents_every_suggestion_kind():
    """memai-maintenance holds the table of what a pass may stage. A kind
    missing from it is a kind no pass ever proposes -- which is how `review`
    stayed invisible after it shipped.
    """
    text = _text(hook_install.skills_source() / "memai-maintenance")
    missing = [kind for kind in db.SUGGESTION_KINDS if f"`{kind}`" not in text]
    assert not missing, missing
