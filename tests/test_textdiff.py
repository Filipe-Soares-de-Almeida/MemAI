"""What the before/after panes mark as changed.

The diff is JavaScript, so these run it: tools/diff-cases.mjs feeds
core/textdiff.js a set of pairs under node and prints the ranges it found,
spliced back into the text as [-dropped-] and [+added+].

Skipped where node is absent; the diff still ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tools" / "diff-cases.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def marked() -> dict[str, dict]:
    out = subprocess.run(["node", str(CASES)], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_two_equal_texts_mark_nothing(marked):
    case = marked["identical"]
    assert case["del"] == [] and case["ins"] == []


def test_a_dropped_clause_is_marked_on_the_before_side_only(marked):
    case = marked["a_clause_dropped"]
    assert case["before"].endswith(
        "[-The second run uploads every row again.-]")
    assert case["ins"] == []


def test_an_added_clause_is_marked_on_the_after_side_only(marked):
    case = marked["a_clause_added"]
    assert "[+, and the batch retries what it could not+]" in case["after"]
    assert case["del"] == []


def test_a_swapped_word_is_marked_on_both_sides(marked):
    case = marked["one_word_swapped"]
    assert case["before"].endswith("[-boot-]")
    assert case["after"].endswith("[+deploy+]")


def test_a_mark_covers_whole_words(marked):
    """The range hugs the words, not the spaces beside them."""
    case = marked["punctuation_survives_its_word"]
    assert "[-queue drain,-]" in case["before"]


def test_whitespace_alone_is_not_a_change(marked):
    case = marked["whitespace_only"]
    assert case["del"] == [] and case["ins"] == []


def test_an_empty_side_marks_the_whole_of_the_other(marked):
    assert marked["empty_before"]["after"] == "[+the report export+]"
    assert marked["empty_after"]["before"] == "[-the report export-]"


def test_a_value_field_marks_only_what_was_appended(marked):
    """The panes also hold tag sets and domain paths, not only prose."""
    assert marked["a_tag_appended"]["after"] == "cache, queue, token[+, index+]"
    assert marked["a_field_replaced"]["after"] == "acme/x100[+/p200+]"


def test_short_unchanged_words_between_two_changes_join_them(marked):
    """A rewritten clause is one mark, not a fragment per surviving "the"."""
    case = marked["short_survivors_are_bridged"]
    assert len(case["del"]) == 1 and len(case["ins"]) == 1
    assert case["before"] == "RESULT: the drain [-counts every message twice-]."
    assert case["after"] == "RESULT: the drain [+reports a total the batch never wrote+]."


def test_an_unchanged_run_wider_than_the_bridge_keeps_the_marks_apart(marked):
    case = marked["a_survivor_wider_than_the_bridge_splits_the_mark"]
    assert case["after"] == (
        "the [+queue drains+] before the index rebuild [+finishes+] every deployment")


def test_a_reordered_pair_marks_each_side_once(marked):
    case = marked["moved_clause"]
    assert case["before"] == "[-first the cache warms, then the queue drains-]"
    assert case["after"] == "[+then the queue drains, first the cache warms+]"


def test_a_pair_over_the_cap_marks_the_changed_middle_whole(marked):
    """Past the LCS ceiling the diff still says where the change is."""
    case = marked["long_bodies_over_the_cap"]
    assert len(case["del"]) == 1 and len(case["ins"]) == 1
    assert case["before"].startswith("[-w0 ") and case["before"].endswith("-]")
    assert case["after"].startswith("[+x0 ") and case["after"].endswith("+]")
