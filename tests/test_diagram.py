"""Tests for the diagram memory type.

A diagram stores a graph -- one row per step -- and generates the prose
FTS indexes. Two invariants get most of the attention here, because
everything else leans on them:

* the generated projection is the only thing that ever writes
  memories.content, so a hand edit has to be refused;
* node coordinates are authoritative in the store, so a node always has
  them, a drag never looks like an edit, and no renderer needs a layout
  algorithm of its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memai import admin, db

# The dashboard's sources. admin.WEBUI_DIR is the build output, which is one
# bundled file and answers none of the questions below.
WEBUI_SRC = Path(__file__).resolve().parents[1] / "src" / "memai" / "webui"


def _own_modules() -> list[Path]:
    """Every module the dashboard is written in, without the copied-in
    third-party code under public/ or the build output under dist/."""
    skip = {"public", "dist"}
    return [p for p in WEBUI_SRC.rglob("*.js") if not skip & set(p.parts)]


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


# A branch that rejoins: 'done' is reachable from 'check' directly AND
# through 'write', which is what makes it a longest-path layering case.
NODES = [
    {"key": "start", "shape": "start", "label": "Receive the schedule trigger"},
    {"key": "load", "label": "Read the export window", "note": "Inclusive on both ends."},
    {"key": "check", "shape": "decision", "label": "Any rows in the window?"},
    {"key": "write", "shape": "io", "label": "Write one file per store"},
    {"key": "done", "shape": "end", "label": "Report the run as finished"},
]
EDGES = [
    {"from": "start", "to": "load"},
    {"from": "load", "to": "check"},
    {"from": "check", "to": "done", "label": "no"},
    {"from": "check", "to": "write", "label": "yes"},
    {"from": "write", "to": "done"},
]


def _mk(conn, **kw):
    kw.setdefault("title", "Nightly export routine")
    kw.setdefault("nodes", NODES)
    kw.setdefault("edges", EDGES)
    uid, errors = db.insert_diagram(conn, **kw)
    assert errors == []
    return uid


def _nodes(conn, uid):
    return {n["key"]: n for n in db.get_diagram(conn, uid)["nodes"]}


# ------------------------------------------------------------------ structure

def test_insert_persists_graph_and_generates_projection(conn):
    uid = _mk(conn, summary="Ships one file per store.", domain="proj-1042")
    d = db.get_diagram(conn, uid)
    assert (d["title"], d["kind"]) == ("Nightly export routine", "flowchart")
    assert [n["key"] for n in d["nodes"]] == [n["key"] for n in NODES]
    assert len(d["edges"]) == len(EDGES)

    row = db.get_memory(conn, uid)
    assert row["type"] == "diagram"
    # the projection, not the raw graph, is what search indexes
    assert row["content"] == db.render_diagram_text(conn, uid)
    assert "DIAGRAM: Nightly export routine" in row["content"]
    assert "SUMMARY: Ships one file per store." in row["content"]
    assert "check [decision]: Any rows in the window?" in row["content"]
    assert "  -> write [yes]" in row["content"]
    assert "load: Inclusive on both ends." in row["content"]


def test_projection_reads_in_flow_order(conn):
    uid = _mk(conn)
    flow = db.get_memory(conn, uid)["content"]
    # node lines start at column 0; the '  -> target' arrows are indented
    order = [line.split(" ", 1)[0] for line in flow.splitlines()
             if " [" in line and not line.startswith(" ")]
    assert order == ["start", "load", "check", "write", "done"]


def test_tags_hold_what_the_writer_supplied(conn):
    """The type is a column of its own and every read filters on it, so a
    copy of it in the weighted tags column buys no reach and costs a term."""
    assert db.get_memory(conn, _mk(conn))["tags"] == ""
    assert db.get_memory(conn, _mk(conn, tags="export,nightly"))["tags"] == "export,nightly"


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("nodes, edges, expected", [
    ([], [], "no nodes"),
    ([{"key": "a", "label": "x"}], [], "no 'start' node"),
    ([{"key": "s", "shape": "start", "label": "x"},
      {"key": "t", "shape": "start", "label": "y"}], [{"from": "s", "to": "t"}], "'start' nodes"),
    ([{"key": "s", "shape": "start", "label": "x"},
      {"key": "s", "shape": "step", "label": "y"}], [], "duplicate node key"),
    ([{"key": "s", "shape": "start", "label": ""}], [], "empty label"),
    ([{"key": "s", "shape": "start", "label": "x"},
      {"key": "b", "shape": "weird", "label": "y"}], [{"from": "s", "to": "b"}], "unknown shape"),
    ([{"key": "s", "shape": "start", "label": "x"}], [{"from": "s", "to": "ghost"}], "not a node key"),
    ([{"key": "s", "shape": "start", "label": "x"},
      {"key": "b", "label": "y"}], [], "unreachable from start"),
    ([{"key": "s p", "shape": "start", "label": "x"}], [], "invalid node key"),
    ([{"key": "s", "shape": "start", "label": "x"}], [{"from": "s", "to": "s"}], "self-loop"),
])
def test_validation_rejects_broken_graphs(conn, nodes, edges, expected):
    uid, errors = db.insert_diagram(conn, title="T", nodes=nodes, edges=edges)
    assert uid is None
    assert any(expected in e for e in errors), errors


def test_failed_validation_writes_nothing(conn):
    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    uid, errors = db.insert_diagram(conn, title="T", nodes=NODES, edges=[{"from": "start", "to": "ghost"}])
    assert (uid, bool(errors)) == (None, True)
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM diagram_nodes").fetchone()[0] == 0


def test_title_and_kind_are_checked(conn):
    assert "title" in db.insert_diagram(conn, title="  ", nodes=NODES, edges=EDGES)[1][0]
    assert "kind" in db.insert_diagram(conn, title="T", nodes=NODES, edges=EDGES, kind="er")[1][0]


# ---------------------------------------------------------------------- layout

def test_every_node_has_coordinates_without_a_client(conn):
    """Nothing may depend on the editor having been opened once."""
    for n in db.get_diagram(conn, _mk(conn))["nodes"]:
        assert isinstance(n["x"], float) and isinstance(n["y"], float)


def test_layout_is_deterministic(conn):
    nodes, edges = db._load_graph(conn, _mk(conn))
    assert db._layout_graph(nodes, edges) == db._layout_graph(nodes, edges)


def test_layout_sinks_a_rejoining_node_below_every_branch(conn):
    """Longest path, not first-visit depth: 'done' sits under 'write'.

    'check -> done' comes first in EDGES, so first-visit depth would put
    the terminal step level with 'write' instead of after it.
    """
    pos = _nodes(conn, _mk(conn))
    assert pos["done"]["y"] > pos["write"]["y"] > pos["check"]["y"]


def test_layout_spreads_siblings_across_a_row(conn):
    pos = _nodes(conn, _mk(conn))
    assert pos["start"]["y"] < pos["load"]["y"]
    # the two branch targets of one decision are in different columns
    uid = _mk(conn, edges=[
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "write", "label": "yes"},
        {"from": "check", "to": "done", "label": "no"},
    ])
    pos = _nodes(conn, uid)
    assert pos["write"]["y"] == pos["done"]["y"]
    assert pos["write"]["x"] != pos["done"]["x"]


def test_store_predating_the_size_columns_is_migrated(tmp_path):
    """The columns arrived after the tables did, in a live store.

    `CREATE TABLE IF NOT EXISTS` -- how every other change here migrates --
    is a no-op once the table exists, so a column added later needs the
    explicit pass. This builds the older table shape by hand and checks
    that opening it repairs and then works.
    """
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE diagrams (
            memory_uid TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'flowchart',
            title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '');
        CREATE TABLE diagram_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_uid TEXT NOT NULL,
            node_key TEXT NOT NULL, shape TEXT NOT NULL DEFAULT 'step',
            label TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
            seq INTEGER NOT NULL DEFAULT 0, x REAL NOT NULL, y REAL NOT NULL);
    """)
    raw.commit()
    raw.close()

    with db.connect(path) as c:
        cols = lambda t: {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        assert "font_scale" in cols("diagrams")
        assert {"w", "h"} <= cols("diagram_nodes")
        uid = _mk(c)
        assert db.get_diagram(c, uid)["font_scale"] == 1.0
        assert _nodes(c, uid)["load"]["w"] is None

    with db.connect(path) as c:      # idempotent: opening it again is fine
        assert db.get_diagram(c, uid)["title"] == "Nightly export routine"


def test_layout_leaves_more_room_than_the_boxes_take(conn):
    """A box is 170x48 in these units; a packed flow is a wall of text.

    Pinned as a gap, not as the constants themselves: whoever re-tightens
    the layout has to argue with the reason, not just with two numbers.
    """
    uid = _mk(conn, edges=[
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "write", "label": "yes"},
        {"from": "check", "to": "done", "label": "no"},
    ])
    pos = _nodes(conn, uid)
    assert abs(pos["write"]["x"] - pos["done"]["x"]) - 170 >= 100   # side by side
    assert pos["load"]["y"] - pos["start"]["y"] - 48 >= 100         # one under the other


