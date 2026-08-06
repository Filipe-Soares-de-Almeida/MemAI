"""Getting the store out as text, and back in.

`VACUUM INTO` already makes a byte-perfect copy, which restores a machine
and answers nothing else: you cannot diff two of them, grep one, or carry
one domain to another store. What matters here is that the round trip
preserves the things a renumbering import would quietly destroy -- uids,
timestamps, the arrangement somebody dragged a diagram into -- and that
importing the same file twice changes nothing.
"""

from __future__ import annotations

import json

import pytest

from memai import db, portable


@pytest.fixture
def source(tmp_path):
    with db.connect(tmp_path / "source.db") as c:
        yield c


@pytest.fixture
def target(tmp_path):
    with db.connect(tmp_path / "target.db") as c:
        yield c


NODES = [
    {"key": "start", "shape": "start", "label": "begin the warmup"},
    {"key": "check", "shape": "decision", "label": "cache cold?", "note": "why it asks"},
    {"key": "done", "shape": "end", "label": "done"},
]
EDGES = [{"from": "start", "to": "check"},
         {"from": "check", "to": "done", "label": "no"}]


def _populate(conn) -> dict:
    ids = {
        "note": db.insert_memory(conn, type="note", domain="acme/x100", tags="cache,warmup",
                                 content="cache warmup runs before the first request",
                                 also="omni/x900", review_after="2027-01-01",
                                 source_ref="src/acme/x100/warmup.py"),
        "old": db.insert_memory(conn, type="note", domain="acme/x100",
                                content="cache warmup used to run hourly"),
    }
    db.add_relation(conn, ids["note"], ids["old"], "supersedes", "corrected")
    ids["flow"], _ = db.insert_diagram(conn, title="Cache warmup", domain="acme/x100",
                                       nodes=NODES, edges=EDGES, summary="how it warms")
    db.add_node_link(conn, ids["flow"], "check", ids["note"], "explains")
    # two separate reads: one call handing back the same row twice is one read
    db.record_recall(conn, [ids["note"]])
    db.record_recall(conn, [ids["note"]])
    return ids


def _roundtrip(source, target) -> dict:
    text = portable.to_jsonl(portable.export_records(source))
    return portable.import_records(target, portable.read_jsonl(text))


# ---------------------------------------------------------------- exporting

def test_the_export_is_one_json_object_per_line(source):
    _populate(source)
    lines = portable.to_jsonl(portable.export_records(source)).strip().splitlines()
    kinds = [json.loads(l)["record"] for l in lines]
    assert kinds[0] == "meta"
    assert kinds.count("memory") == 3 and "diagram" in kinds and "relation" in kinds


def test_memories_come_before_the_rows_that_join_them(source):
    """An import writes in file order; a relation names two memories."""
    _populate(source)
    kinds = [r["record"] for r in portable.export_records(source)]
    last_memory = max(i for i, k in enumerate(kinds) if k == "memory")
    assert kinds.index("relation") > last_memory
    assert kinds.index("diagram") > last_memory


def test_an_empty_field_is_left_out(source):
    db.insert_memory(source, type="note", content="row merge keeps the older id")
    record = [r for r in portable.export_records(source) if r["record"] == "memory"][0]
    assert "review_after" not in record and "session" not in record


def test_the_export_can_be_scoped(source):
    _populate(source)
    db.insert_memory(source, type="note", domain="zeta/p300", content="elsewhere")
    records = list(portable.export_records(source, domain="acme/x100"))
    assert all(r.get("domain") == "acme/x100"
               for r in records if r["record"] == "memory")


def test_archived_rows_are_out_unless_asked_for(source):
    uid = db.insert_memory(source, type="note", content="retired")
    db.set_status(source, uid, "archived")
    assert not [r for r in portable.export_records(source) if r["record"] == "memory"]
    assert [r for r in portable.export_records(source, include_archived=True)
            if r["record"] == "memory"]


# ---------------------------------------------------------------- the trip

def test_a_memory_comes_back_whole(source, target):
    ids = _populate(source)
    _roundtrip(source, target)
    before, after = db.get_memory(source, ids["note"]), db.get_memory(target, ids["note"])
    for col in ("uid", "type", "domain", "tags", "content", "status", "confidence",
                "created_at", "review_after", "source_ref", "also_domains"):
        assert after[col] == before[col], col


def test_the_uid_is_not_renumbered(source, target):
    """Every relation, node link and jump in the store points at one."""
    ids = _populate(source)
    _roundtrip(source, target)
    assert db.get_memory(target, ids["note"]) is not None


def test_cross_listings_survive_as_rows_not_just_the_mirror(source, target):
    ids = _populate(source)
    _roundtrip(source, target)
    assert db.get_domain_links(target, ids["note"]) == ["omni/x900"]
    assert [r["uid"] for r in db.list_by_domain(target, "omni/x900")] == [ids["note"]]


