"""Writes over every memory under a domain path: move, archive or restore, and purge it."""

from __future__ import annotations

import sqlite3

from memai.lite import DOMAIN_SEP, normalize_domain, now_iso
from memai.store.domains import domain_clause, in_domain
from memai.store.memories import get_domain_links, purge_memory, set_domain_links, set_status
from memai.store.sections import refuse_long


def move_domain(
    conn: sqlite3.Connection, src: str, dst: str, *, subtree: bool = True
) -> dict:
    """Re-home a domain and, by default, everything nested under it.

    Moving 'acme/x100' to 'acme/legacy' takes 'acme/x100/p200' along as
    'acme/legacy/p200': the descendants are part of what the operator
    pointed at, and leaving them behind would silently split a subject in
    two. A merge is not a separate operation -- renaming onto a path that
    already holds memories means those two sets are one domain now.

    Every moved row is reindexed (domain is part of the index source) and
    audited in `edits`, so a re-home is reconstructible.

    Cross-listings pointing INTO the moved scope follow it: renaming a
    subject renames it for the memories that merely belong to it too, or
    they would be left pointing at a path that does not exist. What does
    NOT follow is the memory itself -- the rows to re-home are matched on
    the filed path alone (also=False), because a cross-listing says a memory
    belongs to a subject, not that it lives there.

    Refuses to move a domain into its own subtree: 'acme' -> 'acme/x100'
    would make the path its own ancestor.

    `src` is matched as given AND in canonical form. Every writer normalizes,
    so the two are the same string in practice -- but a row written straight
    into the table can hold a shape no writer would produce ('acme//x100 '),
    and repairing exactly that is what the normalize pass names it for.
    """
    src_given, src, dst = src or "", normalize_domain(src), normalize_domain(dst)
    if not src:
        raise ValueError("source domain is required")
    if not dst:
        raise ValueError("target domain is required")
    if src == dst and src_given == src:
        raise ValueError("source and target are the same")
    if subtree and in_domain(dst, src):
        raise ValueError(f"cannot move '{src}' into its own subtree ('{dst}')")

    # `memory_domains` names its path `domain` too, so one clause selects
    # the rows filed in the moved scope and the cross-listings into it.
    clause, params = domain_clause(src, alias="", subtree=subtree, also=False)
    if src_given != src:
        clause = f"AND ({clause.removeprefix('AND ')} OR domain = ?)"
        params = [*params, src_given]
    rows = conn.execute(
        f"SELECT rowid_pk, uid, content, tags, domain, also_domains FROM memories "
        f"WHERE 1=1 {clause}", params).fetchall()
    link_rows = conn.execute(
        f"SELECT memory_uid, domain FROM memory_domains WHERE 1=1 {clause}",
        params).fetchall()
    if not rows and not link_rows:
        raise ValueError(f"no memories in domain '{src}'")

    # "merge": rows outside this move already live at the target; asked before the UPDATE, or
    # every move would look like one
    moving = {r["rowid_pk"] for r in rows}
    dst_clause, dst_params = domain_clause(dst, alias="", subtree=True, also=False)
    merged = any(
        r["rowid_pk"] not in moving for r in conn.execute(
            f"SELECT rowid_pk FROM memories WHERE 1=1 {dst_clause}", dst_params))

    def retarget(old: str) -> str:
        return dst if old in (src, src_given) else dst + DOMAIN_SEP + old[len(src) + 1:]

    for old in {r["domain"] for r in rows} | {r["domain"] for r in link_rows}:
        refuse_long(domain=retarget(old))
    now = now_iso()
    touched: set[str] = set()
    for r in rows:
        old = r["domain"]
        target = retarget(old)
        conn.execute(
            "UPDATE memories SET domain = ?, updated_at = ? WHERE rowid_pk = ?",
            (target, now, r["rowid_pk"]))
        conn.execute(
            "INSERT INTO edits (memory_uid, edited_at, prev_content, new_content, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["uid"], now, r["content"], r["content"],
             f"meta: domain '{old}' → '{target}'"))
        touched.add(old)

    # Cross-listings go through set_domain_links, whose policy drops one that lands on or under the
    # filed path. Only paths this move SELECTED are retargeted, so a subtree=False plan stays valid.
    moved_paths = {r["domain"] for r in link_rows}
    relinked = sorted({r["memory_uid"] for r in link_rows})
    for uid in relinked:
        want = [retarget(p) if p in moved_paths else p
                for p in get_domain_links(conn, uid)]
        set_domain_links(conn, uid, want, coerce=False,
                         note=f"meta: also '{src}' → '{dst}'")
    for r in rows:
        if r["also_domains"] and r["uid"] not in relinked:
            set_domain_links(conn, r["uid"], get_domain_links(conn, r["uid"]),
                             coerce=False)
    touched.update(r["domain"] for r in link_rows)
    return {
        "moved": len(rows), "domains": len(touched), "merged": merged,
        "also_moved": len(relinked),
    }