def test_layout_survives_a_cycle(conn):
    """A retry loop is a legal flow; the back-edge must not hang layering."""
    uid = _mk(conn, title="Retry loop", nodes=[
        {"key": "start", "shape": "start", "label": "Begin"},
        {"key": "call", "label": "Call the endpoint"},
        {"key": "ok", "shape": "decision", "label": "Succeeded?"},
        {"key": "done", "shape": "end", "label": "Finish"},
    ], edges=[
        {"from": "start", "to": "call"},
        {"from": "call", "to": "ok"},
        {"from": "ok", "to": "call", "label": "no, retry"},
        {"from": "ok", "to": "done", "label": "yes"},
    ])
    pos = _nodes(conn, uid)
    assert pos["start"]["y"] < pos["call"]["y"] < pos["ok"]["y"] < pos["done"]["y"]


def test_drag_is_not_an_edit(conn):
    uid = _mk(conn)
    before = db.get_memory(conn, uid)
    edits = len(db.get_edit_history(conn, uid))

    assert db.set_node_positions(conn, uid, {"load": (999.0, 111.0)}) == 1

    after = db.get_memory(conn, uid)
    assert after["content"] == before["content"]
    assert after["updated_at"] == before["updated_at"]
    assert len(db.get_edit_history(conn, uid)) == edits
    assert (_nodes(conn, uid)["load"]["x"], _nodes(conn, uid)["load"]["y"]) == (999.0, 111.0)


def test_drag_accepts_a_dict_pair_and_skips_junk(conn):
    uid = _mk(conn)
    moved = db.set_node_positions(conn, uid, {
        "load": {"x": 5, "y": 6},   # what a JSON body looks like
        "ghost": (1, 2),            # unknown key
        "check": ("left", "up"),    # unparseable
    })
    assert moved == 1
    assert (_nodes(conn, uid)["load"]["x"], _nodes(conn, uid)["load"]["y"]) == (5.0, 6.0)


def test_resize_rides_the_same_call_and_is_clamped(conn):
    """A card resized from a corner moves its centre too, so both go together."""
    uid = _mk(conn)
    assert db.set_node_positions(conn, uid, {
        "load": {"x": 10, "y": 20, "w": 300, "h": 120}}) == 1
    load = _nodes(conn, uid)["load"]
    assert (load["w"], load["h"]) == (300.0, 120.0)

    db.set_node_positions(conn, uid, {"load": {"x": 10, "y": 20, "w": 9999, "h": 1}})
    load = _nodes(conn, uid)["load"]
    assert (load["w"], load["h"]) == (db.NODE_MAX_W, db.NODE_MIN_H)

    # and a plain move leaves the size alone
    db.set_node_positions(conn, uid, {"load": (1.0, 2.0)})
    assert _nodes(conn, uid)["load"]["w"] == db.NODE_MAX_W


def test_resizing_is_not_an_edit_either(conn):
    uid = _mk(conn)
    before = db.get_memory(conn, uid)
    db.set_node_positions(conn, uid, {"load": {"x": 1, "y": 2, "w": 260, "h": 90}})
    after = db.get_memory(conn, uid)
    assert (after["content"], after["updated_at"]) == (before["content"], before["updated_at"])


def test_reset_boxes_restores_the_shape_defaults(conn):
    uid = _mk(conn)
    db.set_node_positions(conn, uid, {
        "load": {"x": 1, "y": 2, "w": 260, "h": 90},
        "check": {"x": 3, "y": 4, "w": 300, "h": 120}})
    assert db.reset_node_boxes(conn, uid, ["load"]) == 1
    assert _nodes(conn, uid)["load"]["w"] is None
    assert _nodes(conn, uid)["check"]["w"] == 300.0

    assert db.reset_node_boxes(conn, uid) == len(NODES)
    assert all(n["w"] is None and n["h"] is None for n in _nodes(conn, uid).values())


def test_default_box_follows_the_shape(conn):
    assert db.node_box({"shape": "step"}) == (db.NODE_DEFAULT_W, db.NODE_DEFAULT_H)
    assert db.node_box({"shape": "decision"}) == (db.NODE_DEFAULT_W, db.DECISION_DEFAULT_H)
    assert db.node_box({"shape": "step", "w": 400, "h": 200}) == (400.0, 200.0)


def test_a_wide_card_widens_the_arrangement(conn):
    """Otherwise the next auto-arrange drops a resized card on its neighbour."""
    uid = _mk(conn, edges=[
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "write", "label": "yes"},
        {"from": "check", "to": "done", "label": "no"},
    ])
    narrow = abs(_nodes(conn, uid)["write"]["x"] - _nodes(conn, uid)["done"]["x"])
    db.set_node_positions(conn, uid, {"write": {"x": 0, "y": 0, "w": db.NODE_MAX_W}})
    db.relayout_diagram(conn, uid)
    wide = abs(_nodes(conn, uid)["write"]["x"] - _nodes(conn, uid)["done"]["x"])
    assert wide >= narrow + (db.NODE_MAX_W - db.NODE_DEFAULT_W)


def test_font_scale_is_stored_clamped_and_is_not_content(conn):
    uid = _mk(conn)
    before = db.get_memory(conn, uid)["content"]
    assert db.get_diagram(conn, uid)["font_scale"] == 1.0

    assert db.set_diagram_meta(conn, uid, font_scale=1.6) == (True, [])
    assert db.get_diagram(conn, uid)["font_scale"] == 1.6
    assert db.get_memory(conn, uid)["content"] == before      # how it looks, not what it says
    assert db.get_diagram(conn, uid)["title"] == "Nightly export routine"

    db.set_diagram_meta(conn, uid, font_scale=99)
    assert db.get_diagram(conn, uid)["font_scale"] == db.FONT_SCALE_MAX
    assert "font_scale" in db.set_diagram_meta(conn, uid, font_scale="huge")[1][0]


def test_bigger_text_grows_the_default_boxes_and_the_arrangement(conn):
    """Otherwise 'bigger font' just truncates every label in an unchanged card."""
    assert db.node_box({"shape": "step"}, 2) == (db.NODE_DEFAULT_W * 2, db.NODE_DEFAULT_H * 2)
    # a hand-sized box is the user's choice and stays put
    assert db.node_box({"shape": "step", "w": 400, "h": 200}, 2) == (400.0, 200.0)

    # a branch, so 'write' and 'done' are side by side and the pitch is visible
    uid = _mk(conn, edges=[
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "write", "label": "yes"},
        {"from": "check", "to": "done", "label": "no"},
    ])
    before = _nodes(conn, uid)
    narrow = abs(before["write"]["x"] - before["done"]["x"])

    db.set_diagram_meta(conn, uid, font_scale=2)
    after = _nodes(conn, uid)
    # the whole arrangement scales with it, so a hand-arranged flow stays arranged
    for key, node in before.items():
        assert after[key]["x"] == pytest.approx(node["x"] * 2)
        assert after[key]["y"] == pytest.approx(node["y"] * 2)

    db.relayout_diagram(conn, uid)
    assert abs(_nodes(conn, uid)["write"]["x"] - _nodes(conn, uid)["done"]["x"]) > narrow


