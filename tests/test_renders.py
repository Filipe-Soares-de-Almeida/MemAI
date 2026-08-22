"""Generated SVG renders: where they land, and when they are swept away.

A render is a cache. The diagram it came from is the record, so the only
real questions are how much disk it occupies and whether the sweep that
clears it can be trusted. That second one is why the guards get more tests
than the happy path: this deletes files, and MEMAI_HOME is whatever an
environment variable says it is.
"""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest
from starlette.testclient import TestClient

from memai import admin, db

DAY = 86400


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(home):
    with TestClient(admin.app) as c:
        yield c


@pytest.fixture
def mcp(home):
    from memai import server
    return server


NODES = [
    {"key": "start", "label": "Begin the run", "shape": "start",
     "note": "a note, so the render has a tooltip to carry"},
    {"key": "check", "label": "Batch complete?", "shape": "decision"},
    {"key": "write", "label": "Persist the rows", "shape": "io"},
    {"key": "done", "label": "Run finished", "shape": "end"},
]
EDGES = [
    {"from": "start", "to": "check"},
    {"from": "check", "to": "write", "label": "no, retry the whole batch"},
    {"from": "write", "to": "check", "label": "again"},
    {"from": "check", "to": "done", "label": "yes"},
]


def aged(path, days: float) -> None:
    when = time.time() - days * DAY
    path.write_text("<svg/>", encoding="utf-8")
    os.utime(path, (when, when))


# ── the sweep ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(("mode", "survivors"), [
    ("never", {"fresh.svg", "twoday.svg", "tenday.svg", "old.svg", "shell.html"}),
    ("30d", {"fresh.svg", "twoday.svg", "tenday.svg"}),
    ("7d", {"fresh.svg", "twoday.svg"}),
    ("1d", {"fresh.svg"}),
])
def test_retention_keeps_what_is_inside_the_window(home, mode, survivors):
    folder = db.renders_dir()
    for name, days in [("fresh.svg", 0.5), ("twoday.svg", 2),
                       ("tenday.svg", 10), ("old.svg", 60),
                       ("shell.html", 40)]:
        aged(folder / name, days)
    db.prune_renders(mode)
    assert {p.name for p in folder.iterdir() if p.is_file()} == survivors


def test_the_sweep_leaves_alone_what_it_did_not_create(home):
    """Only the two render suffixes, and never a subdirectory.

    The rule is narrow so it can stay a rule. A sweep that decided by age
    alone would take the backups with it the first time someone pointed
    MEMAI_HOME at a directory that had some.
    """
    folder = db.renders_dir()
    aged(folder / "render.svg", 90)
    aged(folder / "notes.txt", 90)
    (folder / "sub").mkdir()
    aged(folder / "sub" / "deep.svg", 90)

    db.prune_renders("1d")

    assert not (folder / "render.svg").exists()
    assert (folder / "notes.txt").exists()
    assert (folder / "sub" / "deep.svg").exists()


def test_the_sweep_never_deletes_the_render_being_written(home):
    """Whatever the retention window, the file this call just produced has
    to survive it -- otherwise a 1-day setting plus a clock skew hands the
    caller a path to a file that is already gone."""
    folder = db.renders_dir()
    aged(folder / "current.svg", 99)
    db.prune_renders("1d", keep=folder / "current.svg")
    assert (folder / "current.svg").exists()


def test_prune_all_ignores_age(home):
    folder = db.renders_dir()
    aged(folder / "fresh.svg", 0)
    aged(folder / "old.svg", 99)
    swept = db.prune_renders_all()
    assert swept["pruned"] == 2 and swept["mode"] == "all"
    assert not any(p.suffix == ".svg" for p in folder.iterdir())


def test_usage_counts_only_renders(home):
    folder = db.renders_dir()
    aged(folder / "a.svg", 1)
    aged(folder / "b.html", 1)
    aged(folder / "c.txt", 1)
    usage = db.renders_usage()
    assert usage["files"] == 2 and usage["bytes"] > 0


def test_retention_setting_roundtrips(home):
    with db.connect() as conn:
        assert db.get_svg_retention(conn) == db.SVG_RETENTION_DEFAULT
        assert db.set_svg_retention(conn, "30d") == "30d"
        assert db.get_svg_retention(conn) == "30d"
        with pytest.raises(ValueError):
            db.set_svg_retention(conn, "forever")


# ── the MCP wire ────────────────────────────────────────────────────────

@pytest.mark.parametrize(("fmt", "suffix"),
                         [("svg", ".svg"), ("svg-interactive", ".html")])
