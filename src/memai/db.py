"""SQLite-backed store for memai.

One store is one WAL-mode file holding memory rows, an FTS5 index, edit
history, a relations graph, and the node/edge tables behind type='diagram'
memories together under one set of ACID transactions, so there is nothing
that can desync from the metadata on a hard-kill. One such file is a
PROJECT: a home directory holds any number of them and names the one every
connect() opens by default.

Retrieval is FTS5 BM25 keyword search. It only widens the candidate set --
semantic judgment is left to the calling agent, which reads the candidates
back and decides relevance itself.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from memai import contract, guard
from memai.lite import (  # noqa: F401
    DOMAIN_SEP,
    TASK_ASK_MINUTES_DEFAULT,
    WARDEN_MINUTES_DEFAULT,
    home,
    normalize_domain,
    now_iso,
    split_domain,
)
from memai.store.backups import (  # noqa: F401
    ARCHIVE_GROUPS,
    ARCHIVE_LABEL_MAX,
    ARCHIVES_DIRNAME,
    BACKUPS_DIRNAME,
    SHELF_META_FILE,
    archive_backups,
    archive_files,
    archive_group_name,
    archive_grouped,
    archive_label,
    archive_label_name,
    archive_members,
    archive_name,
    archive_plan,
    archives_dir,
    backup_files,
    backup_name,
    backup_to,
    backups_dir,
    delete_archive,
    delete_backups,
    forget_shelf_meta,
    rename_archive,
    restore_backup,
    set_shelf_meta,
    shelf_meta,
    unarchive,
)
from memai.store.connection import (  # noqa: F401
    _FTS_COLUMNS,
    CHARS_PER_TOKEN,
    COMPACT_REASON_KEY,
    COMPACT_REASON_VECTORS,
    SCHEMA,
    _drop_vector_store,
    _ensure_columns,
    clear_compact_reason,
    connect,
    est_tokens,
    get_compact_reason,
    new_uid,
)
from memai.store.dedup import (  # noqa: F401
    SIMILAR_ON_WRITE,
    SIMILAR_ON_WRITE_MAX,
    SIMILAR_SNIPPET,
    _pair_ratio,
    _Prepared,
    _ratio_bound,
    dedup_candidates,
    similar_memories,
    text_ratio,
)
from memai.store.domains import (  # noqa: F401
    ALSO_SEP,
    DOMAIN_CASE_DEFAULT,
    DOMAIN_CASE_KEY,
    DOMAIN_CASE_MODES,
    all_domains_sql,
    apply_domain_policy,
    apply_link_policy,
    case_domain,
    coerce_domain,
    domain_ancestors,
    domain_census,
    domain_clause,
    domain_depth,
    domain_parent,
    domain_scope_clause,
    get_domain_case,
    in_domain,
    list_domains,
    parse_domains,
    resolve_domain_scopes,
    set_domain_case,
)
from memai.store.health import (  # noqa: F401
    STALE_DAYS,
    _due_clause,
    health_axes,
    health_since,
    health_snapshot,
    normalize_review_after,
    today_iso,
)
from memai.store.memories import (  # noqa: F401
    CONFIDENCE_CONTRADICTED,
    DIAGRAM_TYPE,
    GENERATED_TYPES,
    MEMORY_TYPES,
    PIN_VALUES,
    TAG_SEP,
    TASK_TYPE,
    add_domain_link,
    domain_links_for,
    edit_count,
    get_domain_links,
    get_edit_history,
    get_memory,
    insert_memory,
    memory_row,
    merge_tags,
    migrate_sections,
    pin_error,
    purge_memories,
    purge_memory,
    record_recall,
    remove_domain_link,
    restore_diagram,
    restore_diagram_refs,
    restore_edit,
    restore_memory,
    search_share,
    set_confidence,
    set_domain,
    set_domain_links,
    set_generated_content,
    set_pin,
    set_review_after,
    set_sections,
    set_source_ref,
    set_status,
    set_tags,
    set_title,
    type_error,
    update_memory_content,
    usage_for,
)
from memai.store.paths import (  # noqa: F401
    ACTIVE_FILE,
    GENERAL_FILE,
    GENERAL_PROJECT,
    PROJECT_NAME_MAX,
    PROJECTS_DIRNAME,
    active_project,
    default_db_path,
    find_project,
    project_exists,
    project_name,
    project_name_error,
    project_path,
    set_active_project,
)
from memai.store.projects import (  # noqa: F401
    create_project,
    delete_project,
    list_projects,
)
from memai.store.relations import (  # noqa: F401
    add_relation,
    get_relations,
)
from memai.store.renders import (  # noqa: F401
    RENDER_SUFFIXES,
    prune_renders,
    prune_renders_all,
    renders_dir,
    renders_usage,
)
from memai.store.search import (  # noqa: F401
    _sound_clause,
    due_for_review,
    is_opaque_query,
    latest_by_type,
    list_by_domain,
    list_recent,
    search_memories,
    search_ranked,
    timeline_neighbours,
)
from memai.store.sections import (  # noqa: F401
    _BODY_LINK,
    TITLE_MAX,
    _refuse_leak,
    _write_sections,
    body_links,
    get_sections,
    leak_error,
    section_error,
    section_problem,
    section_queue,
    sections_read,
    title_error,
    unread_sections,
)
from memai.store.settings import (  # noqa: F401
    SVG_RETENTION_DEFAULT,
    SVG_RETENTION_KEY,
    SVG_RETENTION_MODES,
    TASK_ASK_ENABLED_DEFAULT,
    TASK_ASK_ENABLED_KEY,
    TASK_ASK_MINUTES_KEY,
    TASK_ASK_MINUTES_RANGE,
    WARDEN_ENABLED_DEFAULT,
    WARDEN_ENABLED_KEY,
    WARDEN_MINUTES_KEY,
    WARDEN_MINUTES_RANGE,
    _get_meta,
    _set_meta,
    get_svg_retention,
    get_task_ask_enabled,
    get_task_ask_minutes,
    get_warden_enabled,
    get_warden_minutes,
    set_svg_retention,
    set_task_ask_enabled,
    set_task_ask_minutes,
    set_warden_enabled,
    set_warden_minutes,
)
from memai.store.subtree import (  # noqa: F401
    move_domain,
    purge_domain,
    set_domain_status,
)

# -------------------------------------------------------------------- diagrams

# A diagram is a graph, one row per step. The graph is the record; memories.content is a generated
# rendering for FTS (_render_text), so the free-text editors refuse a diagram (is_diagram).

DIAGRAM_KINDS = ("flowchart",)
NODE_SHAPES = contract.NODE_SHAPES

# Cap on a rendered body returned to an agent; the stored content is never truncated.
DIAGRAM_BODY_BUDGET = 12_000

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


def _render_text(title: str, summary: str, kind: str, nodes: list[dict], edges: list[dict]) -> str:
    """The prose projection stored in memories.content.

    Generated, never hand-written: this is what FTS indexes, so a diagram
    is findable by what the routine actually does rather than by its title
    alone. One line per node in
    flow order keeps the edit-history diff readable.
    """
    by_key = {n["key"]: n for n in nodes}
    out = [f"DIAGRAM: {title}", f"KIND: {kind}"]
    if summary:
        out.append(f"SUMMARY: {summary}")
    out += ["", "FLOW:"]
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for e in sorted(edges, key=lambda x: x["seq"]):
        outgoing.setdefault(e["from"], []).append((e["to"], e["label"]))
    ordered = _flow_order(nodes, edges)
    for k in ordered:
        n = by_key[k]
        out.append(f"{k} [{n['shape']}]: {n['label']}")
        for to, label in outgoing.get(k, []):
            out.append(f"  -> {to}" + (f" [{label}]" if label else ""))
    notes = [(k, by_key[k]["note"]) for k in ordered if by_key[k]["note"]]
    if notes:
        out += ["", "NOTES:"]
        out += [f"{k}: {note}" for k, note in notes]
    return "\n".join(out)


def _mermaid_escape(text: str) -> str:
    """Quotes and newlines would break out of a mermaid node label."""
    return text.replace('"', "#quot;").replace("\n", " ")


# Mermaid keywords that cannot be bare node ids; `end` closes a subgraph, so a step keyed 'end'
# silently breaks the diagram.
_MERMAID_RESERVED = frozenset({
    "end", "graph", "subgraph", "flowchart", "class", "classdef",
    "click", "style", "linkstyle", "direction",
})


def _mermaid_id(key: str) -> str:
    return f"n_{key}" if key.lower() in _MERMAID_RESERVED else key


def _mermaid_node(key: str, shape: str, label: str) -> str:
    node_id = _mermaid_id(key)
    text = _mermaid_escape(label)
    if shape in ("start", "end"):
        return f'{node_id}(["{text}"])'
    if shape == "decision":
        return f'{node_id}{{"{text}"}}'
    if shape == "io":
        return f'{node_id}[/"{text}"/]'
    return f'{node_id}["{text}"]'


def _render_mermaid(title: str, nodes: list[dict], edges: list[dict]) -> str:
    """Mermaid source, for a host that can render a fenced diagram inline.

    Mermaid always applies its own layout, so this is the one renderer
    that ignores the stored coordinates -- the price of a one-line render
    in a chat client. Consumers that want the exact admin arrangement read
    the coordinates from get_diagram() instead.
    """
    by_key = {n["key"]: n for n in nodes}
    lines = []
    if title:
        lines += ["---", f"title: {_mermaid_escape(title)}", "---"]
    lines.append("flowchart TD")
    for k in _flow_order(nodes, edges):
        lines.append("    " + _mermaid_node(k, by_key[k]["shape"], by_key[k]["label"]))
    for e in sorted(edges, key=lambda x: x["seq"]):
        label = f'|"{_mermaid_escape(e["label"])}"|' if e["label"] else ""
        lines.append(f'    {_mermaid_id(e["from"])} -->{label} {_mermaid_id(e["to"])}')
    return "\n".join(lines)


def get_diagram_row(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM diagrams WHERE memory_uid = ?", (uid,)).fetchone()


def _font_scale(conn: sqlite3.Connection, uid: str) -> float:
    """The diagram's text scale, which every default box is sized from."""
    row = get_diagram_row(conn, uid)
    return float((row["font_scale"] if row is not None else 1) or 1)