def test_loop_closers_come_from_the_graph_not_the_coordinates(conn):
    """Dashed means 'closes a cycle'. Dragging a card must not change that."""
    uid = _mk(conn, nodes=[
        {"key": "start", "shape": "start", "label": "Receive the trigger"},
        {"key": "call", "label": "Call the endpoint"},
        {"key": "ok", "shape": "decision", "label": "Accepted?"},
        {"key": "done", "shape": "end", "label": "Report the run"},
    ], edges=[
        {"from": "start", "to": "call"},
        {"from": "call", "to": "ok"},
        {"from": "ok", "to": "call", "label": "no, retry"},
        {"from": "ok", "to": "done", "label": "yes"},
    ])
    loops = {(e["from"], e["to"]) for e in db.get_diagram(conn, uid)["edges"] if e["loops"]}
    assert loops == {("ok", "call")}

    # drag 'done' above 'start': the flow is unchanged, so the marks are too
    db.set_node_positions(conn, uid, {"done": (0.0, -5000.0)})
    again = {(e["from"], e["to"]) for e in db.get_diagram(conn, uid)["edges"] if e["loops"]}
    assert again == loops


def test_relayout_discards_dragged_positions(conn):
    uid = _mk(conn)
    original = _nodes(conn, uid)["load"]["x"]
    db.set_node_positions(conn, uid, {"load": (999.0, 999.0)})
    assert db.relayout_diagram(conn, uid) == len(NODES)
    assert _nodes(conn, uid)["load"]["x"] == original


def test_relayout_leaves_content_untouched(conn):
    uid = _mk(conn)
    before = db.get_memory(conn, uid)["content"]
    db.set_node_positions(conn, uid, {"load": (999.0, 999.0)})
    db.relayout_diagram(conn, uid)
    assert db.get_memory(conn, uid)["content"] == before


# ----------------------------------------------------------- incremental edits

def test_a_new_node_may_sit_unattached_then_be_wired(conn):
    """Two-call authoring has to be legal, so reachability is not enforced here."""
    uid = _mk(conn)
    ok, errors = db.upsert_diagram_node(conn, uid, "audit", label="Append to the audit log")
    assert (ok, errors) == (True, [])
    assert "audit [step]: Append to the audit log" in db.get_memory(conn, uid)["content"]
    assert db.upsert_diagram_edge(conn, uid, "write", "audit") == (True, [])


def test_a_new_node_gets_coordinates_and_the_others_keep_theirs(conn):
    uid = _mk(conn)
    db.set_node_positions(conn, uid, {"load": (777.0, 888.0)})
    db.upsert_diagram_node(conn, uid, "audit", label="Append to the audit log")
    pos = _nodes(conn, uid)
    assert isinstance(pos["audit"]["x"], float)
    assert (pos["load"]["x"], pos["load"]["y"]) == (777.0, 888.0)


def test_patching_one_field_leaves_the_others_alone(conn):
    uid = _mk(conn)
    assert db.upsert_diagram_node(conn, uid, "load", note="Rewritten.") == (True, [])
    load = _nodes(conn, uid)["load"]
    assert (load["label"], load["note"]) == ("Read the export window", "Rewritten.")
    assert db.upsert_diagram_node(conn, uid, "load", note="") == (True, [])
    assert _nodes(conn, uid)["load"]["note"] == ""


def test_node_edit_lands_in_the_audit_log(conn):
    uid = _mk(conn)
    edits = len(db.get_edit_history(conn, uid))
    db.upsert_diagram_node(conn, uid, "load", label="Read the window from config")
    assert len(db.get_edit_history(conn, uid)) == edits + 1


def test_a_no_op_edit_writes_no_audit_row(conn):
    uid = _mk(conn)
    edits = len(db.get_edit_history(conn, uid))
    db.upsert_diagram_node(conn, uid, "load", label="Read the export window")
    assert len(db.get_edit_history(conn, uid)) == edits


def test_edge_upsert_relabels_instead_of_duplicating(conn):
    uid = _mk(conn)
    before = len(db.get_diagram(conn, uid)["edges"])
    assert db.upsert_diagram_edge(conn, uid, "check", "write", label="rows found") == (True, [])
    edges = db.get_diagram(conn, uid)["edges"]
    assert len(edges) == before
    assert next(e for e in edges if e["to"] == "write")["label"] == "rows found"


def test_incremental_writers_still_check_endpoints(conn):
    uid = _mk(conn)
    assert "not a node key" in db.upsert_diagram_edge(conn, uid, "check", "ghost")[1][0]
    assert "self-loop" in db.upsert_diagram_edge(conn, uid, "check", "check")[1][0]
    assert "unknown shape" in db.upsert_diagram_node(conn, uid, "x", label="y", shape="blob")[1][0]


def test_writers_refuse_a_uid_that_is_not_a_diagram(conn):
    note = db.insert_memory(conn, type="note", content="a fact")
    assert "not a diagram" in db.upsert_diagram_node(conn, note, "a", label="b")[1][0]
    assert "not a diagram" in db.upsert_diagram_edge(conn, note, "a", "b")[1][0]
    assert "not a diagram" in db.replace_diagram_graph(conn, note, NODES, EDGES)[1][0]
    assert db.relayout_diagram(conn, note) == 0
    assert db.get_diagram(conn, note) is None