def test_get_diagram_writes_a_file_and_returns_a_thin_payload(mcp, fmt, suffix):
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    out = mcp.get_diagram(uid, format=fmt)

    written = db.renders_dir() / f"diagram-{uid}{suffix}"
    assert out["path"] == str(written)
    assert written.is_file()
    assert out["bytes"] == len(written.read_text(encoding="utf-8").encode())

    # the markup is NOT in the payload -- it went to a file, and a caller
    # that only has to display it should not pay for it
    flat = repr(out)
    assert "<svg" not in flat and "<path" not in flat
    # ...but enough to talk about what was drawn
    assert [n["key"] for n in out["nodes"]] == [n["key"] for n in NODES]
    assert out["edges"] == len(EDGES)
    assert len(out["viewbox"]) == 4
    # and the notes stay in the file rather than being repeated in the reply
    assert "note" not in out["nodes"][0]
    assert "a note" in written.read_text(encoding="utf-8")


def test_get_diagram_reports_what_it_swept(mcp):
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    aged(db.renders_dir() / "stale.svg", 40)
    out = mcp.get_diagram(uid, format="svg")
    assert out["retention"] == db.SVG_RETENTION_DEFAULT
    assert out["pruned"] == 1
    assert not (db.renders_dir() / "stale.svg").exists()


def test_get_diagram_rejects_an_unknown_format(mcp):
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    assert "unknown format" in mcp.get_diagram(uid, format="pdf")["errors"][0]


# ── telling the caller what to do with it ───────────────────────────────
#
# These assert on prose, which is unusual and deliberate: for an MCP tool
# the docstring IS the interface. A caller asked to "render the diagram"
# reached for mermaid and silently showed a re-laid-out flow instead of the
# one the user arranged, because the docstring called mermaid "the fastest
# way to show a flow in a chat client" and buried the SVG path. Wording is
# the mechanism here, so the wording gets tests.
#
# The guidance now lives in two places, and the split matters. What a caller
# needs in order to CHOOSE stays in the docstring, because that is the schema
# and the schema is paid for on every request. The worked detail moved to
# help(command='get_diagram'), which is read when someone reads it. Each half
# is asserted where it belongs.


def test_the_schema_leads_with_how_to_display_it(mcp):
    doc = mcp.get_diagram.__doc__
    assert "svg-interactive" in doc
    # the format that shows the real arrangement, before mermaid is offered
    assert doc.index("svg-interactive") < doc.index("'mermaid'")


def test_the_full_documentation_names_reading_the_file_as_the_step(mcp):
    assert "READ THE FILE" in mcp.help(command="get_diagram")["doc"]


def test_the_help_summary_names_the_format_to_show_it_with(mcp):
    """help() with no argument prints only the first line of each docstring,
    so for this tool that line has to carry the choice, not restate the name."""
    summary = mcp.help()["tools"]["get_diagram"]
    assert "svg-interactive" in summary
    assert mcp.help(command="get_diagram")["doc"].startswith(summary)


def test_the_schema_itself_warns_that_mermaid_relayouts(mcp):
    """The load-bearing warning stays in the description a caller always
    sees, not only in the documentation it has to ask for."""
    assert "DISCARDS the arrangement" in mcp.get_diagram.__doc__


def test_the_full_documentation_explains_the_relayout(mcp):
    # unwrapped: the claim is the wording, not where the lines happen to break
    doc = " ".join(mcp.help(command="get_diagram")["doc"].split())
    assert "own layout" in doc and "discards the stored positions" in doc


def test_mermaid_output_points_at_the_faithful_format(mcp):
    """The docstring is behind the caller by the time it holds a result."""
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    note = mcp.get_diagram(uid)["note"]          # default format
    assert "svg-interactive" in note
    assert "discards the stored positions" in note


@pytest.mark.parametrize(("fmt", "expected"), [
    ("svg-interactive", "put its contents in your reply"),
    ("svg", "send or link this file"),
])
def test_render_payload_names_the_next_step(mcp, fmt, expected):
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    assert expected in mcp.get_diagram(uid, format=fmt)["next_step"]


def test_the_token_cost_is_never_offered_as_a_reason_to_skip_display(mcp):
    """The reason the markup goes to a file must not read as "do not emit it".

    It did once, and a caller followed it exactly: it picked the right
    format, read the file, then attached it instead of showing it and said
    so -- "without spending 12k tokens re-emitting the SVG". The saving is
    about calls that never display; a request to SEE the flow is not one.
    """
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    step = mcp.get_diagram(uid, format="svg-interactive")["next_step"]
    assert "does NOT display anything" in step
    assert "that is the work" in step

    doc = mcp.help(command="get_diagram")["doc"]
    assert "IT IS NOT A REASON TO AVOID EMITTING THE MARKUP" in doc
    assert "is NOT showing it" in doc