def is_diagram(conn: sqlite3.Connection, uid: str | None) -> bool:
    """True for a diagram memory.

    The guard the free-text content editors use: hand-editing a diagram's
    content would desync it from the graph that generates it, so they
    refuse and point at the diagram writers instead.
    """
    row = get_memory(conn, uid) if uid else None
    return row is not None and row["type"] == DIAGRAM_TYPE


def _load_graph(conn: sqlite3.Connection, uid: str) -> tuple[list[dict], list[dict]]:
    """A stored diagram's nodes/edges as the same dicts the writers accept."""
    nodes = [
        {"key": r["node_key"], "label": r["label"], "shape": r["shape"],
         "note": r["note"], "seq": r["seq"], "x": r["x"], "y": r["y"],
         "w": r["w"], "h": r["h"]}
        for r in conn.execute(
            "SELECT * FROM diagram_nodes WHERE memory_uid = ? ORDER BY seq, id", (uid,)
        )
    ]
    edges = [
        {"from": r["from_key"], "to": r["to_key"], "label": r["label"], "seq": r["seq"]}
        for r in conn.execute(
            "SELECT * FROM diagram_edges WHERE memory_uid = ? ORDER BY seq, id", (uid,)
        )
    ]
    return nodes, edges


def _write_nodes(
    conn: sqlite3.Connection, uid: str, nodes: list[dict],
    positions: dict[str, tuple[float, float]],
) -> None:
    conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ?", (uid,))
    conn.executemany(
        """INSERT INTO diagram_nodes (memory_uid, node_key, shape, label, note, seq, x, y)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(uid, n["key"], n["shape"], n["label"], n["note"], i,
          positions[n["key"]][0], positions[n["key"]][1])
         for i, n in enumerate(nodes)],
    )


def _write_edges(conn: sqlite3.Connection, uid: str, edges: list[dict]) -> None:
    conn.execute("DELETE FROM diagram_edges WHERE memory_uid = ?", (uid,))
    conn.executemany(
        "INSERT INTO diagram_edges (memory_uid, from_key, to_key, label, seq) VALUES (?, ?, ?, ?, ?)",
        [(uid, e["from"], e["to"], e["label"], i) for i, e in enumerate(edges)],
    )


def _refresh_diagram_content(conn: sqlite3.Connection, uid: str, note: str = "") -> None:
    """Re-generate memories.content after a structural change.

    Routed through update_memory_content so the change lands in the audit
    log and the index is refreshed -- the same path a hand edit of any
    other type takes. A change that leaves the projection identical (an
    edge label rewritten to itself, say) writes nothing.
    """
    d = get_diagram_row(conn, uid)
    row = get_memory(conn, uid)
    if d is None or row is None:
        return
    nodes, edges = _load_graph(conn, uid)
    content = _render_text(d["title"], d["summary"], d["kind"], nodes, edges)
    if content == row["content"]:
        return
    update_memory_content(conn, uid, content, note=note)


def insert_diagram(
    conn: sqlite3.Connection,
    *,
    title: str,
    nodes: object,
    edges: object,
    summary: str = "",
    kind: str = "flowchart",
    domain: str = "",
    also: str = "",
    session: str = "",
    tags: str = "",
    review_after: str = "",
    source_ref: str = "",
) -> tuple[str | None, list[str]]:
    """Create a type='diagram' memory from a whole graph.

    Returns (uid, errors). On any validation error nothing at all is
    written and uid is None -- a half-written flow is worse than no flow.
    """
    if kind not in DIAGRAM_KINDS:
        return None, [f"unknown diagram kind {kind!r}; use one of: {', '.join(DIAGRAM_KINDS)}"]
    title = str(title).strip()
    if not title:
        return None, ["diagram needs a title"]
    error = title_error(title)
    if error:
        return None, [error]
    n = _norm_nodes(nodes)
    e = _norm_edges(edges)
    errors = _validate_graph(n, e)
    if errors:
        return None, errors
    summary = str(summary).strip()
    uid = insert_memory(
        conn, type=DIAGRAM_TYPE, content=_render_text(title, summary, kind, n, e),
        title=title, domain=domain, also=also, session=session, tags=tags,
        review_after=review_after, source_ref=source_ref,
    )
    conn.execute(
        "INSERT INTO diagrams (memory_uid, kind, title, summary) VALUES (?, ?, ?, ?)",
        (uid, kind, title, summary),
    )
    _write_nodes(conn, uid, n, _layout_graph(n, e))
    _write_edges(conn, uid, e)
    return uid, []


def replace_diagram_graph(
    conn: sqlite3.Connection, uid: str, nodes: object, edges: object
) -> tuple[bool, list[str]]:
    """Swap a diagram's whole graph, keeping the positions of surviving nodes.

    A node the user dragged keeps its coordinates across a rewrite; only
    keys that are new to the graph get layout coordinates. Call
    relayout_diagram() for a clean arrangement. Node links and jumps
    pointing at keys the rewrite dropped go with them -- including a jump
    ANOTHER diagram aimed at one of those keys, which this diagram is the
    only one able to notice.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    n = _norm_nodes(nodes)
    e = _norm_edges(edges)
    errors = _validate_graph(n, e)
    if errors:
        return False, errors
    old_nodes, _ = _load_graph(conn, uid)
    kept = {o["key"]: (o["x"], o["y"]) for o in old_nodes}
    fresh = _layout_graph(n, e, _font_scale(conn, uid))
    positions = {node["key"]: kept.get(node["key"], fresh[node["key"]]) for node in n}
    _write_nodes(conn, uid, n, positions)
    _write_edges(conn, uid, e)
    live = [node["key"] for node in n]
    holes = ",".join("?" * len(live))
    conn.execute(
        f"DELETE FROM diagram_node_links WHERE memory_uid = ? AND node_key NOT IN ({holes})",
        (uid, *live),
    )
    conn.execute(
        f"DELETE FROM diagram_jumps WHERE from_uid = ? AND from_node NOT IN ({holes})",
        (uid, *live),
    )
    # to_node = '' is the diagram as a whole and survives any rewrite
    conn.execute(
        "DELETE FROM diagram_jumps WHERE to_uid = ? AND to_node <> '' "
        f"AND to_node NOT IN ({holes})",
        (uid, *live),
    )
    _refresh_diagram_content(conn, uid)
    return True, []


def upsert_diagram_node(
    conn: sqlite3.Connection,
    uid: str,
    node_key: str,
    *,
    label: str | None = None,
    shape: str | None = None,
    note: str | None = None,
) -> tuple[bool, list[str]]:
    """Create or patch one node; only the fields passed are touched.

    Structural rules are NOT enforced here -- see _validate_graph. A new
    node gets its coordinates from a fresh layout of the resulting graph,
    but only its OWN coordinate is applied: every existing node keeps
    wherever the user dragged it.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    nodes, edges = _load_graph(conn, uid)
    existing = next((n for n in nodes if n["key"] == node_key), None)
    prev = existing or {}
    candidate = {
        "key": str(node_key).strip(),
        "label": str(prev.get("label", "") if label is None else label).strip(),
        "shape": str(prev.get("shape", "step") if shape is None else shape).strip() or "step",
        "note": str(prev.get("note", "") if note is None else note).strip(),
        "seq": prev.get("seq", len(nodes)),
    }
    errors = _node_field_errors(candidate)
    if errors:
        return False, errors
    if existing is not None:
        conn.execute(
            """UPDATE diagram_nodes SET label = ?, shape = ?, note = ?
               WHERE memory_uid = ? AND node_key = ?""",
            (candidate["label"], candidate["shape"], candidate["note"], uid, node_key),
        )
    else:
        x, y = _layout_graph(
            nodes + [candidate], edges, _font_scale(conn, uid))[candidate["key"]]
        conn.execute(
            """INSERT INTO diagram_nodes (memory_uid, node_key, shape, label, note, seq, x, y)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, candidate["key"], candidate["shape"], candidate["label"],
             candidate["note"], candidate["seq"], x, y),
        )
    _refresh_diagram_content(conn, uid)
    return True, []