def test_deleting_a_node_takes_its_edges_and_links_with_it(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    assert db.add_node_link(conn, uid, "write", note) == (True, [])

    assert db.delete_diagram_node(conn, uid, "write") == (True, [])
    d = db.get_diagram(conn, uid)
    assert "write" not in {n["key"] for n in d["nodes"]}
    assert not [e for e in d["edges"] if "write" in (e["from"], e["to"])]
    assert d["links"] == []
    assert "no node" in db.delete_diagram_node(conn, uid, "write")[1][0]


def test_replace_keeps_surviving_positions_and_drops_stale_links(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    db.add_node_link(conn, uid, "write", note)
    db.set_node_positions(conn, uid, {"load": (321.0, 654.0)})

    trimmed = [n for n in NODES if n["key"] != "write"]
    ok, errors = db.replace_diagram_graph(conn, uid, trimmed, [
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "done"},
    ])
    assert (ok, errors) == (True, [])
    pos = _nodes(conn, uid)
    assert (pos["load"]["x"], pos["load"]["y"]) == (321.0, 654.0)  # drag survived
    assert db.get_diagram(conn, uid)["links"] == []                # link went with 'write'


def test_replace_rejects_a_broken_graph_without_touching_the_stored_one(conn):
    uid = _mk(conn)
    before = db.get_diagram(conn, uid)
    ok, errors = db.replace_diagram_graph(conn, uid, [{"key": "a", "label": "x"}], [])
    assert (ok, bool(errors)) == (False, True)
    assert db.get_diagram(conn, uid)["nodes"] == before["nodes"]


def test_set_diagram_meta_regenerates_the_projection(conn):
    uid = _mk(conn)
    assert db.set_diagram_meta(conn, uid, title="Nightly export", summary="One file per store.") == (True, [])
    content = db.get_memory(conn, uid)["content"]
    assert content.startswith("DIAGRAM: Nightly export\n")
    assert "SUMMARY: One file per store." in content
    assert "title" in db.set_diagram_meta(conn, uid, title="")[1][0]


# ----------------------------------------------------------------- node links

def test_node_links_resolve_both_ways(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact", domain="proj-1042")
    assert db.add_node_link(conn, uid, "load", note, "explains") == (True, [])

    link = db.get_node_links(conn, uid)[0]
    assert (link["node_key"], link["target_uid"], link["target_type"]) == ("load", note, "note")

    back = db.diagrams_referencing(conn, note)[0]
    assert (back["memory_uid"], back["node_key"], back["label"]) == (uid, "load", "Read the export window")


def test_node_link_upsert_replaces_the_relation_type(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    db.add_node_link(conn, uid, "load", note, "explains")
    db.add_node_link(conn, uid, "load", note, "contradicts")
    links = db.get_node_links(conn, uid)
    assert len(links) == 1 and links[0]["relation_type"] == "contradicts"


def test_node_link_endpoints_are_validated(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    assert "no node" in db.add_node_link(conn, uid, "ghost", note)[1][0]
    assert "unknown target" in db.add_node_link(conn, uid, "load", "nope")[1][0]
    assert "itself" in db.add_node_link(conn, uid, "load", uid)[1][0]
    assert db.delete_node_link(conn, uid, "load", note) is False


# --------------------------------------------------------------------- jumps
#
# A jump is one row read from two sides: the flow that hands off and the
# flow that takes over are both documented by the same statement.


def _pair(conn):
    return _mk(conn), _mk(conn, title="Store reconciliation routine")


def test_a_jump_reads_from_both_ends(conn):
    a, b = _pair(conn)
    assert db.add_diagram_jump(conn, a, "write", b, "load", label="per store") == (True, [])

    out = db.get_diagram(conn, a)["jumps"]
    assert len(out) == 1
    assert (out[0]["direction"], out[0]["node_key"]) == ("out", "write")
    assert (out[0]["peer_uid"], out[0]["peer_node"]) == (b, "load")
    assert out[0]["peer_title"] == "Store reconciliation routine"
    # the label of the step being jumped TO, so the row names a destination
    # a reader recognises instead of a node key
    assert out[0]["peer_node_label"] == "Read the export window"

    back = db.get_diagram(conn, b)["jumps"]
    assert len(back) == 1
    # the same row, mirrored: what to select on arrival is the step it left
    assert (back[0]["direction"], back[0]["node_key"]) == ("in", "load")
    assert (back[0]["peer_uid"], back[0]["peer_node"]) == (a, "write")
    assert back[0]["peer_node_label"] == "Write one file per store"


def test_a_whole_diagram_jump_lands_on_no_step(conn):
    a, b = _pair(conn)
    assert db.add_diagram_jump(conn, a, "done", b) == (True, [])
    incoming = db.get_diagram(conn, b)["jumps"][0]
    assert incoming["node_key"] == ""       # the diagram as a whole, not a step
    assert incoming["peer_node"] == "done"


def test_jump_endpoints_are_validated(conn):
    a, b = _pair(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    assert "draw an edge" in db.add_diagram_jump(conn, a, "write", a)[1][0]
    assert "not a diagram" in db.add_diagram_jump(conn, a, "write", note)[1][0]
    assert "not a diagram" in db.add_diagram_jump(conn, note, "write", b)[1][0]
    assert "no node 'ghost'" in db.add_diagram_jump(conn, a, "ghost", b)[1][0]
    assert "no node 'ghost'" in db.add_diagram_jump(conn, a, "write", b, "ghost")[1][0]
    assert db.delete_diagram_jump(conn, a, "write", b, "load") is False


def test_jump_upsert_replaces_the_label(conn):
    a, b = _pair(conn)
    db.add_diagram_jump(conn, a, "write", b, "load", label="per store")
    db.add_diagram_jump(conn, a, "write", b, "load", label="one file per store")
    jumps = db.get_diagram(conn, a)["jumps"]
    assert len(jumps) == 1 and jumps[0]["label"] == "one file per store"


def test_a_jump_can_be_cut_from_the_receiving_end(conn):
    """Otherwise the only way out of an unwanted incoming jump is to go
    and open whichever diagram made it."""
    a, b = _pair(conn)
    db.add_diagram_jump(conn, a, "write", b, "load")
    assert db.delete_diagram_jump(conn, b, "load", a, "write") is True
    assert db.get_diagram(conn, a)["jumps"] == []


def test_deleting_a_step_takes_its_jumps_from_either_side(conn):
    a, b = _pair(conn)
    db.add_diagram_jump(conn, a, "write", b, "load")   # b's step is the target
    db.add_diagram_jump(conn, b, "check", a, "done")   # b's step is the source

    assert db.delete_diagram_node(conn, b, "load") == (True, [])
    assert [j["node_key"] for j in db.get_diagram(conn, a)["jumps"]] == ["done"]
    assert db.delete_diagram_node(conn, b, "check") == (True, [])
    assert db.get_diagram(conn, a)["jumps"] == []


def test_replacing_a_graph_drops_jumps_whose_step_is_gone(conn):
    a, b = _pair(conn)
    db.add_diagram_jump(conn, a, "write", b, "write")  # dropped by the rewrite below
    db.add_diagram_jump(conn, a, "load", b)           # aimed at b as a whole; survives

    trimmed = [n for n in NODES if n["key"] != "write"]
    ok, errors = db.replace_diagram_graph(conn, b, trimmed, [
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "done"},
    ])
    assert (ok, errors) == (True, [])
    assert [j["node_key"] for j in db.get_diagram(conn, a)["jumps"]] == ["load"]


def test_overview_counts_jumps_at_both_ends(conn):
    a, b = _pair(conn)
    db.add_diagram_jump(conn, a, "write", b, "load")
    counts = {d["uid"]: d["jumps"] for d in db.diagram_overview(conn)}
    assert (counts[a], counts[b]) == (1, 1)


# --------------------------------------------------------------------- cascade

def test_purge_clears_every_diagram_table(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    db.add_node_link(conn, uid, "load", note)

    assert db.purge_memory(conn, uid) is True
    for table in ("diagrams", "diagram_nodes", "diagram_edges", "diagram_node_links"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_purging_a_diagram_takes_the_jumps_pointing_at_it(conn):
    a, b = _pair(conn)
    db.add_diagram_jump(conn, a, "write", b, "load")
    db.add_diagram_jump(conn, b, "check", a, "done")

    assert db.purge_memory(conn, b) is True
    assert db.get_diagram(conn, a)["jumps"] == []
    assert conn.execute("SELECT COUNT(*) FROM diagram_jumps").fetchone()[0] == 0


def test_purging_a_linked_memory_leaves_no_dangling_node_link(conn):
    uid = _mk(conn)
    note = db.insert_memory(conn, type="note", content="a fact")
    db.add_node_link(conn, uid, "load", note)

    assert db.purge_memory(conn, note) is True
    assert db.get_diagram(conn, uid)["links"] == []
    assert conn.execute("SELECT COUNT(*) FROM diagram_nodes").fetchone()[0] == len(NODES)


# ------------------------------------------------------------------- retrieval

def test_search_finds_a_diagram_by_a_node_label(conn):
    uid = _mk(conn, domain="proj-1042")
    hits = db.search_memories(conn, "export window")
    assert uid in [r["uid"] for r in hits]


def test_search_finds_a_diagram_by_its_tags_and_summary(conn):
    """Both are stored on the memory row / in the projection, so both index."""
    uid = _mk(conn, tags="export,nightly,store-files", summary="Ships one file per store.")
    for query in ("store-files", "Ships one file", "Nightly export routine"):
        assert uid in [r["uid"] for r in db.search_memories(conn, query)], query


def test_a_matching_diagram_comes_back_ranked_like_anything_else(conn):
    """No type tier and no `rank_reason`: a flow sits where its scores put it."""
    note = db.insert_memory(conn, type="note",
                            content="The export window is inclusive on both ends.")
    uid = _mk(conn, summary="Reads the export window and ships one file per store.")

    hits = db.search_ranked(conn, "export window")
    uids = [h["uid"] for h in hits]
    assert uid in uids and note in uids
    assert all("rank_reason" not in h for h in hits)


def test_a_diagram_that_lost_the_ranking_is_not_backfilled_in(conn):
    """No retrieval goes looking for a type, so a flow the query never hit
    is not injected into the results."""
    for i in range(12):
        db.insert_memory(conn, type="note",
                         content=f"export window note number {i}: inclusive on both ends")
    # deliberately shares no word with the query, so this flow can only
    # reach the results if something lifts a type into them
    _mk(conn, title="Cache warmup flow", summary="How the cache warms up.", nodes=[
        {"key": "start", "shape": "start", "label": "warmup starts"},
        {"key": "done", "shape": "end", "label": "done"}],
        edges=[{"from": "start", "to": "done"}])

    hits = db.search_ranked(conn, "export window", limit=3)
    assert len(hits) == 3
    assert {h["type"] for h in hits} == {"note"}


def test_confidence_no_longer_changes_a_diagrams_position(conn):
    """It only ever mattered because promotion had to exclude a flow that had
    stopped being the truth. Ranking is scores now, for every type."""
    uid = _mk(conn, summary="Reads the export window.")
    before = [h["uid"] for h in db.search_ranked(conn, "export window")]
    db.set_confidence(conn, uid, "contradicted")
    assert [h["uid"] for h in db.search_ranked(conn, "export window")] == before


def test_a_type_filter_still_excludes_diagrams(conn):
    """recall() is search(type='note') -- the scope, not a ranking preference,
    is what keeps a flow out of it."""
    _mk(conn, summary="Reads the export window.")
    note = db.insert_memory(conn, type="note", content="The export window is inclusive.")
    hits = db.search_ranked(conn, "export window", type="note")
    assert [h["uid"] for h in hits] == [note]



def test_api_lookup_ranks_by_relevance(client):
    """The picker is choosing any memory to attach, and now so is everything
    else: the better match leads."""
    client.post("/api/memories", json={
        "title": "Export window bounds", "type": "note",
        "content": "The export window is inclusive on both ends."})
    _create(client, summary="Reads the export window.")
    items = client.get("/api/lookup?q=export+window").json()["items"]
    assert items and items[0]["type"] == "note"


def test_diagrams_never_become_dedup_candidates(conn):
    """Their content is generated, so a prose merge could never be applied."""
    _mk(conn, title="Export routine one")
    _mk(conn, title="Export routine two")
    assert db.dedup_candidates(conn, threshold=0.1) == []
    assert db.dedup_candidates(conn, type="diagram", threshold=0.1) == []



# --------------------------------------------------------------------- mermaid

def test_mermaid_uses_a_shape_per_node_kind(conn):
    src = db.render_diagram_mermaid(conn, _mk(conn))
    assert "flowchart TD" in src
    assert 'start(["Receive the schedule trigger"])' in src
    assert 'load["Read the export window"]' in src
    assert 'check{"Any rows in the window?"}' in src
    assert 'write[/"Write one file per store"/]' in src
    assert 'check -->|"yes"| write' in src
    assert "    start --> load" in src


def test_mermaid_renames_a_node_keyed_with_a_reserved_word(conn):
    """'end' closes a subgraph -- left bare it breaks the whole diagram."""
    uid = _mk(conn, nodes=[
        {"key": "start", "shape": "start", "label": "Begin"},
        {"key": "end", "shape": "end", "label": "Finish"},
    ], edges=[{"from": "start", "to": "end"}])
    src = db.render_diagram_mermaid(conn, uid)
    assert 'n_end(["Finish"])' in src
    assert "start --> n_end" in src
    assert "\n    end(" not in src


def test_mermaid_escapes_a_quote_in_a_label(conn):
    uid = _mk(conn, nodes=[
        {"key": "start", "shape": "start", "label": 'Read the "window" setting'},
    ], edges=[])
    assert '#quot;window#quot;' in db.render_diagram_mermaid(conn, uid)


def test_renderers_return_empty_for_a_non_diagram(conn):
    note = db.insert_memory(conn, type="note", content="a fact")
    assert db.render_diagram_text(conn, note) == ""
    assert db.render_diagram_mermaid(conn, note) == ""


# ------------------------------------------------------------------ mcp layer

@pytest.fixture
def mcp(tmp_path, monkeypatch):
    """The MCP tools call db.connect() with no path, so point MEMAI_HOME at tmp."""
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    from memai import server
    return server


def test_mcp_builds_a_diagram_across_several_calls(mcp):
    uid = mcp.diagram(
        title="Nightly export routine", nodes=NODES[:3], edges=EDGES[:2], domain="proj-1042",
    )["uid"]
    assert mcp.diagram_node(uid, "write", label="Write one file per store", shape="io") == {
        "ok": True, "node_key": "write",
    }
    assert mcp.diagram_edge(uid, "check", "write", label="yes") == {"ok": True}
    assert mcp.diagram_node(uid, "write", delete=True) == {"ok": True, "node_key": "write"}
    assert "write" not in mcp.get_diagram(uid, format="text")["body"]


def test_mcp_reports_validation_errors_instead_of_raising(mcp):
    res = mcp.diagram(title="T", nodes=[{"key": "a", "label": "x"}], edges=[])
    assert res["ok"] is False and "no 'start' node" in res["errors"][0]
    assert "not a diagram" in mcp.get_diagram("nope")["errors"][0]
    # 'pdf' is the unknown format here -- 'svg' is a real one
    assert "unknown format" in mcp.get_diagram("nope", format="pdf")["errors"][0]


def test_mcp_get_diagram_formats(mcp):
    uid = mcp.diagram(title="Nightly export routine", nodes=NODES, edges=EDGES)["uid"]

    assert mcp.get_diagram(uid, format="mermaid")["body"].splitlines()[0] == "---"
    assert mcp.get_diagram(uid, format="text")["body"].startswith("DIAGRAM:")

    data = mcp.get_diagram(uid, format="json")
    assert data["format"] == "json"
    # json is the one format carrying the stored arrangement
    assert all(isinstance(n["x"], float) for n in data["nodes"])


def test_mcp_refuses_a_hand_written_diagram_content(mcp):
    uid = mcp.diagram(title="Nightly export routine", nodes=NODES, edges=EDGES)["uid"]
    res = mcp.edit_memory(uid, "hand written")
    assert res["ok"] is False and "generated from the graph" in res["errors"][0]
    assert mcp.get_memory(uid)["content"].startswith("DIAGRAM:")


def test_mcp_get_memory_shows_the_link_from_both_ends(mcp):
    uid = mcp.diagram(title="Nightly export routine", nodes=NODES, edges=EDGES)["uid"]
    note = mcp.note(title="Export window bounds",
                    content="Export window is inclusive on both ends.")["uid"]
    assert mcp.diagram_link(uid, "load", note) == {"ok": True}

    diagram_view = mcp.get_memory(uid)
    assert "flowchart TD" in diagram_view["mermaid"]
    assert diagram_view["node_links"][0]["target_uid"] == note

    note_view = mcp.get_memory(note)
    assert note_view["referenced_by_diagrams"][0]["memory_uid"] == uid
    assert mcp.diagram_link(uid, "load", note, delete=True) == {"ok": True}
    assert mcp.diagram_link(uid, "load", note, delete=True)["ok"] is False


def test_mcp_jump_is_visible_on_both_diagrams(mcp):
    a = mcp.diagram(title="Nightly export routine", nodes=NODES, edges=EDGES)["uid"]
    b = mcp.diagram(title="Store reconciliation routine", nodes=NODES, edges=EDGES)["uid"]
    assert mcp.diagram_jump(a, "write", b, "load", label="per store") == {"ok": True}

    assert mcp.get_memory(a)["jumps"][0]["direction"] == "out"
    assert mcp.get_memory(b)["jumps"][0]["node_key"] == "load"
    assert mcp.get_diagram(b, format="json")["jumps"][0]["peer_uid"] == a

    assert "draw an edge" in mcp.diagram_jump(a, "write", a)["errors"][0]
    assert mcp.diagram_jump(b, "load", a, "write", delete=True) == {"ok": True}
    assert mcp.diagram_jump(b, "load", a, "write", delete=True)["ok"] is False


def test_mcp_pulse_names_diagrams_without_inlining_them(mcp):
    uid = mcp.diagram(
        title="Nightly export routine", nodes=NODES, edges=EDGES, domain="proj-1042",
    )["uid"]
    entry = mcp.pulse(domain="proj-1042")["diagrams"][0]
    assert entry == {"uid": uid, "domain": "proj-1042", "title": "Nightly export routine"}
    assert "content" not in entry  # a whole graph would swamp a warm-up


def test_mcp_relayout_reports_what_it_moved(mcp):
    uid = mcp.diagram(title="Nightly export routine", nodes=NODES, edges=EDGES)["uid"]
    assert mcp.diagram_relayout(uid) == {"ok": True, "nodes": len(NODES)}
    assert mcp.diagram_relayout("nope") == {"ok": False, "nodes": 0}


def test_capped_body_points_at_the_dashboard(mcp):
    assert mcp._capped("x" * 10) == "x" * 10
    long = mcp._capped("x" * (db.DIAGRAM_BODY_BUDGET + 500))
    assert len(long) < db.DIAGRAM_BODY_BUDGET + 200
    assert "admin dashboard" in long


def test_every_registered_tool_is_documented_by_help(mcp):
    """help() reads _TOOLS, so a tool missing from it is invisible to agents."""
    registered = set(mcp.mcp._tool_manager._tools)
    assert registered - set(mcp._TOOLS) == set()
    assert {"diagram", "diagram_node", "diagram_edge",
            "diagram_link", "diagram_relayout", "get_diagram"} <= set(mcp.help()["tools"])
    # help() splits the summary on the first newline; it has to stand alone
    for name in mcp._TOOLS:
        assert mcp.help(command=name)["doc"].split("\n", 1)[0].strip()


# ------------------------------------------------------------------ admin api

def _create(client, **kw):
    body = {"title": "Nightly export routine", "nodes": NODES, "edges": EDGES, **kw}
    res = client.post("/api/diagrams", json=body)
    assert res.status_code == 200, res.json()
    return res.json()["uid"]


def test_api_creates_and_reads_a_diagram(client):
    uid = _create(client, domain="proj-1042", summary="One file per store.")
    data = client.get(f"/api/diagrams/{uid}").json()
    assert data["title"] == "Nightly export routine"
    assert [n["key"] for n in data["nodes"]] == [n["key"] for n in NODES]
    assert "flowchart TD" in data["mermaid"]
    assert all(isinstance(n["x"], float) for n in data["nodes"])


def test_api_rejects_a_broken_graph_with_400(client):
    res = client.post("/api/diagrams", json={
        "title": "T", "nodes": [{"key": "a", "label": "x"}], "edges": [],
    })
    assert res.status_code == 400
    assert "no 'start' node" in res.json()["error"]


def test_api_unknown_diagram_is_400(client):
    for path in ("", "/mermaid"):
        assert client.get(f"/api/diagrams/nope{path}").status_code == 400
    assert client.post("/api/diagrams/nope/relayout", json={}).status_code == 400


def test_api_node_and_edge_roundtrip(client):
    uid = _create(client)
    assert client.post(f"/api/diagrams/{uid}/node", json={
        "key": "audit", "label": "Append to the audit log"}).status_code == 200
    assert client.post(f"/api/diagrams/{uid}/edge", json={
        "from": "write", "to": "audit"}).status_code == 200

    data = client.get(f"/api/diagrams/{uid}").json()
    assert "audit" in {n["key"] for n in data["nodes"]}

    assert client.post(f"/api/diagrams/{uid}/edge", json={
        "from": "write", "to": "audit", "delete": True}).status_code == 200
    assert client.post(f"/api/diagrams/{uid}/node", json={
        "key": "audit", "delete": True}).status_code == 200
    assert "audit" not in {n["key"] for n in client.get(f"/api/diagrams/{uid}").json()["nodes"]}


def test_api_node_requires_a_key(client):
    uid = _create(client)
    assert client.post(f"/api/diagrams/{uid}/node", json={"label": "x"}).status_code == 400
    assert client.post(f"/api/diagrams/{uid}/edge", json={"from": "write"}).status_code == 400


def test_api_layout_persists_a_drag(client):
    uid = _create(client)
    res = client.post(f"/api/diagrams/{uid}/layout", json={
        "positions": {"load": {"x": 42, "y": 84}}})
    assert res.json() == {"ok": True, "moved": 1}

    node = next(n for n in client.get(f"/api/diagrams/{uid}").json()["nodes"] if n["key"] == "load")
    assert (node["x"], node["y"]) == (42.0, 84.0)

    # ...and a reload sees the same thing
    again = next(n for n in client.get(f"/api/diagrams/{uid}").json()["nodes"] if n["key"] == "load")
    assert (again["x"], again["y"]) == (42.0, 84.0)


def test_api_layout_needs_a_positions_object(client):
    uid = _create(client)
    assert client.post(f"/api/diagrams/{uid}/layout", json={}).status_code == 400
    assert client.post(f"/api/diagrams/{uid}/layout",
                       json={"positions": [["load", 1, 2]]}).status_code == 400


def test_api_layout_carries_a_resize_and_can_undo_it(client):
    uid = _create(client)
    res = client.post(f"/api/diagrams/{uid}/layout", json={
        "positions": {"check": {"x": 0, "y": 0, "w": 320, "h": 140}}})
    assert res.json() == {"ok": True, "moved": 1}
    node = lambda: next(n for n in client.get(f"/api/diagrams/{uid}").json()["nodes"]
                        if n["key"] == "check")
    assert (node()["w"], node()["h"]) == (320.0, 140.0)

    assert client.post(f"/api/diagrams/{uid}/layout",
                       json={"reset_boxes": ["check"]}).json()["moved"] == 1
    assert (node()["w"], node()["h"]) == (None, None)


def test_api_meta_sets_the_font_scale(client):
    uid = _create(client)
    assert client.post(f"/api/diagrams/{uid}/meta", json={"font_scale": 1.25}).status_code == 200
    assert client.get(f"/api/diagrams/{uid}").json()["font_scale"] == 1.25
    assert client.post(f"/api/diagrams/{uid}/meta", json={"font_scale": "big"}).status_code == 400


def test_api_marks_which_edges_close_a_loop(client):
    uid = _create(client)
    client.post(f"/api/diagrams/{uid}/edge", json={"from": "done", "to": "load"})
    edges = client.get(f"/api/diagrams/{uid}").json()["edges"]
    assert {(e["from"], e["to"]) for e in edges if e["loops"]} == {("done", "load")}


def test_api_relayout_resets_a_drag(client):
    uid = _create(client)
    client.post(f"/api/diagrams/{uid}/layout", json={"positions": {"load": {"x": 900, "y": 900}}})
    assert client.post(f"/api/diagrams/{uid}/relayout", json={}).json()["moved"] == len(NODES)
    node = next(n for n in client.get(f"/api/diagrams/{uid}").json()["nodes"] if n["key"] == "load")
    assert (node["x"], node["y"]) != (900.0, 900.0)


def test_api_graph_replace_keeps_positions(client):
    uid = _create(client)
    client.post(f"/api/diagrams/{uid}/layout", json={"positions": {"load": {"x": 11, "y": 22}}})
    res = client.post(f"/api/diagrams/{uid}/graph", json={
        "nodes": [n for n in NODES if n["key"] != "write"],
        "edges": [{"from": "start", "to": "load"}, {"from": "load", "to": "check"},
                  {"from": "check", "to": "done"}],
    })
    assert res.status_code == 200
    node = next(n for n in client.get(f"/api/diagrams/{uid}").json()["nodes"] if n["key"] == "load")
    assert (node["x"], node["y"]) == (11.0, 22.0)


def test_api_meta_update(client):
    uid = _create(client)
    assert client.post(f"/api/diagrams/{uid}/meta", json={"title": "Nightly export"}).status_code == 200
    assert client.get(f"/api/diagrams/{uid}").json()["title"] == "Nightly export"
    assert client.post(f"/api/diagrams/{uid}/meta", json={}).status_code == 400
    assert client.post(f"/api/diagrams/{uid}/meta", json={"title": " "}).status_code == 400


def test_api_link_carries_a_peer_card(client):
    uid = _create(client)
    note = client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "a fact"}).json()["uid"]
    assert client.post(f"/api/diagrams/{uid}/link", json={
        "node_key": "load", "target_uid": note}).status_code == 200

    link = client.get(f"/api/diagrams/{uid}").json()["links"][0]
    assert link["peer"]["uid"] == note and link["peer"]["type"] == "note"
    assert "target_content" not in link  # the card already carries a snippet

    # and the note's own detail view knows which flows depend on it
    detail = client.get(f"/api/memories/{note}").json()
    assert detail["referenced_by_diagrams"][0]["node_key"] == "load"

    assert client.post(f"/api/diagrams/{uid}/link", json={
        "node_key": "load", "target_uid": note, "delete": True}).status_code == 200
    assert client.post(f"/api/diagrams/{uid}/link", json={
        "node_key": "load", "target_uid": note, "delete": True}).status_code == 400


def test_api_jump_round_trips_from_either_end(client):
    a = _create(client)
    b = _create(client, title="Store reconciliation routine")
    assert client.post(f"/api/diagrams/{a}/jump", json={
        "node_key": "write", "peer_uid": b, "peer_node": "load",
        "label": "per store"}).status_code == 200

    out = client.get(f"/api/diagrams/{a}").json()["jumps"][0]
    assert (out["direction"], out["node_key"], out["peer_node"]) == ("out", "write", "load")
    incoming = client.get(f"/api/diagrams/{b}").json()["jumps"][0]
    assert (incoming["direction"], incoming["node_key"]) == ("in", "load")

    # cut from the side that did not create it
    assert client.post(f"/api/diagrams/{b}/jump", json={
        "node_key": "load", "peer_uid": a, "peer_node": "write",
        "delete": True}).status_code == 200
    assert client.get(f"/api/diagrams/{a}").json()["jumps"] == []
    assert client.post(f"/api/diagrams/{b}/jump", json={
        "node_key": "load", "peer_uid": a, "peer_node": "write",
        "delete": True}).status_code == 400
    assert client.post(f"/api/diagrams/{a}/jump", json={"node_key": "write"}).status_code == 400


def test_api_whole_diagram_jump_is_cut_from_the_receiving_end(client):
    """That end has no step to name -- the jump arrives at the diagram -- and
    it is still the end most likely to want the jump gone."""
    a = _create(client)
    b = _create(client, title="Store reconciliation routine")
    client.post(f"/api/diagrams/{a}/jump", json={"node_key": "done", "peer_uid": b})

    incoming = client.get(f"/api/diagrams/{b}").json()["jumps"][0]
    assert incoming["node_key"] == ""
    assert client.post(f"/api/diagrams/{b}/jump", json={
        "node_key": "", "peer_uid": a, "peer_node": "done",
        "delete": True}).status_code == 200
    assert client.get(f"/api/diagrams/{a}").json()["jumps"] == []

    # creating one still has to say which step it leaves from
    assert client.post(f"/api/diagrams/{a}/jump", json={"peer_uid": b}).status_code == 400
    assert client.post(f"/api/diagrams/{a}/jump", json={"node_key": "done"}).status_code == 400


def test_api_clean_orphans_drops_a_jump_to_a_missing_step(client):
    a = _create(client)
    b = _create(client, title="Store reconciliation routine")
    client.post(f"/api/diagrams/{a}/jump", json={
        "node_key": "write", "peer_uid": b, "peer_node": "load"})

    # forge the desync a crash mid-delete could leave behind
    with db.connect() as conn:
        conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ? AND node_key = 'load'", (b,))

    assert client.post("/api/maintenance/clean-orphans", json={}).json()["jumps_removed"] == 1
    assert client.get(f"/api/diagrams/{a}").json()["jumps"] == []


def test_api_memory_detail_embeds_the_graph(client):
    uid = _create(client)
    detail = client.get(f"/api/memories/{uid}").json()
    assert detail["type"] == "diagram"
    assert [n["key"] for n in detail["diagram"]["nodes"]] == [n["key"] for n in NODES]


def test_api_refuses_a_hand_written_diagram_content(client):
    uid = _create(client)
    res = client.post(f"/api/memories/{uid}/content", json={"content": "hand written"})
    assert res.status_code == 400
    assert "generated from the graph" in res.json()["error"]


def test_api_refuses_creating_a_diagram_as_a_plain_memory(client):
    """It would leave a memory whose content nothing regenerates."""
    res = client.post("/api/memories", json={"title": "fixture title", "type": "diagram", "content": "x"})
    assert res.status_code == 400
    assert "POST /api/diagrams" in res.json()["error"]


def test_api_type_allowlist_is_enforced(client):
    assert client.post("/api/memories", json={"title": "fixture title", "type": "wat", "content": "x"}).status_code == 400
    uid = client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "x"}).json()["uid"]
    assert client.post(f"/api/memories/{uid}/meta", json={"title": "fixture title", "type": "wat"}).status_code == 400
    # handoff, not reasoning: claiming a type that has fields is a write of
    # that shape, and this body has none of them
    assert client.post(f"/api/memories/{uid}/meta", json={"title": "fixture title", "type": "handoff"}).status_code == 200


def test_api_refuses_retyping_across_the_diagram_boundary(client):
    uid = _create(client)
    res = client.post(f"/api/memories/{uid}/meta", json={"title": "fixture title", "type": "note"})
    assert res.status_code == 400 and "cannot be changed" in res.json()["error"]

    note = client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "x"}).json()["uid"]
    assert client.post(f"/api/memories/{note}/meta", json={"title": "fixture title", "type": "diagram"}).status_code == 400