def test_interactive_writes_a_document_and_a_fragment(mcp):
    """One file to open, one to embed, because the two cannot be one file.

    Opening needs a document: doctype, charset (the titles carry accents),
    and a body that is not white behind a dark drawing. Embedding needs the
    opposite -- no doctype and no <body>, which inline renderers reject, and
    no styling that would reach the host page. Shipping the document alone
    and asking the caller to cut the middle out of it is the version that
    breaks without saying so.
    """
    uid = mcp.diagram(title="Título com acento", nodes=NODES, edges=EDGES)["uid"]
    out = mcp.get_diagram(uid, format="svg-interactive")

    document = Path(out["path"]).read_text(encoding="utf-8")
    fragment = Path(out["inline_path"]).read_text(encoding="utf-8")

    assert document.startswith("<!doctype html>")
    assert 'charset="utf-8"' in document
    assert "Título com acento" in document
    assert "<body>" in document

    assert not fragment.lstrip().startswith("<!doctype")
    assert "<body" not in fragment
    assert "<html" not in fragment

    # and they are the same drawing, not two renders that could drift
    marker = '<svg id="dg"'
    assert fragment[fragment.index(marker):] in document


def test_the_shell_waits_for_a_measurable_box(mcp):
    """The opening scale is computed FROM the box's own transform matrix.

    Run against a box that has not been laid out, that matrix means nothing
    and the flow opens fitted -- at a scale where the canvas draws no labels
    at all, which is what "renders broken inline" looked like.
    """
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    fragment = Path(
        mcp.get_diagram(uid, format="svg-interactive")["inline_path"]
    ).read_text(encoding="utf-8")
    assert "r.width<2" in fragment            # the guard
    assert "requestAnimationFrame(place)" in fragment
    assert "ResizeObserver" in fragment
    # and the readout starts at the no-script state rather than blank, so a
    # shell that never ran is visible instead of just looking zoomed out
    assert 'id="dg-z">fit<' in fragment


def test_both_render_files_are_swept_by_retention(mcp, home):
    """The fragment is a render too; leaving it out would make the folder
    grow at half the rate it looks like it is growing."""
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    out = mcp.get_diagram(uid, format="svg-interactive")
    for path in (Path(out["path"]), Path(out["inline_path"])):
        os.utime(path, (time.time() - 40 * DAY,) * 2)
    assert db.prune_renders("7d")["pruned"] == 2
    assert db.renders_usage()["files"] == 0


def test_interactive_and_static_draw_the_same_flow(mcp):
    uid = mcp.diagram(title="Nightly export", nodes=NODES, edges=EDGES)["uid"]
    static = mcp.get_diagram(uid, format="svg")
    live = mcp.get_diagram(uid, format="svg-interactive")
    assert static["viewbox"] == live["viewbox"]
    assert live["bytes"] > static["bytes"]      # the shell, and only that


# ── the dashboard ───────────────────────────────────────────────────────

def test_health_reports_render_usage(client, home):
    aged(db.renders_dir() / "a.svg", 1)
    body = client.get("/api/maintenance/health").json()
    assert body["renders"]["files"] == 1
    assert body["renders"]["retention"] == db.SVG_RETENTION_DEFAULT
    assert body["renders"]["path"].endswith("renders")


def test_prune_endpoint_honours_the_window(client, home):
    aged(db.renders_dir() / "fresh.svg", 0)
    aged(db.renders_dir() / "old.svg", 40)
    body = client.post("/api/maintenance/prune-renders", json={}).json()
    assert body["pruned"] == 1
    assert body["before"]["files"] == 2 and body["after"]["files"] == 1


def test_prune_endpoint_can_clear_everything(client, home):
    aged(db.renders_dir() / "fresh.svg", 0)
    aged(db.renders_dir() / "old.svg", 40)
    body = client.post("/api/maintenance/prune-renders",
                       json={"all": True}).json()
    assert body["pruned"] == 2 and body["after"]["files"] == 0


def test_retention_endpoint_roundtrips(client):
    assert client.post(
        "/api/config", json={"svg_retention": "1d"}).json()["svg_retention"] == "1d"
    assert client.get("/api/config").json()["svg_retention"] == "1d"
    assert client.post("/api/config",
                       json={"svg_retention": "forever"}).status_code == 400