def delete_diagram_node(
    conn: sqlite3.Connection, uid: str, node_key: str
) -> tuple[bool, list[str]]:
    """Remove a node together with its edges, its memory links and its jumps.

    Both ends of a jump, because the step is gone from the picture either
    way: a jump leaving it has nothing to leave from, and a jump another
    diagram aimed AT it has nowhere to land.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    cur = conn.execute(
        "DELETE FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (uid, node_key)
    )
    if cur.rowcount == 0:
        return False, [f"no node {node_key!r} in {uid}"]
    conn.execute(
        "DELETE FROM diagram_edges WHERE memory_uid = ? AND (from_key = ? OR to_key = ?)",
        (uid, node_key, node_key),
    )
    conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? AND node_key = ?", (uid, node_key)
    )
    conn.execute(
        """DELETE FROM diagram_jumps
           WHERE (from_uid = ? AND from_node = ?) OR (to_uid = ? AND to_node = ?)""",
        (uid, node_key, uid, node_key),
    )
    _refresh_diagram_content(conn, uid)
    return True, []


def upsert_diagram_edge(
    conn: sqlite3.Connection, uid: str, from_key: str, to_key: str, label: str = ""
) -> tuple[bool, list[str]]:
    """Wire two nodes, or relabel an existing wire between them."""
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    nodes, edges = _load_graph(conn, uid)
    keys = {n["key"] for n in nodes}
    errors = [f"edge endpoint {k!r} is not a node key" for k in (from_key, to_key) if k not in keys]
    if from_key == to_key:
        errors.append(f"self-loop on {from_key!r}: model a retry with an explicit decision node")
    if errors:
        return False, errors
    conn.execute(
        """INSERT INTO diagram_edges (memory_uid, from_key, to_key, label, seq)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(memory_uid, from_key, to_key) DO UPDATE SET label = excluded.label""",
        (uid, from_key, to_key, str(label).strip(), len(edges)),
    )
    _refresh_diagram_content(conn, uid)
    return True, []


def delete_diagram_edge(
    conn: sqlite3.Connection, uid: str, from_key: str, to_key: str
) -> tuple[bool, list[str]]:
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    cur = conn.execute(
        "DELETE FROM diagram_edges WHERE memory_uid = ? AND from_key = ? AND to_key = ?",
        (uid, from_key, to_key),
    )
    if cur.rowcount == 0:
        return False, [f"no edge {from_key!r} -> {to_key!r} in {uid}"]
    _refresh_diagram_content(conn, uid)
    return True, []


def set_diagram_meta(
    conn: sqlite3.Connection, uid: str, *, title: str | None = None,
    summary: str | None = None, font_scale: object = None,
) -> tuple[bool, list[str]]:
    """Rename a diagram, rewrite its summary, or set how big its text draws.

    Title and summary feed the projection; font_scale does not -- it is how
    the flow is drawn, not what it says. It is stored rather than kept in
    the browser because a card sized to fit its text at one scale is the
    wrong size at another, and the sizes ARE stored.
    """
    d = get_diagram_row(conn, uid)
    if d is None:
        return False, [f"{uid} is not a diagram"]
    new_title = d["title"] if title is None else str(title).strip()
    new_summary = d["summary"] if summary is None else str(summary).strip()
    if not new_title:
        return False, ["diagram needs a title"]
    error = title_error(new_title)
    if error:
        return False, [error]
    was = float(d["font_scale"] or 1)
    scale = was
    if font_scale is not None:
        scale = _clamp(font_scale, FONT_SCALE_MIN, FONT_SCALE_MAX)
        if scale is None:
            return False, ["font_scale must be a number"]
    conn.execute(
        "UPDATE diagrams SET title = ?, summary = ?, font_scale = ? WHERE memory_uid = ?",
        (new_title, new_summary, scale, uid),
    )
    # the graph's title is the memory's title; one name, stored twice
    conn.execute("UPDATE memories SET title = ? WHERE uid = ?", (new_title, uid))
    if scale != was:
        # Default boxes just changed size, so coordinates scale by the same factor to keep the
        # arrangement; a hand-sized box keeps its size.
        conn.execute(
            "UPDATE diagram_nodes SET x = x * ?, y = y * ? WHERE memory_uid = ?",
            (scale / was, scale / was, uid),
        )
    _refresh_diagram_content(conn, uid)
    return True, []


def _clamp(value: object, low: float, high: float) -> float | None:
    try:
        return min(high, max(low, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def set_node_positions(conn: sqlite3.Connection, uid: str, positions: object) -> int:
    """Persist a dragged or resized box. Geometry is not content.

    Deliberately touches nothing else: no content re-render, no `edits`
    row, not even memories.updated_at -- moving a box on a
    canvas must not read as an edit or reorder list_recent().

    Accepts {key: (x, y)} or {key: {"x": .., "y": .., "w": .., "h": ..}}.
    x/y are required; w/h are optional and clamped, so a card can be
    resized through the same call that moves it. Unknown keys and
    unparseable numbers are skipped, and the count of rows actually
    written comes back.
    """
    written = 0
    for key, raw in (positions or {}).items():  # type: ignore[union-attr]
        try:
            pair: tuple[Any, Any] = ((raw.get("x"), raw.get("y")) if isinstance(raw, dict)
                                     else (raw[0], raw[1]))
            x, y = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        sets, params = ["x = ?", "y = ?"], [x, y]
        if isinstance(raw, dict):
            w = _clamp(raw.get("w"), NODE_MIN_W, NODE_MAX_W) if raw.get("w") is not None else None
            h = _clamp(raw.get("h"), NODE_MIN_H, NODE_MAX_H) if raw.get("h") is not None else None
            if w is not None:
                sets.append("w = ?")
                params.append(w)
            if h is not None:
                sets.append("h = ?")
                params.append(h)
        params += [uid, key]
        cur = conn.execute(
            f"UPDATE diagram_nodes SET {', '.join(sets)} WHERE memory_uid = ? AND node_key = ?",
            params,
        )
        written += cur.rowcount
    return written


def reset_node_boxes(conn: sqlite3.Connection, uid: str, keys: object = None) -> int:
    """Drop stored sizes so the shapes' defaults apply again.

    The way back from a resize, per card or for the whole flow -- the same
    role relayout_diagram plays for positions.
    """
    if keys:
        rows = 0
        for key in keys:  # type: ignore[union-attr]
            rows += conn.execute(
                "UPDATE diagram_nodes SET w = NULL, h = NULL "
                "WHERE memory_uid = ? AND node_key = ?", (uid, key),
            ).rowcount
        return rows
    return conn.execute(
        "UPDATE diagram_nodes SET w = NULL, h = NULL WHERE memory_uid = ?", (uid,)
    ).rowcount


def relayout_diagram(conn: sqlite3.Connection, uid: str) -> int:
    """Discard stored coordinates and recompute the whole arrangement.

    The escape hatch for a diagram dragged into a mess. Content is
    untouched: the projection never mentions coordinates.
    """
    if get_diagram_row(conn, uid) is None:
        return 0
    nodes, edges = _load_graph(conn, uid)
    return set_node_positions(
        conn, uid, _layout_graph(nodes, edges, _font_scale(conn, uid)))


def add_node_link(
    conn: sqlite3.Connection, uid: str, node_key: str, target_uid: str,
    relation_type: str = "explains",
) -> tuple[bool, list[str]]:
    """Point one step of a flow at another memory that explains it.

    This is what makes a diagram an index of its domain: the flow says
    what happens, and the linked note/anti_pattern says why that step is
    the way it is.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    node = conn.execute(
        "SELECT 1 FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (uid, node_key)
    ).fetchone()
    if node is None:
        return False, [f"no node {node_key!r} in {uid}"]
    if target_uid == uid:
        return False, ["a diagram cannot link a node back to itself"]
    if get_memory(conn, target_uid) is None:
        return False, [f"unknown target memory {target_uid!r}"]
    conn.execute(
        """INSERT INTO diagram_node_links (memory_uid, node_key, target_uid, relation_type, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(memory_uid, node_key, target_uid)
           DO UPDATE SET relation_type = excluded.relation_type""",
        (uid, node_key, target_uid, str(relation_type).strip() or "explains", now_iso()),
    )
    return True, []


def delete_node_link(
    conn: sqlite3.Connection, uid: str, node_key: str, target_uid: str
) -> bool:
    cur = conn.execute(
        "DELETE FROM diagram_node_links WHERE memory_uid = ? AND node_key = ? AND target_uid = ?",
        (uid, node_key, target_uid),
    )
    return cur.rowcount > 0


def get_node_links(conn: sqlite3.Connection, uid: str) -> list[sqlite3.Row]:
    """A diagram's node links, joined to the linked memory's own columns."""
    return conn.execute(
        """SELECT l.node_key, l.target_uid, l.relation_type, l.created_at,
                  m.type AS target_type, m.domain AS target_domain,
                  m.status AS target_status, m.confidence AS target_confidence,
                  m.content AS target_content
           FROM diagram_node_links l JOIN memories m ON m.uid = l.target_uid
           WHERE l.memory_uid = ? ORDER BY l.node_key, l.created_at""",
        (uid,),
    ).fetchall()


def diagrams_referencing(conn: sqlite3.Connection, target_uid: str) -> list[sqlite3.Row]:
    """Which diagrams point a node at this memory -- the reverse of a node link."""
    return conn.execute(
        """SELECT l.memory_uid, l.node_key, l.relation_type, d.title, n.label
           FROM diagram_node_links l
           JOIN diagrams d ON d.memory_uid = l.memory_uid
           LEFT JOIN diagram_nodes n
                  ON n.memory_uid = l.memory_uid AND n.node_key = l.node_key
           WHERE l.target_uid = ? ORDER BY d.title, l.node_key""",
        (target_uid,),
    ).fetchall()


