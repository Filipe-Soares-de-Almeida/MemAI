"""A diagram's graph as plain data: normalizing and checking it, and laying it out on the canvas."""

from __future__ import annotations

import re

from memai import contract

NODE_SHAPES = contract.NODE_SHAPES

# Abstract canvas units, laid out here so every renderer draws the same picture.
NODE_DEFAULT_W = contract.NODE_W
NODE_DEFAULT_H = contract.NODE_H
DECISION_DEFAULT_H = contract.DECISION_H
NODE_MIN_W, NODE_MAX_W = contract.NODE_MIN_W, contract.NODE_MAX_W
NODE_MIN_H, NODE_MAX_H = contract.NODE_MIN_H, contract.NODE_MAX_H
FONT_SCALE_MIN, FONT_SCALE_MAX = 0.7, 2.5

# Air between boxes, wider than a default box: edges carry the sequence. A change applies only to
# diagrams arranged afterwards; stored coordinates are never rewritten (see relayout_diagram).
LAYOUT_GAP_X = 130.0
LAYOUT_GAP_Y = 152.0
LAYOUT_COL_W = NODE_DEFAULT_W + LAYOUT_GAP_X   # 300, the pitch for default boxes
LAYOUT_ROW_H = NODE_DEFAULT_H + LAYOUT_GAP_Y   # 200


def node_box(node: dict, font_scale: float = 1.0) -> tuple[float, float]:
    """The size a node is drawn at: its own, or its shape's default.

    A default box grows with the diagram's font scale, because otherwise
    asking for bigger text just truncates the label -- the card it has to
    fit in never changed. A box the user sized by hand is left alone: they
    chose that size while looking at that text.
    """
    default_h = DECISION_DEFAULT_H if node.get("shape") == "decision" else NODE_DEFAULT_H
    scale = max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, float(font_scale or 1)))
    w = node.get("w") or NODE_DEFAULT_W * scale
    h = node.get("h") or default_h * scale
    return float(w), float(h)


# Node keys double as mermaid node ids, so keep them to characters that
# need no escaping on either side.
_NODE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _as_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _norm_nodes(nodes: object) -> list[dict]:
    """Coerce whatever came over MCP/HTTP into the one node shape used below."""
    out: list[dict] = []
    for i, n in enumerate(nodes or []):  # type: ignore[arg-type]
        if not isinstance(n, dict):
            continue
        out.append({
            "key": str(n.get("key", "")).strip(),
            "label": str(n.get("label", "")).strip(),
            "shape": str(n.get("shape", "")).strip() or "step",
            "note": str(n.get("note", "")).strip(),
            "seq": _as_int(n.get("seq"), i),
        })
    return out


def _norm_edges(edges: object) -> list[dict]:
    """Same for edges; accepts from/to or the from_key/to_key column names."""
    out: list[dict] = []
    for i, e in enumerate(edges or []):  # type: ignore[arg-type]
        if not isinstance(e, dict):
            continue
        out.append({
            "from": str(e.get("from", e.get("from_key", ""))).strip(),
            "to": str(e.get("to", e.get("to_key", ""))).strip(),
            "label": str(e.get("label", "")).strip(),
            "seq": _as_int(e.get("seq"), i),
        })
    return out


def _node_field_errors(n: dict) -> list[str]:
    """Rules that hold for a single node in isolation."""
    errors: list[str] = []
    key = n["key"] or "?"
    if not n["key"]:
        errors.append("node is missing a key")
    elif not _NODE_KEY_RE.match(n["key"]):
        errors.append(f"invalid node key {n['key']!r}: use letters, digits, '_' or '-'")
    if not n["label"]:
        errors.append(f"node {key!r} has an empty label")
    if n["shape"] not in NODE_SHAPES:
        errors.append(
            f"node {key!r} has unknown shape {n['shape']!r}; use one of: {', '.join(NODE_SHAPES)}"
        )
    return errors


def _adjacency(edges: list[dict]) -> dict[str, list[str]]:
    """Successors per node in edge order -- what keeps the layout deterministic."""
    adj: dict[str, list[str]] = {}
    for e in sorted(edges, key=lambda x: x["seq"]):
        adj.setdefault(e["from"], []).append(e["to"])
    return adj


