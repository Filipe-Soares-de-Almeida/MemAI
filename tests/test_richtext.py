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


def test_a_gap_between_items_does_not_start_a_second_list(drawn):
    """The store's own bodies space long lists out. Closing the run at every
    gap gave each item a list of its own, so all of them read as `1.`."""
    assert drawn["spaced_items"].count("<ol") == 1
    assert drawn["spaced_items"].count("<li>") == 3


def test_a_spaced_item_still_takes_the_lines_under_it(drawn):
    assert "<li>first point\na line under it</li>" in drawn["spaced_items_with_body"]


def test_a_gap_before_something_that_is_not_an_item_ends_the_list(drawn):
    assert drawn["list_then_paragraph"].endswith(
        "</ul><p class=\"rt-p\">not a point any more</p>")


def test_a_spaced_list_is_marked_so_it_keeps_its_air(drawn):
    assert "rt-list-loose" in drawn["spaced_items"]
    assert "rt-list-loose" not in drawn["ordered"]


def test_a_run_that_opens_at_three_counts_from_three(drawn):
    assert "<ol class=\"rt-list\" start=\"3\">" in drawn["list_starting_at_three"]


def test_a_run_that_opens_at_one_says_nothing_about_where_to_start(drawn):
    assert "start=" not in drawn["ordered"]


def test_a_gap_does_not_break_a_nested_list_out_of_its_item(drawn):
    assert "<li>outer<ul" in drawn["spaced_nested"]
    assert "</li><ul" not in drawn["spaced_nested"]


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


# ------------------------------------------------------------ fenced blocks

def test_a_fence_becomes_a_code_block_naming_its_language(drawn):
    assert '<span class="rt-code-lang">powershell</span>' in drawn["fenced"]
    assert '<code data-lang="powershell">' in drawn["fenced"]


def test_the_language_sits_in_a_bar_of_its_own(drawn):
    """Drawn over the <pre> it covered the first line, and a block wide
    enough to scroll slid its own text under the label."""
    head = drawn["fenced"].index('rt-code-head')
    assert head < drawn["fenced"].index('<pre class="rt-code">')


def test_every_code_block_offers_to_copy_itself(drawn):
    for case in ("fenced", "fenced_no_language", "fenced_unclosed"):
        assert "data-copy-code" in drawn[case]


def test_a_fence_with_no_language_is_still_a_code_block(drawn):
    assert '<pre class="rt-code">' in drawn["fenced_no_language"]
    assert "data-lang" not in drawn["fenced_no_language"]


def test_nothing_inside_a_fence_is_markup(drawn):
    """Fencing it is how a writer says the contents are not prose."""
    out = drawn["fenced_holds_its_markup"]
    assert "<strong>" not in out and "rt-link" not in out
    assert "`code` span and **bold** and [[1111111111111111]]" in out


def test_a_fence_keeps_its_blank_lines(drawn):
    assert "one\n\ntwo" in drawn["fenced_keeps_blank_lines"]


def test_a_fence_nobody_closed_runs_to_the_end(drawn):
    assert "print(1)\nprint(2)" in drawn["fenced_unclosed"]
    assert "```" not in drawn["fenced_unclosed"]


def test_a_fence_escapes_what_is_inside_it(drawn):
    assert "&lt;b&gt;&amp;amp;&lt;/b&gt;" in drawn["fenced_escapes"]


def test_prose_after_a_fence_is_prose_again(drawn):
    assert drawn["text_after_fence"].endswith('<p class="rt-p">back to prose</p>')


# ------------------------------------------------- the vendored highlighter

CSS = ROOT / "src" / "memai" / "webui" / "admin.css"
HIGHLIGHT_CASES = ROOT / "tools" / "highlight-cases.mjs"

# Scopes left to inherit the block's colour on purpose: containers that wrap
# the parts that ARE coloured, and punctuation, which reads as noise in five
# colours and as code in one.
INHERITS = {"hljs-function", "hljs-params", "hljs-punctuation", "hljs-operator",
            "hljs-property", "hljs-tag"}


@pytest.fixture(scope="module")
def coloured() -> dict:
    out = subprocess.run(["node", str(HIGHLIGHT_CASES)], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_every_vendored_grammar_loads_and_colours(coloured):
    assert coloured["failed"] == []
    assert set(coloured["coloured"]) == set(coloured["shipped"])


def test_colouring_a_snippet_changes_no_character_of_it(coloured):
    """The block is already on screen when the grammar arrives; a highlighter
    that edited the text would be rewriting a memory to look at it."""
    for language, result in coloured["coloured"].items():
        assert result["intact"], f"{language} altered its snippet"


def test_the_dashboard_styles_every_scope_the_grammars_emit(coloured):
    css = CSS.read_text(encoding="utf-8")
    emitted = {scope for r in coloured["coloured"].values() for scope in r["scopes"]}
    unstyled = {s for s in emitted - INHERITS if f".{s}" not in css}
    assert not unstyled, f"no colour for {sorted(unstyled)} -- style it or list it in INHERITS"


def test_the_vendored_tree_carries_its_licence():
    vendor = ROOT / "src" / "memai" / "webui" / "public" / "vendor" / "highlight"
    assert (vendor / "LICENSE").read_text(encoding="utf-8").startswith("BSD 3-Clause")
    assert (vendor / "core.min.js").exists()
