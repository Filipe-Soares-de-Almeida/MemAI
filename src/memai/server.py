"""memai MCP server.

Tools for long-term agent memory: note/checkpoint/anti_pattern/
reasoning/handoff/diagram to write, search/recall/list_by_domain/
list_recent/timeline/list_domains/pulse to read, plus edit history, a relations
graph, a dedup-candidate scanner, confidence/status tracking, and help()
for self-documentation straight from these docstrings. Retrieval is FTS5
(BM25) keyword search over one ACID SQLite file -- it only narrows
candidates, the calling agent judges relevance.

Writer tool names match the `type` value they store (note stores
type='note', reasoning stores type='reasoning', ...), so what an agent
calls is exactly what search/list_* filter on.

A memory's `domain` is the subject it belongs to, and subjects contain
subjects: write it as a path, outermost first -- 'acme/x100/p200' is a
routine inside a module inside a product. Every read that takes a domain
covers its subdomains too, so one call asks about a whole product or
about exactly one routine, depending on how much of the path it gives.
list_domains() returns the tree that exists.

diagram is the one type whose body is not prose: it stores a graph, one
row per step, and generates the prose the retrieval side indexes. Its
graph is edited through diagram_* and read back through get_diagram().

Everything above answers a call, and an MCP server cannot make an agent
call anything. Three things here exist for that gap: INSTRUCTIONS, which
the host may inject; the warm_up prompt, invoked by the person rather than
the agent; and the memai-hook CLI (memai.hook), which puts the store's
state in a session's context without any of this being running. Writes
carry a per-process `session` stamp unless one is passed.
"""

from __future__ import annotations

import inspect
import json
import os

from mcp.server.fastmcp import FastMCP

from memai import autostart, brief, db, diagram_svg, hook_install, portable, sections

# Sent to the host in the initialize handshake, and injected into the
# model's context by the hosts that support it. Kept to a paragraph on
# purpose: it is paid on every request, exactly like a tool schema, and
# what it has to buy is the first call -- an agent that never calls pulse()
# has a memory server and no memory. The rest is in help().
INSTRUCTIONS = """\
Long-term memory that survives between sessions. Read before working:
pulse(domain) for the state of a subject, recall(query) or search(query)
for anything specific, list_domains() for the tree that exists. Write as
you go, not at the end: note() a durable fact, anti_pattern() a pitfall
worth not repeating, checkpoint() where the work stands before a pause,
diagram() a routine start to end. A claim you could not check says so in
its own body; set_confidence(uid, 'confirmed'|'contradicted') closes it
once the evidence turns up.

A domain is a path ('acme/x100/p200') and every read covers its
subdomains, so the same call asks about a product or one routine depending
on how much of the path it gives. help() documents every tool from its own
source."""

# Appended to INSTRUCTIONS while the user's settings register no memai hook.
# `{command}` is filled with the absolute path to this environment's
# memai-hook: the name alone is on PATH only for a shell with the environment
# activated, which a host's shell is not.
HOOKS_MISSING = """\
NOTE: no memai hook is registered in the user's settings, so nothing puts the
store in front of this session -- memai is read only when you call it, and a
session that forgets to call it starts blind. Say so in your first reply and
offer to run this, exactly as written (the name alone is not on PATH):

  {command} install

It registers SessionStart, PreCompact, Stop and PreToolUse in
`~/.claude/settings.json`, for every project -- the last of those is the guard
that refuses a memai write whose required text never arrived. `--check` reports what is registered, `--print` shows the block
without writing it."""


# Appended to INSTRUCTIONS while memai is registered for this session but part
# of what was installed is out of date. `{findings}` and `{commands}` are
# rendered by _stale_note; the command is absolute for the same reason as
# above.
INSTALL_STALE = """\
NOTE: memai is registered for this session, but part of the installation is out
of date -- an install does not keep itself current, so a hook or a skill copied
by an earlier version stays as it was:

{findings}

Say so in your first reply and offer to run this, exactly as written (the name
alone is not on PATH):

{commands}

memai's own hook entries are replaced rather than appended, the bundled skills
are copied over, and any file either has to overwrite is backed up first. Add
`--check` to report the state without writing anything."""


def _quoted(command: str) -> str:
    """A command a shell reads as one word."""
    return f'"{command}"' if " " in command else command


def _stale_note(command: str) -> str:
    """INSTALL_STALE for what hook_install.stale() reports, or "" when the
    installation is current."""
    stale = hook_install.stale()
    findings: list[str] = []
    commands: list[str] = []
    if stale["events"]:
        findings.append(f"  - {', '.join(stale['events'])}: not registered.")
    if stale["broken"]:
        findings.append(f"  - {', '.join(stale['broken'])}: registered, but the "
                        "command they fire is not on disk any more.")
    if stale["outdated"]:
        findings.append(f"  - {', '.join(stale['outdated'])}: registered through an "
                        "entry this version would write differently.")
    if stale["events"] or stale["broken"] or stale["outdated"]:
        commands.append(f"  {command} install")
    if stale["skills"]:
        findings.append("  - an update is waiting for these skills, and what is "
                        f"installed is untouched: {', '.join(stale['skills'])}.")
        commands.append(f"  {command} install --skills")
    if stale["agents"]:
        findings.append("  - an update is waiting for these subagents, and what is "
                        f"installed is untouched: {', '.join(stale['agents'])}.")
        commands.append(f"  {command} install --agents")
    if not findings:
        return ""
    return INSTALL_STALE.format(findings="\n".join(findings),
                                commands="\n".join(commands))


def _instructions() -> str:
    """INSTRUCTIONS, with a note appended when the installation needs a hand.

    HOOKS_MISSING when the user's settings register no memai hook,
    INSTALL_STALE when they do but an event is unregistered, a registered
    command has left the disk, an entry is not the one an install writes, or
    an installed skill is not the version this package ships.

    Read once, at import, from the user's settings alone -- the scope memai
    installs into. Unreadable settings are read as no registration.
    """
    try:
        command = _quoted(hook_install.hook_command())
        note = (_stale_note(command)
                if hook_install.registered(hook_install.user_settings_path())
                else HOOKS_MISSING.format(command=command))
    except Exception:
        return INSTRUCTIONS
    return f"{INSTRUCTIONS}\n\n{note}" if note else INSTRUCTIONS


mcp = FastMCP("memai", instructions=_instructions())


def _new_session_id() -> str:
    """This process's default `session` stamp for everything it writes.

    Derived here rather than asked of the caller. `session` was an optional
    free-text argument on every writer, which meant it was usually absent
    and the dashboard's session filter had nothing to filter -- the value
    of grouping a conversation's memories is real and the chance of an
    agent remembering to pass a consistent id on every call is not. An
    explicit `session=` still wins.
    """
    stamp = db.now_iso()[:16].replace("-", "").replace(":", "")
    return f"{stamp}-{os.getpid():04x}"


SESSION = _new_session_id()


# Which tools this process offers. A tool's schema -- name, description,
# argument types -- is sent with EVERY request for the whole session, so the
# full set is a fixed tax on every context window whether or not a session
# ever documents a flow or runs a curation pass. Naming the groups lets a
# session pay for what it uses.
#
# 'full' stays the default: dropping a tool an existing setup calls is not
# something to do to somebody quietly. help() still lists every tool in
# either case, and says which ones this process did not load, so an agent
# that needs one can be told rather than left guessing why it is missing.
TOOL_SETS = ("core", "diagrams", "curation")
_ACTIVE_SETS = frozenset(
    TOOL_SETS if (raw := os.environ.get("MEMAI_TOOLS", "full").strip().lower()) in ("", "full")
    else {"core", *(s.strip() for s in raw.split(",") if s.strip())}
)


_GROUP_OF: dict[str, str] = {}


def tool(group: str):
    """Register a tool with FastMCP when its group is active.

    Always returns the plain function: the module-level name has to stay
    callable either way, because help() reads its signature and docstring
    out of the code whether or not the schema was published.
    """
    def wrap(fn):
        _GROUP_OF[fn.__name__] = group
        if group in _ACTIVE_SETS:
            mcp.tool()(fn)
        return fn
    return wrap


def _row_to_dict(row) -> dict:
    """A memory row as a payload dict, with its cross-listings as `also`.

    `also_domains` never leaves here: it is the one-field mirror the FTS
    index reads (see db), and db.parse_domains is its inverse. A caller
    gets the paths as a list or not at all.
    """
    if row is None:
        return {}
    d = dict(row)
    blob = d.pop("also_domains", "")
    if blob:
        d["also"] = db.parse_domains(blob)
    # Blank on most rows, since most memories are not about anything that
    # goes stale -- and a field that is empty nine times out of ten still
    # costs its name in every result of every search.
    for optional in ("review_after", "source_ref"):
        if not d.get(optional):
            d.pop(optional, None)
    return d


SNIPPET_LIMIT = 400
# What one warm-up is allowed to cost, per list. Named rather than inline
# because pulse() reports which of these it hit: a brief that silently
# stopped at five reads as "there were five".
PULSE_NOTES = 5  # recent note()'d facts surfaced as warm-up breadcrumbs
PULSE_DIAGRAMS = 5  # documented flows named, never inlined -- see pulse()
PULSE_HANDOFFS = 5
PULSE_ANTI_PATTERNS = 10
# the type each pulse list is drawn from, for "how many did it leave out"
PULSE_LIST_TYPES = {
    "recent_notes": "note",
    "handoffs": "handoff",
    "anti_patterns": "anti_pattern",
    "diagrams": "diagram",
    "latest_checkpoint": "checkpoint",
}

# Memory type tag per writer -- the retrieval tools filter on these exact
# strings (search/recall/list_*(type=...)). Each writer tool is named
# after the type it stores, so tool name and stored type cannot drift.
TYPE_NOTE = "note"                  # note()
TYPE_CHECKPOINT = "checkpoint"      # checkpoint()
TYPE_ANTI_PATTERN = "anti_pattern"  # anti_pattern()
TYPE_REASONING = "reasoning"        # reasoning()
TYPE_HANDOFF = "handoff"            # handoff()
TYPE_DIAGRAM = "diagram"            # diagram()


