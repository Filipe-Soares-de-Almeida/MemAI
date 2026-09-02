"""The store as a few hundred words, for a reader that has not asked yet.

session_brief renders what the store holds -- its size, active domains, the
latest checkpoint, open handoffs, pitfalls, recent notes, documented flows --
and ends with the instruction to open the subject with pulse(domain) before
working, spelling out what the store's own casing policy means for the path
that instruction asks for. The SessionStart hook emits it (memai.hook); the
warm_up prompt returns it.

Plain text, not JSON, read by a language model. Every section is capped and
every cap is reported, so a brief that stopped short does not read as a store
that was empty.
"""

from __future__ import annotations

from memai import db

# What a warm-up may cost. Generous next to a tool result and small next to
# a context window -- this is paid once per session, and the alternative is
# the agent spending a tool call to find out the store has nothing to say.
DEFAULT_BUDGET = 2400
SNIPPET = 220
DOMAINS = 8
HANDOFFS = 3
PITFALLS = 4
NOTES = 4
FLOWS = 5

# What the casing policy means for a caller writing a path. Under `lower`
# and `upper` a domain is folded on the way in AND on the way through a
# read, so any spelling finds the same rows; under `preserve` it is not, and
# two spellings are two paths.
CASING = {
    "lower": "Domain paths are stored lowercase here, and a path passed in any "
             "case is folded to it.",
    "upper": "Domain paths are stored UPPERCASE here, and a path passed in any "
             "case is folded to it.",
    "preserve": "This store keeps the casing a path was written with, so "
                "'Acme/X100' and 'acme/x100' are two different domains -- reuse "
                "an existing one exactly as list_domains() spells it.",
}

# The tail of every brief. _fit reserves its room before dividing what is
# left between the sections, so it is never the part that gets trimmed.
# {casing} is filled from the store's active policy, never assumed.
CALL_TO_ACTION = (
    "Before the first tool call of this session, call pulse(domain) for the "
    "subject the prompt names -- what was decided, what was already tried, "
    "where the last session stopped. A domain is a path, outermost scope first "
    "('acme/x100/p200'). {casing} If the path is not obvious: list_domains() "
    "for the tree, recall(query) or search(query) to find a subject by name, "
    "get_memory(uid) for one record in full. If the memai tools are not loaded "
    "yet -- an MCP connection comes up asynchronously -- load them first, then "
    "call it. Write as you go: note() a durable fact, anti_pattern() a pitfall, "
    "checkpoint() before a pause, set_confidence(uid, 'confirmed'|'contradicted') "
    "once evidence settles a claim you could not check when you wrote it."
)


def call_to_action(conn) -> str:
    """The instruction, with the casing line the store's policy calls for."""
    mode = db.get_domain_case(conn)
    return CALL_TO_ACTION.format(casing=CASING.get(mode, CASING[db.DOMAIN_CASE_DEFAULT]))


def _snip(text: str, limit: int = SNIPPET) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _lines(rows, label: str, cap: int, total: int) -> list[str]:
    """One block: a heading that admits what it left out, then the rows."""
    if not rows:
        return []
    head = f"{label} ({total})" if total > len(rows) else f"{label}"
    out = [f"{head}:"]
    for r in rows[:cap]:
        where = f" [{r['domain']}]" if r["domain"] else ""
        out.append(f"  - {r['uid']}{where} {_snip(r['content'])}")
    return out