def add_diagram_jump(
    conn: sqlite3.Connection, uid: str, from_node: str, to_uid: str,
    to_node: str = "", label: str = "",
) -> tuple[bool, list[str]]:
    """Point one step of a flow at another FLOW -- optionally at one of its steps.

    A routine documented as one diagram usually is not one routine: a
    branch hands off to a second flow, which hands back. That handoff is
    not prose to read beside the step (add_node_link) but a place to go,
    and it is the same statement from either side -- so it is stored once
    and read from both ends.

    An empty `to_node` means the target diagram as a whole. Jumping inside
    one diagram is refused: that is an edge, and drawing it is the honest
    way to say it.
    """
    if get_diagram_row(conn, uid) is None:
        return False, [f"{uid} is not a diagram"]
    if to_uid == uid:
        return False, ["a jump goes to another diagram; inside one, draw an edge"]
    if get_diagram_row(conn, to_uid) is None:
        return False, [f"jump target {to_uid!r} is not a diagram"]
    from_node = str(from_node).strip()
    to_node = str(to_node or "").strip()
    if conn.execute(
        "SELECT 1 FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (uid, from_node)
    ).fetchone() is None:
        return False, [f"no node {from_node!r} in {uid}"]
    if to_node and conn.execute(
        "SELECT 1 FROM diagram_nodes WHERE memory_uid = ? AND node_key = ?", (to_uid, to_node)
    ).fetchone() is None:
        return False, [f"no node {to_node!r} in {to_uid}"]
    conn.execute(
        """INSERT INTO diagram_jumps (from_uid, from_node, to_uid, to_node, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(from_uid, from_node, to_uid, to_node)
           DO UPDATE SET label = excluded.label""",
        (uid, from_node, to_uid, to_node, str(label or "").strip(), now_iso()),
    )
    return True, []


def delete_diagram_jump(
    conn: sqlite3.Connection, uid: str, node_key: str, peer_uid: str, peer_node: str = ""
) -> bool:
    """Drop one jump, named from EITHER end.

    `uid`/`node_key` is the caller's own side and `peer_uid`/`peer_node`
    the other one, whichever way the arrow points. The row is a single
    statement shared by two diagrams, so the diagram on the receiving end
    has to be able to cut it too -- otherwise the only way out of an
    unwanted incoming jump is to go and open the diagram that made it.
    """
    cur = conn.execute(
        """DELETE FROM diagram_jumps
           WHERE (from_uid = ? AND from_node = ? AND to_uid = ? AND to_node = ?)
              OR (from_uid = ? AND from_node = ? AND to_uid = ? AND to_node = ?)""",
        (uid, node_key, peer_uid, peer_node, peer_uid, peer_node, uid, node_key),
    )
    return cur.rowcount > 0


# One query per direction: which column carries the peer is the only difference, kept visible.
_JUMP_SQL = """SELECT j.*, d.title AS peer_title, m.status AS peer_status,
                      n.label AS peer_node_label
               FROM diagram_jumps j
               JOIN diagrams d ON d.memory_uid = j.{far}_uid
               JOIN memories m ON m.uid = j.{far}_uid
               LEFT JOIN diagram_nodes n
                      ON n.memory_uid = j.{far}_uid AND n.node_key = j.{far}_node
               WHERE j.{near}_uid = ? ORDER BY j.{near}_node, j.created_at"""


def get_diagram_jumps(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """Every jump touching this diagram, both directions, as one list.

    `node_key` is always the step in THIS diagram, so the editor groups
    them per step without caring which way a jump points -- '' for an
    incoming jump aimed at the diagram as a whole. `peer_node` is where to
    land at the other end, which is what makes the trip back arrive on the
    step it left from rather than on a diagram and a hunt.
    """
    return [
        {
            "direction": direction,
            "node_key": r[f"{near}_node"],
            "peer_uid": r[f"{far}_uid"],
            "peer_node": r[f"{far}_node"],
            "peer_title": r["peer_title"],
            "peer_node_label": r["peer_node_label"] or "",
            "peer_status": r["peer_status"],
            "label": r["label"],
            "created_at": r["created_at"],
        }
        for direction, near, far in (("out", "from", "to"), ("in", "to", "from"))
        for r in conn.execute(_JUMP_SQL.format(near=near, far=far), (uid,)).fetchall()
    ]


def get_diagram(conn: sqlite3.Connection, uid: str) -> dict | None:
    """Everything one diagram is made of, ready to render."""
    d = get_diagram_row(conn, uid)
    if d is None:
        return None
    nodes, edges = _load_graph(conn, uid)
    loops = loop_edges(nodes, edges)
    return {
        "uid": uid,
        "kind": d["kind"],
        "title": d["title"],
        "summary": d["summary"],
        "font_scale": float(d["font_scale"] or 1),
        "nodes": nodes,
        # `loops` is derived, never stored: it says the edge closes a cycle,
        # which is why it is drawn dashed and pointing back
        "edges": [dict(e, loops=(e["from"], e["to"]) in loops) for e in edges],
        "links": [dict(r) for r in get_node_links(conn, uid)],
        "jumps": get_diagram_jumps(conn, uid),
    }


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


def diagram_overview(
    conn: sqlite3.Connection, *, domain: str = "", status: str = "active",
    subtree: bool = True,
) -> list[dict]:
    """One card per diagram: its size, its links and what is structurally wrong.

    Batched on purpose -- nodes, edges and links come back in one query
    each and are grouped in memory, so N diagrams still cost four queries
    instead of 3N+1.
    """
    sql = [
        """SELECT d.memory_uid AS uid, d.kind, d.title, d.summary,
                  m.domain, m.status, m.confidence, m.tags,
                  m.created_at, m.updated_at
           FROM diagrams d JOIN memories m ON m.uid = d.memory_uid"""
    ]
    params: list = []
    where = []
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, subtree=subtree)
        where.append(clause.removeprefix("AND "))
        params.extend(values)
    if status:
        where.append("m.status = ?")
        params.append(status)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY m.updated_at DESC")
    rows = conn.execute(" ".join(sql), params).fetchall()

    nodes_by: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT memory_uid, node_key, shape, label, note FROM diagram_nodes ORDER BY memory_uid, seq, id"
    ):
        nodes_by.setdefault(r["memory_uid"], []).append(
            {"key": r["node_key"], "shape": r["shape"], "label": r["label"], "note": r["note"]})
    edges_by: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT memory_uid, from_key, to_key, label, seq FROM diagram_edges ORDER BY memory_uid, seq, id"
    ):
        edges_by.setdefault(r["memory_uid"], []).append(
            {"from": r["from_key"], "to": r["to_key"], "label": r["label"], "seq": r["seq"]})
    links_by: dict[str, int] = {}
    for r in conn.execute("SELECT memory_uid, COUNT(*) AS n FROM diagram_node_links GROUP BY memory_uid"):
        links_by[r["memory_uid"]] = r["n"]
    # counted from both ends: a diagram nothing leaves but three flows arrive
    # into is just as tied into the set as the one that made those jumps
    jumps_by: dict[str, int] = {}
    for r in conn.execute(
        """SELECT uid, COUNT(*) AS n FROM (
               SELECT from_uid AS uid FROM diagram_jumps
               UNION ALL SELECT to_uid AS uid FROM diagram_jumps
           ) GROUP BY uid"""
    ):
        jumps_by[r["uid"]] = r["n"]

    # also-paths, so the Diagrams filter finds a flow by the end-to-end process it is a step of
    also_by = domain_links_for(conn, [r["uid"] for r in rows])

    out = []
    for r in rows:
        nodes = nodes_by.get(r["uid"], [])
        edges = edges_by.get(r["uid"], [])
        issues = _graph_issues(nodes, edges)
        out.append({
            **dict(r),
            "also": also_by.get(r["uid"], []),
            "nodes": len(nodes),
            "edges": len(edges),
            "links": links_by.get(r["uid"], 0),
            "jumps": jumps_by.get(r["uid"], 0),
            "documented": sum(1 for n in nodes if n["note"]),
            "issues": issues,
            "issue_count": sum(max(1, len(i["keys"])) for i in issues),
        })
    return out


def render_diagram_text(conn: sqlite3.Connection, uid: str) -> str:
    d = get_diagram_row(conn, uid)
    if d is None:
        return ""
    nodes, edges = _load_graph(conn, uid)
    return _render_text(d["title"], d["summary"], d["kind"], nodes, edges)


def render_diagram_mermaid(conn: sqlite3.Connection, uid: str) -> str:
    d = get_diagram_row(conn, uid)
    if d is None:
        return ""
    nodes, edges = _load_graph(conn, uid)
    return _render_mermaid(d["title"], nodes, edges)


# ------------------------------------------------------------------ optimization

CONFIDENCE_VALUES = contract.CONFIDENCES
SUGGESTION_KINDS = (
    "compact", "reword", "retag", "retitle", "redomain", "crosslist",
    "set_confidence", "review", "archive", "link", "merge", "distill",
    "unleak",
)
# Text fields a leaked tool call lands in and `unleak` repairs, one per suggestion so each can be
# decided and undone on its own.
LEAK_FIELDS = ("content", "tags", "source_ref")
# Findings a scan reports; the count of what it left behind comes with it.
LEAK_SCAN_CAP = 40
# distill targets must be durable knowledge types -- distilling INTO a
# checkpoint/handoff would just recreate the ephemera it exists to retire
DISTILL_TYPES = ("note", "reasoning", "anti_pattern")
# the payload keys distill applies; any other key is a staging error
DISTILL_PAYLOAD_KEYS = ("source_uids", "new_type", "new_content", "title", "tags", "domain")
# Kinds staging refuses without a non-empty `verified`, each archiving a memory; set_confidence
# needs it only for `contradicted`, checked where that payload is read.
VERIFIED_REQUIRED = {
    "archive": "verified required: describe the live-facts check that makes this memory archivable",
    "merge": "verified required: merge archives payload.drop_uid -- describe the live-facts check",
    "distill": "verified required: distill archives its sources -- describe the live-facts check",
}