def set_domain_status(
    conn: sqlite3.Connection, domain: str, status: str, *, note: str = ""
) -> dict:
    """Archive (or restore) every memory FILED in a domain scope.

    A domain has no status of its own -- it exists because memories name it,
    so "archive this domain" means archiving what is filed under it,
    subdomains included. The view then reads a level with archived memories
    and none active as an archived branch.

    Matched on the filed path alone (also=False), like a re-home: a memory
    cross-listed into the subject lives in another branch, and archiving a
    subject it merely belongs to would reach outside what was pointed at.

    Only rows that actually change are touched, and their uids come back --
    which is what makes an Undo exact. Restoring "everything in the scope"
    would also revive whatever had been archived long before, for reasons
    that have nothing to do with this pass.
    """
    path = normalize_domain(domain)
    if not path:
        raise ValueError("domain is required")
    if status not in ("active", "archived"):
        raise ValueError("status must be 'active' or 'archived'")
    clause, params = domain_clause(path, alias="", subtree=True, also=False)
    rows = conn.execute(
        f"SELECT uid, domain FROM memories WHERE status <> ? {clause}",
        [status, *params]).fetchall()
    for r in rows:
        set_status(conn, r["uid"], status, note=note)
    return {
        "uids": [r["uid"] for r in rows],
        "domains": len({r["domain"] for r in rows}),
    }


def purge_domain(conn: sqlite3.Connection, domain: str) -> dict:
    """Irreversibly delete a domain: every memory filed in it, subtree included.

    The scope-wide counterpart of purge_memory, and it carries the same
    warning N times over -- callers must gate it behind an explicit typed
    confirmation. set_domain_status(status='archived') is the reversible
    reading of "get rid of this domain" and is what the UI offers first.

    Cross-listings pointing INTO the scope go too, because the path they name
    stops existing. The memories holding them do NOT: one is filed in another
    branch and only belonged to this subject, so it loses the membership and
    keeps its own life. Dropped through set_domain_links, which is what keeps
    `memory_domains` and the `also_domains` mirror telling one story.
    """
    path = normalize_domain(domain)
    if not path:
        raise ValueError("domain is required")
    clause, params = domain_clause(path, alias="", subtree=True, also=False)
    rows = conn.execute(
        f"SELECT uid, domain FROM memories WHERE 1=1 {clause}", params).fetchall()
    link_rows = conn.execute(
        f"SELECT memory_uid, domain FROM memory_domains WHERE 1=1 {clause}",
        params).fetchall()
    if not rows and not link_rows:
        raise ValueError(f"no memories in domain '{path}'")

    purged = {r["uid"] for r in rows}
    for uid in sorted(purged):
        purge_memory(conn, uid)
    # a purged memory took its own memberships with it, so what is left here
    # is the memories filed elsewhere that merely belonged to the scope
    unlinked = sorted({r["memory_uid"] for r in link_rows} - purged)
    for uid in unlinked:
        set_domain_links(
            conn, uid,
            [p for p in get_domain_links(conn, uid) if not in_domain(p, path)],
            coerce=False, note=f"meta: also '{path}' dropped (domain deleted)")
    return {
        "purged": len(purged), "unlinked": len(unlinked),
        "domains": len({r["domain"] for r in rows}
                       | {r["domain"] for r in link_rows}),
    }