def test_api_purge_clears_the_graph(client):
    uid = _create(client)
    res = client.post(f"/api/memories/{uid}/purge", json={"confirm": f"DELETE {uid}"})
    assert res.status_code == 200
    assert client.get(f"/api/diagrams/{uid}").status_code == 400


def test_api_clean_orphans_drops_a_link_to_a_missing_node(client):
    uid = _create(client)
    note = client.post("/api/memories", json={"title": "fixture title", "type": "note", "content": "a fact"}).json()["uid"]
    client.post(f"/api/diagrams/{uid}/link", json={"node_key": "load", "target_uid": note})

    # forge the desync a crash mid-delete could leave behind
    with db.connect() as conn:
        conn.execute("DELETE FROM diagram_nodes WHERE memory_uid = ? AND node_key = 'load'", (uid,))

    assert client.post("/api/maintenance/clean-orphans", json={}).json()["node_links_removed"] == 1
    assert client.get(f"/api/diagrams/{uid}").json()["links"] == []


def test_api_overview_counts_the_new_type(client):
    _create(client)
    assert client.get("/api/overview").json()["by_type"].get("diagram") == 1


# --------------------------------------------------------- structural upkeep

def _issue_kinds(overview_entry) -> set[str]:
    return {i["kind"] for i in overview_entry["issues"]}