def session_brief(conn, *, domain: str = "", budget: int = DEFAULT_BUDGET,
                  project: str = "") -> str:
    """What is already known, for a session that has not started working.

    No domain, because at session start nobody knows the subject yet: this
    is the store's own state, ordered by recency, and the drill-down is the
    agent's next move. Passing one narrows it the way pulse() does.

    `project` is the name of the project `conn` is on, for the opening line.
    """
    census = db.domain_census(conn, domain)
    if not census["total"]:
        return ""

    scope = domain or (f"project '{project}'" if project else "the whole store")
    parts = [f"MemAI long-term memory: {census['total']} memories in {scope}."]

    tree = [d for d in db.list_domains(conn) if not domain or db.in_domain(d["domain"], domain)]
    if tree:
        named = ", ".join(f"{d['domain']} ({d['subtree']})" for d in tree[:DOMAINS])
        more = f", +{len(tree) - DOMAINS} more" if len(tree) > DOMAINS else ""
        parts.append(f"Active domains, most recent first: {named}{more}.")

    checkpoint = db.latest_by_type(conn, "checkpoint", domain=domain, exclude_contradicted=True)
    if checkpoint is not None:
        where = f" [{checkpoint['domain']}]" if checkpoint["domain"] else ""
        parts.append(f"Latest checkpoint{where} {checkpoint['created_at'][:16]} "
                     f"({checkpoint['uid']}): {_snip(checkpoint['content'], SNIPPET * 2)}")

    by_type = census["by_type"]
    for label, type_, cap in (("Open handoffs", "handoff", HANDOFFS),
                              ("Pitfalls on record", "anti_pattern", PITFALLS),
                              ("Recent notes", "note", NOTES)):
        rows = db.list_by_domain(conn, domain, type=type_, limit=cap,
                                 exclude_contradicted=True) if domain else \
            db.list_recent(conn, type=type_, limit=cap, exclude_contradicted=True)
        block = _lines(rows, label, cap, by_type.get(type_, 0))
        if block:
            parts.append("\n".join(block))

    flows = db.list_recent(conn, type="diagram", domain=domain, limit=FLOWS)
    if flows:
        titles = []
        for r in flows:
            meta = db.get_diagram_row(conn, r["uid"])
            titles.append(f"{r['uid']} {meta['title'] if meta else ''}".strip())
        parts.append("Documented flows (get_diagram(uid) to read one): " + "; ".join(titles))

    return _fit(parts, budget, tail=call_to_action(conn))


def _shares(sizes: list[int], room: int) -> list[int]:
    """Split `room` between sections, giving unused space to the hungry ones.

    An equal cut each, then whatever the short sections did not need is
    handed to the ones that overflowed. Two passes rather than a loop to
    convergence: a third round would move characters nobody can see.
    """
    if not sizes:
        return []
    even = room // len(sizes)
    spare = sum(even - s for s in sizes if s < even)
    hungry = sum(1 for s in sizes if s > even)
    bonus = spare // hungry if hungry else 0
    return [even + bonus if s > even else s for s in sizes]


def _cap(part: str, room: int) -> str:
    """Trim a section by dropping whole ITEMS off the end of it.

    Never mid-sentence: a memory cut in half reads as if it said something
    it did not, which is worse than not showing it at all.

    What it drops, it says. _lines writes the heading before this runs and
    can only count what the QUERY left out, so a section trimmed here would
    otherwise show two of four items and read as if there were two.
    """
    if len(part) <= room:
        return part
    lines = part.split("\n")
    if len(lines[0]) > room:
        return ""  # not even the heading fits: the section goes, and is counted
    marker = "  ... +{} not shown"
    kept, used = [lines[0]], len(lines[0]) + len(marker.format(len(lines) - 1))
    for line in lines[1:]:
        if used + len(line) + 1 > room:
            break
        kept.append(line)
        used += len(line) + 1
    left = len(lines) - len(kept)
    return "\n".join(kept + ([marker.format(left)] if left else []))


def _fit(parts: list[str], budget: int, *, tail: str) -> str:
    """Fit the sections into the budget, trimming rather than truncating.

    Every section gets a share (see _shares) instead of the budget being
    spent first-come-first-served. That ordering looked harmless and was
    not: against a real store, four pitfalls at full length took half the
    warm-up and the recent notes -- the part a session is most likely to
    act on -- fell off the end and were reported as "omitted for length".

    `tail` is what the reader is meant to DO next, so its room comes off
    the top rather than being the first thing a tight budget throws away.
    """
    room = max(budget - len(tail) - 1, 0)
    fitted = [_cap(p, s) for p, s in zip(parts, _shares([len(p) for p in parts], room))]

    # The share is a floor, not a ceiling. Whatever the short sections left
    # unspent goes back to the trimmed ones, earliest first -- otherwise a
    # section a few characters over its cut is dropped whole while a third
    # of the budget sits unused, which is what happened to the latest
    # checkpoint: one long line, nothing in it to trim, so all or nothing.
    spare = room - sum(len(f) + 1 for f in fitted if f)
    for i, part in enumerate(parts):
        if spare <= 0:
            break
        if len(fitted[i]) == len(part):
            continue
        grown = _cap(part, len(fitted[i]) + spare)
        spare -= len(grown) - len(fitted[i])
        fitted[i] = grown

    kept = [f for f in fitted if f]
    dropped = len(fitted) - len(kept)
    if dropped:
        kept.append(f"[{dropped} more section(s) omitted for length]")
    kept.append(tail)
    return "\n".join(kept)