def _reachable(start: str, edges: list[dict]) -> set[str]:
    adj = _adjacency(edges)
    seen = {start}
    queue = [start]
    while queue:
        for nxt in adj.get(queue.pop(0), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _validate_graph(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Reject a graph that cannot be a flow, before anything is written.

    One gatekeeper for both the MCP tool and the HTTP API. The structural
    rules here (exactly one start, everything reachable) only make sense
    for a WHOLE graph -- the incremental single-node/single-edge writers
    deliberately skip them, because "add a node, then wire it up" has to
    be expressible in two calls.

    Cycles are legal: a retry loop is a real flow, not a mistake.
    """
    if not nodes:
        return ["graph has no nodes"]
    errors: list[str] = []
    keys: set[str] = set()
    for n in nodes:
        errors.extend(_node_field_errors(n))
        if n["key"] in keys:
            errors.append(f"duplicate node key: {n['key']!r}")
        keys.add(n["key"])

    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    if not starts:
        errors.append("graph has no 'start' node")
    elif len(starts) > 1:
        errors.append(
            f"graph has {len(starts)} 'start' nodes, expected exactly one: {', '.join(starts)}"
        )

    pairs: set[tuple[str, str]] = set()
    for e in edges:
        if not e["from"] or not e["to"]:
            errors.append("edge is missing an endpoint")
            continue
        for endpoint in (e["from"], e["to"]):
            if endpoint not in keys:
                errors.append(f"edge endpoint {endpoint!r} is not a node key")
        if e["from"] == e["to"]:
            errors.append(
                f"self-loop on {e['from']!r}: model a retry with an explicit decision node"
            )
        if (e["from"], e["to"]) in pairs:
            errors.append(f"duplicate edge {e['from']!r} -> {e['to']!r}")
        pairs.add((e["from"], e["to"]))

    # only worth reporting once the basics hold, else the list is noise
    if not errors:
        orphans = sorted(keys - _reachable(starts[0], edges))
        if orphans:
            errors.append("unreachable from start: " + ", ".join(orphans))
    return errors


def _back_edges(adj: dict[str, list[str]], roots: list[str]) -> set[tuple[str, str]]:
    """The loop-closing edges: those pointing back into the path being walked.

    A flow may legitimately cycle (a retry), but layering needs a DAG.
    Removing exactly the back-edges leaves the forward skeleton to layer;
    the loop closer is still drawn, just pointing back up the canvas.
    """
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished
    for root in roots:
        if state.get(root):
            continue
        state[root] = 1
        stack = [(root, iter(adj.get(root, ())))]
        while stack:
            node, successors = stack[-1]
            descended = False
            for nxt in successors:
                if state.get(nxt) == 1:
                    back.add((node, nxt))
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(adj.get(nxt, ()))))
                    descended = True
                    break
            if not descended:
                state[node] = 2
                stack.pop()
    return back


def loop_edges(nodes: list[dict], edges: list[dict]) -> set[tuple[str, str]]:
    """Which edges close a cycle -- a retry, a return to a menu.

    A property of the GRAPH, computed the same way the layout computes it,
    so a renderer can mark a loop closer without guessing. The canvas used
    to guess from the coordinates (does this edge point up the page?),
    which made a normal edge look like a loop as soon as someone dragged
    its target above its source.
    """
    keys = [n["key"] for n in nodes]
    known = set(keys)
    adj = {k: [t for t in v if t in known] for k, v in _adjacency(edges).items()}
    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    roots = [r for r in (starts or keys[:1]) if r in known]
    return _back_edges(adj, roots)


def _layout_graph(
    nodes: list[dict], edges: list[dict], font_scale: float = 1.0,
) -> dict[str, tuple[float, float]]:
    """Deterministic layered layout: the row is a node's longest path from start.

    A pure function of the graph, so the same flow always arranges the
    same way and the result is testable without a browser.

    Longest path, not first-visit depth: a step sits one row below its
    DEEPEST predecessor, so a terminal step lands under every branch that
    reaches it rather than level with whichever branch was walked first.
    Cycles cannot make this hang -- back-edges are dropped before
    layering (see _back_edges) and drawn as edges that point back up.

    Deliberately simple otherwise: dense graphs will still produce
    crossing edges. The user drags, and the drag persists, so
    crossing-minimisation can stay a later refinement of this one
    function.
    """
    if not nodes:
        return {}
    keys = [n["key"] for n in nodes]
    known = set(keys)
    adj = {k: [t for t in v if t in known] for k, v in _adjacency(edges).items()}
    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    roots = [r for r in (starts or keys[:1]) if r in known]
    back = _back_edges(adj, roots)

    def forward(node: str) -> list[str]:
        return [t for t in adj.get(node, []) if (node, t) not in back]

    # BFS over the forward skeleton: reachability, plus a stable
    # discovery order that decides left-to-right placement within a row
    order: list[str] = []
    seen = set(roots)
    queue = list(roots)
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in forward(cur):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    # Kahn over the same skeleton, relaxing depth upward: one pass gives longest-path layering,
    # since a node is released only once every predecessor is placed
    indeg = {k: 0 for k in order}
    for k in order:
        for nxt in forward(k):
            if nxt in indeg:
                indeg[nxt] += 1
    ready = [k for k in order if indeg[k] == 0]
    depth = {k: 0 for k in ready}
    while ready:
        cur = ready.pop(0)
        for nxt in forward(cur):
            if nxt not in indeg:
                continue
            depth[nxt] = max(depth.get(nxt, 0), depth[cur] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)

    # Nodes still without a depth get a row of their own: unreachable ones (the incremental writers
    # skip that rule) and any a residual cycle held back.
    floor = max(depth.values()) + 1 if depth else 0
    stragglers = [k for k in keys if k not in depth]
    for k in stragglers:
        depth[k] = floor

    # Within a row, discovery order: BFS keeps siblings together, and many-to-many fan-in crossings
    # cannot be removed by any row order.
    rows: dict[int, list[str]] = {}
    for k in order + [k for k in stragglers if k not in seen]:
        rows.setdefault(depth[k], []).append(k)

    # The pitch follows the biggest box, so a resized card or a larger font scale cannot make the
    # next auto-arrange overlap it.
    boxes = [node_box(n, font_scale) for n in nodes]
    col_w = max(LAYOUT_COL_W, max(w for w, _ in boxes) + LAYOUT_GAP_X)
    row_h = max(LAYOUT_ROW_H, max(h for _, h in boxes) + LAYOUT_GAP_Y)

    pos: dict[str, tuple[float, float]] = {}
    for d, row in rows.items():
        span = (len(row) - 1) / 2.0
        for i, k in enumerate(row):
            pos[k] = ((i - span) * col_w, d * row_h)
    return pos


def _flow_order(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Node keys in reading order: down the rows, left to right within one."""
    pos = _layout_graph(nodes, edges)
    return sorted((n["key"] for n in nodes), key=lambda k: (pos[k][1], pos[k][0], k))


def _clamp(value: object, low: float, high: float) -> float | None:
    try:
        return min(high, max(low, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _graph_issues(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """The structural defects of one flow, worst first.

    Diagram upkeep is not memory curation: what goes wrong in a flow is
    shape, not confidence, and none of it is visible to the dedup or
    optimization passes built for prose. All of these are reachable
    through the incremental writers, which skip the whole-graph rules on
    purpose so a flow can be built across several calls -- so something
    has to report them afterwards.

    Every rule here has to be worth acting on. "This fork has an arrow
    with no condition on it" was not: on a real routine most forks have an
    obvious fall-through, so it flagged healthy diagrams and taught the
    reader to ignore the whole strip. It was removed rather than tuned.
    """
    if not nodes:
        return [{"kind": "empty", "keys": []}]
    keys = [n["key"] for n in nodes]
    starts = [n["key"] for n in nodes if n["shape"] == "start"]
    ends = [n["key"] for n in nodes if n["shape"] == "end"]
    outgoing: dict[str, list[dict]] = {}
    for e in edges:
        outgoing.setdefault(e["from"], []).append(e)

    issues: list[dict] = []
    if not starts:
        issues.append({"kind": "no_start", "keys": []})
    elif len(starts) > 1:
        issues.append({"kind": "many_starts", "keys": sorted(starts)})
    else:
        orphans = sorted(set(keys) - _reachable(starts[0], edges))
        if orphans:
            issues.append({"kind": "unreachable", "keys": orphans})

    # a step the flow just stops at, without saying it ended
    dead = sorted(n["key"] for n in nodes
                  if n["shape"] != "end" and not outgoing.get(n["key"]))
    if dead:
        issues.append({"kind": "dead_end", "keys": dead})

    if not ends:
        issues.append({"kind": "no_end", "keys": []})
    return issues
