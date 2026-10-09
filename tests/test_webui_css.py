"""admin.css, read as the stylesheet it is: the type colours, the checklist's
motion and marks, the item panel's contrast, and the scrollbar gutters."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from memai.store import memories

CSS = Path(__file__).resolve().parents[1] / "src" / "memai" / "webui" / "admin.css"


@pytest.fixture(scope="module")
def css() -> str:
    return CSS.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


# ------------------------------------------------------------- type colours

def test_every_type_has_its_colour_tokens_and_class(css):
    for tp in memories.MEMORY_TYPES:
        assert re.search(rf"--t-{tp}:\s*#[0-9a-f]{{6}}", css), f"--t-{tp} is not declared"
        assert re.search(rf"--t-{tp}-ink:\s*#[0-9a-f]{{6}}", css), f"--t-{tp}-ink is not declared"
        assert re.search(rf"\.t-{tp}\s*\{{", css), f".t-{tp} is not declared"


# ------------------------------------------------------------ the checklist

def test_the_doing_mark_turns_and_reduced_motion_stops_it(css):
    assert re.search(r'\.tk-state\[data-s="doing"\] \.ico \{ animation: spin var\(--spin\) linear infinite var\(--spin-at, 0s\); \}', css)
    assert re.search(r"--spin: ([\d.]+)s", css)
    assert ':root[data-motion="reduce"] .tk-state[data-s="doing"] .ico { animation: none; }' in css
    assert ':root[data-motion="reduce"] .tk { --pop-from: 1; --leave-x: 0px; --tk-in-y: 0px; }' in css


def test_the_checklist_animates_only_compositor_properties_and_never_layout(css):
    start = css.index("/* A task's checklist")
    end = css.index("/* a task in the memories list")
    tk = css[start:end]
    for keyframes in re.findall(r"@keyframes tk-[\w-]+ \{[^}]*\}", tk):
        assert not re.search(r"\b(width|height|top|left|margin|padding)\s*:", keyframes), keyframes
    assert "tk-bar-drop" not in css


def test_the_item_row_column_comes_from_a_token_not_a_literal(css):
    row = re.search(r"^\.tk-row \{([^}]*)\}", css, re.M).group(1)
    assert "28px" not in row
    assert "var(--ctl-h-sm)" in row.split("grid-template-columns:")[1].split(";")[0]


def test_a_comment_mark_is_round_for_a_person_and_square_for_an_agent(css):
    """shape, not colour alone, and no tinted bubble per comment"""
    assert re.search(r"\.tk-c\.is-person \.tk-c-av \{[^}]*border-radius: 50%", css)
    assert re.search(r"\.tk-c\.is-agent \.tk-c-av \{[^}]*border-radius: var\(--r-sm\)", css)
    assert not re.search(r"\.tk-c \{[^}]*background", css)


# ------------------------------------------------------------ reduced motion

def test_no_stylesheet_rule_reads_the_media_query_directly():
    assert "prefers-reduced-motion" not in _css()
    assert ':root[data-motion="reduce"]' in _css()


def test_the_reduced_spinner_outranks_the_spinner_declared_after_it():
    css = _css()
    assert ':root[data-motion="reduce"] .spin { animation: breathe' in css
    assert re.search(r"^\.spin \{[^}]*animation: spin", css, re.M | re.S)


# ----------------------------------------------------------- the item panel

def _srgb(v: float) -> float:
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    r, g, b = (_srgb(c / 255) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex(css: str, name: str) -> tuple[int, int, int]:
    h = re.search(rf"--{name}:\s*#([0-9a-f]{{6}})", css).group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _alpha(css: str, name: str) -> float:
    return float(re.search(rf"--{name}:\s*rgba\(255, 255, 255, ([\d.]+)\)", css).group(1))


def _ratio(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_the_item_panel_sits_between_the_page_and_its_row_and_its_text_reads_on_it():
    css = _css()
    panel = re.search(r"^\.tk-panel \{([^}]*)\}", css, re.M).group(1)
    assert "background: var(--inset)" in panel
    assert "margin" not in panel and "border-radius" not in panel
    page, well, rows = _hex(css, "bg"), _hex(css, "inset"), _hex(css, "surface")
    assert _lum(page) < _lum(well) < _lum(rows)
    for tier in ("ink", "ink-2", "ink-3"):
        a = _alpha(css, tier)
        text = tuple(a * 255 + (1 - a) * c for c in well)
        assert _ratio(_lum(text), _lum(well)) >= 4.5, tier


def test_the_wash_sits_on_the_row_header_and_never_on_the_panel():
    css = _css()
    assert not re.search(r"^\.tk-item[^{>]*\{[^}]*background", css, re.M)
    assert re.search(r'^\.tk-item\[data-s="doing"\] > \.tk-row \{ background: color-mix\(in srgb, var\(--f-live\) 7%', css, re.M)
    assert re.search(r"^\.tk-item\.is-open > \.tk-row \{ background: var\(--hover-2\)", css, re.M)


def test_the_panes_that_scroll_keep_room_for_their_scrollbar():
    css = _css()
    rule = re.search(r"([^{}]*)\{ scrollbar-gutter: stable; \}", css).group(1)
    selectors = [s.strip() for s in rule.split("*/")[-1].split(",")]
    assert selectors == [
        ".view", ".rec-main", ".rec-index", ".rs-body", ".rf-body", ".mem-list #memList",
        ".mi-body", ".dom-col-body", ".dom-detail-body", ".opt-rail", ".opt-rows",
        ".mnt-log", ".mnt-shelf-body", ".mnt-panel .panel > .panel-body"]


def test_a_view_that_clips_instead_of_scrolling_reserves_no_strip():
    css = _css()
    for sel in (".view.wide", ".view.fill"):
        body = re.search(rf"^  {re.escape(sel)} \{{([^}}]*)\}}", css, re.M).group(1)
        assert "overflow: hidden" in body and "scrollbar-gutter: auto" in body, sel


def test_the_graph_view_reserves_no_strip_because_its_canvas_never_scrolls_the_page(css):
    assert ".view:has(> .anim > .graph-wrap) { scrollbar-gutter: auto; }" in css