CORPUS_SNIPPET_LEN = 120
CORPUS_TAGS_LEN = 100
CORPUS_ANCHORS_CAP = 5
# Per-page ceiling on the serialized listing (compact-JSON chars). Hosts cap output near 25k tokens
# and dense JSON runs ~3 chars/token; 28k keeps the full response near 12k tokens.
CORPUS_CHAR_BUDGET = 28_000
# A full=True body longer than this is cut; get_memory(uid, content_offset=...) reads the rest.
CORPUS_FULL_LEN = 8_000

# Verifiable anchors an agent can go check against live facts: URLs,
# file paths, table/field-style identifiers and SNAKE_CASE constants.
_ANCHOR_PATTERNS = (
    re.compile(r"""https?://[^\s)>\]"']+"""),
    re.compile(
        r"[\w./\\~-]*\w\.(?:pas|py|js|ts|tsx|sql|json|ya?ml|toml|md|css|html|ini|cfg|bat|ps1|sh)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z][A-Z0-9]{0,4}\d{3,}\b"),          # X100, AB1234 …
    re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b"),      # F100_TOTAL, SOME_FLAG …
)


def _extract_anchors(content: str, cap: int = 8) -> list[str]:
    """Pull the verifiable anchors out of a memory's full content."""
    seen: list[str] = []
    for pat in _ANCHOR_PATTERNS:
        for m in pat.findall(content):
            if m not in seen:
                seen.append(m)
            if len(seen) >= cap:
                return seen
    return seen