def _with_est_tokens(d: dict) -> dict:
    """Annotate a result with `est_tokens` for its FULL content.

    Call before truncating: the number a caller budgets a get_memory(uid)
    with is what the whole record costs, not what the snippet cost.
    """
    d["est_tokens"] = db.est_tokens(len(d.get("content", "")))
    return d


def _snippet_dict(d: dict) -> dict:
    """Truncate content in list-style results so N hits can't blow the
    caller's token budget. Full content is one get_memory(uid) away --
    this only needs to be enough for the agent to judge which
    candidates are worth opening.

    Carries `est_tokens` for the full record, so the caller can price that
    call before making it.
    """
    _with_est_tokens(d)
    content = d.get("content", "")
    if len(content) > SNIPPET_LIMIT:
        d["content"] = content[:SNIPPET_LIMIT].rstrip() + f"... [+{len(content) - SNIPPET_LIMIT} chars, see get_memory(uid)]"
    return d


def _listing(rows) -> dict:
    """Rows as a list result: {"results": [...], "est_tokens": N}.

    Each result is snippet-truncated and carries its own `est_tokens`; the
    top-level one is their sum -- what opening everything this response
    returned would cost.
    """
    results = [_snippet_dict(_row_to_dict(r)) for r in rows]
    return {"results": results, "est_tokens": sum(r["est_tokens"] for r in results)}


def _read(conn, rows):
    """Count these as read, and hand them straight back.

    Every tool that puts memory content in front of a caller goes through
    here, and only these tools do -- see db.record_recall for why the
    dashboard deliberately does not.

    A row that came out of a search carries `match_source`, and that goes
    with the count: it is what lets the store say later how much of what it
    holds is ever found rather than merely listed, over real queries,
    without anyone reading transcripts. Reads with no search behind them (a
    warm-up, a list, get_memory) are counted plainly.

    What this is NOT for is ranking. A memory read twice a year is about a
    rarer subject, not a worse one, and boosting what is already popular
    buries the rare thing further every time it loses.
    """
    sources = {r["uid"]: r["match_source"] for r in rows
               if isinstance(r, dict) and r.get("match_source")}
    db.record_recall(conn, [r["uid"] for r in rows], sources=sources or None)
    return rows


def _list_scoped(conn, domain: str, type: str, limit: int) -> list:
    """Recency-ordered rows of one type, for a warm-up: scoped to a domain
    subtree if given, else global.

    Contradicted rows are left out here and nowhere else. This feeds
    pulse(), which presents what it returns as the current state of a
    scope, and a pitfall that turned out not to be one reads there as a
    pitfall. list_by_domain()/list_recent() still return them: a caller
    asking a scope for everything means everything.
    """
    if domain:
        return db.list_by_domain(conn, domain, type=type, limit=limit,
                                 exclude_contradicted=True)
    return db.list_recent(conn, type=type, limit=limit, exclude_contradicted=True)


def _coerce_domain(conn, domain: str) -> tuple[str, dict | None]:
    """Apply the store's domain policy before a write: casing, and path shape.

    Returns (coerced_domain, warning). warning is None when the domain
    already conforms; otherwise it describes the adjustment so the tool
    can echo it back to the agent (coerce-and-warn, never reject) -- which
    is also how an agent learns that 'acme / x100' was filed as
    'acme/x100'.
    """
    coerced, mode = db.coerce_domain(conn, domain)
    if coerced == domain:
        return coerced, None
    return coerced, {"from": domain, "to": coerced, "policy": mode}


# What to do about a collision the write just revealed. One line, because
# it is paid for on every write that trips the threshold.
SIMILAR_HINT = (
    "the store already held these. If this one CORRECTS one of them, "
    "edit_memory(uid, ...) or forget(uid, superseded_by=<new uid>); if it "
    "restates one, forget() the copy; if they are genuinely different "
    "facts, link_memories() and leave both."
)


TAGS_HINT = (
    "this memory has no tags. Search is BM25 over content and tags, so it is "
    "findable only by the words its own body uses. edit_memory(uid, "
    "tags='...') adds the identifier, the symbol and the plain-language "
    "phrasing someone will type instead."
)


def _write_result(conn, uid: str, warning: dict | None, also: str,
                  tags: str = "") -> dict:
    """The dict a writer tool returns: the uid, and whatever was adjusted.

    `also` is echoed only when it was asked for, because the stored set can
    differ from what was written -- casing policy applies, and a path the
    memory's own domain already covers is dropped (see
    db.apply_link_policy). An agent that cross-listed into three subjects
    and got two back learns which reading was redundant.

    `tags_indexed` counts what reached the tags column, and `tags_hint`
    replaces it with what an untagged memory costs in a BM25-only store.

    `similar` is the write-time half of dedup: the agent still has the
    context that produced this text, so it is the one moment when "the
    store already said something close to this" can be acted on for free.
    Present only when something crossed the threshold -- a store with no
    collision never sees the field, and the write is never blocked by one.
    """
    # the project as well as the uid: the active project is switched from the
    # dashboard and a running server follows on its next call, so this is
    # where a writer learns which file its memory landed in
    result = {"uid": uid, "project": db.active_project()}
    # the count is the feedback: a writer sees what it indexed while it
    # still holds the context that would supply the missing words
    result["tags_indexed"] = len([t for t in tags.split(",") if t.strip()])
    if not result["tags_indexed"]:
        result["tags_hint"] = TAGS_HINT
    if warning:
        result["domain_adjusted"] = warning
    if also:
        result["also"] = db.get_domain_links(conn, uid)
    similar = db.similar_memories(conn, uid)
    if similar:
        result["similar"] = similar
        result["similar_hint"] = SIMILAR_HINT
    return result


@tool("core")
def note(title: str, content: str, domain: str = "", also: str = "", tags: str = "",
         session: str = "", review_after: str = "", source_ref: str = "") -> dict:
    """Save a general long-term memory (fact, decision, finding). Stored as type='note'.

    Timeless knowledge -- retrieved by relevance, not recency. Bring it
    back with recall() (or search(type='note')); pulse() also shows the
    few most recent ones as warm-up breadcrumbs.

    title: one line naming what this memory is about, in the words someone
    would look for it by. It is what a list shows instead of the opening of
    the body, and it outweighs every other field in search, so a title that
    repeats the type ("note about the parser") names nothing. At most 120
    characters, and a name that needs more than that is summarizing the
    body instead of naming it.

    domain: the subject this belongs to, as a path from the outermost
    scope in ('acme/x100/p200'). File it as deep as the fact is specific
    -- a note about one routine goes on the routine, and still comes back
    when someone asks about the module or the product above it.

    also: other domain paths this belongs to, comma-separated. `domain` is
    where the memory LIVES -- one path, one parent chain. `also` is for the
    subjects that cut ACROSS that tree: the same routine belongs to the
    module it runs in and to the end-to-end flow it is one step of, and
    neither of those is the other's ancestor. Every read scoped to any of
    those paths returns it. A path that `domain` already sits under is
    dropped as redundant -- the result echoes what was stored.

    tags: comma-separated keywords and synonyms. Retrieval is BM25 over
    content, tags and domain paths, and tags weigh second only to the body,
    so they are where a memory becomes findable by words its own text never
    uses -- the identifier, the symbol, the error string, the plain-language
    phrasing someone will actually type. A memory with none is reachable
    only by quoting itself.

    review_after: when this stops being safe to trust unchecked, as a date
    ('2026-11-01') or a span from today ('90d'). pulse() counts what is
    overdue in a scope as `scope.stale` and optimize_scan lists it. Leave
    it empty for anything that does not go stale -- most facts do not, and
    a date nobody meant is worse than none.

    source_ref: what the fact came FROM -- a path, a URL, a table name --
    so a later pass can check the claim against the thing itself instead
    of inferring what to check from the wording.
    """
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(conn, type=TYPE_NOTE, content=content, title=title,
                               domain=domain, also=also, session=session or SESSION,
                               tags=tags, review_after=review_after,
                               source_ref=source_ref)
        return _write_result(conn, uid, warning, also, tags)


@tool("core")
def checkpoint(
    title: str,
    intent: str,
    established: str,
    pursuing: str,
    open_questions: str,
    session: str = "",
    domain: str = "",
    also: str = "",
    tags: str = "",
) -> dict:
    """Snapshot current working state (intent/established/pursuing/open_questions).

    A summary of where the work stands, so the next session picks up
    the right bearing via pulse(). Fields are free-length; still prefer
    a readable summary here and put timeless detail into note() --
    checkpoints are read for bearing, not as an archive. Stored as
    type='checkpoint'.

    title: one line naming what this memory is about, in the words someone
    would look for it by. It is what a list shows instead of the opening of
    the body, and it outweighs every other field in search, so a title that
    repeats the type ("note about the parser") names nothing. At most 120
    characters, and a name that needs more than that is summarizing the
    body instead of naming it.

    also: other domain paths this belongs to, comma-separated -- the
    cross-cutting subjects beside the one it is filed under. See note().

    `tags` carries the synonyms the body never uses: retrieval is BM25 over
    content and tags, so a memory with none is reachable only by quoting
    itself. See note() for what belongs there.
    """
    content = sections.render(TYPE_CHECKPOINT, {
        "intent": intent, "established": established,
        "pursuing": pursuing, "open_questions": open_questions})
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(
            conn, type=TYPE_CHECKPOINT, content=content, title=title,
            domain=domain, also=also, session=session or SESSION, tags=tags,
        )
        return _write_result(conn, uid, warning, also, tags)


