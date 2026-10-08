"""Helpers the route modules share: snippets, row shaping, query parameters, backups."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from memai import contract, db, sections

SNIPPET_LIMIT = 280


STATUSES = ("active", "archived")

# uids per /api/bulk call, and the cap on the uid list a scope-wide archive echoes for its Undo:
# an Undo longer than bulk accepts could not work.
BULK_MAX = contract.BULK_MAX


def _snip(text: str, limit: int = SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


# Body markup as webui/core/richtext.js reads it: a heading between '=' rules, '**' bold, a
# backtick code span, a fence line, and a [[uid]] reference.
_MARKUP = (
    (re.compile(r"^\s*={2,}\s+(.+?)\s+={2,}\s*$", re.M), r"\1"),   # heading
    (re.compile(r"^\s*```[A-Za-z0-9_+#-]*\s*$", re.M), ""),        # fence line
    (re.compile(r"\*\*([^*\n]+)\*\*"), r"\1"),                     # bold
    (re.compile(r"`([^`\n]+)`"), r"\1"),                           # code span
    (re.compile(r"\[\[([^\]\n]+)\]\]"), r"\1"),                    # reference
)


def _plain(text: str) -> str:
    """A body flattened to one line of prose, for a PREVIEW of it.

    A preview identifies a memory; it is not read as a document. Left as
    stored it put `**`, `===` and backticks on the screen -- and cutting
    first, as a snippet must, could sever a `**` and leave the stray half
    visible. So the markup goes before the cut, and the line breaks with it:
    a preview sits on one line wherever one is shown.

    Deliberately NOT applied to every _snip: the memories list, a diagram's
    node label and the MCP payloads each have their own reason to hold what
    was stored, and one sweep over all of them is a different change.
    """
    out = str(text or "")
    for pattern, repl in _MARKUP:
        out = pattern.sub(repl, out)
    # a bullet is structure, and structure does not survive one line
    out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.M)
    # Unclosed openers go too. Two or more asterisks are bold by construction; a single one is
    # kept, so `SELECT *` survives a preview.
    out = re.sub(r"\*{2,}", "", out).replace("`", "")
    return " ".join(out.split())


def _paths(d: dict) -> dict:
    """Swap the `also_domains` mirror for the `also` list a view reads.

    That column exists for the FTS index, which cannot join (see db);
    db.parse_domains is its inverse. No payload carries the
    mirror -- a view that filtered on it would be reading the copy instead
    of the rows in memory_domains.
    """
    blob = d.pop("also_domains", "")
    if blob:
        d["also"] = db.parse_domains(blob)
    return d


def _section_spec(s: sections.Section) -> dict:
    """One field of a type, as a form needs it. max_len is 0 for no ceiling."""
    return {"key": s.key, "label": s.label, "max_len": s.max_len}


def _summary(row, limit: int = SNIPPET_LIMIT) -> dict:
    d = _paths(dict(row))
    d["content_len"] = len(d.get("content", ""))
    d["content"] = _snip(d.get("content", ""), limit)
    return d


def _peer_card(conn: sqlite3.Connection, uid: str) -> dict | None:
    row = db.get_memory(conn, uid)
    if row is None:
        return None
    # `title` is what a peer is called; views name it by that, with the body as fallback and
    # tooltip (shared.peerName).
    return {
        "uid": row["uid"], "type": row["type"], "domain": row["domain"],
        "title": row["title"],
        "status": row["status"], "confidence": row["confidence"],
        "snippet": _snip(_plain(row["content"]), 160), "created_at": row["created_at"],
    }


def _int_param(request, name: str, default: int, lo: int, hi: int) -> int:
    try:
        val = int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _subtree_param(request) -> bool:
    """Whether a domain filter covers its subdomains. On unless told otherwise.

    A domain is a scope, and the useful default for a filter is the whole
    scope -- picking 'acme/x100' and seeing none of its routines reads as
    an empty module. `subtree=0` narrows to the exact path.
    """
    return request.query_params.get("subtree", "1").lower() not in ("0", "false", "no")


def _scope_echo(conn: sqlite3.Connection, domain: str) -> dict:
    """`domain_scope` for a response, but only when it is news.

    A domain filter is allowed to resolve a name that is the deep end of a
    path ('p200' -> 'acme/x100/p200'); a view that showed those rows
    without saying so would be claiming a filter it did not run. Omitted
    when the filter matched literally, which is the ordinary case.
    """
    if not domain:
        return {}
    scopes = db.resolve_domain_scopes(conn, domain)
    return {} if scopes == [db.normalize_domain(domain)] else {"domain_scope": scopes}


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _backup(kind: str = "") -> Path:
    """A fresh backup of the active project, in its own folder and named after
    it (db.backups_dir, db.backup_name)."""
    project = db.active_project()
    dest = db.backups_dir(project) / db.backup_name(project, kind)
    if dest.exists():
        raise ValueError(f"backup already exists: {dest.name}")
    return db.backup_to(dest, project=project)
