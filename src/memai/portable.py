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

The FTS index is never in an export: it is derived, and an import rebuilds
it from the content it writes. The edit history is left out by default --
`VACUUM INTO` keeps it -- and carried when asked for (`include_edits`),
which is how move() takes a memory from one project to another whole.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from memai import db

FORMATS = ("jsonl", "md")
FORMAT_VERSION = 1
# how many crossings of each kind a boundary report spells out; the count
# beside them is always the whole number
BOUNDARY_LIMIT = 20
# uids per IN (...) list; SQLite refuses a statement with too many parameters
_CHUNK = 500


def _chunks(items: list, size: int = _CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --------------------------------------------------------------- exporting

def _memory_record(conn, row, usage: dict) -> dict:
    out = {"record": "memory"}
    for col in ("uid", "type", "domain", "session", "tags", "title", "content",
                "status", "confidence", "superseded_by", "created_at", "updated_at",
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


def export_records(conn, *, domain: str = "", uids=None, include_archived: bool = False,
                   include_edits: bool = False):
    """Every record of the export, in the order an import has to read them.

    Memories first, then their edit history when `include_edits` asks for
    it, then the diagram graphs, then relations and diagram references:
    each of those names a memory that has to exist before the row about it
    can.

    `domain` narrows the export to a path and its subdomains; `uids` to
    those memories, leaving out any the store does not hold. Given both,
    the export is their intersection.
    """
    where, params = ["1=1"], []
    if domain:
        clause, values, _ = db.domain_scope_clause(conn, domain, alias="", subtree=True)
        where.append(clause)
        params.extend(values)
    if uids is not None:
        wanted = list(dict.fromkeys(str(u) for u in uids))
        where.append(f"AND uid IN ({','.join('?' * len(wanted)) or 'NULL'})")
        params.extend(wanted)
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
    if include_edits:
        for chunk in _chunks(uids):
            for r in conn.execute(
                "SELECT memory_uid, edited_at, prev_content, new_content, note FROM edits "
                f"WHERE memory_uid IN ({','.join('?' * len(chunk))}) ORDER BY edited_at, id",
                chunk,
            ).fetchall():
                yield {"record": "edit", "uid": r["memory_uid"], "edited_at": r["edited_at"],
                       "prev_content": r["prev_content"], "new_content": r["new_content"],
                       **({"note": r["note"]} if r["note"] else {})}
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

    An `edit` record is written only under a memory this import added: a
    memory already here keeps the history it has.
    """
    added, skipped, errors = 0, 0, []
    fresh: set[str] = set()
    diagrams, relations, edits = [], [], []
    for i, rec in enumerate(records):
        kind = rec.get("record")
        try:
            if kind == "memory":
                if db.get_memory(conn, rec["uid"]) is not None:
                    skipped += 1
                    continue
                db.restore_memory(conn, rec)
                fresh.add(str(rec["uid"]))
                added += 1
            elif kind == "edit":
                edits.append(rec)
            elif kind == "diagram":
                diagrams.append(rec)
            elif kind == "relation":
                relations.append(rec)
        except Exception as exc:  # one bad line must not lose the rest
            errors.append({"line": i + 1, "error": str(exc)})

    history = 0
    for rec in edits:
        if str(rec.get("uid")) not in fresh:
            continue
        try:
            db.restore_edit(conn, rec)
            history += 1
        except Exception as exc:
            errors.append({"uid": rec.get("uid"), "error": str(exc)})
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
    return {"added": added, "skipped": skipped, "relations": linked, "edits": history,
            "errors": errors}


def read_jsonl(text: str):
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


# ----------------------------------------------------- moving between projects

def boundary(conn, uids) -> dict:
    """What a slice of memories points at, or is pointed at from, outside itself.

    An export carries only what joins two memories of the slice, so a
    relation, a diagram link or jump, a `superseded_by` or a [[uid]] written
    in a body that crosses the edge of the slice is dropped by the copy and
    deleted with the originals. Each kind comes back as `count` plus up to
    BOUNDARY_LIMIT `items`; the count is always the whole number.
    """
    inside = list(dict.fromkeys(str(u) for u in uids))
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS export_slice (uid TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM export_slice")
    conn.executemany("INSERT OR IGNORE INTO export_slice (uid) VALUES (?)",
                     [(u,) for u in inside])
    try:
        held = "IN (SELECT uid FROM export_slice)"

        def crossing(sql: str) -> dict:
            rows = conn.execute(sql).fetchall()
            return {"count": len(rows), "items": [dict(r) for r in rows[:BOUNDARY_LIMIT]]}

        out = {
            "relations": crossing(
                "SELECT from_uid, to_uid, relation_type FROM relations "
                f"WHERE (from_uid {held}) <> (to_uid {held}) ORDER BY id"),
            "diagram_links": crossing(
                "SELECT memory_uid AS diagram_uid, node_key, target_uid FROM diagram_node_links "
                f"WHERE (memory_uid {held}) <> (target_uid {held}) ORDER BY created_at"),
            "diagram_jumps": crossing(
                "SELECT from_uid, from_node, to_uid FROM diagram_jumps "
                f"WHERE (from_uid {held}) <> (to_uid {held}) ORDER BY id"),
            "superseded_by": crossing(
                "SELECT uid, superseded_by FROM memories WHERE superseded_by IS NOT NULL "
                f"AND (uid {held}) <> (superseded_by {held}) ORDER BY created_at"),
        }
        # a [[uid]] is written inside the text, so the bodies are read: those
        # of the slice for a name outside it, and every other for a name inside
        slice_set = set(inside)
        mentions = []
        for row in conn.execute("SELECT uid, content FROM memories WHERE content LIKE '%[[%'"):
            here = row["uid"] in slice_set
            for target in sorted({m.group(1) for m in db._BODY_LINK.finditer(row["content"] or "")}):
                if target != row["uid"] and (target in slice_set) != here:
                    mentions.append({"uid": row["uid"], "target_uid": target})
        # a name nothing in the store resolves is already dangling, and no move
        # changes that
        known: set[str] = set()
        for chunk in _chunks(sorted({m["target_uid"] for m in mentions})):
            known |= {r[0] for r in conn.execute(
                f"SELECT uid FROM memories WHERE uid IN ({','.join('?' * len(chunk))})", chunk)}
        mentions = [m for m in mentions if m["target_uid"] in known]
        out["body_links"] = {"count": len(mentions), "items": mentions[:BOUNDARY_LIMIT]}
        return out
    finally:
        conn.execute("DROP TABLE IF EXISTS export_slice")


def _spare_name(folder: Path, name: str) -> Path:
    """`folder/name`, or the same name with -2, -3, ... when it is taken."""
    dest, n = folder / name, 2
    while dest.exists():
        dest = folder / f"{name[:-3]}-{n}.db"
        n += 1
    return dest


def move(source: str, target: str, *, uids=(), domain: str = "", dry_run: bool = True,
         create: bool = False) -> dict:
    """Carry memories from one project to another, whole, and remove the originals.

    The slice is `uids`, `domain` (a path and its subdomains, archived rows
    included) or both. It is exported with its edit history, imported into
    `target`, and only a memory confirmed to be there afterwards is purged
    from `source` -- after a backup of `source` is written. A memory whose
    uid `target` already holds is left in both places and listed under
    `conflicts`. Two projects are two files, so a failure between the copy
    and the purge leaves a duplicate, never a loss.

    Project names are matched in any casing and reported as the projects
    spell them. `dry_run` (the default) returns the same report and changes
    nothing. `create` makes `target` when it does not exist; a dry run only
    reports `creates`.
    """
    for name in (source, target):
        error = db.project_name_error(name)
        if error:
            raise ValueError(error)
    source = db.project_name(source)
    target = db.find_project(target) or target
    if source.casefold() == target.casefold():
        raise ValueError("source and target are the same project")
    if not uids and not domain:
        raise ValueError("name what to move: uids, a domain, or both")
    creates = not db.project_exists(target)
    if creates and not create:
        raise ValueError(f"no project named '{target}'")

    wanted = [str(u).strip() for u in uids if str(u).strip()]
    with db.connect(project=source) as src:
        records = list(export_records(src, domain=domain, uids=wanted or None,
                                      include_archived=True, include_edits=True))
        slice_uids = [r["uid"] for r in records if r["record"] == "memory"]
        outside = boundary(src, slice_uids)
    conflicts: list[str] = []
    if not creates:
        with db.connect(project=target) as dst:
            conflicts = [u for u in slice_uids if db.get_memory(dst, u) is not None]
    kinds = Counter(r["record"] for r in records)
    report = {
        "source": source, "target": target, "creates": creates,
        "memories": len(slice_uids), "diagrams": kinds.get("diagram", 0),
        "relations": kinds.get("relation", 0), "edits": kinds.get("edit", 0),
        "conflicts": conflicts, "unknown": sorted(set(wanted) - set(slice_uids)),
        "outside": outside,
    }
    if dry_run:
        return {"dry_run": True, **report}

    movable = [u for u in slice_uids if u not in set(conflicts)]
    if not movable:
        return {"dry_run": False, "moved": 0, "backup": "", "errors": [], **report}
    if creates:
        db.create_project(target)
    backup = _spare_name(db.backups_dir(source), db.backup_name(source, "move"))
    db.backup_to(backup, project=source)
    with db.connect(project=target) as dst:
        result = import_records(dst, records)
    with db.connect(project=target) as dst:
        landed = [u for u in movable if db.get_memory(dst, u) is not None]
    with db.connect(project=source) as src:
        for uid in landed:
            db.purge_memory(src, uid)
    return {"dry_run": False, "moved": len(landed), "backup": str(backup),
            "errors": result["errors"], **report}


# --------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memai-store",
        description="Export the memai store as text, import one back, or move "
                    "memories between projects.")
    sub = parser.add_subparsers(dest="command", required=True)

    out = sub.add_parser("export", help="write the store as jsonl or markdown")
    out.add_argument("--format", choices=FORMATS, default="jsonl")
    out.add_argument("--out", help="file to write (default: stdout)")
    out.add_argument("--domain", default="", help="only this path and its subdomains")
    out.add_argument("--include-archived", action="store_true")
    out.add_argument("--include-edits", action="store_true",
                     help="carry the edit history as well")

    into = sub.add_parser("import", help="read a jsonl export into this store")
    into.add_argument("path", help="the .jsonl file, or - for stdin")
    into.add_argument("--dry-run", action="store_true",
                      help="report what would be written, write nothing")

    mv = sub.add_parser("move", help="carry memories from one project into another")
    mv.add_argument("--to", dest="target", required=True, help="the target project")
    mv.add_argument("--from", dest="source", default="",
                    help="the source project (default: the active one)")
    mv.add_argument("--domain", default="", help="this path and its subdomains")
    mv.add_argument("--uids", default="", help="comma-separated uids")
    mv.add_argument("--create", action="store_true",
                    help="create the target project when it does not exist")
    mv.add_argument("--dry-run", action="store_true",
                    help="report what would move, move nothing")

    args = parser.parse_args(argv)

    if args.command == "export":
        with db.connect() as conn:
            records = list(export_records(conn, domain=args.domain,
                                          include_archived=args.include_archived,
                                          include_edits=args.include_edits))
        text = to_jsonl(records) if args.format == "jsonl" else to_markdown(records)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"{len(records)} records -> {args.out}", file=sys.stderr)
        else:
            sys.stdout.buffer.write(text.encode("utf-8"))
        return 0

    if args.command == "move":
        result = move(args.source or db.active_project(), args.target,
                      uids=[u for u in args.uids.split(",") if u.strip()],
                      domain=args.domain, dry_run=args.dry_run, create=args.create)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("errors") else 0

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