@tool("core")
def anti_pattern(
    title: str, pattern: str, why_wrong: str, instead: str, domain: str = "",
    also: str = "", tags: str = "", session: str = "", review_after: str = "",
    source_ref: str = "",
) -> dict:
    """Record a mistake/temptation to avoid repeating, and the correct approach.

    Stored as type='anti_pattern'; open ones for a domain are surfaced by pulse().
    `also` cross-lists it into further domain paths, `review_after` dates
    when to recheck it and `source_ref` says what it came from -- see note().

    title: one line naming what this memory is about, in the words someone
    would look for it by. It is what a list shows instead of the opening of
    the body, and it outweighs every other field in search, so a title that
    repeats the type ("note about the parser") names nothing. At most 120
    characters, and a name that needs more than that is summarizing the
    body instead of naming it.

    `tags` carries the synonyms the body never uses: retrieval is BM25 over
    content and tags, so a memory with none is reachable only by quoting
    itself. See note() for what belongs there.
    """
    content = sections.render(TYPE_ANTI_PATTERN, {
        "pattern": pattern, "why_wrong": why_wrong, "instead": instead})
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(
            conn, type=TYPE_ANTI_PATTERN, content=content, title=title,
            domain=domain, also=also, session=session or SESSION, tags=tags,
            review_after=review_after, source_ref=source_ref,
        )
        return _write_result(conn, uid, warning, also, tags)


@tool("core")
def reasoning(
    title: str,
    hypothesis: str,
    reasoning: str,  # shadows the tool's own name inside the body; nothing here calls it
    result: str,
    revised_belief: str,
    next_time: str,
    domain: str = "",
    also: str = "",
    tags: str = "",
    session: str = "",
    review_after: str = "",
    source_ref: str = "",
) -> dict:
    """Record an analysis worth keeping: what was thought, and what it settled.

    For the PROCESS, not the fact it produced -- note() takes the fact.
    Stored as type='reasoning'; filter search/list_* with type='reasoning'
    to get these back.

    title: one line naming what this memory is about, in the words someone
    would look for it by. It is what a list shows instead of the opening of
    the body, and it outweighs every other field in search, so a title that
    repeats the type ("note about the parser") names nothing. At most 120
    characters, and a name that needs more than that is summarizing the
    body instead of naming it.

    hypothesis: what you believed going in, as a claim that could be wrong.
    reasoning: how you tested it -- what you read, ran or compared.
    result: what came back. The measurement, not the interpretation.
    revised_belief: what you believe now, and where it differs from the
    hypothesis. Say plainly when the hypothesis survived unchanged.
    next_time: what someone hitting this again should do differently.

    `also`, `review_after` and `source_ref` behave as in note().

    `tags` carries the synonyms the body never uses: retrieval is BM25 over
    content and tags, so a memory with none is reachable only by quoting
    itself. See note() for what belongs there.
    """
    content = sections.render(TYPE_REASONING, {
        "hypothesis": hypothesis, "reasoning": reasoning, "result": result,
        "revised_belief": revised_belief, "next_time": next_time})
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(conn, type=TYPE_REASONING, content=content, title=title,
                               domain=domain, also=also, session=session or SESSION,
                               tags=tags,
                               review_after=review_after, source_ref=source_ref)
        return _write_result(conn, uid, warning, also, tags)


@tool("core")
def handoff(title: str, content: str, domain: str = "", also: str = "",
            tags: str = "", session: str = "") -> dict:
    """Leave a note for another agent/session picking up this work.

    Stored as type='handoff'; open ones for a domain are surfaced by pulse().
    `also` cross-lists it into further domain paths -- see note().

    title: one line naming what this memory is about, in the words someone
    would look for it by. It is what a list shows instead of the opening of
    the body, and it outweighs every other field in search, so a title that
    repeats the type ("note about the parser") names nothing. At most 120
    characters, and a name that needs more than that is summarizing the
    body instead of naming it.

    `tags` carries the synonyms the body never uses: retrieval is BM25 over
    content and tags, so a memory with none is reachable only by quoting
    itself. See note() for what belongs there.
    """
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = db.insert_memory(conn, type=TYPE_HANDOFF, content=content, title=title,
                               domain=domain, also=also, session=session or SESSION,
                               tags=tags)
        return _write_result(conn, uid, warning, also, tags)


def _errors(errors: list[str]) -> dict:
    return {"ok": False, "errors": errors}


def _capped(body: str) -> str:
    """Keep one rendered diagram from eating a whole context window."""
    budget = db.DIAGRAM_BODY_BUDGET
    if len(body) <= budget:
        return body
    return body[:budget].rstrip() + (
        f"\n... [+{len(body) - budget} chars; open the admin dashboard for the full diagram]"
    )


@tool("diagrams")
def diagram(
    title: str,
    nodes: list[dict],
    edges: list[dict],
    summary: str = "",
    domain: str = "",
    also: str = "",
    session: str = "",
    tags: str = "",
    kind: str = "flowchart",
    review_after: str = "",
    source_ref: str = "",
) -> dict:
    """Document what a routine does, start to end, as a graph. Stored as type='diagram'.

    For a PROCESS, not a fact: note() records what is true, checkpoint()
    where the work stands, this one how a routine runs. Every step is a
    separate object that can carry its own explanation and its own links
    to other memories, which is what makes a diagram the source of truth
    for its domain instead of one more wall of prose.

    Keep every `label` objective -- what happens at that step, nothing
    more. The reasoning, caveats and history belong in that node's
    `note`, where they explain without cluttering the flow.

    nodes: [{"key": "load", "label": "Read the export window",
             "shape": "step", "note": "optional long explanation"}]
    edges: [{"from": "load", "to": "check", "label": "optional branch"}]

    key: stable id the edges refer to; letters, digits, '_' or '-'.
    shape: start|step|decision|io|end. Exactly one 'start' is required
    and every node must be reachable from it. Cycles are allowed -- a
    retry loop is a real flow, not a mistake.

    also: other domain paths this flow belongs to, comma-separated. `domain`
    is the routine's own place in the tree; `also` is for the flows that run
    ACROSS routines -- several of them can be steps of one end-to-end
    process without any of them being the parent of the others. Cross-list
    each into that process's path and asking about it returns all of them,
    instead of hoping one search phrasing reaches every one.

    `review_after` and `source_ref` behave as in note(), and a flow is
    exactly the kind of memory they are for: it describes code, and the
    code moves.

    Returns {"uid": ...}, or {"ok": False, "errors": [...]} with nothing
    written at all. Node positions are computed and stored server-side,
    so the flow renders identically for every reader -- see get_diagram().
    """
    with db.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid, errors = db.insert_diagram(
            conn, title=title, nodes=nodes, edges=edges, summary=summary,
            kind=kind, domain=domain, also=also, session=session or SESSION, tags=tags,
            review_after=review_after, source_ref=source_ref,
        )
        if errors:
            return _errors(errors)
        return _write_result(conn, uid, warning, also, tags)


@tool("diagrams")
def diagram_node(
    uid: str,
    key: str,
    label: str | None = None,
    shape: str | None = None,
    note: str | None = None,
    delete: bool = False,
) -> dict:
    """Add, patch or remove one step of a diagram.

    Only the arguments you pass are touched, so patching a note leaves
    the label alone; pass note="" to clear one. delete=True removes the
    step together with its edges and its memory links.

    The whole-graph rules are relaxed here on purpose: a step may sit
    unattached until you add its edges, which is what lets a flow be
    built up across several calls. diagram() enforces them.
    """
    with db.connect() as conn:
        if delete:
            ok, errors = db.delete_diagram_node(conn, uid, key)
        else:
            ok, errors = db.upsert_diagram_node(conn, uid, key, label=label, shape=shape, note=note)
    return {"ok": True, "node_key": key} if ok else _errors(errors)


@tool("diagrams")
def diagram_edge(
    uid: str, from_key: str, to_key: str, label: str = "", delete: bool = False
) -> dict:
    """Wire two steps of a diagram together, relabel that wire, or remove it.

    label carries the condition on a branch out of a decision node
    ('yes', 'no', 'on timeout'). Calling again with the same endpoints
    updates the label instead of adding a second edge between them.
    """
    with db.connect() as conn:
        if delete:
            ok, errors = db.delete_diagram_edge(conn, uid, from_key, to_key)
        else:
            ok, errors = db.upsert_diagram_edge(conn, uid, from_key, to_key, label=label)
    return {"ok": True} if ok else _errors(errors)


@tool("diagrams")
def diagram_link(
    uid: str, node_key: str, target_uid: str,
    relation_type: str = "explains", delete: bool = False,
) -> dict:
    """Attach another memory to one specific step of a diagram.

    What turns a diagram into an index of its domain: the step states
    what happens, the linked note/anti_pattern/reasoning states why it is
    that way. Point at the step the memory actually concerns -- for an
    edge to the diagram as a whole use link_memories() instead.

    get_memory() on the linked memory reports the diagrams that reference
    it, so the connection is visible from both ends.
    """
    with db.connect() as conn:
        if delete:
            ok = db.delete_node_link(conn, uid, node_key, target_uid)
            errors = [] if ok else [f"no link from node {node_key!r} to {target_uid!r}"]
        else:
            ok, errors = db.add_node_link(conn, uid, node_key, target_uid, relation_type)
    return {"ok": True} if ok else _errors(errors)


@tool("diagrams")
def diagram_jump(
    uid: str, node_key: str, peer_uid: str, peer_node: str = "",
    label: str = "", delete: bool = False,
) -> dict:
    """Continue one step of a flow into ANOTHER flow, optionally at one of its steps.

    Not the same statement as diagram_link: that attaches prose explaining
    a step, this says the rest of this branch is documented elsewhere. Use
    it where a routine hands off -- a sub-process, an error path owned by
    another flow, a variant of the same job.

    Leave `peer_node` empty to arrive at the target diagram as a whole.
    Stored once and read from both ends, so the return trip already exists
    and get_diagram(format='json') reports it on both diagrams. `uid` and
    `node_key` are this diagram's side either way, which is also how a jump
    is deleted from the receiving end.
    """
    with db.connect() as conn:
        if delete:
            ok = db.delete_diagram_jump(conn, uid, node_key, peer_uid, peer_node)
            errors = [] if ok else [f"no jump between {node_key!r} and {peer_uid!r}"]
        else:
            ok, errors = db.add_diagram_jump(
                conn, uid, node_key, peer_uid, peer_node, label=label)
    return {"ok": True} if ok else _errors(errors)


