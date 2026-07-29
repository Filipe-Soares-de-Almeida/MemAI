"""Server-side SVG rendering of a diagram, pixel-for-pixel with the canvas.

=========================================================================
THIS FILE IS THE PYTHON TWIN OF webui/diagram-engine.js
=========================================================================
The admin dashboard draws a diagram on a <canvas> in JavaScript; this
module draws the same diagram as SVG for readers with no browser -- a
chat client, an exporter. Neither one is derived from the other, so
every constant and every geometry decision below exists TWICE.

Change one, change both, then run:

    node tools/route-parity.mjs

which routes a shared fixture through both implementations and diffs the
result. A silent divergence here does not raise: it draws a diagram that
is subtly not the one the user arranged, which is worse.

The split is deliberate rather than ideal. The canvas needs the geometry
in JavaScript anyway -- it hit-tests, drags and zooms against it -- so
one implementation could not serve both without shipping a browser on the
server side.
=========================================================================

What is NOT duplicated: node layout. The x/y of every node is computed
once, in db.py, and both renderers read it. Only edge routing and text
measurement live in two places.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from . import db

# ── mirrored constants ──────────────────────────────────────────────────
# Every value in this block has a twin in webui/diagram-engine.js under
# the same name (SCREAMING_CASE there too). The comments explaining WHY
# each number is what it is live in that file, next to the drawing code
# they govern; they are not repeated here, so that there is one place to
# read the reasoning and no chance of two prose explanations drifting
# apart. Box geometry is NOT here -- it comes from db (see below), which
# is where the layout that produced the coordinates lives.
#
# Absent on purpose: HANDLE, SNAP_PX and EDGE_PICK_PX. Those are hit-test
# tolerances for dragging and clicking, and nothing here is interactive.

LABEL_PX = 12.0          # node label size, world units, before font_scale
LABEL_LH = 14.0          # and its line height
BADGE_PX = 10.0          # edge label and corner counts
BADGE_WORDS = 3          # an edge label is cut past BOTH of these...
BADGE_CHARS = 20         # ...never just one, see short_label()

IO_SKEW = 14.0           # the lean on an input/output parallelogram
ARROW_GAP = 5.0          # air between the box edge and the arrow tip
ORTH_STUB = 26.0         # how far a right-angled edge leaves its box
FAN_GAP = 22.0           # spread of several edges along one side
FAN_STUB = 14.0          # and how much deeper each one turns
MERGE_GAP = 30.0         # where an edge's own parallel track begins
MERGE_TAIL = 9.0         # the straight bit right at the card
ORTH_RADIUS = 11.0       # corner rounding on a right-angled edge
ORTH_SNAP = 18.0         # below this offset a run is drawn as one segment

ROUTINGS = ("orthogonal", "curved")
NODE_SHAPES = ("start", "step", "decision", "io", "end")

# ── text measurement ────────────────────────────────────────────────────

_METRICS_FILE = Path(__file__).with_name("roboto_metrics.json")

# ctx.measureText in the canvas asks the browser for the advance of the
# actual face. Here it is summed from a table extracted from the same
# woff2 files -- see tools/gen-roboto-metrics.py for why the table is
# committed and the fonts are not.
FACE_UI = "ui"
FACE_MONO = "mono"

# Compiled up here rather than inline: a backslash is not allowed inside an
# f-string expression before Python 3.12, and this package supports 3.11.
_ELLIPSIS_TAIL = re.compile(r"…$")
_TRAILING_PUNCT = re.compile(r"[\s,;:.\-/]+$")


@lru_cache(maxsize=1)
def _metrics() -> dict:
    return json.loads(_METRICS_FILE.read_text(encoding="utf-8"))


def measure(text: str, px: float, face: str = FACE_UI) -> float:
    """Width of `text` set at `px`, in the same units the canvas reports.

    Advance widths only: no kerning and no ligatures, which the browser
    does apply. The error is a fraction of a pixel per pair and only
    changes an outcome for a line that was already sitting exactly on the
    wrap boundary -- tests/test_diagram_svg.py pins the labels where that
    is closest to happening.
    """
    face_metrics = _metrics()[face]
    widths = face_metrics["widths"]
    notdef = face_metrics["notdef"]
    total = sum(widths.get(str(ord(ch)), notdef) for ch in str(text))
    return total * px / face_metrics["units_per_em"]


def clamp_line(line: str, max_width: float, px: float,
               face: str = FACE_UI) -> str:
    """One line shortened by characters until it fits, ellipsis included.

    Twin of DiagramEditor.clamp. Cuts by character rather than by word
    because the thing that overflows is usually a single long CamelCase
    token, which has no break opportunity to cut at.
    """
    if measure(line, px, face) <= max_width:
        return line
    cut = _ELLIPSIS_TAIL.sub("", line)
    while cut and measure(f"{cut}…", px, face) > max_width:
        cut = cut[:-1]
    return f"{cut}…"


def wrap(text: str, max_width: float, max_lines: int, px: float,
         face: str = FACE_UI) -> list[str]:
    """Twin of DiagramEditor.wrap. Greedy by word, then clamped per line.

    A word that fits on no line is still placed on its own (the `or not
    line` branch), so the per-line clamp at the end is what keeps it
    inside the card.
    """
    words = str(text).split()
    lines: list[str] = []
    line = ""
    for word in words:
        nxt = f"{line} {word}" if line else word
        if measure(nxt, px, face) <= max_width or not line:
            line = nxt
        else:
            lines.append(line)
            line = word
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines and line:
        lines.append(line)
    if len(lines) == max_lines:
        # Something was dropped, so the last line has to say so. Compared
        # by length rather than by content because the words were
        # re-joined with single spaces and the original may not have been.
        consumed = len(" ".join(lines))
        if consumed < len(str(text).strip()):
            lines[max_lines - 1] = clamp_line(
                f"{lines[max_lines - 1]}…", max_width, px, face)
    return [clamp_line(ln, max_width, px, face) for ln in lines]


def short_label(text: str) -> str:
    """An edge label cut down to the phrase drawn on the line.

    Twin of DiagramEditor.shortLabel. Returns the label unchanged when
    the whole thing fits, so a caller can compare the two to find out
    whether anything is hidden.
    """
    full = str(text or "").strip()
    words = full.split()
    if len(words) <= BADGE_WORDS or len(full) <= BADGE_CHARS:
        return full
    cut = " ".join(words[:BADGE_WORDS])
    if len(cut) > BADGE_CHARS:
        cut = cut[:BADGE_CHARS]
    # either cut can land on punctuation, which reads as a typo once the
    # ellipsis is stuck to it
    return f"{_TRAILING_PUNCT.sub('', cut)}…"


# ── geometry ────────────────────────────────────────────────────────────
# Everything from here down is a transcription of the same-named function
# in diagram-engine.js. Read it there for the reasoning; what is worth
# saying HERE is only what the transcription itself risks getting wrong.

def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def node_size(node: dict, font_scale: float = 1.0) -> tuple[float, float]:
    """Twin of nodeSize. Same bounds as db.node_box, font scale included."""
    default_h = (db.DECISION_DEFAULT_H if node.get("shape") == "decision"
                 else db.NODE_DEFAULT_H)
    # `or` rather than `is None`, matching JS `Number(n.w) || NODE_W`: a
    # stored zero is not a size, it is a missing one.
    w = _clamp(float(node.get("w") or db.NODE_DEFAULT_W * font_scale),
               db.NODE_MIN_W, db.NODE_MAX_W)
    h = _clamp(float(node.get("h") or default_h * font_scale),
               db.NODE_MIN_H, db.NODE_MAX_H)
    return w, h


def anchors(n: dict) -> dict[str, dict]:
    """The four points an edge may attach to: the centre of each side.

    Returns FRESH dicts every call, which route() relies on -- it slides
    an anchor by mutating it, and a shared dict would move every edge that
    touched that side.
    """
    hw, hh = n["w"] / 2, n["h"] / 2
    if n["shape"] == "io":
        s = IO_SKEW / 2
        return {
            "top": {"x": n["x"], "y": n["y"] - hh},
            "bottom": {"x": n["x"], "y": n["y"] + hh},
            "left": {"x": n["x"] - hw + s, "y": n["y"]},
            "right": {"x": n["x"] + hw - s, "y": n["y"]},
        }
    return {
        "top": {"x": n["x"], "y": n["y"] - hh},
        "bottom": {"x": n["x"], "y": n["y"] + hh},
        "left": {"x": n["x"] - hw, "y": n["y"]},
        "right": {"x": n["x"] + hw, "y": n["y"]},
    }


def sides(a: dict, b: dict, via_corridor: bool) -> tuple[str, str]:
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    if via_corridor:
        return ("bottom", "top") if dy >= 0 else ("top", "bottom")
    if abs(dy) >= abs(dx):
        return ("bottom", "top") if dy >= 0 else ("top", "bottom")
    return ("right", "left") if dx >= 0 else ("left", "right")


def slide_span(n: dict, side: str) -> tuple[float, float] | None:
    hw, hh, inset = n["w"] / 2, n["h"] / 2, 12.0
    vertical = side in ("top", "bottom")
    if n["shape"] == "decision":
        return None
    if n["shape"] == "io":
        if not vertical:
            return None
        lo = n["x"] - hw + IO_SKEW if side == "top" else n["x"] - hw
        hi = n["x"] + hw if side == "top" else n["x"] + hw - IO_SKEW
        return (lo + inset, hi - inset)
    round_r = hh if n["shape"] in ("start", "end") else 10.0
    if vertical:
        return (n["x"] - hw + round_r + inset, n["x"] + hw - round_r - inset)
    if round_r == hh:                      # a stadium's round cap
        return None
    return (n["y"] - hh + round_r + inset, n["y"] + hh - round_r - inset)


def slide(n: dict, side: str, frm: float, to: float) -> float | None:
    span = slide_span(n, side)
    if span is None or span[0] >= span[1]:
        return None
    if abs(to - frm) > ORTH_SNAP:
        return None
    landed = min(span[1], max(span[0], to))
    return landed if abs(landed - to) < 0.01 else None


def along_side(pt: dict, side: str, by: float) -> dict:
    if side in ("top", "bottom"):
        return {"x": pt["x"] + by, "y": pt["y"]}
    return {"x": pt["x"], "y": pt["y"] + by}


def offset(p: dict, side: str, by: float) -> dict:
    if side == "top":
        return {"x": p["x"], "y": p["y"] - by}
    if side == "bottom":
        return {"x": p["x"], "y": p["y"] + by}
    if side == "left":
        return {"x": p["x"] - by, "y": p["y"]}
    return {"x": p["x"] + by, "y": p["y"]}


def half_width_at(n: dict, dy: float) -> float:
    hw, hh = n["w"] / 2, n["h"] / 2
    t = min(1.0, abs(dy) / (hh or 1))
    if n["shape"] == "decision":
        return hw * (1 - t)
    if n["shape"] == "io":
        return hw - IO_SKEW * (dy + hh) / (n["h"] or 1)
    if n["shape"] in ("start", "end"):
        return hw - hh + math.sqrt(max(0.0, hh * hh - dy * dy))
    return hw


def seg_hits_box(p: dict, q: dict, n: dict, pad: float = 6.0) -> bool:
    x0, x1 = n["x"] - n["w"] / 2 - pad, n["x"] + n["w"] / 2 + pad
    y0, y1 = n["y"] - n["h"] / 2 - pad, n["y"] + n["h"] / 2 + pad
    if max(p["x"], q["x"]) < x0 or min(p["x"], q["x"]) > x1:
        return False
    if max(p["y"], q["y"]) < y0 or min(p["y"], q["y"]) > y1:
        return False
    if abs(p["x"] - q["x"]) < 0.01 or abs(p["y"] - q["y"]) < 0.01:
        return True
    steps = 24
    for i in range(steps + 1):
        t = i / steps
        x = p["x"] + (q["x"] - p["x"]) * t
        y = p["y"] + (q["y"] - p["y"]) * t
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


def flatten(pts: list[dict], curve: dict | None) -> list[dict]:
    if not curve:
        return pts
    p, q = pts
    out = []
    for i in range(13):
        t = i / 12
        u = 1 - t
        out.append({
            "x": u * u * p["x"] + 2 * u * t * curve["x"] + t * t * q["x"],
            "y": u * u * p["y"] + 2 * u * t * curve["y"] + t * t * q["y"],
        })
    return out


def midpoint(pts: list[dict]) -> dict:
    """Point half way ALONG the polyline, for placing an edge label."""
    segs = []
    total = 0.0
    for i in range(1, len(pts)):
        length = math.hypot(pts[i]["x"] - pts[i - 1]["x"],
                            pts[i]["y"] - pts[i - 1]["y"])
        segs.append(length)
        total += length
    walked = 0.0
    for i, length in enumerate(segs):
        if walked + length >= total / 2:
            f = (total / 2 - walked) / length if length else 0.0
            return {"x": pts[i]["x"] + (pts[i + 1]["x"] - pts[i]["x"]) * f,
                    "y": pts[i]["y"] + (pts[i + 1]["y"] - pts[i]["y"]) * f}
        walked += length
    return pts[0]


class DiagramLayout:
    """The routed geometry of one diagram: the drawing decisions, no drawing.

    Mirrors the parts of DiagramEditor that decide WHERE things go, and
    nothing that reacts to a pointer. Construction routes everything, the
    same way the editor's setData does, so a caller can read `edges` and
    their routes straight away.

    Edge dicts carry the mutable routing state the JS objects carry on the
    edge itself -- lane/bow/via/back and the four fan and stub offsets --
    because several passes read and rewrite it in order, and that order is
    the algorithm.
    """

    def __init__(self, data: dict, routing: str = "orthogonal") -> None:
        self.font_scale = _clamp(float(data.get("font_scale") or 1),
                                 db.FONT_SCALE_MIN, db.FONT_SCALE_MAX)
        self.routing = routing if routing in ROUTINGS else "orthogonal"
        self.nodes: list[dict] = []
        for n in data.get("nodes") or []:
            shape = n.get("shape") if n.get("shape") in NODE_SHAPES else "step"
            w, h = node_size({**n, "shape": shape}, self.font_scale)
            self.nodes.append({
                "key": n["key"],
                "label": n.get("label") or "",
                "note": n.get("note") or "",
                "shape": shape,
                "x": float(n["x"]),
                "y": float(n["y"]),
                "w": w,
                "h": h,
                "sized": n.get("w") is not None or n.get("h") is not None,
            })
        self.by_key = {n["key"]: n for n in self.nodes}
        # An edge with a missing end is dropped, exactly as the canvas
        # drops it. Not cosmetic: the lane counter below walks the surviving
        # edges in order, so counting a dangling one would put every
        # corridor after it on the other side of the diagram.
        self.edges: list[dict] = [
            {"from": e["from"], "to": e["to"], "label": e.get("label") or "",
             "loops": bool(e.get("loops")),
             "lane": 0, "bow": 0.0, "via": None, "back": False,
             "fan_from": 0.0, "fan_to": 0.0, "stub_from": 0.0, "stub_to": 0.0}
            for e in data.get("edges") or []
            if e.get("from") in self.by_key and e.get("to") in self.by_key
        ]
        self.link_count: dict[str, int] = {}
        for link in data.get("links") or []:
            key = link.get("node_key")
            self.link_count[key] = self.link_count.get(key, 0) + 1
        self.lane_span: dict[str, float] | None = None
        self.orphans = set(self.orphan_keys())
        self.lane_edges()

    # ── reachability ───────────────────────────────────────────────────

    def orphan_keys(self) -> list[str]:
        start = next((n for n in self.nodes if n["shape"] == "start"), None)
        if start is None:
            return [n["key"] for n in self.nodes]
        out: dict[str, list[str]] = {}
        for e in self.edges:
            out.setdefault(e["from"], []).append(e["to"])
        reached = {start["key"]}
        queue = [start["key"]]
        while queue:
            for to in out.get(queue.pop(0), []):
                if to not in reached:
                    reached.add(to)
                    queue.append(to)
        return [n["key"] for n in self.nodes if n["key"] not in reached]

    # ── lanes, detours and fans ────────────────────────────────────────

    def lane_edges(self, detours: bool = True) -> None:
        max_x = max([n["x"] + n["w"] / 2 for n in self.nodes] + [0.0])
        min_x = min([n["x"] - n["w"] / 2 for n in self.nodes] + [0.0])
        taken = 0
        lane_right, lane_left = max_x, min_x

        def assign(e: dict) -> None:
            nonlocal taken, lane_right, lane_left
            a, b = self.by_key[e["from"]], self.by_key[e["to"]]
            side = 1 if taken % 2 == 0 else -1
            lane = taken // 2
            lo_y = min(a["y"], b["y"]) - ORTH_STUB
            hi_y = max(a["y"], b["y"]) + ORTH_STUB
            span_max_x = max(a["x"] + a["w"] / 2, b["x"] + b["w"] / 2)
            span_min_x = min(a["x"] - a["w"] / 2, b["x"] - b["w"] / 2)
            for n in self.nodes:
                if n["y"] + n["h"] / 2 < lo_y or n["y"] - n["h"] / 2 > hi_y:
                    continue
                span_max_x = max(span_max_x, n["x"] + n["w"] / 2)
                span_min_x = min(span_min_x, n["x"] - n["w"] / 2)
            clearance = (span_max_x - max(a["x"], b["x"]) if side > 0
                         else min(a["x"], b["x"]) - span_min_x)
            reach = 70 + lane * 45
            e["lane"] = side
            e["bow"] = side * (max(clearance, 0) + reach)
            corridor_x = (max(a["x"], b["x"]) + abs(e["bow"]) if side > 0
                          else min(a["x"], b["x"]) - abs(e["bow"]))
            if side > 0:
                lane_right = max(lane_right, corridor_x + 20)
            else:
                lane_left = min(lane_left, corridor_x - 20)
            taken += 1

        for e in self.edges:
            a, b = self.by_key[e["from"]], self.by_key[e["to"]]
            # geometry, not graph: `back` says the line currently runs UP
            # the page. `loops` says it closes a cycle. Only the second is
            # drawn dashed.
            e["back"] = b["y"] <= a["y"]
            if detours:
                e["lane"] = 0
                e["bow"] = 0.0
                e["via"] = None

        def fits_between_its_ends(e: dict) -> bool:
            if not self.route_hits_a_box(e):
                return True
            for via in self.detours(e):
                e["via"] = via
                if not self.route_hits_a_box(e):
                    return True
            e["via"] = None
            return False

        if detours:
            # fanned FIRST, so the collision search sees the routes as they
            # will be drawn
            self.assign_fans()
            need_lane = [e for e in self.edges
                         if e["back"] and not fits_between_its_ends(e)]
            need_lane += [e for e in self.edges
                          if not e["back"] and not fits_between_its_ends(e)]
            for e in need_lane:
                assign(e)
            # again, because an edge that took a corridor now leaves by a
            # different side, which puts it in a different fan group
            self.assign_fans()
            self.unfan_collisions()
            self.lane_span = {"left": lane_left, "right": lane_right}

    def detours(self, e: dict) -> list[dict]:
        a, b = self.by_key[e["from"]], self.by_key[e["to"]]
        mid_y = (a["y"] + b["y"]) / 2
        lo_y, hi_y = min(a["y"], b["y"]), max(a["y"], b["y"])
        bars: list[dict] = []
        corridors: list[dict] = []
        for n in self.nodes:
            for y in (n["y"] - n["h"] / 2 - ORTH_STUB,
                      n["y"] + n["h"] / 2 + ORTH_STUB):
                if lo_y < y < hi_y:
                    bars.append({"crossY": y})
            for x in (n["x"] - n["w"] / 2 - ORTH_STUB * 1.5,
                      n["x"] + n["w"] / 2 + ORTH_STUB * 1.5):
                corridors.append({"corridor": x})
        bars.sort(key=lambda u: abs(u["crossY"] - mid_y))

        def over(x: float) -> float:
            return abs(x - a["x"]) + abs(x - b["x"]) - abs(a["x"] - b["x"])

        def near(x: float) -> float:
            return min(abs(x - a["x"]), abs(x - b["x"]))

        # a tuple key is the same ordering as the JS comparator's
        # `over(u) - over(v) || near(u) - near(v)`, and both sorts are stable
        corridors.sort(key=lambda u: (over(u["corridor"]), near(u["corridor"])))
        return bars[:10] + corridors[:12]

    def route_hits_a_box(self, e: dict) -> bool:
        pts, curve = self.route(e)
        flat = flatten(pts, curve)
        for n in self.nodes:
            if n["key"] in (e["from"], e["to"]):
                continue
            for i in range(1, len(flat)):
                if seg_hits_box(flat[i - 1], flat[i], n):
                    return True
        return False

    def assign_fans(self) -> None:
        groups: dict[str, list[dict]] = {}
        for e in self.edges:
            a, b = self.by_key[e["from"]], self.by_key[e["to"]]
            side_from, side_to = sides(a, b, self.corridor_for(e) is not None)
            e["fan_from"] = e["fan_to"] = e["stub_from"] = e["stub_to"] = 0.0
            for key, side, peer, end in ((e["from"], side_from, b, "from"),
                                         (e["to"], side_to, a, "to")):
                groups.setdefault(f"{key}|{side}", []).append(
                    {"e": e, "side": side, "peer": peer, "end": end})
        for members in groups.values():
            if len(members) < 2:
                continue
            vertical = members[0]["side"] in ("top", "bottom")
            members.sort(key=lambda m: m["peer"]["x" if vertical else "y"])
            mid = (len(members) - 1) / 2
            for i, m in enumerate(members):
                m["e"][f"fan_{m['end']}"] = (i - mid) * FAN_GAP
                # strictly increasing, NOT symmetric: a symmetric depth
                # gives the two members of a pair the same stub, which is
                # the one case that needs them different
                m["e"][f"stub_{m['end']}"] = i * FAN_STUB

    def unfan_collisions(self) -> None:
        for e in self.edges:
            if not (e["fan_from"] or e["fan_to"]
                    or e["stub_from"] or e["stub_to"]):
                continue
            if not self.route_hits_a_box(e):
                continue
            saved = (e["fan_from"], e["fan_to"], e["stub_from"], e["stub_to"])
            e["fan_from"] = e["fan_to"] = e["stub_from"] = e["stub_to"] = 0.0
            if self.route_hits_a_box(e):
                (e["fan_from"], e["fan_to"],
                 e["stub_from"], e["stub_to"]) = saved

    # ── routing ────────────────────────────────────────────────────────

    def corridor_for(self, e: dict) -> float | None:
        if e["lane"]:
            a, b = self.by_key[e["from"]], self.by_key[e["to"]]
            reach = abs(e["bow"] or ORTH_STUB)
            return (max(a["x"], b["x"]) + reach if e["lane"] > 0
                    else min(a["x"], b["x"]) - reach)
        return e["via"].get("corridor") if e["via"] else None

    def curve_ctrl(self, p: dict, q: dict, e: dict) -> dict | None:
        mx, my = (p["x"] + q["x"]) / 2, (p["y"] + q["y"]) / 2
        corridor = self.corridor_for(e)
        if corridor is None:
            return None
        return {"x": mx + (corridor - mx) * 2, "y": my}

    def route(self, e: dict) -> tuple[list[dict], dict | None]:
        """The polyline an edge follows, ends included. Twin of route().

        Note the identity games, which are load-bearing rather than
        sloppy: when an edge is not fanned, `p` IS `tail` and `q` IS
        `head`, so the slide below mutates the point already sitting in
        `lead`/`trail`. Copying here would silently drop the alignment
        nudge from the emitted path.
        """
        a, b = self.by_key[e["from"]], self.by_key[e["to"]]
        corridor_x = self.corridor_for(e)
        side_from, side_to = sides(a, b, corridor_x is not None)
        tail = offset(anchors(a)[side_from], side_from, 0)
        head = offset(anchors(b)[side_to], side_to, ARROW_GAP)

        if self.routing == "curved":
            return [tail, head], self.curve_ctrl(tail, head, e)

        p = (along_side(offset(tail, side_from, MERGE_GAP), side_from,
                        e["fan_from"]) if e["fan_from"] else tail)
        q = (along_side(offset(head, side_to, MERGE_GAP), side_to,
                        e["fan_to"]) if e["fan_to"] else head)
        lead = [tail]
        if p is not tail:
            lead += [offset(tail, side_from, MERGE_TAIL), p]
        trail: list[dict] = []
        if q is not head:
            trail += [q, offset(head, side_to, MERGE_TAIL)]
        trail.append(head)

        def done(body: list[dict]) -> tuple[list[dict], None]:
            return [*lead, *body, *trail], None

        stub_from = ORTH_STUB + e["stub_from"]
        stub_to = ORTH_STUB + e["stub_to"]
        fanned = bool(e["fan_from"] or e["fan_to"]
                      or e["stub_from"] or e["stub_to"])

        if corridor_x is not None:
            leave = offset(p, side_from, stub_from)
            enter = offset(q, side_to, stub_to)
            return done([leave, {"x": corridor_x, "y": leave["y"]},
                         {"x": corridor_x, "y": enter["y"]}, enter])

        vertical = side_from in ("top", "bottom")
        axis = "x" if vertical else "y"
        if not fanned and abs(p[axis] - q[axis]) > 0.01:
            moved_tail = slide(a, side_from, p[axis], q[axis])
            if moved_tail is not None:
                p[axis] = moved_tail
            else:
                moved_head = slide(b, side_to, q[axis], p[axis])
                if moved_head is not None:
                    q[axis] = moved_head

        across = abs(p["x"] - q["x"]) if vertical else abs(p["y"] - q["y"])
        along = abs(p["y"] - q["y"]) if vertical else abs(p["x"] - q["x"])
        bar = e["via"].get("crossY") if e["via"] else None
        nearly_in_line = across <= ORTH_SNAP and not fanned
        if bar is None and (nearly_in_line or along <= ORTH_RADIUS * 2):
            return done([])
        if vertical:
            if side_to in ("top", "bottom"):
                mid_y = (bar if bar is not None
                         else (p["y"] + q["y"]) / 2) + e["stub_from"]
                return done([{"x": p["x"], "y": mid_y},
                             {"x": q["x"], "y": mid_y}])
            return done([{"x": p["x"], "y": q["y"]}])       # L: down, across
        if side_to in ("left", "right"):
            mid_x = (p["x"] + q["x"]) / 2
            return done([{"x": mid_x, "y": p["y"]},
                         {"x": mid_x, "y": q["y"]}])
        return done([{"x": q["x"], "y": p["y"]}])           # L: across, down


# ── emitting SVG ────────────────────────────────────────────────────────
# A third place the canvas has to be matched, and the one the route golden
# CANNOT check: it records the polyline, so a bug in turning that polyline
# into path data is invisible to it. Two such bugs were found by looking at
# the output, and both are noted where they were fixed.
#
# The palette is the admin theme, resolved rather than referenced: the
# canvas reads these from CSS custom properties (readTheme), but an SVG that
# has to render outside the dashboard cannot. Values from webui/admin.css --
# a fourth thing to keep in step, though these change far less often than
# the geometry.

BG = "#121212"           # --bg
SURFACE = "#1e1e1e"      # --surface
NODE = "#009688"         # --t-diagram
INK = "rgba(255,255,255,.87)"   # --ink
INK2 = "rgba(255,255,255,.6)"   # --ink-2
LINE = "rgba(255,255,255,.12)"  # --line
WARN = "#ffd54f"         # --warn

ARROW_LEN = 9.0          # drawEdge's `s` for a normal edge
ARROW_FLARE = 0.45       # and its half-width factor

# Below these the canvas draws no label at all (drawNode / drawEdge), which
# an interactive shell has to reproduce or a zoomed-out flow turns into a
# field of grey smears.
LABEL_MIN_SCALE = 0.3
BADGE_MIN_SCALE = 0.45

_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}

# vector-effect is doing real work here. drawEdge sets lineWidth to
# 1.7/this.scale and the dash to [5/scale, 4/scale]: both constant in SCREEN
# pixels at any zoom. A plain stroke-width is in world units, so at the
# scale a 34-step flow fits into, a 1.7-unit line renders around 0.15px and
# vanishes -- while the arrowheads (world units in the engine too, so right
# as they are) and the labels stay, leaving arrows and text floating with no
# lines between them. non-scaling-stroke is the SVG spelling of /this.scale.
#
# Class names are prefixed because this markup can be injected into a page
# whose stylesheet we do not control, and ".t"/".b"/".e" are exactly the
# names a host theme also picks.
_CSS = (
    ".dg-e{{fill:none;stroke:{line};stroke-width:1.7;stroke-linejoin:round;"
    "marker-end:url(#dg-a);vector-effect:non-scaling-stroke}}"
    ".dg-el{{fill:none;stroke:{warn};stroke-width:1.4;stroke-dasharray:5 4;"
    "marker-end:url(#dg-al);vector-effect:non-scaling-stroke}}"
    ".dg-n,.dg-nt,.dg-no{{vector-effect:non-scaling-stroke}}"
    ".dg-n{{fill:{surface};stroke:{node};stroke-width:1.6}}"
    ".dg-nt{{fill:{node};stroke:{node};stroke-width:1.6}}"
    ".dg-no{{fill:{surface};stroke:{warn};stroke-width:2;"
    "stroke-dasharray:4 3}}"
    ".dg-t,.dg-tt{{font:{lpx}px Roboto,'Segoe UI',system-ui,sans-serif;"
    "text-anchor:middle;dominant-baseline:central}}"
    ".dg-t{{fill:{ink}}}.dg-tt{{fill:#000}}"
    ".dg-b,.dg-bl,.dg-c{{font:{bpx}px 'Roboto Mono',ui-monospace,Consolas,"
    "monospace;dominant-baseline:central}}"
    ".dg-b,.dg-bl{{text-anchor:middle}}"
    ".dg-c{{text-anchor:end;fill:{ink2}}}.dg-ct{{fill:rgba(0,0,0,.6)}}"
    ".dg-b{{fill:{ink2}}}.dg-bl{{fill:{warn}}}.dg-bg{{fill:{surface}}}"
    ".dg-lm{{stroke:{ink2};fill:{ink2}}}"
    "#dg-vp.zs .dg-t,#dg-vp.zs .dg-tt{{display:none}}"
    "#dg-vp.zb .dg-b,#dg-vp.zb .dg-bl,#dg-vp.zb .dg-bg,"
    "#dg-vp.zb .dg-c,#dg-vp.zb .dg-lm{{display:none}}"
)


def esc(value: object) -> str:
    return "".join(_XML_ESCAPES.get(c, c) for c in str(value or ""))


def _n(value: float) -> str:
    """Coordinates as integers. Sub-unit precision buys nothing visible and
    costs a fifth of the file size on a flow with fifty routed edges."""
    return f"{value:.0f}"


def node_path(n: dict) -> str:
    """The outline, as a <rect> when a rect will do and a <path> otherwise.

    Only the diamond and the parallelogram need path data; emitting the
    rounded rectangles as 8-command paths (which is what shapePath does,
    because a canvas has no other option) was a fifth of the output.
    """
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    hw, hh = w / 2, h / 2
    if n["shape"] == "decision":
        return (f'd="M{_n(x)} {_n(y - hh)}L{_n(x + hw)} {_n(y)}'
                f'L{_n(x)} {_n(y + hh)}L{_n(x - hw)} {_n(y)}Z"')
    if n["shape"] == "io":
        return (f'd="M{_n(x - hw + IO_SKEW)} {_n(y - hh)}L{_n(x + hw)} '
                f'{_n(y - hh)}L{_n(x + hw - IO_SKEW)} {_n(y + hh)}'
                f'L{_n(x - hw)} {_n(y + hh)}Z"')
    rx = hh if n["shape"] in ("start", "end") else 10.0
    return (f'x="{_n(x - hw)}" y="{_n(y - hh)}" width="{_n(w)}" '
            f'height="{_n(h)}" rx="{_n(rx)}"')


def edge_d(pts: list[dict], curve: dict | None) -> str:
    """The routed polyline as path data, corners rounded like arcTo does.

    The inset is r/tan(alpha/2), NOT r. Those agree only at 90 degrees, so
    using r looked right on every orthogonal corner and drew a small loop
    on the funnel's diagonals -- where the lead-in from a fanned anchor
    meets the run at a shallow angle. Not clamped to the leg either:
    clamping would change the effective radius and break tangency, and
    ctx.arcTo does not clamp. The engine's cap of r at half of each leg is
    the real guard.
    """
    first = pts[0]
    if curve:
        return (f"M{_n(first['x'])} {_n(first['y'])}Q{_n(curve['x'])} "
                f"{_n(curve['y'])} {_n(pts[-1]['x'])} {_n(pts[-1]['y'])}")
    if len(pts) == 2:
        return (f"M{_n(first['x'])} {_n(first['y'])}"
                f"L{_n(pts[1]['x'])} {_n(pts[1]['y'])}")
    out = [f"M{_n(first['x'])} {_n(first['y'])}"]
    for i in range(1, len(pts) - 1):
        prev, here, nxt = pts[i - 1], pts[i], pts[i + 1]
        d1 = math.hypot(here["x"] - prev["x"], here["y"] - prev["y"])
        d2 = math.hypot(nxt["x"] - here["x"], nxt["y"] - here["y"])
        r = min(ORTH_RADIUS, d1 / 2, d2 / 2) if d1 and d2 else 0.0
        ux, uy = ((here["x"] - prev["x"]) / d1,
                  (here["y"] - prev["y"]) / d1) if d1 else (0.0, 0.0)
        vx, vy = ((nxt["x"] - here["x"]) / d2,
                  (nxt["y"] - here["y"]) / d2) if d2 else (0.0, 0.0)
        cross = ux * vy - uy * vx
        if r < 0.01 or abs(cross) < 1e-9:
            out.append(f"L{_n(here['x'])} {_n(here['y'])}")
            continue
        inset = r * (1 - (ux * vx + uy * vy)) / abs(cross)
        out.append(f"L{_n(here['x'] - ux * inset)} "
                   f"{_n(here['y'] - uy * inset)}")
        out.append(f"A{_n(r)} {_n(r)} 0 0 {1 if cross > 0 else 0} "
                   f"{_n(here['x'] + vx * inset)} "
                   f"{_n(here['y'] + vy * inset)}")
    out.append(f"L{_n(pts[-1]['x'])} {_n(pts[-1]['y'])}")
    return "".join(out)


def _markers() -> str:
    """Two arrowheads as <marker> defs instead of 49 drawn triangles.

    A marker is auto-oriented to the path's end tangent, which is exactly
    what drawEdge computes by hand from the last segment -- so the geometry
    is the same one. markerUnits is userSpaceOnUse because the engine's
    arrowhead is a fixed 9 world units and does NOT scale with the line
    width the way a strokeWidth-relative marker would.
    """
    fl = ARROW_LEN * ARROW_FLARE
    box = f'viewBox="{-ARROW_LEN} {-fl} {ARROW_LEN} {fl * 2}"'
    tri = f'd="M0 0L{-ARROW_LEN} {-fl}L{-ARROW_LEN} {fl}Z"'
    common = (f'markerUnits="userSpaceOnUse" markerWidth="{ARROW_LEN}" '
              f'markerHeight="{fl * 2}" refX="0" refY="0" orient="auto"')
    return (f'<defs><marker id="dg-a" {common} {box}>'
            f'<path {tri} fill="{INK2}"/></marker>'
            f'<marker id="dg-al" {common} {box}>'
            f'<path {tri} fill="{WARN}"/></marker></defs>')


def _link_mark(right: float, mid_y: float, size: float) -> str:
    """The "has memories attached" glyph: two nodes and a tie.

    Same construction as drawLinkMark, which draws it by hand because a
    canvas cannot use the SVG icon set and the character it used before was
    a tofu box on any system without it.
    """
    r = size * 0.15
    ax, ay = right - size * 0.62, mid_y + size * 0.22
    bx, by = right - size * 0.1, mid_y - size * 0.24
    return (f'<g class="dg-lm" stroke-width="{max(0.5, size * 0.1):.2f}">'
            f'<path d="M{ax:.1f} {ay:.1f}L{bx:.1f} {by:.1f}" fill="none"/>'
            f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="{r:.2f}"/>'
            f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{r:.2f}"/></g>')


def frame(layout: DiagramLayout,
          routes: dict) -> tuple[float, float, float, float]:
    """The viewBox: every card, the lane corridors, and every routed point.

    Wider than fit()'s frame on purpose. The canvas can be panned, so
    anything it crops is still reachable; an SVG cannot, so a corridor
    falling outside the frame would simply be gone.
    """
    xs: list[float] = []
    ys: list[float] = []
    for n in layout.nodes:
        xs += [n["x"] - n["w"] / 2, n["x"] + n["w"] / 2]
        ys += [n["y"] - n["h"] / 2, n["y"] + n["h"] / 2]
    if layout.lane_span:
        xs += [layout.lane_span["left"], layout.lane_span["right"]]
    for pts, curve in routes.values():
        for p in flatten(pts, curve):
            xs.append(p["x"])
            ys.append(p["y"])
    if not xs:
        return (0.0, 0.0, 1.0, 1.0)
    pad = 22.0
    x0, y0 = min(xs) - pad, min(ys) - pad
    return (x0, y0, max(xs) + pad - x0, max(ys) + pad - y0)


def render_svg(data: dict, *, notes: bool = True,
               routing: str = "orthogonal") -> tuple[str, tuple]:
    """One diagram as a self-contained SVG. Returns (markup, viewBox)."""
    layout = DiagramLayout(data, routing)
    fs = layout.font_scale
    routes = {(e["from"], e["to"]): layout.route(e) for e in layout.edges}
    x0, y0, vw, vh = frame(layout, routes)

    css = _CSS.format(line=LINE, warn=WARN, surface=SURFACE, node=NODE,
                      ink=INK, ink2=INK2, lpx=_n(LABEL_PX * fs),
                      bpx=_n(BADGE_PX * fs))
    out = [
        f'<svg id="dg" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'viewBox="{_n(x0)} {_n(y0)} {_n(vw)} {_n(vh)}">',
        f"<desc>Flowchart with {len(layout.nodes)} steps and "
        f"{len(layout.edges)} connections, drawn from stored coordinates."
        f"</desc>",
        f"<style>{css}</style>", _markers(),
        f'<rect x="{_n(x0)}" y="{_n(y0)}" width="{_n(vw)}" '
        f'height="{_n(vh)}" fill="{BG}"/>',
        '<g id="dg-vp">',
    ]

    # edges under the cards, same order as draw()
    for e in layout.edges:
        pts, curve = routes[(e["from"], e["to"])]
        out.append(f'<path class="dg-{"el" if e["loops"] else "e"}" '
                   f'd="{edge_d(pts, curve)}"/>')

    bpx = BADGE_PX * fs
    for e in layout.edges:
        if not e["label"]:
            continue
        pts, curve = routes[(e["from"], e["to"])]
        if curve:
            at = {"x": (pts[0]["x"] + 2 * curve["x"] + pts[-1]["x"]) / 4,
                  "y": (pts[0]["y"] + 2 * curve["y"] + pts[-1]["y"]) / 4}
        else:
            at = midpoint(pts)
        text = short_label(e["label"])
        wide = measure(text, bpx, FACE_MONO)
        pad = bpx * 0.4
        out.append(f'<rect class="dg-bg" x="{_n(at["x"] - wide / 2 - pad)}" '
                   f'y="{_n(at["y"] - bpx * 0.8)}" '
                   f'width="{_n(wide + pad * 2)}" '
                   f'height="{_n(bpx * 1.6)}"/>')
        # textLength is what keeps a line inside the width it was measured
        # for when the viewer resolves Roboto to a different release
        out.append(f'<text class="dg-{"bl" if e["loops"] else "b"}" '
                   f'x="{_n(at["x"])}" y="{_n(at["y"])}" '
                   f'textLength="{_n(wide)}" '
                   f'lengthAdjust="spacingAndGlyphs">{esc(text)}</text>')

    px, lh = LABEL_PX * fs, LABEL_LH * fs
    for n in layout.nodes:
        terminal = n["shape"] in ("start", "end")
        cls = ("dg-no" if n["key"] in layout.orphans
               else "dg-nt" if terminal else "dg-n")
        tag = "path" if n["shape"] in ("decision", "io") else "rect"
        group = notes and n["note"]
        if group:
            # a <title> is the whole tooltip story: the canvas needs a
            # hover handler and a floating div for the same thing
            out.append(f"<g><title>{esc(n['note'])}</title>")
        out.append(f'<{tag} class="{cls}" {node_path(n)}/>')

        max_lines = max(1, int((n["h"] - 8) // lh))
        room = half_width_at(n, 0) * 2 - (
            n["w"] * 0.32 if n["shape"] == "decision" else 20)
        lines = wrap(n["label"], room, max_lines, px)
        top = n["y"] - (len(lines) - 1) * lh / 2
        for i, line in enumerate(lines):
            out.append(f'<text class="dg-{"tt" if terminal else "t"}" '
                       f'x="{_n(n["x"])}" y="{_n(top + i * lh)}" '
                       f'textLength="{_n(measure(line, px))}" '
                       f'lengthAdjust="spacingAndGlyphs">{esc(line)}</text>')

        count = layout.link_count.get(n["key"])
        if count:
            # inside the SHAPE, not its bounding box: the top-right corner
            # of the box is empty air on a diamond
            dy = -n["h"] / 2 + bpx * 0.9
            right = n["x"] + half_width_at(n, dy) - 5
            mid_y = n["y"] + dy + bpx / 2
            num = str(count)
            cls_c = "dg-c dg-ct" if terminal else "dg-c"
            out.append(f'<text class="{cls_c}" x="{_n(right)}" '
                       f'y="{mid_y:.1f}">{num}</text>')
            out.append(_link_mark(
                right - measure(num, bpx, FACE_MONO) - bpx * 0.3,
                mid_y, bpx))
        if group:
            out.append("</g>")
    out.append("</g></svg>")
    return "".join(out), (x0, y0, vw, vh)


# ── interactive shell ───────────────────────────────────────────────────
# The static SVG above is the whole picture at one scale. For a 34-step
# routine that is a choice between "readable" and "all of it": ~3000x6300
# units scaled into a chat column puts a 12-unit label under 3px. So the
# same markup also ships wrapped in pan and zoom, which is the only form of
# it that is actually usable for a long flow.

_SHELL_CSS = (
    ".dgw{{position:relative;height:{h}px;border:1px solid {line};"
    "border-radius:10px;overflow:hidden;background:{bg};touch-action:none}}"
    ".dgw svg{{position:absolute;inset:0;width:100%;height:100%;"
    "cursor:grab}}.dgw svg.grab{{cursor:grabbing}}"
    ".dgb{{position:absolute;top:8px;left:8px;display:flex;gap:6px;z-index:2}}"
    ".dgb button{{font:12px Roboto,system-ui,sans-serif;color:{ink};"
    "background:{surface};border:1px solid {line};border-radius:6px;"
    "padding:4px 9px;cursor:pointer}}.dgb button:hover{{background:#2a2a2a}}"
    ".dgz{{position:absolute;bottom:8px;right:10px;z-index:2;"
    "font:11px 'Roboto Mono',monospace;color:{ink2}}}"
    ".dgh{{position:absolute;bottom:8px;left:10px;z-index:2;"
    "font:11px Roboto,system-ui,sans-serif;color:rgba(255,255,255,.38)}}"
    ".dg-sr{{position:absolute;width:1px;height:1px;overflow:hidden;"
    "clip:rect(0,0,0,0)}}"
)

# Two things here are easy to get wrong and were:
#
#  * getScreenCTM, not a width/height ratio, to turn client pixels into
#    viewBox units. The ratio ignores the preserveAspectRatio letterbox,
#    and on a 1:2 diagram in a wide box the letterbox is most of the width.
#  * the zoom is clamped in EFFECTIVE terms -- what the viewer actually
#    sees. Clamping the transform's own scale is meaningless, because what
#    it buys depends on how the root fitted the viewBox, which depends on
#    the size of the box. Opening at "scale 3" landed at 25% on screen,
#    under the threshold where labels stop being drawn: a diagram that
#    opened with no text on it.
_SHELL_JS = """
(function(){
 var svg=document.getElementById('dg'),vp=document.getElementById('dg-vp'),
     zl=document.getElementById('dg-z'),W=%(w)s,H=%(h)s,X=%(x)s,Y=%(y)s;
 var s=1,tx=0,ty=0,drag=null;
 function root(){var m=svg.getScreenCTM();return m?m.a:1;}
 function apply(){
  vp.setAttribute('transform','translate('+tx.toFixed(1)+' '+ty.toFixed(1)+
                  ') scale('+s.toFixed(4)+')');
  var eff=root()*s;
  vp.setAttribute('class',(eff<%(lmin)s?'zs ':'')+(eff<%(bmin)s?'zb':''));
  zl.textContent=Math.round(eff*100)+'%%';
 }
 function vb(cx,cy){
  var p=svg.createSVGPoint();p.x=cx;p.y=cy;
  return p.matrixTransform(svg.getScreenCTM().inverse());
 }
 function zoom(k,cx,cy){
  var r=svg.getBoundingClientRect();
  if(cx==null){cx=r.left+r.width/2;cy=r.top+r.height/2;}
  var q=vb(cx,cy),ns=Math.min(3/root(),Math.max(1,s*k));
  tx=q.x-(q.x-tx)*(ns/s);ty=q.y-(q.y-ty)*(ns/s);s=ns;apply();
 }
 function centreOn(px,py,eff){
  var ns=Math.max(1,eff/root());
  s=ns;tx=(X+W/2)-px*ns;ty=(Y+H/2)-py*ns;apply();
 }
 svg.addEventListener('wheel',function(e){
  e.preventDefault();zoom(e.deltaY<0?1.15:1/1.15,e.clientX,e.clientY);
 },{passive:false});
 svg.addEventListener('pointerdown',function(e){
  var m=svg.getScreenCTM();
  drag={x:e.clientX,y:e.clientY,tx:tx,ty:ty,a:m.a,d:m.d};
  svg.classList.add('grab');
  try{svg.setPointerCapture(e.pointerId);}catch(err){}
 });
 svg.addEventListener('pointermove',function(e){
  if(!drag)return;
  tx=drag.tx+(e.clientX-drag.x)/drag.a;ty=drag.ty+(e.clientY-drag.y)/drag.d;
  apply();
 });
 function stop(){drag=null;svg.classList.remove('grab');}
 svg.addEventListener('pointerup',stop);
 svg.addEventListener('pointercancel',stop);
 document.getElementById('dg-in').onclick=function(){zoom(1.5);};
 document.getElementById('dg-out').onclick=function(){zoom(1/1.5);};
 document.getElementById('dg-st').onclick=function(){
  centreOn(%(sx)s,%(sy)s,%(sz)s);};
 document.getElementById('dg-all').onclick=function(){
  s=1;tx=0;ty=0;apply();};
 centreOn(%(sx)s,%(sy)s,%(sz)s);
 addEventListener('resize',apply);
})();
"""


def render_interactive(data: dict, *, notes: bool = True,
                       routing: str = "orthogonal", height: int = 560,
                       open_scale: float = 0.85) -> str:
    """The same drawing, wrapped in pan/zoom. Returns an HTML fragment.

    `open_scale` is the on-screen scale to open at, not a transform
    multiplier -- see the note above _SHELL_JS. It defaults above
    LABEL_MIN_SCALE so the flow opens with its text readable, near the
    start step rather than fitted.
    """
    svg, (x0, y0, vw, vh) = render_svg(data, notes=notes, routing=routing)
    nodes = data.get("nodes") or []
    start = next((n for n in nodes if n.get("shape") == "start"),
                 nodes[0] if nodes else {"x": 0, "y": 0})
    css = _SHELL_CSS.format(h=height, line=LINE, bg=BG, ink=INK,
                            ink2=INK2, surface=SURFACE)
    js = _SHELL_JS % {
        "w": _n(vw), "h": _n(vh), "x": _n(x0), "y": _n(y0),
        "sx": _n(float(start.get("x") or 0)),
        # a little below the start step, so the first few rows are in view
        "sy": _n(float(start.get("y") or 0) + 360),
        "sz": open_scale, "lmin": LABEL_MIN_SCALE, "bmin": BADGE_MIN_SCALE,
    }
    return (
        f'<h2 class="dg-sr">Interactive flowchart, {len(nodes)} steps. '
        f"Drag to pan, scroll to zoom.</h2>"
        f"<style>{css}</style><div class=\"dgw\">"
        f'<div class="dgb"><button id="dg-in">+</button>'
        f'<button id="dg-out">−</button>'
        f'<button id="dg-st">start</button>'
        f'<button id="dg-all">all</button></div>'
        f'<div class="dgz" id="dg-z"></div>'
        f'<div class="dgh">drag to pan &middot; scroll to zoom</div>'
        f"{svg}</div><script>{js}</script>"
    )
