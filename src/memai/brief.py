"""The store as a few hundred words, for a reader that has not asked yet.

Everything else in memai answers a call. This renders the answer to a call
nobody made -- what a session should know before its first message, and
what an incoming prompt turns out to touch -- because an MCP server cannot
make an agent call anything. A host hook can put text in front of one, and
text is what this produces.

Plain text, not JSON: it is read by a language model, and a budget spent on
punctuation is a budget not spent on the memories. Every section is capped
and every cap is reported, so a brief that stopped short never reads as a
store that was empty.
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

CALL_TO_ACTION = (
    "Go deeper with the memai tools: pulse(domain) for one subject's state, "
    "search(query, domain) or recall(query) for anything specific, "
    "get_memory(uid) for a record in full, list_domains() for the tree. "
    "Write as you go -- note() a durable fact, anti_pattern() a pitfall, "
    "checkpoint() before a pause."
)


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


def session_brief(conn, *, domain: str = "", budget: int = DEFAULT_BUDGET) -> str:
    """What is already known, for a session that has not started working.

    No domain, because at session start nobody knows the subject yet: this
    is the store's own state, ordered by recency, and the drill-down is the
    agent's next move. Passing one narrows it the way pulse() does.
    """
    census = db.domain_census(conn, domain)
    if not census["total"]:
        return ""

    scope = domain or "the whole store"
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

    return _fit(parts, budget, tail=CALL_TO_ACTION)


def prompt_brief(conn, query: str, *, limit: int = 3, budget: int = DEFAULT_BUDGET) -> str:
    """What the store holds about the thing that was just asked.

    The session-start brief cannot know the subject; by the time a prompt
    arrives, its text IS the query. One hybrid search, the top few, as
    lines -- the recall an agent would have had to think to ask for.
    """
    query = " ".join((query or "").split())
    if len(query) < 8:
        return ""
    hits = db.search_hybrid(conn, query, limit=limit, collapse=True)
    if not hits:
        return ""
    parts = ["MemAI -- what the store already holds about this:"]
    for h in hits:
        where = f" [{h['domain']}]" if h["domain"] else ""
        after = f" (superseded by {', '.join(h['succeeded_by'])})" if h.get("succeeded_by") else ""
        flag = " (CONTRADICTED)" if h["confidence"] == db.CONFIDENCE_CONTRADICTED else ""
        parts.append(f"  - {h['type']} {h['uid']}{where}{flag}{after} {_snip(h['content'])}")
    return _fit(parts, budget,
                tail="Not necessarily relevant -- judge it. get_memory(uid) for one in full.")


def _fit(parts: list[str], budget: int, *, tail: str) -> str:
    """Drop whole sections from the end until it fits, and say one went.

    Truncating mid-sentence would leave a memory that reads as if it said
    something it did not, which is worse than not showing it. `tail` is
    what the reader is meant to DO next, so its room comes off the top
    rather than being the first thing a tight budget throws away.
    """
    kept: list[str] = []
    used = len(tail)
    for i, part in enumerate(parts):
        if used + len(part) > budget and kept:
            kept.append(f"[{len(parts) - i} more section(s) omitted for length]")
            break
        kept.append(part)
        used += len(part) + 1
    kept.append(tail)
    return "\n".join(kept)