@tool("diagrams")
def diagram_relayout(uid: str) -> dict:
    """Recompute a diagram's stored node positions from scratch.

    Positions live in the store, not in a viewer, so every reader sees
    the same picture and positions hand-adjusted in the admin dashboard
    persist. This discards those adjustments and rebuilds the layered
    arrangement -- the fix for a diagram dragged into a mess.
    """
    with db.connect() as conn:
        moved = db.relayout_diagram(conn, uid)
    return {"ok": moved > 0, "nodes": moved}


_DIAGRAM_FORMATS = ("mermaid", "text", "json", "svg", "svg-interactive")


@tool("core")
def get_diagram(uid: str, format: str = "mermaid") -> dict:
    """Read a diagram back: format='svg-interactive' to show it, 'json' to reason about it.

    Formats: 'svg-interactive' (canvas drawing in a pan/zoom shell -- the one
    to SHOW), 'svg' (same drawing as a plain file, to attach or link),
    'mermaid' (portable, but re-lays out and DISCARDS the arrangement the
    user made), 'text' (the prose projection), 'json' (the full graph with
    positions, notes and links -- the only round-trippable one, and the one
    to reason over).

    Both SVG formats write the markup to a file and return its path plus a
    thin index of the steps; the payload is deliberately too small to draw
    from. The returned `next_step` says what to do with the path, at the
    point where it matters. help(command='get_diagram') has the rest.
    """
    if format not in _DIAGRAM_FORMATS:
        return _errors([f"unknown format {format!r}; use "
                        f"{', '.join(repr(f) for f in _DIAGRAM_FORMATS)}"])
    with db.connect() as conn:
        data = db.get_diagram(conn, uid)
        if data is None:
            return _errors([f"{uid} is not a diagram"])
        db.record_recall(conn, [uid])
        if format == "json":
            return {"format": "json", **data}
        if format in ("svg", "svg-interactive"):
            return _write_render(conn, uid, data, format)
        body = (
            db.render_diagram_text(conn, uid) if format == "text"
            else db.render_diagram_mermaid(conn, uid)
        )
    out = {"uid": uid, "title": data["title"], "format": format,
           "body": _capped(body)}
    if format == "mermaid":
        # Said here as well as in the docstring, because by now the docstring
        # is behind the caller and this is what it is looking at. A request
        # to "render the diagram" answered with mermaid silently swaps the
        # user's arrangement for a fresh layout.
        out["note"] = (
            "mermaid re-lays out the flow and discards the stored positions. "
            "To show the arrangement the user actually made, call again with "
            "format='svg-interactive' and emit the returned file inline.")
    return out


def _write_render(conn, uid: str, data: dict, format: str) -> dict:
    """Draw, write, sweep, and report -- without the markup in the payload.

    The index of steps IS the payload: labels and link targets, so the
    caller can talk about the diagram it just rendered, but not the notes,
    which are already in the file and are most of its size.
    """
    interactive = format == "svg-interactive"
    inline_target = None
    if interactive:
        # TWO files, because the two uses genuinely differ. Opening a file
        # needs a document -- doctype, charset, a body whose background is
        # not white behind a dark diagram. Embedding in a reply needs the
        # opposite: no doctype and no body, which most inline renderers
        # reject, and no styling that would reach the host page. Writing one
        # and telling the caller which part to cut out is the version of
        # this that breaks quietly.
        markup = diagram_svg.render_interactive(data, standalone=True)
        viewbox = diagram_svg.render_svg(data)[1]
        inline_target = db.renders_dir() / f"diagram-{uid}.inline.html"
        inline_target.write_text(
            diagram_svg.render_interactive(data), encoding="utf-8")
    else:
        markup, viewbox = diagram_svg.render_svg(data)
    target = db.renders_dir() / f"diagram-{uid}.{'html' if interactive else 'svg'}"
    target.write_text(markup, encoding="utf-8")
    swept = db.prune_renders(db.get_svg_retention(conn), keep=target)
    links: dict[str, list[str]] = {}
    for link in data.get("links") or []:
        links.setdefault(link["node_key"], []).append(link["target_uid"])
    return {
        "uid": uid,
        "title": data["title"],
        "format": format,
        "path": str(target),
        # The payload cannot be drawn from -- that is the point of writing the
        # file -- so it says what to do with it instead. Without this, a
        # caller that has the path and a way to render inline still has to
        # infer that reading the file is the intended next step.
        **({"inline_path": str(inline_target)} if inline_target else {}),
        "next_step": (
            "read `inline_path` and put its contents in your reply -- that "
            "is what displays the diagram. It is a fragment on purpose: no "
            "doctype, no <body>, nothing that touches the host page, which "
            "is what an inline renderer needs. `path` is the same drawing "
            "as a standalone document, for opening or sending as a file. "
            "Sending a file instead of emitting the fragment does NOT "
            "display anything, it gives the user something to open later. "
            "Yes, emitting it costs tokens: that is the work, not an "
            "overrun."
            if interactive else
            "send or link this file to show the diagram, marked to render "
            "rather than to download; read it only if you need the markup"),
        "bytes": len(markup.encode("utf-8")),
        "viewbox": [round(v) for v in viewbox],
        "nodes": [
            {"key": n["key"], "label": n["label"], "shape": n["shape"],
             **({"links": links[n["key"]]} if n["key"] in links else {})}
            for n in data["nodes"]
        ],
        "edges": len(data["edges"]),
        "retention": swept["mode"],
        "pruned": swept["pruned"],
    }


@tool("core")
def search(query: str, domain: str = "", type: str = "", limit: int = 10) -> dict:
    """Keyword search over memory content+tags+domain: FTS5 BM25.

    Each result is annotated with match_source ("fts", or "uid" for the row
    a pasted identifier names) and fts_rank (bm25, lower = better). The
    search only widens the candidate set -- judge the returned candidates
    yourself.

    SPEND TERMS FREELY. Every space-separated term is asked for separately
    and a row matching more of them ranks higher, so piling on synonyms,
    the identifier, the routine name and the plain-language phrasing into
    one query costs one call and finds strictly more. Twenty terms beat
    ten. Write a sentence if that is what you have -- common words score
    near zero and cost nothing, so there is nothing to strip.

    Only active memories by default.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated per result -- call get_memory(uid) for the full
    record; a result's `est_tokens` estimates what that full record costs,
    and the top-level `est_tokens` is the sum over the results.

    Two annotations worth acting on. `succeeded_by` means something in the
    store supersedes this memory: read that one instead. `collapsed` lists
    near-identical results folded into this one, so a fact written five
    times spends one slot -- raise `limit` if you want the copies.

    A memory marked confidence='contradicted' sorts behind everything that
    still holds, but it does come back: knowing a claim was ruled out is
    worth a slot, and it is what stops it being written again.

    A diagram ranks like any other memory: it comes back when it matches
    the query, in the position its score earns. Nothing lifts a type to the
    top, so a flow in the results is a flow this query actually hit -- and
    when one does show up it is worth opening first, because it states a
    whole routine the surrounding notes only annotate.

    type filters (one writer each): 'note', 'reasoning', 'checkpoint',
    'anti_pattern', 'handoff', 'diagram'. Ask for type='diagram' to sweep
    the documented flows on purpose. To recall note()'d knowledge
    specifically, recall() is the sugar for search(type='note') -- which
    also means recall() never surfaces a diagram; use search() for that.

    domain scopes to a path AND everything under it: domain='acme/x100'
    searches the module and each of its routines. Give more of the path to
    narrow it. A domain naming only the deep end of a path ('p200') is
    resolved to the branches it sits in -- every result carries its real
    `domain`, which is where to read what the filter actually covered.
    """
    with db.connect() as conn:
        results = _read(conn, db.search_ranked(conn, query, domain=domain, type=type,
                                              limit=limit, collapse=True))
    return _listing(results)


@tool("core")
def recall(query: str, domain: str = "", limit: int = 10) -> dict:
    """Recall long-term knowledge saved with note() (type='note').

    The dedicated verb for "bring back what I noted": a BM25 search scoped
    to type='note', ranked by relevance -- which is what you want for
    timeless facts/rules/decisions. note() has no
    recency warm-up hook the way checkpoints have pulse(); this (or
    search(type='note')) is how notes come back.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated -- call get_memory(uid) for the full record; a
    result's `est_tokens` estimates what that full record costs, and the
    top-level `est_tokens` is the sum over the results.

    domain scopes to a path and everything nested under it, and resolves a
    bare deep segment the same way search() does. Results carry the same
    `succeeded_by` / `collapsed` annotations search() explains.
    """
    with db.connect() as conn:
        results = _read(conn, db.search_ranked(conn, query, domain=domain, type=TYPE_NOTE,
                                              limit=limit, collapse=True))
    return _listing(results)


@tool("core")
def list_by_domain(
    domain: str, type: str = "", limit: int = 50, subtree: bool = True
) -> dict:
    """List active memories for a domain and its subdomains, most recent first.

    Fallback when search misses. domain is a path, matched from the
    outermost segment in: 'acme/x100' lists the module's own memories plus
    every routine under it. Pass subtree=False for what is filed at
    exactly that path and nowhere deeper.

    A domain that matches no path is retried as a run of segments INSIDE
    one, so list_by_domain('p200') still finds the routine once it lives
    at 'acme/x100/p200'. The literal reading wins whenever it has rows,
    and an ambiguous name (the same code under two modules) covers both
    branches rather than picking one -- each row's `domain` says which
    branch it came from. list_domains() is the way to see the paths first.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated per result -- call get_memory(uid) for the full
    record; a result's `est_tokens` estimates what that full record costs,
    and the top-level `est_tokens` is the sum over the results.
    """
    with db.connect() as conn:
        rows = _read(conn, db.list_by_domain(conn, domain, type=type, limit=limit,
                                            subtree=subtree))
    return _listing(rows)


