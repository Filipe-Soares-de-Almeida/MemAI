"""What the body renderer draws, held to the markup the store actually uses.

The renderer is JavaScript, so these run it: tools/richtext-cases.mjs feeds
core/richtext.js a set of bodies under node and prints what came back. The
same arrangement as the diagram route golden -- the browser's code is the
code under test, rather than a Python re-implementation of it standing in
for it.

Skipped where node is absent; the renderer still ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tools" / "richtext-cases.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def drawn() -> dict[str, str]:
    out = subprocess.run(["node", str(CASES)], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ------------------------------------------------------------------ blocks

def test_a_blank_line_starts_a_paragraph(drawn):
    assert drawn["paragraphs"].count("<p class=\"rt-p\">") == 2


def test_a_line_break_inside_a_paragraph_survives(drawn):
    assert "first line\nstill the same paragraph" in drawn["paragraphs"]


def test_a_fenced_heading_becomes_one(drawn):
    assert "<h4 class=\"rt-h\">1. WHAT WAS MEASURED</h4>" in drawn["heading"]


def test_bullets_and_numbers_pick_their_own_list(drawn):
    assert drawn["bullets"].startswith("<ul")
    assert drawn["ordered"].startswith("<ol")


def test_a_nested_list_sits_inside_the_item_above_it(drawn):
    """A <ul> as a sibling of <li> is invalid, and browsers reparent it."""
    assert "<li>outer point<ul" in drawn["nested"]
    assert "</li><ul" not in drawn["nested"]


def test_a_wrapped_line_continues_its_item(drawn):
    assert "<li>a point that runs\nonto a second line</li>" in drawn["continuation"]


# ------------------------------------------------------------------ tables

def test_a_gfm_table_becomes_a_table(drawn):
    assert "<table class=\"rt-table\">" in drawn["table"]
    assert "<th>field</th>" in drawn["table"]


def test_the_separator_carries_the_alignment(drawn):
    assert "<th style=\"text-align:right\">holds</th>" in drawn["table"]


def test_a_one_column_table_is_still_a_table(drawn):
    assert "<table class=\"rt-table\">" in drawn["escaping_in_table"]


def test_a_short_row_is_padded_rather_than_dropped(drawn):
    assert drawn["table_short_row"].count("<td") == 3


def test_pipes_with_no_separator_keep_their_spacing(drawn):
    """Not a grid -- and collapsing it would lose what was lined up by hand."""
    assert "<pre class=\"rt-raw\">" in drawn["pipes_without_separator"]
    assert "<table" not in drawn["pipes_without_separator"]


def test_a_shape_it_does_not_know_comes_out_as_it_went_in(drawn):
    assert "    indented text nobody taught it\n    lined up by hand" in drawn["unknown_shape"]


# ------------------------------------------------------------------ inline

def test_a_code_span_becomes_code(drawn):
    assert "<code>USE_NEW_PARSER</code>" in drawn["code"]


def test_what_is_inside_a_code_span_stays_text(drawn):
    assert "<code>**not bold**</code>" in drawn["code_holds_its_text"]
    assert "<code>[[1111111111111111]]</code>" in drawn["code_holds_its_text"]
    assert "<strong>" not in drawn["code_holds_its_text"]
    assert "rt-link" not in drawn["code_holds_its_text"]


def test_bold_becomes_strong(drawn):
    assert "<strong>not</strong>" in drawn["bold"]


def test_a_url_is_a_link_without_the_punctuation_after_it(drawn):
    assert 'href="https://example.com/x100"' in drawn["url_trailing_dot"]
    assert drawn["url_trailing_dot"].endswith(".</p>")


# ------------------------------------------------------------------- links

def test_a_live_uid_becomes_a_button_that_says_where_it_goes(drawn):
    assert 'data-uid="1111111111111111"' in drawn["link_live"]
    assert "note · acme/x100 · the cache warms on boot" in drawn["link_live"]


def test_a_reference_with_no_edge_behind_it_is_marked(drawn):
    assert "rt-link-unlinked" in drawn["link_unlinked"]
    assert "rt-link-unlinked" not in drawn["link_live"]


def test_a_uid_that_resolves_to_nothing_is_not_a_button(drawn):
    assert "rt-link-dead" in drawn["link_dead"]
    assert "<button" not in drawn["link_dead"]


def test_a_wikilink_that_is_not_a_uid_is_not_a_link(drawn):
    assert "rt-link-plain" in drawn["link_by_name"]
    assert "<button" not in drawn["link_by_name"]


# ---------------------------------------------------------------- escaping

@pytest.mark.parametrize("case", ["escaping", "escaping_in_code", "escaping_in_table"])
def test_nothing_from_a_body_reaches_the_page_as_markup(drawn, case):
    assert "<script>" not in drawn[case]
    assert "<b>" not in drawn[case]
    assert "&lt;" in drawn[case]


def test_an_empty_body_draws_nothing(drawn):
    assert drawn["empty"] == ""
