"""Tests for the server-side SVG renderer, against the canvas it mirrors.

The point of memai.diagram_svg is that a diagram drawn without a browser
comes out the same as the one the dashboard draws. So the numbers in this
file are not this module's own output pinned to lock in a regression --
they were MEASURED IN THE CANVAS and are pinned so that the Python side
has to agree with it.

To re-measure, serve the dashboard and run, in its console:

    const m = await import('/static/diagram-engine.js');
    const ed = Object.create(m.DiagramEditor.prototype);
    ed.cx = document.createElement('canvas').getContext('2d');
    ed.cx.font = "12px 'Roboto', 'Segoe UI', system-ui, sans-serif";
    ed.wrap("Persist through the queue worker", 150, 3);

That only holds while the canvas is looking at the same font file the
width table was extracted from -- see the src order in admin.fonts_css,
which is load-bearing for exactly this reason.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from memai import diagram_svg as ds

FIXTURES = Path(__file__).parent / "fixtures"

# ── goldens measured in the canvas ──────────────────────────────────────

# Advance widths at 2048px, where one pixel is one font unit. Exact: this
# is the table doing nothing but reporting what the file says.
ADVANCE_2048 = {
    "x": 1016, "A": 1336, "V": 1304, "W": 1817, "T": 1222, "i": 498,
    "a": 1114, "o": 1168, "f": 712, ".": 540, "_": 924, "1": 1151,
    " ": 508, "ç": 1072, "ã": 1114, "é": 1086,
}

# Whole strings, ctx.measureText at the sizes the diagram actually draws.
# Deliberately synthetic, and chosen to bracket the failure mode: prose,
# a CamelCase token with no break opportunity, repeated narrow and wide
# glyphs, accented latin, and strings loaded with the pairs Roboto kerns
# hardest (AV, AW, AT, Ta).
CANVAS_UI_12PX = {
    "Load the export window": 127.8047,
    "ValidateReceiverAccount": 133.2070,
    "proj-1042 / X100": 89.3555,
    "Persist through the queue worker": 176.3496,
    "naive cafe facade": 93.9668,
    "naïve café façade — ç ã õ é ê í ó ú à â": 197.9941,
    "iiiiiiiiiiii": 35.0156,
    "WWWWWWWWWWWW": 127.7578,
    "The quick brown fox jumps over the lazy dog": 236.8711,
    "x": 5.9531,
    "retry loop closes here": 114.4512,
}
CANVAS_MONO_10PX = {
    "Load the export window": 132.0215,
    "ValidateReceiverAccount": 138.0225,
    "proj-1042 / X100": 96.0156,
    "Persist through the queue worker": 192.0313,
    "naive cafe facade": 102.0166,
    "naïve café façade — ç ã õ é ê í ó ú à â": 234.0381,
    "iiiiiiiiiiii": 72.0117,
    "WWWWWWWWWWWW": 72.0117,
    "AVAWATa.": 48.0078,
    "AV AW AT Ta ff fi rn": 120.0195,
    "F100_TOTAL USE_NEW_PARSER": 150.0244,
    "The quick brown fox jumps over the lazy dog": 258.0420,
    "x": 6.0010,
    "retry loop closes here": 132.0215,
}
# Kept apart from the block above because these are the KNOWN divergence,
# not a tolerance the ordinary cases need. See test_kerning_is_the_gap.
CANVAS_UI_12PX_KERNED = {
    "AVAWATa.": 55.5996,
    "AV AW AT Ta ff fi rn": 103.8340,
    "F100_TOTAL USE_NEW_PARSER": 173.2441,
}

# Half a pixel on a 12px label. Big enough to absorb the fraction that
# advance-summing loses on ordinary text, small enough that a wrong glyph
# width or a wrong units_per_em cannot hide under it -- the narrowest
# glyph in the table is still ~2.9px at this size.
PROSE_TOLERANCE_PX = 0.5


def test_per_glyph_advance_is_exact():
    """No tolerance here: one glyph cannot kern against anything."""
    for ch, expected in ADVANCE_2048.items():
        assert ds.measure(ch, 2048.0, ds.FACE_UI) == pytest.approx(expected, abs=0.01), ch


def test_mono_matches_the_canvas_exactly():
    """Roboto Mono has no kerning to lose, so this one has to be exact."""
    for text, expected in CANVAS_MONO_10PX.items():
        got = ds.measure(text, 10.0, ds.FACE_MONO)
        assert got == pytest.approx(expected, abs=0.001), text


def test_mono_is_monospaced():
    """A width table read from the wrong face would not survive this."""
    narrow = ds.measure("i" * 12, 10.0, ds.FACE_MONO)
    wide = ds.measure("W" * 12, 10.0, ds.FACE_MONO)
    assert narrow == pytest.approx(wide, abs=0.001)


def test_prose_matches_the_canvas_within_half_a_pixel():
    for text, expected in CANVAS_UI_12PX.items():
        got = ds.measure(text, 12.0, ds.FACE_UI)
        assert got == pytest.approx(expected, abs=PROSE_TOLERANCE_PX), text


def test_kerning_is_the_gap_and_it_is_bounded():
    """The one thing summing advances cannot do is kern.

    Recorded rather than fixed. Extracting Roboto's pair-kerning tables
    would close it, and it is not worth the size: the error only reaches
    a readable fraction of a pixel on text built out of the pairs Roboto
    kerns hardest, which a step label is not. What matters downstream is
    that it stays small enough not to move a wrap boundary on ordinary
    text, and that an SVG line carries textLength so a line that DID
    measure differently still occupies the width it was laid out for.

    Note the sign is not guaranteed either way -- 'Load the export
    window' measures NARROWER here than in the canvas -- so this is not
    a safe-direction error, just a small one.
    """
    worst = 0.0
    for text, expected in CANVAS_UI_12PX_KERNED.items():
        worst = max(worst, abs(ds.measure(text, 12.0, ds.FACE_UI) - expected))
    assert 1.0 < worst < 3.5, f"kerning gap moved to {worst:.4f}px"


def test_unknown_codepoint_falls_back_to_notdef():
    """A glyph outside the latin subset must measure something bounded.

    Not what a browser does -- it reaches for another installed font --
    but a number, so a label containing one still wraps instead of
    silently measuring zero and overflowing its card.
    """
    assert ds.measure("中", 12.0, ds.FACE_UI) > 0


# ── wrap / clamp / short_label, all goldens from the canvas ─────────────

WRAP_CASES = [
    ("Load the export window", 150, 3, ["Load the export window"]),
    ("Persist through the queue worker", 150, 3,
     ["Persist through the queue", "worker"]),
    ("Persist through the queue worker", 150, 2,
     ["Persist through the queue", "worker"]),
    # one line left, and something was dropped, so it has to say so
    ("Persist through the queue worker", 150, 1, ["Persist through the queue…"]),
    # a single CamelCase token: no break opportunity, so the per-line
    # clamp is the only thing keeping it inside the card
    ("ValidateReceiverAccountBeforeSettlement", 150, 2, ["ValidateReceiverAccountB…"]),
    ("The quick brown fox jumps over the lazy dog", 150, 2,
     ["The quick brown fox jumps", "over the lazy dog"]),
    ("naive cafe facade with a long tail", 110, 2,
     ["naive cafe facade", "with a long tail"]),
    ("F100_TOTAL USE_NEW_PARSER proj-1042", 90, 3,
     ["F100_TOTAL", "USE_NEW_PAR…", "proj-1042"]),
    ("x", 150, 1, ["x"]),
    # the kerning-heavy case, at a width where the gap could plausibly
    # move the break -- it must not
    ("AV AW AT Ta ff fi rn", 60, 2, ["AV AW AT", "Ta ff fi rn"]),
]


@pytest.mark.parametrize(("text", "width", "lines", "expected"), WRAP_CASES)
def test_wrap_matches_the_canvas(text, width, lines, expected):
    assert ds.wrap(text, width, lines, 12.0) == expected


CLAMP_CASES = [
    ("Load the export window", 60, "Load the …"),
    ("ValidateReceiverAccountBeforeSettlement", 80, "ValidateRece…"),
    ("x", 150, "x"),
]


@pytest.mark.parametrize(("text", "width", "expected"), CLAMP_CASES)
def test_clamp_matches_the_canvas(text, width, expected):
    assert ds.clamp_line(text, width, 12.0) == expected


SHORT_LABEL_CASES = [
    ("ok", "ok"),
    ("retry loop closes here", "retry loop closes…"),
    ("cancelled by the operator, no write", "cancelled by the…"),
    ("one two three four five", "one two three…"),
    # 26 chars but a single word: over BADGE_CHARS, under BADGE_WORDS, and
    # both limits have to be passed before anything is cut
    ("abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstuvwxyz"),
    ("a b c", "a b c"),
    ("yes: go on,", "yes: go on,"),
]


@pytest.mark.parametrize(("text", "expected"), SHORT_LABEL_CASES)
def test_short_label_matches_the_canvas(text, expected):
    assert ds.short_label(text) == expected


def test_short_label_does_not_leave_punctuation_before_the_ellipsis():
    """A cut landing on a comma or a colon reads as a typo, not a cut.

    Only when the cut LANDS there, note. 'first, second, third' is cut at
    BADGE_CHARS and happens to end on a letter, so its commas survive --
    the rule strips what the cut exposed, it does not hunt punctuation.
    """
    assert ds.short_label("check the flag: then continue on") == "check the flag…"
    assert ds.short_label("alpha beta, gamma delta") == "alpha beta, gamma…"
    assert ds.short_label("first, second, third, fourth") == "first, second, third…"


# ── edge routing, against what the canvas routes ────────────────────────
#
# route-golden.json is RECORDED FROM THE CANVAS by tools/route-parity.mjs;
# it is not this module's output. So these tests are the Python half of the
# parity check: the JS half is that same script re-run without --write,
# which fails if the canvas stops agreeing with the recording.
#
# Both halves matter, and neither is enough alone. Without the recording,
# Python would only be tested against itself; without the script, the
# canvas could drift away from the recording and nothing would say so.
# Editing either implementation means: re-record, run pytest, fix the side
# that is now wrong.


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return _load("route-fixture.json")


@pytest.fixture(scope="module")
def golden() -> dict:
    return _load("route-golden.json")


def _record(data: dict, routing: str) -> dict:
    """Mirror of record() in tools/route-parity.mjs, same field for field."""
    layout = ds.DiagramLayout(data, routing)
    edges = []
    for e in layout.edges:
        pts, curve = layout.route(e)
        via = None
        if e["via"] is not None:
            via = ({"corridor": e["via"]["corridor"]} if "corridor" in e["via"]
                   else {"crossY": e["via"]["crossY"]})
        has_corridor = bool(e["lane"]) or (e["via"] is not None
                                          and "corridor" in e["via"])
        edges.append({
            "from": e["from"], "to": e["to"],
            "back": e["back"], "loops": e["loops"],
            "lane": e["lane"], "bow": e["bow"],
            "via": via,
            "fan_from": e["fan_from"], "fan_to": e["fan_to"],
            "stub_from": e["stub_from"], "stub_to": e["stub_to"],
            "corridor": layout.corridor_for(e) if has_corridor else None,
            "curve": dict(curve) if curve else None,
            "pts": [dict(p) for p in pts],
            "label_at": ds.midpoint(pts) if pts else None,
            "short_label": ds.short_label(e["label"]),
        })
    return {
        "routing": routing,
        "font_scale": layout.font_scale,
        "lane_span": (dict(layout.lane_span) if layout.lane_span else None),
        "orphans": sorted(layout.orphans),
        "nodes": [{"key": n["key"], "shape": n["shape"], "sized": n["sized"],
                   "x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"]}
                  for n in layout.nodes],
        "edges": edges,
    }


def _same(got, want, path: str = "") -> None:
    """Compare recursively, with a tolerance only where floats are involved.

    The golden rounds to 1e-6, so anything wider than that is a real
    disagreement about geometry rather than a last-bit difference between
    two IEEE-754 evaluation orders.
    """
    if isinstance(want, bool) or isinstance(got, bool):
        assert got == want, path
    elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
        assert got == pytest.approx(want, abs=1e-6), path
    elif isinstance(want, dict):
        assert isinstance(got, dict), path
        assert set(got) == set(want), f"{path}: keys {sorted(got)} != {sorted(want)}"
        for key in want:
            _same(got[key], want[key], f"{path}.{key}")
    elif isinstance(want, list):
        assert isinstance(got, list), path
        assert len(got) == len(want), f"{path}: {len(got)} items, want {len(want)}"
        for i, item in enumerate(want):
            _same(got[i], item, f"{path}[{i}]")
    else:
        assert got == want, path


@pytest.mark.parametrize("routing", ["orthogonal", "curved"])
def test_routing_matches_the_canvas(fixture_data, golden, routing):
    _same(_record(fixture_data, routing), golden[routing], routing)


def test_golden_was_recorded_from_this_fixture(fixture_data, golden):
    """Catch the recording going stale against an edited fixture.

    Without this, adding an edge to the fixture and forgetting to re-record
    leaves the routing test silently checking fewer edges than exist.
    """
    kept = [e for e in fixture_data["edges"]
            if e["from"] in {n["key"] for n in fixture_data["nodes"]}
            and e["to"] in {n["key"] for n in fixture_data["nodes"]}]
    for routing in ("orthogonal", "curved"):
        assert len(golden[routing]["edges"]) == len(kept)
        assert len(golden[routing]["nodes"]) == len(fixture_data["nodes"])


def test_the_fixture_exercises_every_routing_branch(golden):
    """A fixture that stopped covering a branch is a test that stopped testing.

    Every one of these was absent from the first version of the fixture,
    and the margin-lane case in particular is the hardest code in the
    module -- alternating sides, stacked reach, and the span fit() has to
    reserve.
    """
    edges = golden["orthogonal"]["edges"]
    lanes = [e for e in edges if e["lane"]]
    assert {e["lane"] for e in lanes} == {1, -1}, "both sides must be taken"
    assert len({abs(e["bow"]) for e in lanes}) > 1, "lanes must stack outward"
    assert any(e["via"] and "corridor" in e["via"] for e in edges)
    assert any(e["via"] and "crossY" in e["via"] for e in edges)
    assert any(e["fan_from"] or e["fan_to"] for e in edges)
    assert any(e["stub_from"] or e["stub_to"] for e in edges)
    assert any(len(e["pts"]) == 2 for e in edges), "a straight run"
    assert any(len(e["pts"]) > 6 for e in edges), "a funnelled corridor run"
    assert golden["orthogonal"]["orphans"], "an unreachable step"
    assert any(n["sized"] for n in golden["orthogonal"]["nodes"])
    assert any(not n["sized"] for n in golden["orthogonal"]["nodes"])
    shapes = {n["shape"] for n in golden["orthogonal"]["nodes"]}
    assert shapes == set(ds.NODE_SHAPES)
    assert any(e["curve"] for e in golden["curved"]["edges"])


# Nudged by one unit each: enough to move a route, small enough that a
# fixture only covering the comfortable middle of every threshold would
# not notice. Both ORTH_RADIUS and ORTH_SNAP DID slip through the first
# fixture, which is why this test exists rather than being assumed.
MIRRORED_CONSTANTS = {
    "IO_SKEW": 15.0, "ARROW_GAP": 6.0, "ORTH_STUB": 27.0,
    "FAN_GAP": 23.0, "FAN_STUB": 15.0, "MERGE_GAP": 31.0,
    "MERGE_TAIL": 10.0, "ORTH_RADIUS": 12.0, "ORTH_SNAP": 19.0,
}


# ── the emitted markup ──────────────────────────────────────────────────
#
# A separate layer of tests, because the route golden CANNOT see this one:
# it records the polyline, so anything that goes wrong turning that polyline
# into SVG is invisible to it. Both bugs found by eye in the first working
# render lived here, and both have a test below:
#
#   * rounded corners insetting by r instead of r/tan(alpha/2), which looks
#     right on every 90-degree corner and draws a loop on a diagonal;
#   * stroke widths in world units instead of screen pixels, which makes
#     every line vanish at the scale a long flow fits into while the
#     arrowheads and labels stay.


@pytest.fixture(scope="module")
def svg(fixture_data) -> str:
    return ds.render_svg(fixture_data)[0]


def test_svg_is_well_formed_and_self_contained(svg):
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.get("viewBox")
    # nothing that would reach the network or execute
    assert "<script" not in svg
    assert "foreignObject" not in svg
    assert "http://" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "https://" not in svg


def test_every_stroked_class_holds_its_width_in_screen_pixels(svg):
    """The engine divides lineWidth by this.scale; SVG needs vector-effect.

    Without it, a 1.7-unit line renders at ~0.15px once a 34-step flow is
    scaled to fit, so the drawing loses every edge while keeping the
    arrowheads and labels -- which is what "the arrows point at nothing"
    looked like.
    """
    style = svg.split("<style>")[1].split("</style>")[0]
    for cls in (".dg-e", ".dg-el", ".dg-n", ".dg-nt", ".dg-no"):
        block = _css_block(style, cls)
        assert "non-scaling-stroke" in block, cls


def _css_block(style: str, cls: str) -> str:
    """The declarations attached to `cls`, including grouped selectors."""
    out = []
    for rule in style.split("}"):
        if ":" not in rule:
            continue
        selector, _, body = rule.partition("{")
        if cls in [s.strip() for s in selector.split(",")]:
            out.append(body)
    assert out, f"no rule for {cls}"
    return ";".join(out)


def test_one_shape_and_one_line_per_element(fixture_data, svg):
    layout = ds.DiagramLayout(fixture_data)
    assert svg.count('class="dg-e"') + svg.count('class="dg-el"') == len(layout.edges)
    shapes = sum(svg.count(f'class="{c}"') for c in ("dg-n", "dg-nt", "dg-no"))
    assert shapes == len(layout.nodes)
    # arrowheads are markers, not 49 drawn triangles
    assert svg.count("<marker") == 2


def test_a_diagonal_corner_insets_by_the_tangent_not_the_radius():
    """r/tan(alpha/2), which only equals r at 90 degrees.

    A 45-degree turn with ORTH_RADIUS 11 has to start its arc 4.56 units
    before the vertex, not 11. Insetting by the radius put the arc's start
    past its own tangent point and the renderer closed the gap the long way
    round -- a visible loop on the line.
    """
    square = ds.edge_d([{"x": 0, "y": 0}, {"x": 100, "y": 0},
                        {"x": 100, "y": 100}], None)
    assert "L89 0" in square          # 90 degrees: inset == r == 11

    diagonal = ds.edge_d([{"x": 0, "y": 0}, {"x": 100, "y": 0},
                          {"x": 200, "y": 100}], None)
    assert "L95 0" in diagonal        # 45 degrees: inset == 11*0.4142
    assert "L89 0" not in diagonal, "insetting by r again"


def test_collinear_points_do_not_get_an_arc():
    """Three points in a line have no corner to round, and an arc between
    two coincident tangents is what a renderer draws as a full circle."""
    d = ds.edge_d([{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 100, "y": 0}], None)
    assert "A" not in d


def test_the_frame_contains_every_routed_point(fixture_data):
    """An SVG cannot be panned, so anything outside the frame is simply gone
    -- including the margin corridors, which reach past every card."""
    layout = ds.DiagramLayout(fixture_data)
    routes = {(e["from"], e["to"]): layout.route(e) for e in layout.edges}
    x0, y0, vw, vh = ds.frame(layout, routes)
    for (src, dst), (pts, curve) in routes.items():
        for p in ds.flatten(pts, curve):
            assert x0 <= p["x"] <= x0 + vw, f"{src}->{dst} x"
            assert y0 <= p["y"] <= y0 + vh, f"{src}->{dst} y"


def test_notes_become_tooltips_and_can_be_left_out(fixture_data):
    with_notes = ds.render_svg(fixture_data, notes=True)[0]
    without = ds.render_svg(fixture_data, notes=False)[0]
    noted = [n for n in fixture_data["nodes"] if n.get("note")]
    assert with_notes.count("<title>") == len(noted)
    assert "<title>" not in without
    assert len(without) < len(with_notes)


def test_link_counts_are_drawn_inside_the_shape(fixture_data):
    """Two links on one step, so the count and its glyph both appear."""
    svg_text = ds.render_svg(fixture_data)[0]
    assert 'class="dg-c"' in svg_text
    assert 'class="dg-lm"' in svg_text


def test_interactive_wraps_the_same_drawing(fixture_data):
    html = ds.render_interactive(fixture_data)
    svg_only = ds.render_svg(fixture_data)[0]
    # the drawing is unchanged; only a shell is added around it
    assert svg_only in html
    assert 'id="dg-vp"' in html and "getScreenCTM" in html
    for button in ("dg-in", "dg-out", "dg-st", "dg-all"):
        assert f'id="{button}"' in html


def test_interactive_opens_above_the_label_threshold(fixture_data):
    """Opening below LABEL_MIN_SCALE gives a diagram with no text on it,
    which is what asking for a transform multiplier instead of an on-screen
    scale produced."""
    html = ds.render_interactive(fixture_data, open_scale=0.85)
    assert "centreOn" in html
    assert f"0.85" in html
    assert str(ds.LABEL_MIN_SCALE) in html


@pytest.mark.parametrize(("name", "perturbed"), sorted(MIRRORED_CONSTANTS.items()))
def test_the_fixture_notices_a_changed_constant(
    fixture_data, golden, monkeypatch, name, perturbed
):
    """A constant the golden cannot feel is a constant nothing is checking.

    The parity harness is only worth its complexity if the fixture drives
    every threshold hard enough that a wrong number shows up. So: perturb
    one, and require the comparison to fail.
    """
    monkeypatch.setattr(ds, name, perturbed)
    with pytest.raises(AssertionError):
        for routing in ("orthogonal", "curved"):
            _same(_record(fixture_data, routing), golden[routing], routing)