@tool("core")
def list_recent(
    type: str = "", domain: str = "", limit: int = 20, subtree: bool = True
) -> dict:
    """List the most recent active memories, optionally filtered by type/domain.

    A domain covers its subdomains, and a bare deep segment resolves to the
    branches holding it (see list_by_domain); subtree=False narrows to that
    exact path.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated per result -- call get_memory(uid) for the full
    record; a result's `est_tokens` estimates what that full record costs,
    and the top-level `est_tokens` is the sum over the results.
    """
    with db.connect() as conn:
        rows = _read(conn, db.list_recent(conn, type=type, domain=domain, limit=limit,
                                         subtree=subtree))
    return _listing(rows)


@tool("core")
def timeline(
    uid: str = "", query: str = "", before: int = 3, after: int = 3,
    domain: str = "", type: str = "",
) -> dict:
    """What else was being written around one memory, in creation order.

    For the question search cannot ask: not what mentions this record, but
    what was being written when it was. Neighbours are picked by time
    alone, so they come back whether or not they share a word with the
    anchor -- around a checkpoint, that is the notes and pitfalls of the
    same stretch of work.

    One of uid or query is required. uid names the anchor outright; query
    searches for it and takes the top hit (the same search search() runs,
    scoped by domain/type). Give both and uid wins. The response
    reports `anchored_by` ('uid' or 'query') and the whole anchor record,
    so which record the timeline is built around is never a guess.

    Returns {"anchored_by": ..., "anchor": {...}, "before": [...],
    "after": [...]}: `before` is the `before` records created immediately
    before the anchor and `after` the `after` records created immediately
    after it, both oldest first, so before + [anchor] + after reads
    straight down the clock. Neither list contains the anchor.

    domain and type narrow the NEIGHBOURHOOD, not the anchor: a memory
    named by uid comes back as named, and the records around it are the ones
    matching the filters. domain covers a path and everything under it, plus
    what is cross-listed into it, and resolves a bare deep segment the way
    the other scoped reads do. Archived records are left out of the
    neighbourhood, as everywhere else by default.

    Each record is snippet-truncated with `est_tokens` for its full content
    -- call get_memory(uid) to open one.
    """
    if not uid and not query:
        return _errors(["timeline needs uid or query: one names the anchor, "
                        "the other searches for it"])
    with db.connect() as conn:
        if uid:
            anchored_by = "uid"
            anchor = db.get_memory(conn, uid)
            if anchor is None:
                return _errors([f"no memory {uid}"])
        else:
            anchored_by = "query"
            hits = db.search_ranked(conn, query, domain=domain, type=type, limit=1)
            if not hits:
                return _errors([f"no memory matches query: {query}"])
            # Re-read as a record: a search hit carries retrieval annotations
            # (match_source, ranks) that are not part of the memory.
            anchor = db.get_memory(conn, hits[0]["uid"])
        older, newer = db.timeline_neighbours(
            conn, anchor, before=before, after=after, domain=domain, type=type)
        _read(conn, [anchor, *older, *newer])
    return {
        "anchored_by": anchored_by,
        "anchor": _snippet_dict(_row_to_dict(anchor)),
        "before": [_snippet_dict(_row_to_dict(r)) for r in older],
        "after": [_snippet_dict(_row_to_dict(r)) for r in newer],
    }


@tool("core")
def list_projects() -> dict:
    """The projects in this home, and which one every call here reads and writes.

    A project is one SQLite file with its own memories, domains, relations
    and diagrams. The active one is switched in the admin dashboard, and a
    running server follows on its next call -- so every write's result names
    the project it landed in, and pulse() names the one it read. Each entry
    carries `name`, `memories` (the active rows) and `active`. Names are
    matched without regard to case wherever a tool takes one.
    """
    return {
        "active": db.active_project(),
        "projects": [{"name": s["name"], "memories": s["memories"], "active": s["active"]}
                   for s in db.list_projects(counts=True)],
    }


@tool("core")
def list_domains() -> list[dict]:
    """List the domain tree: every path with its counts and latest activity.

    Warm-up discovery. domain is free text and drifts over time (e.g.
    'proj-1042' vs 'proj-1042-cache-warmup'), so this surfaces the
    paths actually in use instead of leaving you to guess one. Ordered by
    most recent activity.

    Per entry: `domain` (the full path), `parent`, `depth`, `count` (filed
    at exactly this path), `subtree` (that plus everything nested under
    it), `children`, and `implicit` -- true for a level that exists only
    because something deeper is filed under it. Read `subtree` to pick the
    scope worth warming up: a parent holding nothing of its own can still
    be where the work is.

    `also` and `subtree_also` are the same two counts for memories
    CROSS-LISTED here rather than filed here -- the cross-cutting subjects.
    A path with `count` 0 and `also` above it is one of those and nothing
    else: an end-to-end flow whose steps all live under other branches.
    Reads scoped to it return them all.

    Casing may be enforced store-wide -- call get_domain_case() to see
    the active policy before coining a new domain.
    """
    with db.connect() as conn:
        return db.list_domains(conn)


@tool("core")
def also_domain(uid: str, domain: str) -> dict:
    """Cross-list an existing memory into one more domain path.

    For the membership a memory picks up after the fact: it was filed where
    it lives, and later turns out to be part of a subject that cuts across
    the tree. Every read scoped to `domain` returns it from now on, without
    moving it -- use the dashboard's re-home for that.

    Returns {"uid": ..., "also": [...]} with the whole resulting set. A path
    the memory's own domain already sits under is dropped as redundant, so
    the echo is what actually holds.
    """
    with db.connect() as conn:
        if db.get_memory(conn, uid) is None:
            return _errors([f"no memory {uid}"])
        try:
            return {"uid": uid, "also": db.add_domain_link(conn, uid, domain)}
        except ValueError as exc:
            return _errors([str(exc)])


@tool("core")
def unfile_domain(uid: str, domain: str) -> dict:
    """Drop one of a memory's cross-listings. Does not touch where it is filed.

    Matched on the exact path: dropping 'acme' leaves a separate membership
    in 'acme/x100' alone, because that is a different scope. Returns
    {"uid": ..., "also": [...]} with what remains.
    """
    with db.connect() as conn:
        if db.get_memory(conn, uid) is None:
            return _errors([f"no memory {uid}"])
        return {"uid": uid, "also": db.remove_domain_link(conn, uid, domain)}


@tool("curation")
def get_domain_case() -> dict:
    """Report the store's domain-casing policy.

    Returns {"mode": "preserve"|"lower"|"upper"}. 'preserve' stores
    domains as written; 'lower'/'upper' coerce every domain to that case
    on write (a non-conforming domain is adjusted, not rejected, and the
    writer's result carries a `domain_adjusted` note). Read this before
    coining a new domain so its casing matches what will be stored.
    """
    with db.connect() as conn:
        return {"mode": db.get_domain_case(conn)}


@tool("curation")
def set_domain_case(mode: str) -> dict:
    """Set the store's domain-casing policy. mode: 'preserve' | 'lower' | 'upper'.

    'preserve' keeps free-text casing; 'lower'/'upper' coerce every
    domain written from now on to that case. This only governs new
    writes -- to bring already-stored domains into line, run the
    "Normalize domains" action in the admin dashboard (it previews
    collisions before merging variant spellings). Returns the stored
    {"mode": ...}.
    """
    with db.connect() as conn:
        return {"mode": db.set_domain_case(conn, mode)}


@tool("core")
def pulse(domain: str = "") -> dict:
    """Session warm-up: latest checkpoint + open handoffs/anti-patterns + recent notes.

    Picks the checkpoint by created_at DESC, never by similarity --
    a similarity-ranked top-1 can return a stale checkpoint over a
    same-day one, which is exactly the failure mode this avoids.
    latest_checkpoint is returned in full (that's the point of pulse),
    with its relations attached so linked memories are visible without
    a separate get_relations call. handoffs and anti_patterns are notes
    left for whoever resumes; recent_notes are the newest note()'d
    facts, as recency breadcrumbs -- for relevance-ranked recall use
    recall()/search(). Those three lists are snippet-truncated -- call
    get_memory(uid) for one in full.

    latest_checkpoint and those three lists carry `est_tokens`, the
    estimated cost of a record's FULL content: on a truncated one that is
    what the get_memory(uid) would cost, on latest_checkpoint it is what
    this response already spent. `diagrams` has no bodies to price.

    diagrams lists the documented flows by title only, never inlined:
    a whole graph would swamp a warm-up. Read one with get_diagram(uid)
    when the work actually touches that routine.

    domain warms up a path and everything under it, so pulse('acme/x100')
    is the module-wide brief and pulse('acme/x100/p200') the routine's.
    A domain that names only the deep end of a path ('p200') is resolved
    to the branches it sits in -- `scope.paths` reports which, and an
    ambiguous name resolves to ALL of them.

    `scope` is the rest of the brief: what the scope HOLDS, next to what
    came back. This is a warm-up, so each list stops at a handful and the
    newest few of a busy child can fill it on their own -- `scope.not_shown`
    counts what that left behind, per type, and `scope.subdomains` says
    which level it is sitting in (`own` = filed there, `subtree` = with its
    descendants). Read them as the drill-down plan: search(query,
    domain=...) or list_by_domain(domain, type=..., limit=...) on the child
    that holds what this pass only counted. A pulse is the state of a
    scope, never its contents.

    `scope.stale` is the one thing here about DECAY rather than contents:
    how many memories in the scope carry a `review_after` date that has
    passed. Present only when non-zero. It means somebody who knew the
    subject said when to look again and nobody has -- optimize_scan lists
    which ones, with their `source_ref`.

    A scope holds what is CROSS-LISTED into it as well as what is filed
    there, so warming up an end-to-end flow brings back the routines that
    are steps of it wherever they live. `scope.also` counts how much of the
    brief arrived that way, and a subdomain carries its own `also` --
    present only when non-zero, so a store that never cross-lists never
    sees the field.
    """
    with db.connect() as conn:
        census = db.domain_census(conn, domain)
        latest_checkpoint = db.latest_by_type(conn, TYPE_CHECKPOINT, domain=domain,
                                              exclude_contradicted=True)
        handoffs = _list_scoped(conn, domain, TYPE_HANDOFF, PULSE_HANDOFFS)
        anti_patterns = _list_scoped(conn, domain, TYPE_ANTI_PATTERN, PULSE_ANTI_PATTERNS)
        recent_notes = _list_scoped(conn, domain, TYPE_NOTE, PULSE_NOTES)
        diagram_rows = _list_scoped(conn, domain, TYPE_DIAGRAM, PULSE_DIAGRAMS)
        diagrams = []
        for r in diagram_rows:
            meta = db.get_diagram_row(conn, r["uid"])
            diagrams.append({
                "uid": r["uid"],
                "domain": r["domain"],
                "title": meta["title"] if meta else "",
            })
        checkpoint_dict = _row_to_dict(latest_checkpoint)
        if checkpoint_dict:
            # Returned whole, so its est_tokens is what this response spent
            # on it rather than what a fetch would cost.
            _with_est_tokens(checkpoint_dict)
            checkpoint_dict["relations"] = [_row_to_dict(r) for r in db.get_relations(conn, checkpoint_dict["uid"])]
        # A warm-up hands all of this to the caller, so all of it counts as
        # read -- diagrams included, which are named here and nothing else.
        _read(conn, [*handoffs, *anti_patterns, *recent_notes, *diagram_rows,
                     *([latest_checkpoint] if latest_checkpoint else [])])
    shown = {
        "latest_checkpoint": [checkpoint_dict] if checkpoint_dict else [],
        "handoffs": handoffs,
        "anti_patterns": anti_patterns,
        "recent_notes": recent_notes,
        "diagrams": diagrams,
    }
    # what each list left behind, from the census -- reported per LIST, since
    # that is what the reader is looking at, and only where something was
    # actually left (a zero everywhere teaches the block to be skipped)
    not_shown = {}
    for key, type_ in PULSE_LIST_TYPES.items():
        left = census["by_type"].get(type_, 0) - len(shown[key])
        if left > 0:
            not_shown[key] = left
    return {
        "project": db.active_project(),
        "latest_checkpoint": checkpoint_dict,
        "handoffs": [_snippet_dict(_row_to_dict(r)) for r in handoffs],
        "anti_patterns": [_snippet_dict(_row_to_dict(r)) for r in anti_patterns],
        "recent_notes": [_snippet_dict(_row_to_dict(r)) for r in recent_notes],
        "diagrams": diagrams,
        "scope": {
            "domain": domain,
            "paths": census["paths"],
            "total": census["total"],
            **({"also": census["also"]} if census.get("also") else {}),
            **({"stale": census["stale"]} if census.get("stale") else {}),
            "by_type": census["by_type"],
            "not_shown": not_shown,
            "subdomains": census["children"],
        },
    }


