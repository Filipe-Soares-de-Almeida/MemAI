"""Diagram rows: the graph's nodes and edges, node links, jumps, and the overview of a scope."""

from __future__ import annotations

import sqlite3
from typing import Any

from memai.lite import now_iso
from memai.store.diagrams.layout import (
    FONT_SCALE_MAX,
    FONT_SCALE_MIN,
    NODE_MAX_H,
    NODE_MAX_W,
    NODE_MIN_H,
    NODE_MIN_W,
    _clamp,
    _graph_issues,
    _layout_graph,
    _node_field_errors,
    _norm_edges,
    _norm_nodes,
    _validate_graph,
    loop_edges,
)
from memai.store.diagrams.render import _render_mermaid, _render_text
from memai.store.domains import domain_scope_clause
from memai.store.memories import (
    DIAGRAM_TYPE,
    domain_links_for,
    get_memory,
    insert_memory,
    update_memory_content,
)
from memai.store.sections import title_error

# A diagram is a graph, one row per step. The graph is the record; memories.content is a generated
# rendering for FTS (_render_text), so the free-text editors refuse a diagram (is_diagram).

DIAGRAM_KINDS = ("flowchart",)


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