def test_relations_survive(source, target):
    ids = _populate(source)
    result = _roundtrip(source, target)
    assert result["relations"] == 1
    assert [r["to_uid"] for r in db.get_relations(target, ids["note"])] == [ids["old"]]


def test_a_diagram_keeps_its_graph_and_its_arrangement(source, target):
    ids = _populate(source)
    db.set_node_positions(source, ids["flow"], {"check": {"x": 1234, "y": 5678}})
    _roundtrip(source, target)
    after = db.get_diagram(target, ids["flow"])
    assert after["title"] == "Cache warmup"
    assert [n["key"] for n in after["nodes"]] == ["start", "check", "done"]
    moved = next(n for n in after["nodes"] if n["key"] == "check")
    assert (moved["x"], moved["y"]) == (1234, 5678)
    assert next(n for n in after["nodes"] if n["key"] == "check")["note"] == "why it asks"
    assert [(e["from"], e["to"], e["label"]) for e in after["edges"]] == [
        ("start", "check", ""), ("check", "done", "no")]


def test_a_node_link_survives(source, target):
    ids = _populate(source)
    _roundtrip(source, target)
    links = db.get_node_links(target, ids["flow"])
    assert [(l["node_key"], l["target_uid"]) for l in links] == [("check", ids["note"])]


def test_usage_counts_come_along(source, target):
    ids = _populate(source)
    _roundtrip(source, target)
    assert db.usage_for(target, [ids["note"]])[ids["note"]]["recalls"] == 2


def test_the_restored_store_is_searchable(source, target):
    """FTS and the vectors are derived -- an import has to rebuild them."""
    ids = _populate(source)
    _roundtrip(source, target)
    assert [r["uid"] for r in db.search_memories(target, "warmup first request")][:1] == [
        ids["note"]]


# ------------------------------------------------------------- running twice

def test_importing_the_same_file_twice_changes_nothing(source, target):
    _populate(source)
    first = _roundtrip(source, target)
    second = _roundtrip(source, target)
    assert first["added"] == 3 and first["skipped"] == 0
    assert second["added"] == 0 and second["skipped"] == 3
    assert target.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3
    assert target.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1


def test_a_local_row_wins_over_the_file(source, target):
    """An import restores, merges into, or carries -- in all three the row
    somebody has been using is the one to keep."""
    ids = _populate(source)
    db.restore_memory(target, {"uid": ids["note"], "type": "note", "content": "mine"})
    _roundtrip(source, target)
    assert db.get_memory(target, ids["note"])["content"] == "mine"


def test_one_bad_record_does_not_lose_the_rest(target):
    result = portable.import_records(target, [
        {"record": "memory", "uid": "a1", "type": "note", "content": "kept"},
        {"record": "memory", "uid": "b2"},  # no type
        {"record": "memory", "uid": "c3", "type": "note", "content": "also kept"},
    ])
    assert result["added"] == 2 and len(result["errors"]) == 1
    assert db.get_memory(target, "c3") is not None


def test_a_relation_to_something_outside_the_slice_is_dropped(source, target):
    ids = _populate(source)
    records = [r for r in portable.export_records(source)
               if not (r["record"] == "memory" and r["uid"] == ids["old"])]
    result = portable.import_records(target, records)
    assert result["relations"] == 0
    assert db.get_relations(target, ids["note"]) == []


# ---------------------------------------------------------------- markdown

def test_markdown_groups_by_domain_and_carries_the_content(source):
    _populate(source)
    text = portable.to_markdown(portable.export_records(source))
    assert "## acme/x100" in text
    assert "cache warmup runs before the first request" in text
    assert "- source_ref: src/acme/x100/warmup.py" in text
    assert "```mermaid" in text
    assert "## Relations" in text


def test_markdown_names_a_scoped_export_as_scoped(source):
    _populate(source)
    text = portable.to_markdown(portable.export_records(source, domain="acme/x100"))
    assert "scoped to `acme/x100`" in text


# --------------------------------------------------------------------- cli

def test_the_cli_exports_and_imports(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MEMAI_HOME", str(home))
    with db.connect() as conn:
        ids = _populate(conn)

    out = tmp_path / "export.jsonl"
    assert portable.main(["export", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").count("\n") >= 4

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MEMAI_HOME", str(other))
    assert portable.main(["import", str(out)]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["added"] == 3
    with db.connect() as conn:
        assert db.get_memory(conn, ids["note"]) is not None


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MEMAI_HOME", str(home))
    with db.connect() as conn:
        _populate(conn)
    out = tmp_path / "export.jsonl"
    portable.main(["export", "--out", str(out)])

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MEMAI_HOME", str(other))
    capsys.readouterr()
    assert portable.main(["import", str(out), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["would_add"] == 3
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