@mcp.prompt()
def warm_up(domain: str = "") -> str:
    """What memai already knows, as text, for a session about to start.

    The same brief the SessionStart hook emits (see memai.hook), offered
    here for hosts that surface prompts as commands. A prompt is invoked by
    the person, which makes it the one place in the MCP protocol where the
    store can be READ without the agent having decided to read it.
    """
    with db.connect() as conn:
        text = brief.session_brief(conn, domain=domain, project=db.active_project())
    return text or "The memai store is empty -- nothing to warm up from yet."


@tool("core")
def get_memory(uid: str) -> dict:
    """Fetch a single memory's full record, including its edit history and relations.

    A diagram also comes back with its mermaid source, its per-node links
    and its jumps to and from other flows; any other memory comes back
    with `referenced_by_diagrams`, the flows that point a step at it -- so
    a note tells you which processes depend on it without a second lookup.
    """
    with db.connect() as conn:
        row = db.get_memory(conn, uid)
        if row is None:
            # Not {}: an empty dict reads as "the record is empty" and sends a
            # caller looking for content that was never there, when what
            # happened is that the uid does not name a memory at all.
            return _errors([f"no memory {uid}"])
        _read(conn, [row])
        edits = db.get_edit_history(conn, uid)
        rels = db.get_relations(conn, uid)
        result = _row_to_dict(row)
        if row["type"] == TYPE_DIAGRAM:
            result["mermaid"] = _capped(db.render_diagram_mermaid(conn, uid))
            result["node_links"] = [_row_to_dict(r) for r in db.get_node_links(conn, uid)]
            result["jumps"] = db.get_diagram_jumps(conn, uid)
        else:
            result["referenced_by_diagrams"] = [
                _row_to_dict(r) for r in db.diagrams_referencing(conn, uid)
            ]
    result["edit_history"] = [_row_to_dict(e) for e in edits]
    result["relations"] = [_row_to_dict(r) for r in rels]
    return result


@tool("core")
def edit_memory(uid: str, new_content: str = "", note: str = "", mode: str = "replace",
                source_ref: str = "", title: str = "", tags: str = "") -> dict:
    """Correct a memory's content or its source reference, keeping the previous version.

    Corrections are common in append-only memory stores that only
    support delete, not edit; this preserves the old content instead
    of losing it.

    mode='append' adds `new_content` as a new line at the end instead of
    replacing the body. Use it when a memory gains a fact rather than
    turning out to be wrong: the alternative is reading the whole thing,
    restating it and sending it back, which pays for the body twice and
    stakes the existing text on it being copied faithfully.

    source_ref points the memory at what its claim came from -- the field
    note() takes at write time, and the one a later pass checks the claim
    against. It is settable on its own, with no `new_content`, for the
    common case of a body that is right and a reference that is missing or
    has moved; an empty source_ref leaves the stored one alone, and
    clearing one is a dashboard edit. Passing neither is an error rather
    than a silent no-op.

    title renames the memory: the one line a list shows it by, and the
    field weighing most in search, at most 120 characters. Settable on its
    own, like source_ref. A diagram is renamed through its graph instead --
    its title is part of what generates the body, so a rename here would be
    overwritten by the next structural change.

    tags REPLACES the tag set, comma-separated: pass the whole set that
    should survive, not the one being added. Settable on its own, and
    indexed, so this is how an untagged memory becomes findable by the words
    its body never uses. An empty string leaves the stored tags alone --
    clearing them, like clearing a source_ref, is a dashboard edit.

    Refuses to rewrite a diagram's content: that is generated from the
    graph, so a hand-written replacement would be silently overwritten by
    the next structural change -- edit the flow through
    diagram_node/diagram_edge. Its source_ref is ordinary metadata and is
    editable here like any other memory's.
    """
    if mode not in ("replace", "append"):
        return _errors([f"mode must be 'replace' or 'append'; got {mode!r}"])
    if not (new_content.strip() or source_ref.strip() or title.strip() or tags.strip()):
        return _errors(["nothing to change: pass new_content, source_ref, title or tags"])
    changed = []
    with db.connect() as conn:
        if new_content.strip():
            if db.is_diagram(conn, uid):
                return _errors([
                    f"{uid} is a diagram: its content is generated from the graph. "
                    "Use diagram_node/diagram_edge to change the flow."
                ])
            if not db.update_memory_content(conn, uid, new_content, note=note,
                                            append=mode == "append"):
                return _errors([f"no memory {uid}"])
            changed.append("content")
        if source_ref.strip():
            if not db.set_source_ref(conn, uid, source_ref, note=note):
                return _errors([f"no memory {uid}"])
            changed.append("source_ref")
        if title.strip():
            if db.is_diagram(conn, uid):
                return _errors([
                    f"{uid} is a diagram: its title is part of what generates its "
                    "body, so a rename here would be overwritten by the next "
                    "structural change. Rename it in the dashboard."
                ])
            too_long = db.title_error(title)
            if too_long:
                return _errors([too_long])
            if not db.set_title(conn, uid, title, note=note):
                return _errors([f"no memory {uid}"])
            changed.append("title")
        if tags.strip():
            if not db.set_tags(conn, uid, tags, note=note):
                return _errors([f"no memory {uid}"])
            changed.append("tags")
    return {"ok": True, "changed": changed}


@tool("core")
def link_memories(from_uid: str, to_uid: str, relation_type: str, note: str = "") -> dict:
    """Create a queryable edge between two memories.

    relation_type is free text but keep it consistent, e.g.
    'supersedes', 'relates_to', 'contradicts', 'links_to'.

    Refuses an unknown uid, a memory related to itself, and an edge that
    already exists with that same type -- each as
    {"ok": False, "errors": [...]}, so a typo comes back as something to
    fix instead of a dangling edge or a raw database error.
    """
    with db.connect() as conn:
        try:
            rel_id = db.add_relation(conn, from_uid, to_uid, relation_type, note=note)
        except ValueError as exc:
            return _errors([str(exc)])
    return {"relation_id": rel_id}


@tool("core")
def get_relations(uid: str) -> list[dict]:
    """List all relations (incoming and outgoing) for a memory."""
    with db.connect() as conn:
        rows = db.get_relations(conn, uid)
    return [_row_to_dict(r) for r in rows]


@tool("core")
def set_confidence(uid: str, confidence: str) -> dict:
    """Set a memory's confidence: unverified | confirmed | contradicted."""
    if confidence not in ("unverified", "confirmed", "contradicted"):
        return {"ok": False, "error": "confidence must be unverified|confirmed|contradicted"}
    with db.connect() as conn:
        ok = db.set_confidence(conn, uid, confidence)
    return {"ok": ok}


@tool("core")
def forget(uid: str, reason: str = "", superseded_by: str = "") -> dict:
    """Archive a memory (soft delete -- content is kept, just excluded from default search/list).

    A `reason` is recorded as a status-change audit entry, without touching
    the content.
    """
    with db.connect() as conn:
        ok = db.set_status(
            conn, uid, "archived",
            superseded_by=superseded_by or None,
            note=f"archived: {reason}" if reason else "",
        )
    return {"ok": ok}