def _norm_domain(d: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-")


def _domain_hints(domain_counts: dict[str, int]) -> list[dict]:
    """Cluster domain-string variants that likely mean the same thing.

    Groups by normalized form (lowercase, separators collapsed) and, when
    the domain embeds a ticket-style id (e.g. proj-1042), by that id --
    so 'PROJ-1042', 'proj_1042' and 'proj-1042-fix' all cluster.
    Returns only clusters with 2+ distinct raw strings, canonical first.
    """
    groups: dict[str, list[str]] = {}
    for raw in domain_counts:
        if not raw:
            continue
        norm = _norm_domain(raw)
        m = re.search(r"[a-z]{2,}-\d{3,}", norm)
        key = m.group(0) if m else norm
        groups.setdefault(key, []).append(raw)
    hints = []
    for variants in groups.values():
        if len(variants) < 2:
            continue
        variants.sort(key=lambda v: (-domain_counts[v], len(v)))
        hints.append({
            "canonical": variants[0],
            "variants": [{"domain": v, "count": domain_counts[v]} for v in variants],
            "total": sum(domain_counts[v] for v in variants),
        })
    hints.sort(key=lambda h: -h["total"])
    return hints


# A token that reads as a code rather than as prose: 'x100', 'p200',
# 'x1042'. Two digits minimum, so a word like 'v2' does not pass for one.
_CODE_TOKEN = re.compile(r"^[a-z]{1,4}\d{2,}[a-z0-9]*$")
# How many domains a leading token must head before it counts as a root of
# the tree on its own (a code needs no such evidence).
_ROOT_MIN = 3
NESTING_HINT_CAP = 40


def _nesting_hints(domain_counts: dict[str, int]) -> list[dict]:
    """Propose a nested path for each flat domain that already reads like one.

    'acme-x100-p200-cache-warmup' states a hierarchy in a string
    nothing can group by. This lifts its leading part into path segments
    and keeps the descriptive tail as the leaf --
    'acme/x100/p200/cache-warmup' -- so the diagrams and notes of one
    module can be asked for as one scope.

    Only leading tokens that LOOK structural are lifted: a code
    (_CODE_TOKEN), or a token heading _ROOT_MIN or more domains in the
    store, which is what a root looks like from here whatever it means.
    The first token that is neither ends the path, tail included.

    Advisory, and deliberately so: nothing here can tell a real level from
    a hyphen inside a name, so each proposal is meant for a human -- or for
    an agent staging a `redomain` suggestion the human then approves --
    never for the rows directly.
    """
    heads: dict[str, int] = {}
    for raw in domain_counts:
        segs = split_domain(raw)
        if segs:
            head = segs[0].split("-")[0].lower()
            heads[head] = heads.get(head, 0) + 1

    def structural(token: str) -> bool:
        token = token.lower()
        return bool(_CODE_TOKEN.match(token)) or heads.get(token, 0) >= _ROOT_MIN

    hints: list[dict] = []
    for raw, count in sorted(domain_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if not raw or DOMAIN_SEP in raw:
            continue                      # blank, or already a path
        tokens = raw.split("-")
        cut = 0
        while cut < len(tokens) - 1 and structural(tokens[cut]):
            cut += 1
        if not cut:
            continue
        hints.append({
            "domain": raw,
            "count": count,
            "proposed": DOMAIN_SEP.join([*tokens[:cut], "-".join(tokens[cut:])]),
        })
        if len(hints) >= NESTING_HINT_CAP:
            break
    return hints


def _leak_findings(
    conn: sqlite3.Connection, where_sql: str, params: list, *, cap: int = LEAK_SCAN_CAP,
) -> tuple[list[dict], int]:
    """Rows whose text carries a tool call's own source, and how many exist.

    SQL narrows to the rows holding a closing tag at all, which is the cheap
    half of the test; guard.leak_marks judges each candidate, because which
    marks count depends on the row's own type.

    A finding names the fields that carry a mark, what a repair takes out of
    each, and whether the repair CLEARS the field -- `clean: false` is a
    field whose marks sit inside prose, which `unleak` refuses and a reword
    has to rewrite by hand. `declares` is what the debris was trying to
    write, reported only for the columns that are still empty: those are the
    ones with a `redomain`, `crosslist` or `retag` waiting beside the
    `unleak`.
    """
    like = " OR ".join(f"{f} LIKE '%</%'" for f in LEAK_FIELDS)
    rows = conn.execute(
        f"""SELECT uid, type, title, content, tags, source_ref, domain
            FROM memories WHERE {where_sql} AND ({like})
            ORDER BY created_at DESC""", params).fetchall()
    findings, total = [], 0
    for r in rows:
        marks = {f: guard.leak_marks(r["type"], r[f] or "") for f in LEAK_FIELDS}
        marks = {f: m for f, m in marks.items() if m}
        if not marks:
            continue
        total += 1
        if len(findings) >= cap:
            continue
        fields, declares = {}, {}
        for f, found in marks.items():
            clean, dropped = guard.strip_leak(r["type"], r[f])
            declares.update(guard.declared(dropped))
            fields[f] = {
                "marks": found,
                "removes": len(r[f]) - len(clean),
                "clean": not guard.leak_marks(r["type"], clean),
            }
        # `also` is read from memory_domains, never from the one-field
        # mirror beside it: the rows are where a membership lives
        held = {"domain": r["domain"], "also": get_domain_links(conn, r["uid"]),
                "tags": r["tags"], "source_ref": r["source_ref"]}
        entry = {"uid": r["uid"], "type": r["type"], "fields": fields}
        if r["title"]:
            entry["title"] = r["title"][:CORPUS_SNIPPET_LEN]
        empty = {k: v for k, v in declares.items() if k in held and not held[k]}
        if empty:
            entry["declares"] = empty
        findings.append(entry)
    return findings, total


def optimization_corpus(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    since: str = "", include_archived: bool = False, limit: int = 500,
    offset: int = 0, full: bool = False, subtree: bool = True,
) -> dict:
    """Compact whole-corpus dump for an agent to reason over in one call.

    Returns every memory's curation-relevant fields plus the relation edges
    touching them, so the agent can spot missing links, duplicates, stale or
    mis-scoped rows without hundreds of individual reads.

    The listing is aggressively slimmed so a few-hundred-memory store fits
    one MCP response (the full-body version of a real 200-memory store was
    ~450KB; even snippet-only it overflowed on metadata alone):
      - content is a snippet with content_len alongside (full=True keeps
        bodies up to CORPUS_FULL_LEN; get_memory fetches one on demand)
      - tags longer than CORPUS_TAGS_LEN are cut, with tags_len alongside
      - empty/default fields are omitted (blank domain/session/tags, no
        cross-listings, null superseded_by, status matching the filter
        default, confidence 'unverified' -- stats.by_confidence keeps the
        aggregate view)
      - `also` lists the domains a memory belongs to besides its own path,
        because a pass proposing a `crosslist` has to be able to tell a new
        membership from one that already holds
      - created_at drops sub-second precision; updated_at is not listed
        at all (get_memory has it)
      - anchors come as one space-joined string, capped at
        CORPUS_ANCHORS_CAP
    Beyond `limit`, a page also ends early when the serialized listing
    reaches CORPUS_CHAR_BUDGET -- the guarantee is that ONE response
    always fits an MCP host's output cap, whatever the store looks like.
    A `stats` block aggregates the filtered corpus regardless of limit,
    `domain_hints` clusters likely-variant domain strings,
    `domain_nesting` proposes a path for each flat domain that already
    spells a hierarchy out (see _nesting_hints -- the raw material for
    `redomain` suggestions), `leaked_calls` lists the rows carrying a tool
    call's own source with `stats.leaked_calls` counting them all (see
    _leak_findings -- the raw material for `unleak`), and `truncated` flags
    when the listing stopped before the corpus ended -- page onward with
    offset (offset + count is the next page's offset).

    `since` makes curation incremental: only memories created OR updated
    at/after the given ISO timestamp (a date like '2026-07-01' works --
    string comparison over ISO values). Stats then describe that delta,
    but domain_hints stay cross-window: clusters are computed over the
    WHOLE store and reported when they touch the delta, so a new
    domain-string variant still pairs with an old spelling that sits
    outside the scan window.
    """
    status = "" if include_archived else "active"
    where = ["1=1"]
    params: list = []
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        where.append(clause)
        params.extend(values)
    if type:
        where.append("AND type = ?")
        params.append(type)
    if status:
        where.append("AND status = ?")
        params.append(status)
    today = today_iso()
    base_where_sql, base_params = " ".join(where), list(params)
    if since:
        where.append("AND updated_at >= ?")
        params.append(since)
    where_sql = " ".join(where)

    rows = conn.execute(
        f"""SELECT uid, type, domain, also_domains, session, tags, content, status,
                   confidence, superseded_by, created_at, updated_at,
                   review_after, source_ref
            FROM memories WHERE {where_sql}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    mems = []
    budget_used = 0
    for r in rows:
        m = {"uid": r["uid"], "type": r["type"]}
        if r["confidence"] != "unverified":
            m["confidence"] = r["confidence"]
        if r["domain"]:
            m["domain"] = r["domain"]
        # what it already belongs to besides its own path, or a curation pass
        # proposing a cross-listing cannot tell a new one from one that holds
        if r["also_domains"]:
            m["also"] = parse_domains(r["also_domains"])
        if r["session"]:
            m["session"] = r["session"]
        if r["tags"]:
            tags = r["tags"]
            if len(tags) > CORPUS_TAGS_LEN:
                m["tags_len"] = len(tags)
                tags = tags[: CORPUS_TAGS_LEN - 1] + "…"
            m["tags"] = tags
        if include_archived and r["status"] != "active":
            m["status"] = r["status"]
        if r["superseded_by"]:
            m["superseded_by"] = r["superseded_by"]
        # What the writer said needs rechecking and where; `due` answers "is this overdue" so the
        # reader does no date arithmetic.
        if r["review_after"]:
            m["review_after"] = r["review_after"]
            if r["review_after"] <= today:
                m["due"] = True
        if r["source_ref"]:
            m["source_ref"] = r["source_ref"]
        m["created_at"] = r["created_at"][:19]
        content = r["content"]
        m["content_len"] = len(content)
        cap = CORPUS_FULL_LEN if full else CORPUS_SNIPPET_LEN
        m["content"] = content if len(content) <= cap else content[: cap - 1] + "…"
        anchors = _extract_anchors(content, cap=CORPUS_ANCHORS_CAP)
        if anchors:
            m["anchors"] = " ".join(anchors)
        mems.append(m)
        budget_used += len(json.dumps(m, ensure_ascii=False))
        if budget_used >= CORPUS_CHAR_BUDGET:
            break
    uids = {m["uid"] for m in mems}

    # What each one has been worth, omitted when zero like every default here; a memory with no
    # `recalls` is the interesting case.
    usage = usage_for(conn, uids)
    for m in mems:
        u = usage.get(m["uid"])
        if u:
            m["recalls"], m["last_recall"] = u["recalls"], u["last_recall"][:19]
    rels = conn.execute(
        "SELECT id, from_uid, to_uid, relation_type FROM relations"
    ).fetchall()
    edges = [dict(r) for r in rels if r["from_uid"] in uids or r["to_uid"] in uids]

    # stats over the WHOLE filtered corpus (not just the LIMIT window)
    total = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params).fetchone()[0]
    def agg(col: str) -> dict:
        return dict(conn.execute(
            f"SELECT {col}, COUNT(*) FROM memories WHERE {where_sql} GROUP BY {col} ORDER BY COUNT(*) DESC",
            params).fetchall())
    by_domain = agg("domain")
    stats = {
        "total": total,
        "by_type": agg("type"),
        "by_confidence": agg("confidence"),
        "by_domain": by_domain,
        "empty_domain": by_domain.get("", 0),
        # Over the whole filtered corpus. Read as UNPROVEN, never useless: telling store-wide
        # (a write log if near total), meaningless for any single row.
        "never_recalled": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND uid NOT IN "
            "(SELECT memory_uid FROM memory_usage)", params).fetchone()[0],
        # Rechecks a writer dated and nobody did. Tags empty or only the type carry no synonym;
        # `retag` fixes them.
        "untagged": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} "
            "AND (TRIM(tags) = '' OR TRIM(tags) = type)", params).fetchone()[0],
        # No name of its own, so every list falls back to the opening line of
        # its body. `retitle` is the kind that fixes it.
        "untitled": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND TRIM(title) = ''",
            params).fetchone()[0],
        "due_for_review": conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql} AND {_due_clause(today)[0]}",
            [*params, today]).fetchone()[0],
    }

    # Domain and nesting hints cluster over the WHOLE store; with `since`, only clusters touching
    # the delta are kept (counts stay store-wide).
    if since:
        by_domain_global = dict(conn.execute(
            f"SELECT domain, COUNT(*) FROM memories WHERE {base_where_sql} "
            "GROUP BY domain ORDER BY COUNT(*) DESC", base_params).fetchall())
        hints = [h for h in _domain_hints(by_domain_global)
                 if any(v["domain"] in by_domain for v in h["variants"])]
        nesting = [n for n in _nesting_hints(by_domain_global) if n["domain"] in by_domain]
    else:
        hints = _domain_hints(by_domain)
        nesting = _nesting_hints(by_domain)

    # Leaked calls over the scan's own window, not the page: pagination would hide findings.
    leaked, leaked_total = _leak_findings(conn, where_sql, params)
    stats["leaked_calls"] = leaked_total

    return {
        "memories": mems,
        "relations": edges,
        "count": len(mems),
        "offset": offset,
        "truncated": offset + len(mems) < total,
        "stats": stats,
        "domain_hints": hints,
        "domain_nesting": nesting,
        "leaked_calls": leaked,
    }


def _memory_exists(conn: sqlite3.Connection, uid: str | None) -> bool:
    return bool(uid) and get_memory(conn, uid) is not None


def _generated_content_error(conn: sqlite3.Connection, uid: str | None) -> str | None:
    """The free-text editors' refusal, for the suggestion kinds that rewrite content.

    A diagram's or a task's content is the projection of its rows, so a
    hand-authored body applied over it matches no row and survives only
    until the next structural edit regenerates it (see is_diagram).
    """
    row = get_memory(conn, uid) if uid else None
    if row is None:
        return None
    if row["type"] == DIAGRAM_TYPE:
        return (f"{uid} is a diagram: its content is generated from the graph. "
                "Use diagram_node/diagram_edge to change the flow.")
    if row["type"] == TASK_TYPE:
        return (f"{uid} is a task: its content is generated from the goal and items. "
                "Change them through the task tools.")
    return None


def _validate_suggestion(conn: sqlite3.Connection, s: object) -> tuple[dict | None, str | None]:
    """Return (normalized_row, error). error is a human-readable string or None."""
    if not isinstance(s, dict):
        return None, "suggestion must be an object"
    kind = str(s.get("kind", "")).strip()
    if kind not in SUGGESTION_KINDS:
        return None, f"unknown kind {kind!r} (allowed: {', '.join(SUGGESTION_KINDS)})"
    payload = s.get("payload") or {}
    if not isinstance(payload, dict):
        return None, "payload must be an object"
    target_uid = (str(s.get("target_uid", "")) or "").strip() or None
    rationale = str(s.get("rationale", "")).strip()
    verified = str(s.get("verified", "")).strip()

    def target_err() -> str | None:
        return None if _memory_exists(conn, target_uid) else f"target_uid not found: {target_uid!r}"

    if kind in ("compact", "reword"):
        err = target_err() or _generated_content_error(conn, target_uid)
        if err:
            return None, err
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        # checked at staging, so nothing in the human's queue is waiting to fail on apply
        row = memory_row(conn, target_uid)
        err = section_error(conn, row["type"], str(payload["new_content"]))
        if err:
            return None, err
    elif kind == "unleak":
        err = target_err()
        if err:
            return None, err
        field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
        if field not in LEAK_FIELDS:
            return None, (f"payload.field must be one of: {', '.join(LEAK_FIELDS)}; "
                          f"got {field!r}")
        if field == "content":
            err = _generated_content_error(conn, target_uid)
            if err:
                return None, err
        row = memory_row(conn, target_uid)
        text = row[field] or ""
        if not guard.leak_marks(row["type"], text):
            return None, f"nothing leaked in {field} of {target_uid}: no marks to remove"
        # the repair is computed HERE and travels in the payload, so the caller never retypes the
        # body, which is the defect this kind cleans up
        clean, _ = guard.strip_leak(row["type"], text)
        left = guard.leak_marks(row["type"], clean)
        if left:
            return None, (f"{field} of {target_uid} still carries {', '.join(left)} "
                          "after the pass -- the marks are inside its prose. Rewrite "
                          "it with a reword instead")
        if field == "content":
            err = section_error(conn, row["type"], clean)
            if err:
                return None, err
        payload = {"field": field, "new_text": clean}
    elif kind == "retag":
        err = target_err()
        if err:
            return None, err
        if "tags" not in payload:
            return None, "payload.tags required"
    elif kind == "retitle":
        err = target_err()
        if err:
            return None, err
        if not str(payload.get("title", "")).strip():
            return None, "payload.title required (a memory cannot be left unnamed)"
        too_long = title_error(str(payload["title"]))
        if too_long:
            return None, too_long
        if is_diagram(conn, target_uid):
            return None, (f"{target_uid} is a diagram: its title is part of what "
                          "generates its body. Rename it through the graph.")
    elif kind == "review":
        err = target_err()
        if err:
            return None, err
        if "review_after" not in payload:
            return None, "payload.review_after required ('' clears the date)"
        # normalized at staging, as redomain is: the panel shows it as what will hold, and '90d'
        # means a different day depending on when it is read
        try:
            payload = {**payload,
                       "review_after": normalize_review_after(str(payload["review_after"]))}
        except ValueError as exc:
            return None, str(exc)
    elif kind == "redomain":
        err = target_err()
        if err:
            return None, err
        if "domain" not in payload:
            return None, "payload.domain required"
        # normalized at staging too: the panel must show the path the memory will end up in
        payload = {**payload, "domain": normalize_domain(str(payload["domain"]))}
    elif kind == "crosslist":
        err = target_err()
        if err:
            return None, err
        if "also" not in payload:
            return None, "payload.also required"
        # the whole set is REPLACED, so staging runs the apply's policy (casing, path shape, dropping
        # a path the own domain covers) and the panel shows what will hold
        row = memory_row(conn, target_uid)
        given = parse_domains(payload["also"])
        want = apply_link_policy(conn, given, row["domain"])
        # an empty list is a legitimate suggestion, but a non-empty one that empties would apply as
        # a clear, so say so instead
        if given and not want:
            return None, (
                f"every path given is already covered by the memory's domain "
                f"{row['domain']!r}: {', '.join(given)}")
        payload = {**payload, "also": want}
    elif kind == "set_confidence":
        err = target_err()
        if err:
            return None, err
        if payload.get("confidence") not in CONFIDENCE_VALUES:
            return None, f"payload.confidence must be one of {CONFIDENCE_VALUES}"
        if payload["confidence"] == "contradicted" and not verified:
            return None, "verified required: describe the live-facts check that contradicts this memory"
    elif kind == "archive":
        err = target_err()
        if err:
            return None, err
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
    elif kind == "link":
        f = (str(payload.get("from_uid", "")) or "").strip()
        t = (str(payload.get("to_uid", "")) or "").strip()
        if not _memory_exists(conn, f):
            return None, f"payload.from_uid not found: {f!r}"
        if not _memory_exists(conn, t):
            return None, f"payload.to_uid not found: {t!r}"
        if f == t:
            return None, "cannot link a memory to itself"
        if not str(payload.get("relation_type", "")).strip():
            return None, "payload.relation_type required"
        if target_uid and target_uid != f:
            return None, "link derives target_uid from payload.from_uid; omit target_uid or make them match"
        target_uid = f
    elif kind == "merge":
        keep = (str(payload.get("keep_uid", "")) or "").strip()
        drop = (str(payload.get("drop_uid", "")) or "").strip()
        if not _memory_exists(conn, keep):
            return None, f"payload.keep_uid not found: {keep!r}"
        if not _memory_exists(conn, drop):
            return None, f"payload.drop_uid not found: {drop!r}"
        if keep == drop:
            return None, "cannot merge a memory with itself"
        if target_uid and target_uid != drop:
            return None, "merge derives target_uid from payload.drop_uid; omit target_uid or make them match"
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
        target_uid = drop
    elif kind == "distill":
        if target_uid:
            return None, "distill creates a new memory; omit target_uid"
        extra = sorted(k for k in payload if k not in DISTILL_PAYLOAD_KEYS)
        if extra:
            return None, (f"payload keys not accepted by distill: {', '.join(extra)} "
                          f"(allowed: {', '.join(DISTILL_PAYLOAD_KEYS)})")
        sources = payload.get("source_uids")
        if not isinstance(sources, list) or not sources:
            return None, "payload.source_uids must be a non-empty list"
        sources = [str(u).strip() for u in sources]
        if len(set(sources)) != len(sources):
            return None, "payload.source_uids contains duplicates"
        for u in sources:
            if not _memory_exists(conn, u):
                return None, f"payload.source_uids not found: {u!r}"
            if is_diagram(conn, u):
                return None, (f"{u} is a diagram: distill archives its sources. "
                              "Use archive to retire a flow on its own.")
            if memory_row(conn, u)["type"] == TASK_TYPE:
                return None, (f"{u} is a task: distill archives its sources, and a task "
                              "closes through its own items.")
        if payload.get("new_type") not in DISTILL_TYPES:
            return None, f"payload.new_type must be one of {DISTILL_TYPES}"
        if not str(payload.get("new_content", "")).strip():
            return None, "payload.new_content required"
        # no writing tool names this memory afterwards, so a distill with no title stays unnamed
        if not str(payload.get("title", "")).strip():
            return None, "payload.title required (the distilled memory needs a name)"
        too_long = title_error(str(payload["title"]))
        if too_long:
            return None, too_long
        # anti_pattern is a distill target and is made of fields, so the body
        # a distill writes has to read back the same way any other one does
        err = section_error(conn, str(payload["new_type"]), str(payload["new_content"]))
        if err:
            return None, err
        if not verified:
            return None, VERIFIED_REQUIRED[kind]
        payload = {**payload, "source_uids": sources}

    return {
        "kind": kind, "target_uid": target_uid, "payload": payload,
        "rationale": rationale, "verified": verified,
    }, None


# Characters a run note may hold. A longer note is refused, not truncated.
RUN_NOTE_MAX = 250


def stage_optimization(conn: sqlite3.Connection, note: str, suggestions: list) -> dict:
    """Validate a batch of suggestions and write them to a new run.

    Invalid suggestions are skipped and reported in `errors`; only valid
    ones are staged. Returns {run_id, staged, errors}. No run is created
    when nothing validates.

    `note` summarises the run in at most RUN_NOTE_MAX characters; a longer
    one raises and nothing is staged.
    """
    if not isinstance(suggestions, list) or not suggestions:
        raise ValueError("suggestions must be a non-empty list")
    note = str(note or "")
    if len(note) > RUN_NOTE_MAX:
        raise ValueError(
            f"note is {len(note)} characters; the limit is {RUN_NOTE_MAX}")
    valid, errors = [], []
    for i, s in enumerate(suggestions):
        norm, err = _validate_suggestion(conn, s)
        if err:
            errors.append({"index": i, "error": err})
        else:
            valid.append(norm)
    if not valid:
        return {"run_id": None, "staged": 0, "errors": errors}
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO optimization_runs (created_at, note, status) VALUES (?, ?, 'open')",
        (ts, note),
    )
    run_id = cur.lastrowid
    for v in valid:
        conn.execute(
            """INSERT INTO optimization_suggestions
               (run_id, kind, target_uid, payload, rationale, verified, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (run_id, v["kind"], v["target_uid"], json.dumps(v["payload"]),
             v["rationale"], v["verified"], ts),
        )
    return {"run_id": run_id, "staged": len(valid), "errors": errors}


def list_optimization_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id) AS total,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'pending') AS pending,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'applied') AS applied,
                  (SELECT COUNT(*) FROM optimization_suggestions s WHERE s.run_id = r.id AND s.status = 'rejected') AS rejected
           FROM optimization_runs r ORDER BY r.created_at DESC, r.id DESC"""
    ).fetchall()


def optimization_run_kind_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per-run, per-kind suggestion counts across all runs.

    All three states, because a reader of the counts alone has to be able to
    say how many of a kind were APPLIED -- total minus pending minus
    rejected. Without `rejected` a rejected suggestion counts as applied,
    and the day's summary claims work that was turned down.
    """
    return conn.execute(
        """SELECT run_id, kind,
                  COUNT(*) AS total,
                  SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
           FROM optimization_suggestions
           GROUP BY run_id, kind
           ORDER BY run_id, kind"""
    ).fetchall()