def test_a_well_formed_flow_reports_no_issues(conn):
    uid = _mk(conn)
    entry = next(d for d in db.diagram_overview(conn) if d["uid"] == uid)
    assert entry["issues"] == []
    assert (entry["nodes"], entry["edges"]) == (len(NODES), len(EDGES))
    assert entry["documented"] == 1  # only 'load' carries a note


def test_overview_flags_a_step_nothing_reaches(conn):
    """Reachable only through the incremental writers -- which is the point."""
    uid = _mk(conn)
    db.upsert_diagram_node(conn, uid, "audit", label="Append to the audit log")
    entry = next(d for d in db.diagram_overview(conn) if d["uid"] == uid)
    unreachable = next(i for i in entry["issues"] if i["kind"] == "unreachable")
    assert unreachable["keys"] == ["audit"]
    assert "dead_end" in _issue_kinds(entry)   # nothing leaves it either


def test_overview_stays_quiet_about_an_unlabelled_fork(conn):
    """A fall-through arrow with no condition on it is normal, not a defect."""
    uid = _mk(conn, edges=[
        {"from": "start", "to": "load"},
        {"from": "load", "to": "check"},
        {"from": "check", "to": "write", "label": "yes"},
        {"from": "check", "to": "done"},          # no condition on this arrow
        {"from": "write", "to": "done"},
    ])
    entry = next(d for d in db.diagram_overview(conn) if d["uid"] == uid)
    assert entry["issues"] == []


