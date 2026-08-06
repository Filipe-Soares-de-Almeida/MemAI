"""The store as text, so it stops being a thing only memai can read.

The existing backup is `VACUUM INTO`: a byte-perfect copy of a binary file,
which is the right tool for restoring a machine and the wrong one for every
other question. You cannot diff two of them, grep one, put one in a review,
carry a domain to another store, or read what the memory said on a laptop
with no Python. A store nobody can inspect is a store nobody audits.

Two formats, for the two jobs:

  jsonl  one record per line -- every column, cross-listings, relations and
         whole diagram graphs including the positions somebody arranged by
         hand. Round-trippable, and line-oriented so a diff is per memory.
  md     one document, grouped by domain. Export only, for reading and
         grepping.

What is deliberately NOT here: the edit history and the FTS/vector indexes.
History is what `VACUUM INTO` is for, and both indexes are derived -- an
import rebuilds them from the content it writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memai import db

FORMATS = ("jsonl", "md")
FORMAT_VERSION = 1


# --------------------------------------------------------------- exporting

def _memory_record(conn, row, usage: dict) -> dict:
    out = {"record": "memory"}
    for col in ("uid", "type", "domain", "session", "tags", "content", "status",
                "confidence", "superseded_by", "created_at", "updated_at",
                "review_after", "source_ref"):
        value = row[col]
        if value:
            out[col] = value
    links = db.parse_domains(row["also_domains"])
    if links:
        out["also"] = links
    seen = usage.get(row["uid"])
    if seen:
        out["recalls"], out["last_recall"] = seen["recalls"], seen["last_recall"]
    return out


def export_records(conn, *, domain: str = "", include_archived: bool = False):
    """Every record of the export, in the order an import has to read them.

    Memories first, then the diagram graphs, then relations and diagram
    references: each of those names two memories, and both have to exist
    before the row joining them can.
    """
    where, params = ["1=1"], []
    if domain:
        clause, values, _ = db.domain_scope_clause(conn, domain, alias="", subtree=True)
        where.append(clause)
        params.extend(values)
    if not include_archived:
        where.append("AND status = 'active'")
    rows = conn.execute(
        f"SELECT * FROM memories WHERE {' '.join(where)} ORDER BY created_at, uid",
        params).fetchall()
    uids = [r["uid"] for r in rows]
    usage = db.usage_for(conn, uids)
    inside = set(uids)

    yield {"record": "meta", "format": FORMAT_VERSION, "exported_at": db.now_iso(),
           "domain_case": db.get_domain_case(conn), "count": len(rows),
           **({"domain": domain} if domain else {})}
    for row in rows:
        yield _memory_record(conn, row, usage)
    for row in rows:
        if row["type"] != db.DIAGRAM_TYPE:
            continue
        graph = db.get_diagram(conn, row["uid"])
        if graph is None:
            continue
        yield {
            "record": "diagram", "uid": row["uid"], "title": graph["title"],
            "summary": graph["summary"], "diagram_kind": graph["kind"],
            "font_scale": graph["font_scale"],
            # `loops` is derived on read; storing it would be a fact about
            # the drawing, not about the flow
            "nodes": graph["nodes"],
            "edges": [{k: v for k, v in e.items() if k != "loops"} for e in graph["edges"]],
            "links": [{"node_key": l["node_key"], "target_uid": l["target_uid"],
                       "relation_type": l["relation_type"], "created_at": l["created_at"]}
                      for l in graph["links"]],
            "jumps": [{"direction": j["direction"], "node_key": j["node_key"],
                       "peer_uid": j["peer_uid"], "peer_node": j["peer_node"],
                       "label": j["label"], "created_at": j["created_at"]}
                      for j in graph["jumps"]],
        }
    for r in conn.execute(
        "SELECT from_uid, to_uid, relation_type, note, created_at FROM relations "
        "ORDER BY created_at, id"
    ).fetchall():
        # a relation to something outside the exported slice would import as
        # an edge to nothing; the slice is the unit being carried
        if r["from_uid"] in inside and r["to_uid"] in inside:
            yield {"record": "relation", **dict(r)}


def to_jsonl(records) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


_MD_FIELDS = ("created_at", "session", "tags", "status", "confidence",
              "review_after", "source_ref", "superseded_by")
# Printing these on every memory would be three lines of "nothing unusual
# here" per record, in a document whose whole job is to be read and diffed.
_MD_DEFAULTS = {"status": "active", "confidence": "unverified"}


def to_markdown(records) -> str:
    """One document, grouped by where things are filed. Export only.

    Ordered by domain and then by date so two exports of the same store
    diff as the memories that changed, rather than as everything having
    moved.
    """
    records = list(records)
    meta = next((r for r in records if r["record"] == "meta"), {})
    memories = [r for r in records if r["record"] == "memory"]
    diagrams = {r["uid"]: r for r in records if r["record"] == "diagram"}
    relations = [r for r in records if r["record"] == "relation"]

    out = [f"# MemAI export", "",
           f"{meta.get('count', len(memories))} memories, "
           f"exported {meta.get('exported_at', '')[:19]}"
           + (f", scoped to `{meta['domain']}`" if meta.get("domain") else ""), ""]

    for path in sorted({m.get("domain", "") for m in memories}):
        here = sorted((m for m in memories if m.get("domain", "") == path),
                      key=lambda m: (m.get("created_at", ""), m["uid"]))
        out += [f"## {path or '(no domain)'}", ""]
        for m in here:
            out += [f"### {m['type']} · `{m['uid']}`", ""]
            for field in _MD_FIELDS:
                if m.get(field) and m[field] != _MD_DEFAULTS.get(field):
                    out.append(f"- {field}: {m[field]}")
            if m.get("also"):
                out.append(f"- also: {', '.join(m['also'])}")
            if m.get("recalls"):
                out.append(f"- recalls: {m['recalls']}")
            out += ["", m.get("content", ""), ""]
            graph = diagrams.get(m["uid"])
            if graph:
                # the same projection get_diagram(format='mermaid') returns,
                # from the graph in hand rather than from the store
                out += ["```mermaid",
                        db._render_mermaid(graph["title"], graph["nodes"], graph["edges"]),
                        "```", ""]
    if relations:
        out += ["## Relations", ""]
        out += [f"- `{r['from_uid']}` {r['relation_type']} `{r['to_uid']}`"
                + (f" — {r['note']}" if r.get("note") else "")
                for r in relations]
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------- importing

def import_records(conn, records) -> dict:
    """Write records that are not already here. Returns what it did.

    Skip-if-present rather than overwrite: an import is how a store is
    restored, merged into or carried, and in all three the local row is the
    one somebody has been using. Re-importing the same file changes nothing,
    which is what makes it safe to run twice.
    """
    added, skipped, errors = 0, 0, []
    diagrams, relations = [], []
    for i, rec in enumerate(records):
        kind = rec.get("record")
        try:
            if kind == "memory":
                if db.get_memory(conn, rec["uid"]) is not None:
                    skipped += 1
                    continue
                db.restore_memory(conn, rec)
                added += 1
            elif kind == "diagram":
                diagrams.append(rec)
            elif kind == "relation":
                relations.append(rec)
        except Exception as exc:  # one bad line must not lose the rest
            errors.append({"line": i + 1, "error": str(exc)})

    for rec in diagrams:
        try:
            if db.get_memory(conn, rec["uid"]) is None:
                continue  # its memory was skipped as already present
            if db.get_diagram_row(conn, rec["uid"]) is None:
                db.restore_diagram(conn, rec)
            db.restore_diagram_refs(conn, rec)
        except Exception as exc:
            errors.append({"uid": rec.get("uid"), "error": str(exc)})
    linked = 0
    for rec in relations:
        try:
            db.add_relation(conn, rec["from_uid"], rec["to_uid"],
                            rec["relation_type"], rec.get("note", ""))
            linked += 1
        except ValueError:
            pass  # an end that was skipped, or an edge that already exists
    return {"added": added, "skipped": skipped, "relations": linked, "errors": errors}


def read_jsonl(text: str):
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


# --------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memai-store",
        description="Export the memai store as text, or import one back.")
    sub = parser.add_subparsers(dest="command", required=True)

    out = sub.add_parser("export", help="write the store as jsonl or markdown")
    out.add_argument("--format", choices=FORMATS, default="jsonl")
    out.add_argument("--out", help="file to write (default: stdout)")
    out.add_argument("--domain", default="", help="only this path and its subdomains")
    out.add_argument("--include-archived", action="store_true")

    into = sub.add_parser("import", help="read a jsonl export into this store")
    into.add_argument("path", help="the .jsonl file, or - for stdin")
    into.add_argument("--dry-run", action="store_true",
                      help="report what would be written, write nothing")

    args = parser.parse_args(argv)

    if args.command == "export":
        with db.connect() as conn:
            records = list(export_records(conn, domain=args.domain,
                                          include_archived=args.include_archived))
        text = to_jsonl(records) if args.format == "jsonl" else to_markdown(records)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"{len(records)} records -> {args.out}", file=sys.stderr)
        else:
            sys.stdout.buffer.write(text.encode("utf-8"))
        return 0

    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    records = list(read_jsonl(text))
    if args.dry_run:
        with db.connect() as conn:
            fresh = sum(1 for r in records if r.get("record") == "memory"
                        and db.get_memory(conn, r["uid"]) is None)
        print(json.dumps({"dry_run": True, "would_add": fresh,
                          "records": len(records)}, ensure_ascii=False))
        return 0
    with db.connect() as conn:
        result = import_records(conn, records)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":  # pragma: no cover - console-script entry point
    raise SystemExit(main())