@tool("curation")
def purge_memory(uid: str, confirm_phrase: str) -> dict:
    """PERMANENTLY delete a memory + its edit history + relations. Irreversible.

    Use forget() instead unless the user explicitly asked to permanently
    remove data -- forget() is reversible (archived, content kept),
    this is not. Guardrail: confirm_phrase must exactly equal
    "DELETE <uid>", typed by the user in their own message. Do not
    construct this string yourself from an inferred "yes"/"confirm" --
    it must come from the user actually stating the uid back.
    """
    expected = f"DELETE {uid}"
    if confirm_phrase != expected:
        return {"ok": False, "error": f"confirm_phrase must exactly equal '{expected}'"}
    with db.connect() as conn:
        ok = db.purge_memory(conn, uid)
    return {"ok": ok}


@tool("curation")
def move_to_project(target: str, uids: str = "", domain: str = "", dry_run: bool = True,
                  create: bool = False) -> dict:
    """Carry memories from the active project into another one, and remove them here.

    `uids` is comma-separated, `domain` a path (its subdomains and archived
    rows go too); give either or both. Each memory travels whole -- body,
    cross-listings, usage counts, edit history, the relations and diagram
    graph inside the slice -- into `target`, is checked there, and only
    then purged from the active project, after a backup of it is written.

    `dry_run` is the default and moves nothing: it reports what would move,
    `conflicts` (uids `target` already holds, which stay here) and
    `outside` -- the relations, diagram links and jumps, `superseded_by`
    marks and [[uid]] references that cross the edge of the slice, all of
    which the move drops. Read that report with the user, then widen the
    slice or accept the loss BEFORE calling again with dry_run=False: the
    purge is irreversible short of the backup. `create` makes a `target`
    that does not exist yet. list_projects() names the projects there are.
    """
    wanted = [u.strip() for u in uids.split(",") if u.strip()]
    return portable.move(db.active_project(), target, uids=wanted, domain=domain,
                         dry_run=dry_run, create=create)


@tool("curation")
def dedup_scan(domain: str = "", type: str = "", threshold: float = 0.6, limit: int = 20) -> list[dict]:
    """Surface likely-duplicate/contradictory memory pairs.

    Lexical overlap over near-identical text -- each pair carries its
    `method`. Two takes on one subject in different words do not surface
    here. Same-domain/session checkpoint pairs are excluded (timelines, not
    dups)
    and checkpoint pairs rank below durable-type pairs. Not an automatic
    merge -- returns candidate pairs + similarity score for the agent to
    review and decide (link_memories / edit_memory / forget as
    appropriate).

    domain scans a path and everything nested under it, which is usually
    what you want: near-duplicates collect between a module and its own
    routines.
    """
    with db.connect() as conn:
        pairs = db.dedup_candidates(conn, domain=domain, type=type, threshold=threshold, limit=limit)
    return [
        {"a": _row_to_dict(a), "b": _row_to_dict(b), "ratio": round(score, 3), "method": method}
        for a, b, score, method in pairs
    ]


@tool("curation")
def optimize_scan(
    domain: str = "", type: str = "", since: str = "",
    include_archived: bool = False, limit: int = 500, offset: int = 0,
    full: bool = False,
) -> dict:
    """Dump the memory corpus compactly so you can plan a curation pass.

    Step 1 of the "optimize my memories" workflow: every memory's
    curation-relevant fields, the relation edges among them, usage counts,
    dedup and domain hints, and per-memory `anchors` (URLs, paths,
    identifiers) to check against live facts. Read it, then stage what you
    decided with optimize_stage.

    Start with what the store already says is suspect: `due: true` on a
    memory means its own writer dated it for a recheck and the date has
    passed, and `source_ref` says what to check it against.

    `recalls` and stats.never_recalled are NOT that. A low count means
    unproven, not useless -- a memory about a rare subject looks exactly
    like a memory nobody wants, and the rare subject is frequently the
    reason the store is there. Use the aggregate to judge the STORE and
    never a single row: do not propose archiving something because it is
    unread.

    The listing is slim so a big store fits one response, and a page ends
    early at an internal size budget -- `truncated` means page onward with
    offset + count. `since` limits the scan to a delta for recurring passes;
    full=True keeps whole bodies.

    BEFORE PROPOSING ANY CHANGE, CHECK IT AGAINST LIVE FACTS, and record what
    you checked in each suggestion's `verified`. Destructive kinds are
    rejected without it. help(command='optimize_scan') has the rest: what
    every hint means, how `since` stays cross-window, and what "live facts"
    covers per memory type.
    """
    with db.connect() as conn:
        corpus = db.optimization_corpus(
            conn, domain=domain, type=type, since=since,
            include_archived=include_archived,
            limit=limit, offset=offset, full=full)
        pairs = db.dedup_candidates(conn, domain=domain, type=type, since=since, limit=20)
    corpus["dedup_hints"] = [
        {"a": a["uid"], "b": b["uid"], "ratio": round(score, 3), "method": method}
        for a, b, score, method in pairs
    ]
    return corpus


@tool("curation")
def optimize_stage(suggestions: list[dict], note: str = "") -> dict:
    """Stage a batch of curation suggestions for human review in the dashboard.

    Step 2 of the "optimize my memories" workflow. NOT applied here: the
    user reviews and applies or rejects each one in the admin dashboard,
    which backs up before the first apply and can undo any of them.

    Each suggestion is {"kind", "target_uid", "payload", "rationale",
    "verified"}. Kinds: compact/reword {"new_content"}, retag {"tags"},
    redomain {"domain"}, crosslist {"also": [...]}, set_confidence
    {"confidence"}, review {"review_after"} (a date or a span like '180d';
    '' clears it), archive {"reason"}, link {"from_uid","to_uid",
    "relation_type"}, merge {"keep_uid","drop_uid"}, distill
    {"source_uids","new_type","new_content","title"}. link/merge derive target_uid
    from the payload and distill creates its target -- omit it for those.

    Destructive kinds (archive, set_confidence=contradicted, merge,
    distill) require a non-empty `verified` describing the live-facts
    check behind them. Invalid suggestions are skipped and reported in
    `errors`; the rest are staged. Returns {run_id, staged, errors}.
    help(command='optimize_stage') explains each kind in full.
    """
    with db.connect() as conn:
        result = db.stage_optimization(conn, note, suggestions)
    return result


@tool("curation")
def optimize_runs() -> list[dict]:
    """List optimization runs with their review progress.

    Read-only companion to optimize_stage: after staging, use this to see
    whether the user has applied/rejected your suggestions in the admin
    dashboard. Each run carries total/pending/applied/rejected counts,
    its note, and the safety-backup path once the first apply happened.
    Applying/rejecting stays in the dashboard by design -- the agent
    proposes, the human disposes.
    """
    with db.connect() as conn:
        rows = db.list_optimization_runs(conn)
    return [dict(r) for r in rows]


@tool("curation")
def optimize_status(run_id: int) -> dict:
    """Inspect one optimization run: every suggestion and its decision.

    Read-only. Returns the run header plus each suggestion's kind,
    target_uid, payload, rationale, verified, status
    (pending/applied/rejected) and decided_at -- so you can tell which
    proposals landed, follow up on rejected ones, or build on applied
    ones in a later pass.
    """
    with db.connect() as conn:
        run = db.get_optimization_run(conn, run_id)
        if run is None:
            return {"error": f"unknown run: {run_id}"}
        sugs = db.get_optimization_suggestions(conn, run_id)
    return {
        "run": dict(run),
        "suggestions": [
            {**dict(s), "payload": json.loads(s["payload"]) if s["payload"] else {}}
            for s in sugs
        ],
    }


@tool("core")
def help(command: str = "") -> dict:
    """Explain the memai tools, read directly from their code docstrings.

    Without arguments: every tool with its one-line summary, and which of
    them this process did not load (MEMAI_TOOLS). With command='<name>':
    that tool's signature and its FULL documentation -- longer than the
    schema description, because the schema is paid for on every request
    and this is paid for when someone reads it.
    """
    if not command:
        result = {
            "tools": {
                name: (inspect.getdoc(fn) or "").split("\n", 1)[0]
                for name, fn in _TOOLS.items()
            },
            "hint": "call help(command='<name>') for a tool's full signature and documentation",
        }
        # Named, not hidden: a tool left out of this process still exists,
        # and an agent that needs one is better told how to turn it on than
        # left to infer that memai cannot do the thing at all.
        missing = sorted(set(_TOOLS) - _published())
        if missing:
            result["not_loaded"] = missing
            result["not_loaded_hint"] = (
                "these are documented but not offered as tools in this process; set "
                f"MEMAI_TOOLS=full (or add a group: {', '.join(TOOL_SETS)}) in the "
                "MCP server's environment to publish them")
        return result
    fn = _TOOLS.get(command)
    if fn is None:
        return {"error": f"unknown command: {command}", "available": sorted(_TOOLS)}
    doc = (inspect.getdoc(fn) or "") + _LONG_DOC.get(command, "")
    return {
        "command": command,
        "signature": f"{command}{inspect.signature(fn)}",
        "doc": doc,
        **({"loaded": False} if command not in _published() else {}),
    }


def _published() -> set[str]:
    """The tool names FastMCP is actually offering this session."""
    return {name for name, group in _GROUP_OF.items() if group in _ACTIVE_SETS}


