"""The store as a few hundred words, for a reader that has not asked yet.

session_brief renders what the store holds -- its size, active domains, the
latest checkpoint, one line counting what is pending per category, one counting
what is pinned -- kept with the call to action, so the budget never drops it --
and ends with the instruction to call must_read() and open the subject with pulse(domain)
before working, spelling out what the store's own casing policy means for the
path that instruction asks for. The SessionStart hook emits it (memai.hook);
the warm_up prompt returns it.

Plain text, not JSON, read by a language model. Every section is capped and
every cap is reported, so a brief that stopped short does not read as a store
that was empty.
"""

from __future__ import annotations

from memai import pending
from memai.store import domains, memories, search

# What a warm-up may cost: paid once per session, small next to a context window.
DEFAULT_BUDGET = 2400
SNIPPET = 220
DOMAINS = 8

# The pending line's words per category: (singular, plural).
LABELS = {
    "task": ("open task", "open tasks"),
    "anti_pattern": ("pitfall", "pitfalls"),
    "handoff": ("handoff", "handoffs"),
    "note": ("note", "notes"),
    "diagram": ("flow", "flows"),
    "checkpoint": ("checkpoint", "checkpoints"),
    "reasoning": ("reasoning", "reasonings"),
}

# Under `lower` and `upper` a domain is folded on writes and on reads, so any spelling finds the
# same rows; under `preserve` two spellings are two paths.
CASING = {
    "lower": "Domain paths are stored lowercase here, and a path passed in any "
             "case is folded to it.",
    "upper": "Domain paths are stored UPPERCASE here, and a path passed in any "
             "case is folded to it.",
    "preserve": "This store keeps the casing a path was written with, so "
                "'Acme/X100' and 'acme/x100' are two different domains -- reuse "
                "an existing one exactly as list_domains() spells it.",
}

# What the pending line asks for, depending on whether a task is open.
WORK_TASKS_FIRST = ("Call must_read(type='task') first and work through the open tasks, "
                    "then the other categories, before acting.")
WORK_EACH = "Call must_read(type=...) for each category before acting."
# The pin line: counts only, the headers come from must_read(..., pinned=true).
PIN_LINE = ("Pinned, read every one before acting: {said} -- "
            "must_read({domain}type=..., pinned=true), then get_memory(uid) each.")

# The tail of every brief: _fit reserves its room first, so it is never trimmed. {casing} comes
# from the store's active policy.
CALL_TO_ACTION = (
    "REQUIRED FIRST STEP, whatever the prompt asks -- a question, a greeting, a "
    "one-line fix, a git command: before any other tool, call pulse(domain) for "
    "the subject the prompt names and must_read() for what is still open, and "
    "read what they list -- what was decided, what was already tried, where the "
    "last session stopped. No task is too small to skip this. Until one of them "
    "is called, the memai guard refuses every other tool in this session; "
    "ToolSearch and the memai tools go through, so if they are not loaded yet "
    "-- an MCP connection comes up asynchronously -- load them first. A "
    "domain is a path, outermost scope first ('acme/x100/p200'). {casing} If the path is not obvious: list_domains() "
    "for the tree, recall(query) or search(query) to find a subject by name, "
    "get_memory(uid) for one record in full. Write as you go: note() a durable fact, anti_pattern() a pitfall, "
    "checkpoint() before a pause, set_confidence(uid, 'confirmed'|'contradicted') "
    "once evidence settles a claim you could not check when you wrote it."
)


def call_to_action(conn) -> str:
    """The instruction, with the casing line the store's policy calls for."""
    mode = domains.get_domain_case(conn)
    return CALL_TO_ACTION.format(casing=CASING.get(mode, CASING[domains.DOMAIN_CASE_DEFAULT]))


def _snip(text: str, limit: int = SNIPPET) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _said(found: list[dict]) -> str:
    return ", ".join(
        f"{c['count']} {LABELS[c['type']][0 if c['count'] == 1 else 1]}" for c in found)