def get_optimization_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM optimization_runs WHERE id = ?", (run_id,)).fetchone()


def get_optimization_suggestions(
    conn: sqlite3.Connection, run_id: int, status: str = "", kind: str = ""
) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM optimization_suggestions WHERE run_id = ?"]
    params: list = [run_id]
    if status:
        sql.append("AND status = ?")
        params.append(status)
    if kind:
        sql.append("AND kind = ?")
        params.append(kind)
    sql.append("ORDER BY id ASC")
    return conn.execute(" ".join(sql), params).fetchall()


def get_suggestion(conn: sqlite3.Connection, sug_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM optimization_suggestions WHERE id = ?", (sug_id,)
    ).fetchone()


def set_run_backup(conn: sqlite3.Connection, run_id: int, backup_path: str) -> None:
    conn.execute(
        "UPDATE optimization_runs SET backup_path = ? WHERE id = ?", (backup_path, run_id)
    )


def _update_meta_field(conn: sqlite3.Connection, uid: str, field: str, value: str) -> None:
    """Mirror admin.edit_meta for one tag/domain field: UPDATE + audit.

    `field` is only ever 'tags', 'title' or 'domain' (caller-controlled), so
    the f-string interpolation is not an injection surface.

    A domain change re-runs the cross-listing policy: the memory's new path
    may already satisfy a membership the old path needed (see
    apply_link_policy), and leaving that row would count it twice in its
    own branch.
    """
    if field == "domain":
        value = apply_domain_policy(conn, value)
    row = memory_row(conn, uid)
    conn.execute(
        f"UPDATE memories SET {field} = ?, updated_at = ? WHERE uid = ?",
        (value, now_iso(), uid),
    )
    note = f"meta: {field} '{row[field]}' → '{value}'"
    conn.execute(
        "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) VALUES (?, ?, ?, ?, ?)",
        (uid, now_iso(), row["content"], row["content"], note),
    )
    if field == "domain" and row["also_domains"]:
        set_domain_links(conn, uid, get_domain_links(conn, uid))