# The half of a tool's documentation that does not belong in its schema.
#
# A description is sent with every request for the whole session; this is
# sent when help() is called, which is when somebody is actually reading
# it. What stays in the docstring is what a caller needs to pick the tool
# and its arguments correctly; what moves here is the reasoning, the
# failure modes and the worked detail -- true, worth having, and not worth
# a thousand characters of every context window in every session that never
# calls the tool.
_LONG_DOC = {
    "optimize_scan": """
Dump the memory corpus compactly so you can plan a curation pass.

Step 1 of the "optimize my memories" workflow. Returns every memory's
curation-relevant fields, the relation edges among them, and
dedup-candidate pairs as a starting hint. Read this, then decide what
to compact/reword/retag/redomain/set_confidence/archive/link/merge/
distill and stage it with optimize_stage.

The listing is slim on purpose so a few-hundred-memory store fits one
response: content is a ~120-char snippet plus `content_len` (tags cut
at ~100 with `tags_len`); empty/default fields are omitted (incl.
confidence 'unverified' -- stats keeps the aggregate); created_at
drops sub-second precision. Pass full=True for whole
bodies, or fetch one with get_memory(uid) when a snippet is not
enough. A page also ends early if its serialized size hits an
internal budget, so one response ALWAYS fits the host's output cap.
`truncated: true` means the listing stopped before the corpus ended
-- page onward with offset = offset + count (stats.total is the
whole corpus).

On a grown store, prefer INCREMENTAL curation over full-corpus
passes: `since` limits the scan to memories created or updated
at/after an ISO timestamp or date ('2026-07-01'), so a recurring
"optimize my memories" only reviews the delta since the last run
(optimize_runs shows when that was). Cross-window collisions are
still caught: dedup_hints probe FROM the new memories against the
whole store (a new memory duplicating an old one outside the window
surfaces; old x old pairs are skipped), and domain_hints report any
store-wide domain cluster the delta touches. Combine with
domain/type to curate one slice at a time. Also included:
  - stats: totals for the whole filtered corpus (by_type,
    by_confidence, by_domain, empty_domain, untagged) -- computed
    regardless of `limit`. `untagged` counts the memories whose tags
    are empty or are nothing but their own type: BM25 reads content,
    tags and domain, so those answer only a query that quotes their
    own wording. They are the retag work list,
  - domain_hints: clusters of domain-string variants that likely mean
    the same thing (case/separator drift, ticket-id spellings), with
    a suggested canonical -- ready-made redomain candidates,
  - domain_nesting: flat domains that already spell a hierarchy out
    ('acme-x100-p200-cache-warmup'), each with the path it could
    become ('acme/x100/p200/cache-warmup'). Domains nest, and a
    scope only groups what is filed under it, so these are the
    redomain candidates that turn one string per subject into a tree.
    Read them as a proposal, not a verdict: the split cannot tell a
    real level from a hyphen inside a name, so check each one and
    stage only the splits that hold,
  - per memory, `also`: the domains it belongs to beside its own path.
    There is deliberately no hint listing cross-listing CANDIDATES --
    which subjects cut across the tree is a judgement about what the
    memories say, not something a string split can propose. Read the
    corpus, decide, and stage `crosslist` suggestions; `also` is there
    so you can tell a new membership from one that already holds,
  - anchors: per memory, the verifiable references found in its FULL
    content (URLs, file paths, table/field identifiers, constants),
    space-joined -- the things to go check against live facts.

Before proposing any change, CHECK IT AGAINST LIVE FACTS -- do not
rewrite or archive something that was true then but stale now, and do
not "correct" something that is still true:
  - cross-check newer memories already in this corpus (supersession /
    contradiction),
  - for code/config memories, verify the anchors against the live repo,
  - for world-facts, web-check current truth.
Record what you verified in each suggestion's `verified` field --
destructive suggestions (archive, set_confidence=contradicted) are
rejected without it.
""",
    "optimize_stage": """
Stage a batch of curation suggestions for human review in the dashboard.

Step 2 of the "optimize my memories" workflow. Writes the suggestions
to a new optimization run; they are NOT applied here -- the user
reviews and applies/rejects each one in the admin dashboard's
Optimization tab, where a backup is taken before the first apply and
every applied change can be undone.

Each suggestion is an object:
  {"kind": ..., "target_uid": ..., "payload": {...},
   "rationale": "why", "verified": "what live-facts check you did"}

Kinds and their payload:
  compact / reword   {"new_content": str}
  retag              {"tags": str}                 comma-separated
  retitle            {"title": str}                one line naming the memory, max 120 chars
  redomain           {"domain": str}
  crosslist          {"also": [path, ...]}         replaces the whole set
  set_confidence     {"confidence": "unverified|confirmed|contradicted"}
  review             {"review_after": str}         a date or a span ('180d'); '' clears it
  archive            {"reason": str}               soft/reversible; never hard-deletes
  link               {"from_uid", "to_uid", "relation_type", "note"?}
  merge              {"keep_uid", "drop_uid", "note"?}   links supersedes + archives drop
  distill            {"source_uids": [uid, ...], "new_type": "note|reasoning|anti_pattern",
                      "new_content": str, "title": str, "tags"?, "domain"?}

redomain moves where a memory is FILED -- one path, one parent chain.
crosslist sets what it also BELONGS to: the subjects that cut across
that tree, where several routines are each a step of one end-to-end
process without any of them being the parent of the others. It replaces
the whole set rather than adding to it, so include the memberships that
should survive; `also: []` drops them all. The corpus lists each
memory's current `also`, so read that before proposing. A path the
memory's own domain already sits under is redundant and is dropped --
if that leaves nothing, the suggestion is rejected rather than silently
staged as a clear.

distill extracts the durable knowledge out of one or MORE source
memories into a newly authored one: creates it, links it `supersedes`
each source and archives the sources (all reversible). Use it to
retire closed-ticket checkpoints without losing what they taught, or
as an n-ary merge when the survivor needs synthesized content. It is the
only kind that authors a memory, so it takes that memory's `title` the way
a writing tool does: no later step names it. Those payload keys are the
whole set it applies -- any other key is reported in `errors`. A diagram cannot be a source: its content is generated
from its graph, so retire a flow with archive instead.

link/merge derive target_uid from the payload (from_uid / drop_uid)
and distill creates its target -- omit target_uid for those kinds.
Destructive suggestions (archive, set_confidence=contradicted, merge,
distill) require a non-empty `verified` describing the live-facts
check that justifies them.

Invalid suggestions are skipped and reported in `errors`; the rest are
staged. Returns {run_id, staged, errors}.
""",
    "get_diagram": """
TO SHOW THE DIAGRAM TO A USER, pick by what you can actually do with it:

  * you can render inline HTML/SVG in your reply (a widget, an artifact, an
    inline preview) -> format='svg-interactive', then READ THE FILE at the
    returned `inline_path` and emit its contents inline. That file is the
    whole answer: a self-contained fragment with pan and zoom, no doctype,
    no <body>, no network, no external CSS, nothing that reaches the host
    page. (`path` is the same drawing as a standalone document, for opening
    in a browser or sending as a file -- do not paste that one inline, most
    renderers reject a full document.) Do NOT reach for mermaid here.
  * you can only attach or link a file -> format='svg'. Same drawing, no
    shell, openable in any browser or image viewer.
  * you can render neither, but your client draws mermaid natively ->
    format='mermaid'.

The file/payload split exists for the calls that do NOT display -- reasoning
over a flow, checking what a step says, handing a path to something else,
which is most of them. IT IS NOT A REASON TO AVOID EMITTING THE MARKUP WHEN
THE USER ASKED TO SEE THE DIAGRAM. In that case, reading the file and
putting its contents in your reply IS the deliverable, and the tokens it
costs are the cost of doing the work, not an overrun to economise on.

Attaching or linking the file is NOT showing it: that hands the user
something to open later. If your only display mechanism is a file send, at
least mark it to render rather than to download.

Fidelity, which is the reason the SVG formats exist: they reproduce the
admin canvas exactly -- the arrangement the user made, the same edge routing
around it, the same wrapped labels, node notes as <title> tooltips. MERMAID
DOES NOT. Mermaid always applies its own layout, so it discards the stored
positions and shows a flow the user never arranged. Prefer it only when
nothing else can be displayed.

'svg-interactive' over 'svg' for anything long: a 34-step routine is
~3000x6300 units, and scaled to fit a chat column that puts its labels under
3px. The interactive shell opens at a readable scale near the start step.

Each call also prunes older renders per the retention setting (see the
dashboard's maintenance view) and reports how many it removed.
""",
}


# Registry for help(): the decorated functions themselves, so signatures
# and docstrings are read from the exact code that runs.
_TOOLS = {
    "note": note,
    "checkpoint": checkpoint,
    "anti_pattern": anti_pattern,
    "reasoning": reasoning,
    "handoff": handoff,
    "diagram": diagram,
    "diagram_node": diagram_node,
    "diagram_edge": diagram_edge,
    "diagram_link": diagram_link,
    "diagram_jump": diagram_jump,
    "diagram_relayout": diagram_relayout,
    "get_diagram": get_diagram,
    "search": search,
    "recall": recall,
    "list_by_domain": list_by_domain,
    "list_recent": list_recent,
    "timeline": timeline,
    "list_projects": list_projects,
    "list_domains": list_domains,
    "also_domain": also_domain,
    "unfile_domain": unfile_domain,
    "get_domain_case": get_domain_case,
    "set_domain_case": set_domain_case,
    "pulse": pulse,
    "get_memory": get_memory,
    "edit_memory": edit_memory,
    "link_memories": link_memories,
    "get_relations": get_relations,
    "set_confidence": set_confidence,
    "forget": forget,
    "purge_memory": purge_memory,
    "move_to_project": move_to_project,
    "dedup_scan": dedup_scan,
    "optimize_scan": optimize_scan,
    "optimize_stage": optimize_stage,
    "optimize_runs": optimize_runs,
    "optimize_status": optimize_status,
    "help": help,
}

# _TOOLS is written by hand and _GROUP_OF by the decorator, so they can drift
# -- and a tool missing from _TOOLS is a tool help() cannot document and
# _published() cannot report as absent. Cheap to check, once, at import.
assert set(_TOOLS) == set(_GROUP_OF), (
    f"_TOOLS is out of step with the decorated tools: "
    f"{set(_TOOLS) ^ set(_GROUP_OF)}")
assert set(_LONG_DOC) <= set(_TOOLS), f"_LONG_DOC names no such tool: {set(_LONG_DOC) - set(_TOOLS)}"


def main() -> None:
    # Before mcp.run(), deliberately: the last moment on the main thread
    # with no event loop and no stdio reader threads running, and the only
    # place a few tens of milliseconds cost nothing. A lifespan hook would
    # look tidier and be worse: the SDK enters it before the session
    # exists, putting this on the initialize path.
    # Does nothing unless MEMAI_ADMIN_AUTOSTART says otherwise, and
    # cannot raise -- see autostart.ensure_admin_running.
    autostart.ensure_admin_running()
    mcp.run()


if __name__ == "__main__":
    main()