def pending_line(conn, domain: str) -> str:
    """The count line for a scope plus what to call next, or "" when nothing is pending."""
    found = pending.counts(conn, domain)
    if not found:
        return ""
    ask = WORK_TASKS_FIRST if found[0]["type"] == memories.TASK_TYPE else WORK_EACH
    return f"Pending in {domain or 'this project'}: {_said(found)}. {ask}"


def pin_line(conn, domain: str) -> str:
    """The pins in scope as one counted sentence, or "" when nothing is pinned."""
    found = pending.pinned_counts(conn, domain)
    if not found:
        return ""
    return PIN_LINE.format(said=_said(found), domain=f"'{domain}', " if domain else "")


def session_brief(conn, *, domain: str = "", budget: int = DEFAULT_BUDGET,
                  project: str = "") -> str:
    """What is already known, for a session that has not started working.

    No domain, because at session start nobody knows the subject yet: this
    is the store's own state, ordered by recency, and the drill-down is the
    agent's next move. Passing one narrows it the way pulse() does.

    `project` is the name of the project `conn` is on, for the opening line.
    """
    census = domains.domain_census(conn, domain)
    if not census["total"]:
        return ""

    scope = domain or (f"project '{project}'" if project else "the whole store")
    parts = [f"MemAI long-term memory: {census['total']} memories in {scope}."]

    tree = [d for d in domains.list_domains(conn) if not domain or domains.in_domain(d["domain"], domain)]
    if tree:
        named = ", ".join(f"{d['domain']} ({d['subtree']})" for d in tree[:DOMAINS])
        more = f", +{len(tree) - DOMAINS} more" if len(tree) > DOMAINS else ""
        parts.append(f"Active domains, most recent first: {named}{more}.")

    checkpoint = search.latest_by_type(conn, "checkpoint", domain=domain, exclude_contradicted=True)
    if checkpoint is not None:
        where = f" [{checkpoint['domain']}]" if checkpoint["domain"] else ""
        parts.append(f"Latest checkpoint{where} {checkpoint['created_at'][:16]} "
                     f"({checkpoint['uid']}): {_snip(checkpoint['content'], SNIPPET * 2)}")

    line = pending_line(conn, domain)
    if line:
        parts.append(line)

    tail = "\n".join(p for p in (pin_line(conn, domain), call_to_action(conn)) if p)
    return _fit(parts, budget, tail=tail)


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
    """The section when it fits `room`, else "" so it is dropped whole.

    Every section is one line, so a cut would end mid-sentence, and a memory
    cut in half reads as if it said something it did not. _fit counts what
    this drops.
    """
    return part if len(part) <= room else ""


def _fit(parts: list[str], budget: int, *, tail: str) -> str:
    """Fit the sections into the budget, dropping whole sections that do not fit.

    Every section gets a share (see _shares) rather than the budget going to
    the sections in order, so a long section cannot starve the ones after it.

    `tail` is what the reader is meant to DO next, so its room comes off
    the top rather than being the first thing a tight budget throws away.
    """
    room = max(budget - len(tail) - 1, 0)
    fitted = [_cap(p, s) for p, s in zip(parts, _shares([len(p) for p in parts], room), strict=True)]

    # The share is a floor: what the short sections leave unspent goes back to the dropped ones,
    # earliest first.
    spare = room - sum(len(f) + 1 for f in fitted if f)
    for i, part in enumerate(parts):
        if spare <= 0:
            break
        if fitted[i]:
            continue
        grown = _cap(part, spare)
        spare -= len(grown)
        fitted[i] = grown

    kept = [f for f in fitted if f]
    dropped = len(fitted) - len(kept)
    if dropped:
        kept.append(f"[{dropped} more section(s) omitted for length]")
    kept.append(tail)
    return "\n".join(kept)