def _target(kind: str, target_uid: str | None) -> str:
    """The uid a suggestion acts on; link, merge and distill name theirs in the payload."""
    if target_uid:
        return target_uid
    if kind in ("link", "merge", "distill"):
        return ""
    raise ValueError(f"{kind} needs a target_uid")


def _apply_kind(conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict) -> dict:
    """Execute one suggestion and return the prev_state dict for undo."""
    uid = _target(kind, target_uid)
    if kind in ("compact", "reword"):
        # staging refuses these on a diagram or task, but a staged run may still
        # hold one; applying it would write over the projection
        err = _generated_content_error(conn, uid)
        if err:
            raise ValueError(err)
        row = memory_row(conn, uid)
        prev = {"content": row["content"]}
        update_memory_content(conn, uid, payload["new_content"], note=f"optimize:{kind}")
        return prev
    if kind == "unleak":
        field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
        if field == "content":
            err = _generated_content_error(conn, uid)
            if err:
                raise ValueError(err)
        row = memory_row(conn, uid)
        prev = {field: row[field]}
        text = str(payload["new_text"])
        if field == "content":
            update_memory_content(conn, uid, text, note=f"optimize:{kind}")
        else:
            _update_meta_field(conn, uid, field, text)
        return prev
    if kind == "retag":
        row = memory_row(conn, uid)
        prev = {"tags": row["tags"]}
        _update_meta_field(conn, uid, "tags", str(payload["tags"]).strip())
        return prev
    if kind == "retitle":
        row = memory_row(conn, uid)
        prev = {"title": row["title"]}
        _update_meta_field(conn, uid, "title", str(payload["title"]).strip())
        return prev
    if kind == "review":
        row = memory_row(conn, uid)
        prev = {"review_after": row["review_after"]}
        set_review_after(conn, uid, str(payload["review_after"]))
        return prev
    if kind == "redomain":
        row = memory_row(conn, uid)
        prev = {"domain": row["domain"]}
        _update_meta_field(conn, uid, "domain", str(payload["domain"]).strip())
        return prev
    if kind == "crosslist":
        # the whole set, not an addition: undo restores exactly this list
        prev = {"also": get_domain_links(conn, uid)}
        set_domain_links(conn, uid, payload["also"], note=f"optimize:{kind}")
        return prev
    if kind == "set_confidence":
        row = memory_row(conn, uid)
        prev = {"confidence": row["confidence"]}
        set_confidence(conn, uid, payload["confidence"])
        return prev
    if kind == "archive":
        row = memory_row(conn, uid)
        prev = {"status": row["status"], "superseded_by": row["superseded_by"]}
        reason = str(payload.get("reason", "")).strip() or "optimize: archived"
        set_status(conn, uid, "archived", note=reason)
        return prev
    if kind == "link":
        rid = add_relation(
            conn, payload["from_uid"].strip(), payload["to_uid"].strip(),
            str(payload["relation_type"]).strip(), str(payload.get("note", "")).strip(),
        )
        return {"relation_id": rid}
    if kind == "merge":
        keep, drop = payload["keep_uid"].strip(), payload["drop_uid"].strip()
        drow = memory_row(conn, drop)
        prev = {"drop_status": drow["status"], "drop_superseded_by": drow["superseded_by"]}
        rid = add_relation(conn, keep, drop, "supersedes", str(payload.get("note", "")).strip())
        prev["relation_id"] = rid
        set_status(conn, drop, "archived", superseded_by=keep, note="optimize: merged")
        return prev
    if kind == "distill":
        new_uid = insert_memory(
            conn, type=payload["new_type"], content=payload["new_content"],
            # staging requires a title; .get keeps a run staged before it did
            # appliable rather than failing here
            title=str(payload.get("title", "")).strip(),
            tags=str(payload.get("tags", "")).strip(),
            domain=str(payload.get("domain", "")).strip(),
        )
        prev = {"new_uid": new_uid, "relation_ids": [], "sources": []}
        for u in payload["source_uids"]:
            row = memory_row(conn, u)
            prev["sources"].append(
                {"uid": u, "status": row["status"], "superseded_by": row["superseded_by"]})
            prev["relation_ids"].append(
                add_relation(conn, new_uid, u, "supersedes", "optimize: distilled"))
            set_status(conn, u, "archived", superseded_by=new_uid,
                       note=f"optimize: distilled into {new_uid}")
        return prev
    raise ValueError(f"unknown kind: {kind}")


def _revert_kind(
    conn: sqlite3.Connection, kind: str, target_uid: str | None, payload: dict, prev: dict
) -> None:
    uid = _target(kind, target_uid)
    if kind in ("compact", "reword"):
        update_memory_content(conn, uid, prev["content"], note=f"optimize:undo {kind}")
    elif kind == "unleak":
        field = str(payload.get("field", "")).strip() or LEAK_FIELDS[0]
        if field == "content":
            # restores a leaked body that writers are refused: an undo puts back what was there
            update_memory_content(conn, uid, prev["content"],
                                  note=f"optimize:undo {kind}", leaked_ok=True)
        else:
            _update_meta_field(conn, uid, field, prev[field])
    elif kind == "retag":
        _update_meta_field(conn, uid, "tags", prev["tags"])
    elif kind == "retitle":
        _update_meta_field(conn, uid, "title", prev["title"])
    elif kind == "review":
        set_review_after(conn, uid, prev["review_after"])
    elif kind == "redomain":
        _update_meta_field(conn, uid, "domain", prev["domain"])
    elif kind == "crosslist":
        set_domain_links(conn, uid, prev["also"], coerce=False,
                         note="optimize:undo crosslist")
    elif kind == "set_confidence":
        set_confidence(conn, uid, prev["confidence"])
    elif kind == "archive":
        set_status(conn, uid, prev["status"],
                   superseded_by=prev.get("superseded_by"), note="optimize: undo archive")
    elif kind == "link":
        conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
    elif kind == "merge":
        conn.execute("DELETE FROM relations WHERE id = ?", (prev["relation_id"],))
        set_status(conn, payload["drop_uid"].strip(), prev["drop_status"],
                   superseded_by=prev.get("drop_superseded_by"), note="optimize: undo merge")
    elif kind == "distill":
        for s in prev.get("sources", []):
            set_status(conn, s["uid"], s["status"],
                       superseded_by=s.get("superseded_by"), note="optimize: undo distill")
        # purge (not archive) the distilled memory: it was born from this
        # apply, so undo removes it entirely; its relations go with it
        if prev.get("new_uid"):
            purge_memory(conn, prev["new_uid"])
    else:
        raise ValueError(f"unknown kind: {kind}")


def apply_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] != "pending":
        raise ValueError(f"suggestion already {row['status']}")
    payload = json.loads(row["payload"])
    prev = _apply_kind(conn, row["kind"], row["target_uid"], payload)
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'applied', prev_state = ?, decided_at = ? WHERE id = ?",
        (json.dumps(prev), now_iso(), sug_id),
    )
    return True


def reject_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] == "applied":
        raise ValueError("cannot reject an applied suggestion; revert it first")
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'rejected', decided_at = ? WHERE id = ?",
        (now_iso(), sug_id),
    )
    return True


def revert_suggestion(conn: sqlite3.Connection, sug_id: int) -> bool:
    """Put a decided suggestion back on the table.

    An APPLIED one is undone in the store first, from the prev_state its
    apply recorded. A REJECTED one wrote nothing to any memory, so taking
    the answer back is only a change of status.

    Raises ValueError for an unknown id and for one that is already pending.
    """
    row = get_suggestion(conn, sug_id)
    if row is None:
        raise ValueError(f"unknown suggestion: {sug_id}")
    if row["status"] == "pending":
        raise ValueError("suggestion is already pending")
    if row["status"] == "applied":
        payload = json.loads(row["payload"])
        prev = json.loads(row["prev_state"]) if row["prev_state"] else {}
        _revert_kind(conn, row["kind"], row["target_uid"], payload, prev)
    conn.execute(
        "UPDATE optimization_suggestions SET status = 'pending', prev_state = NULL, decided_at = NULL WHERE id = ?",
        (sug_id,),
    )
    return True


def delete_optimization_run(conn: sqlite3.Connection, run_id: int) -> bool:
    conn.execute("DELETE FROM optimization_suggestions WHERE run_id = ?", (run_id,))
    cur = conn.execute("DELETE FROM optimization_runs WHERE id = ?", (run_id,))
    return cur.rowcount > 0