def test_overview_flags_a_missing_start_or_end(conn):
    uid = _mk(conn)
    db.upsert_diagram_node(conn, uid, "start", shape="step")
    kinds = _issue_kinds(next(d for d in db.diagram_overview(conn) if d["uid"] == uid))
    assert "no_start" in kinds

    other = _mk(conn, nodes=[
        {"key": "start", "shape": "start", "label": "Begin"},
        {"key": "loop", "label": "Keep polling"},
    ], edges=[{"from": "start", "to": "loop"}, {"from": "loop", "to": "start"}])
    kinds = _issue_kinds(next(d for d in db.diagram_overview(conn) if d["uid"] == other))
    assert kinds == {"no_end"}   # a daemon loop: no end, but no dead end either


def test_overview_scopes_by_domain_and_status(conn):
    a = _mk(conn, domain="proj-1042")
    b = _mk(conn, domain="proj-2077")
    assert [d["uid"] for d in db.diagram_overview(conn, domain="proj-1042")] == [a]
    db.set_status(conn, b, "archived")
    assert b not in [d["uid"] for d in db.diagram_overview(conn)]
    assert b in [d["uid"] for d in db.diagram_overview(conn, status="")]


def test_api_diagram_list(client):
    uid = _create(client, domain="proj-1042", summary="One file per store.")
    data = client.get("/api/diagrams").json()
    assert data["total"] == 1 and data["with_issues"] == 0
    item = data["items"][0]
    assert (item["uid"], item["title"]) == (uid, "Nightly export routine")
    assert item["nodes"] == len(NODES)

    client.post(f"/api/diagrams/{uid}/node", json={"key": "audit", "label": "Append to the log"})
    data = client.get("/api/diagrams").json()
    assert data["with_issues"] == 1
    assert "unreachable" in {i["kind"] for i in data["items"][0]["issues"]}

    assert client.get("/api/diagrams?domain=nope").json()["total"] == 0


def test_every_referenced_icon_is_defined(client):
    """`<svg data-icon="x">` is filled in from core/icons.js at boot, so a
    name that module does not define is an icon that silently never draws.

    Textual, like the import check below: it reads the ICONS keys off the
    object literal rather than running the module.
    """
    import re

    icons_js = (WEBUI_SRC / "core" / "icons.js").read_text(encoding="utf-8")
    defined = set(re.findall(r"^ {2}'?([\w-]+)'?:\s*\{", icons_js, re.M))
    assert {"brand-seal", "overview", "graph", "search"} <= defined, defined

    used = set()
    for path in [WEBUI_SRC / "index.html", *_own_modules()]:
        src = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""data-icon=["']([\w-]+)["']""", src))
        used |= set(re.findall(r"""\bicon\(\s*['"]([\w-]+)['"]""", src))
    assert used, "expected the shell to reference some icons"
    assert used <= defined, f"undefined icons: {sorted(used - defined)}"


def test_module_imports_resolve(client):
    """A renamed or moved module has to be renamed at its import sites too.

    Cheap textual check -- it does not run the modules, it only asserts
    that every relative import names a file that exists on disk.
    """
    import re

    pattern = re.compile(r"""from\s+['"](\.[^'"]+)['"]""")
    seen = 0
    for path in _own_modules():
        for spec in pattern.findall(path.read_text(encoding="utf-8")):
            target = (path.parent / spec).resolve()
            assert target.is_file(), f"{path.name} imports missing {spec}"
            seen += 1
    assert seen > 20, "expected the modules to import one another"


def test_locale_catalogs_stay_in_parity():
    """A key present in only one catalog silently falls back to English."""
    import json

    i18n = WEBUI_SRC / "public" / "i18n"
    en = json.loads((i18n / "en.json").read_text(encoding="utf-8"))["strings"]
    pt = json.loads((i18n / "pt-BR.json").read_text(encoding="utf-8"))["strings"]
    assert set(en) == set(pt)
    for key in ("type.diagram", "dg.step", "dg.hint.orphans", "dr.openEditor"):
        assert en.get(key) and pt.get(key)
    for shape in db.NODE_SHAPES:
        assert f"dg.shape.{shape}" in en


def test_a_diagram_written_before_the_title_column_is_named_on_connect(tmp_path):
    """The graph's name reaches memories.title when the store is opened.

    A store whose diagrams predate the column carries the name on
    `diagrams` only, and every listing falls back to the generated body.
    """
    path = tmp_path / "old.db"
    with db.connect(path) as conn:
        uid, errors = db.insert_diagram(
            conn, title="Nightly export routine",
            nodes=[{"key": "a", "label": "Start", "shape": "start"},
                   {"key": "b", "label": "Done", "shape": "end"}],
            edges=[{"from": "a", "to": "b"}])
        assert not errors
        # what the store looked like before the column existed
        conn.execute("UPDATE memories SET title = '' WHERE uid = ?", (uid,))
    with db.connect(path) as conn:
        assert db.get_memory(conn, uid)["title"] == "Nightly export routine"


def test_the_backfill_keeps_a_name_longer_than_the_cap(tmp_path):
    """It copies a name the store already holds, so the cap does not apply.

    Refusing the length here would leave that diagram permanently unnamed,
    which is the opposite of what the backfill is for.
    """
    path = tmp_path / "old.db"
    long_name = "Nightly export routine, " + ", ".join(["retries"] * 12)
    assert len(long_name) > db.TITLE_MAX
    with db.connect(path) as conn:
        uid, errors = db.insert_diagram(
            conn, title="Nightly export routine",
            nodes=[{"key": "a", "label": "Start", "shape": "start"},
                   {"key": "b", "label": "Done", "shape": "end"}],
            edges=[{"from": "a", "to": "b"}])
        assert not errors
        conn.execute("UPDATE memories SET title = '' WHERE uid = ?", (uid,))
        conn.execute("UPDATE diagrams SET title = ? WHERE memory_uid = ?", (long_name, uid))
    with db.connect(path) as conn:
        assert db.get_memory(conn, uid)["title"] == long_name


def test_the_backfill_does_not_touch_a_memory_that_has_a_title(tmp_path):
    path = tmp_path / "store.db"
    with db.connect(path) as conn:
        uid = db.insert_memory(conn, type="note", title="A name of its own",
                               content="a fact", domain="acme")
    with db.connect(path) as conn:
        assert db.get_memory(conn, uid)["title"] == "A name of its own"
