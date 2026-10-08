"""memai MCP server.

Tools for long-term agent memory: note/checkpoint/anti_pattern/
reasoning/task/diagram to write, search/recall/list_by_domain/
list_recent/timeline/list_domains/pulse to read, plus edit history, a relations
graph, a dedup-candidate scanner and confidence/status tracking.
Retrieval is FTS5
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

import functools
import json
import logging
import os
import re

from mcp.server.mcpserver import MCPServer

from memai import (
    autostart,
    brief,
    budget,
    diagram_svg,
    hook_install,
    lite,
    portable,
    sections,
    tasks,
    update,
)
from memai import pending as pending_lists
from memai.store import (
    connection,
    dedup,
    domains,
    memories,
    optimizer,
    paths,
    projects,
    relations,
    renders,
    settings,
)
from memai.store import corpus as store_corpus
from memai.store import search as store_search
from memai.store import sections as store_sections
from memai.store.diagrams import persist as diagram_persist
from memai.store.diagrams import render as diagram_render

# Sent in the initialize handshake and injected into context by hosts that support it. One
# paragraph: it is paid on every request, and its job is to get the first pulse() call.
INSTRUCTIONS = """\
Long-term memory that survives between sessions. Read before working:
pulse(domain) for the state of a subject, recall(query) or search(query)
for anything specific, list_domains() for the tree that exists. Write as
you go, not at the end: note() a durable fact, anti_pattern() a pitfall
worth not repeating, checkpoint() where the work stands before a pause,
diagram() a routine start to end. One memory holds ONE fact: when a body
grows into several subjects, write them as separate memories and
link_memories() them to each other. A claim you could not check says so in
its own body; set_confidence(uid, 'confirmed'|'contradicted') closes it
once the evidence turns up. must_read() says which memories wait to be read
in a scope -- open tasks first (task() files one), then pitfalls, handoffs,
notes and flows; work an open task with task_item() and it closes itself
once every item is done or dropped. `pinned` in must_read() counts memories a
person marked as mandatory reading: list them with must_read(type=...,
pinned=true) and open every one before acting.

A domain is a path ('acme/x100/p200') and every read covers its
subdomains, so the same call asks about a product or one routine depending
on how much of the path it gives."""

# Appended to INSTRUCTIONS while the user's settings register no memai hook. `{command}` is the
# absolute memai-hook path: a host's shell has no activated environment to put it on PATH.
HOOKS_MISSING = """\
NOTE: no memai hook is registered in the user's settings, so nothing puts the
store in front of this session -- memai is read only when you call it, and a
session that forgets to call it starts blind. Say so in your first reply and
offer to run this, exactly as written (the name alone is not on PATH):

  {command} install

It registers SessionStart, PreCompact, Stop and PreToolUse in
`~/.claude/settings.json`, for every project -- the last of those is the guard:
it refuses a memai write whose required text never arrived, and records the
domains a session names, which scopes the task ask at Stop. `--check` reports
what is registered, `--print` shows the block without writing it."""


# Appended while memai is registered but part of the install is out of date; _stale_note fills
# `{findings}` and `{commands}`, with absolute commands for the same reason.
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
    """INSTRUCTIONS, with a note appended for each way the install needs a hand.

    HOOKS_MISSING when the user's settings register no memai hook,
    INSTALL_STALE when they do but an event is unregistered, a registered
    command has left the disk, an entry is not the one an install writes, or
    an installed skill is not the version this package ships. Then
    update.notice() when a release above this version is known.

    Read once, at import, from the user's settings alone -- the scope memai
    installs into. Unreadable settings are read as no registration. The
    release is read from the cache a hook writes and never fetched here: see
    memai.update.
    """
    notes: list[str] = []
    try:
        command = _quoted(hook_install.hook_command())
        notes.append(_stale_note(command)
                     if hook_install.registered(hook_install.user_settings_path())
                     else HOOKS_MISSING.format(command=command))
    except Exception:
        pass
    try:
        notes.append(update.notice())
    except Exception:
        pass
    return "\n\n".join([INSTRUCTIONS, *(note for note in notes if note)])


log = logging.getLogger(__name__)
mcp = MCPServer("memai", instructions=_instructions())


def _new_session_id() -> str:
    """This process's default `session` stamp for everything it writes.

    Derived here rather than asked of the caller. `session` was an optional
    free-text argument on every writer, which meant it was usually absent
    and the dashboard's session filter had nothing to filter -- the value
    of grouping a conversation's memories is real and the chance of an
    agent remembering to pass a consistent id on every call is not. An
    explicit `session=` still wins.
    """
    stamp = lite.now_iso()[:16].replace("-", "").replace(":", "")
    return f"{stamp}-{os.getpid():04x}"


SESSION = _new_session_id()


# Tool groups this process offers: every schema rides on every request, so groups let a session
# pay for what it uses. 'full' stays the default so no existing setup loses a tool quietly.
TOOL_SETS = ("core", "diagrams", "curation")
_ACTIVE_SETS = frozenset(
    TOOL_SETS if (raw := os.environ.get("MEMAI_TOOLS", "full").strip().lower()) in ("", "full")
    else {"core", *(s.strip() for s in raw.split(",") if s.strip())}
)


_GROUP_OF: dict[str, str] = {}

# Parameter text several writer tools share. A docstring line holding only
# `@param <key>` is replaced by the entry, at that line's indentation.
PARAM_DOCS: dict[str, str] = {
    "offset_page": """\
offset: where the page starts; `next_offset`, when present, starts the next.""",
    "title": """\
title: one line naming what this memory is about, in the words someone
would look for it by. It is what a list shows instead of the opening of
the body, and it outweighs every other field in search, so a title that
repeats the type ("note about the parser") names nothing. At most 120
characters, and a name that needs more than that is summarizing the
body instead of naming it.""",
    "domain": """\
domain: the subject this belongs to, as a path from the outermost
scope in ('acme/x100/p200'). File it as deep as the fact is specific
-- a note about one routine goes on the routine, and still comes back
when someone asks about the module or the product above it.""",
    "domain_brief": """\
domain: the subject path this is filed under, outermost scope first
('acme/x100/p200'). See note().""",
    "also": """\
also: other domain paths this belongs to, comma-separated. `domain` is
where the memory LIVES -- one path, one parent chain. `also` is for the
subjects that cut ACROSS that tree: the same routine belongs to the
module it runs in and to the end-to-end flow it is one step of, and
neither of those is the other's ancestor. Every read scoped to any of
those paths returns it. A path that `domain` already sits under is
dropped as redundant -- the result echoes what was stored.""",
    "also_brief": """\
also: other domain paths this belongs to, comma-separated -- the
cross-cutting subjects beside the one it is filed under. See note().""",
    "tags": """\
tags: comma-separated keywords and synonyms. Retrieval is BM25 over
content, tags and domain paths, and tags weigh second only to the body,
so they are where a memory becomes findable by words its own text never
uses -- the identifier, the symbol, the error string, the plain-language
phrasing someone will actually type. A memory with none is reachable
only by quoting itself.""",
    "tags_brief": """\
`tags` carries the synonyms the body never uses: retrieval is BM25 over
content and tags, so a memory with none is reachable only by quoting
itself. See note() for what belongs there.""",
    "review_after": """\
review_after: when this stops being safe to trust unchecked, as a date
('2026-11-01') or a span from today ('90d'). pulse() counts what is
overdue in a scope as `scope.stale` and optimize_scan lists it. Leave
it empty for anything that does not go stale -- most facts do not, and
a date nobody meant is worse than none.""",
    "source_ref": """\
source_ref: what the fact came FROM -- a path, a URL, a table name --
so a later pass can check the claim against the thing itself instead
of inferring what to check from the wording.""",
}

_PARAM_LINE = re.compile(r"^([ \t]*)@param (\w+)[ \t]*$", re.M)


def _expand_params(doc: str | None) -> str | None:
    """`doc` with every `@param <key>` line replaced by PARAM_DOCS[key]."""
    if not doc:
        return doc
    return _PARAM_LINE.sub(
        lambda m: "\n".join(m[1] + line if line else line
                            for line in PARAM_DOCS[m[2]].splitlines()), doc)


def tool(group: str):
    """Register a tool with the MCP server when its group is active.

    Always returns the function, wrapped so a result over the output
    ceiling becomes an error, so the module-level name stays callable from
    the admin surface and the tests whether or not the schema was published. Shared parameter text is expanded into `__doc__` first,
    so FastMCP publishes the full description.
    """
    def wrap(fn):
        fn.__doc__ = _expand_params(fn.__doc__)

        @functools.wraps(fn)
        def bounded(*args, **kwargs):
            result = fn(*args, **kwargs)
            size = budget.result_chars(result)
            if size <= budget.MCP_RESULT_MAX_CHARS:
                return result
            log.error("%s produced %d characters, over the %d budget",
                      fn.__name__, size, budget.MCP_RESULT_MAX_CHARS)
            return _errors([f"{fn.__name__} produced {size} characters, over the "
                            f"{budget.MCP_RESULT_MAX_CHARS} budget; this is a memai bug"])

        _GROUP_OF[fn.__name__] = group
        if group in _ACTIVE_SETS:
            mcp.tool()(bounded)
        return bounded
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
        d["also"] = domains.parse_domains(blob)
    # Blank on most rows -- and a field that is empty nine times out of ten
    # still costs its name in every result of every search.
    for optional in ("review_after", "source_ref", "pin"):
        if not d.get(optional):
            d.pop(optional, None)
    return d


SNIPPET_LIMIT = 400

LIST_STATUSES = ("active", "archived", "all")

# Memory type per writer, the exact strings the retrieval tools filter on; each writer is named
# after its type, so tool name and stored type cannot drift.
TYPE_NOTE = "note"                  # note()
TYPE_CHECKPOINT = "checkpoint"      # checkpoint()
TYPE_ANTI_PATTERN = "anti_pattern"  # anti_pattern()
TYPE_REASONING = "reasoning"        # reasoning()
TYPE_DIAGRAM = "diagram"            # diagram()
TYPE_TASK = memories.TASK_TYPE      # task()


def _with_est_tokens(d: dict) -> dict:
    """Annotate a result with `est_tokens` for its FULL content.

    Call before truncating: the number a caller budgets a get_memory(uid)
    with is what the whole record costs, not what the snippet cost.
    """
    d["est_tokens"] = connection.est_tokens(len(d.get("content", "")))
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


def _offset_error(*offsets) -> str:
    for value in offsets:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"offset must be a whole number of 0 or more, got {value!r}"
    return ""


def _page(records: list, offset: int, key: str = "records", **head) -> dict:
    """One page of `records` under `key`, with `total` and, unless it is the last, `next_offset`."""
    rows, nxt = budget.page(records, offset)
    out = {**head, "total": len(records), "offset": offset, key: rows}
    if nxt is not None:
        out["next_offset"] = nxt
    return out


def _listing(conn, rows, offset: int = 0) -> dict:
    """Rows as a list result: {"results": [...], "est_tokens": N}.

    Each result is snippet-truncated and carries its own `est_tokens`; the
    top-level one is their sum -- what opening everything this response
    returned would cost.
    """
    rows = list(rows)[offset:]
    results, nxt = budget.page([_snippet_dict(_row_to_dict(r)) for r in rows], 0)
    _read(conn, rows[:len(results)])
    out = {"results": results, "est_tokens": sum(r["est_tokens"] for r in results)}
    if nxt is not None:
        out["next_offset"] = offset + nxt
    return out


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
    memories.record_recall(conn, [r["uid"] for r in rows], sources=sources or None)
    return rows


def _coerce_domain(conn, domain: str) -> tuple[str, dict | None]:
    """Apply the store's domain policy before a write: casing, and path shape.

    Returns (coerced_domain, warning). warning is None when the domain
    already conforms; otherwise it describes the adjustment so the tool
    can echo it back to the agent (coerce-and-warn, never reject) -- which
    is also how an agent learns that 'acme / x100' was filed as
    'acme/x100'.
    """
    coerced, mode = domains.coerce_domain(conn, domain)
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
    # the project too: the dashboard switches it under a running server, so this is where a
    # writer learns which file its memory landed in
    result: dict[str, object] = {"uid": uid, "project": paths.active_project()}
    # the count is the feedback: a writer sees what it indexed while it
    # still holds the context that would supply the missing words
    result["tags_indexed"] = len([t for t in tags.split(",") if t.strip()])
    if not result["tags_indexed"]:
        result["tags_hint"] = TAGS_HINT
    if warning:
        result["domain_adjusted"] = warning
    if also:
        result["also"] = memories.get_domain_links(conn, uid)
    similar = dedup.similar_memories(conn, uid)
    if similar:
        result["similar"] = similar
        result["similar_hint"] = SIMILAR_HINT
    return result


@tool("core")
def note(title: str, content: str, domain: str = "", also: str = "", tags: str = "",
         session: str = "", review_after: str = "", source_ref: str = "") -> dict:
    """Save a general long-term memory (fact, decision, finding). Stored as type='note'.

    Timeless knowledge -- retrieved by relevance, not recency. Bring it
    back with recall() (or search(type='note')); must_read(type='note') lists
    the most recent ones as headers.

    @param title

    content: ONE fact, and what a reader needs to use it -- what holds,
    where it holds, what it rules out. Retrieval ranks whole memories, so a
    body answering four questions comes back for all four and is read for
    one: write the second subject as its own memory, on its own domain, and
    connect the two with link_memories(). A [[uid]] typed inside a body is
    a reference a reader can follow, not an edge -- get_relations() and the
    graph do not see it until link_memories() creates one. Past a couple of
    thousand characters, a body is usually several memories written as one.

    @param domain

    @param also

    @param tags

    @param review_after

    @param source_ref
    """
    with connection.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = memories.insert_memory(conn, type=TYPE_NOTE, content=content, title=title,
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
    checkpoints are read for bearing, not as an archive. One fact per
    note(), each cited back here by [[uid]] and linked with
    link_memories(); pulse() returns the latest checkpoint IN FULL, so
    every session pays for whatever was parked in these fields. Stored as
    type='checkpoint'.

    @param title

    @param domain_brief

    @param also_brief

    @param tags_brief
    """
    content = sections.render(TYPE_CHECKPOINT, {
        "intent": intent, "established": established,
        "pursuing": pursuing, "open_questions": open_questions})
    with connection.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = memories.insert_memory(
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

    Stored as type='anti_pattern'; pulse() counts the open ones for a domain
    and must_read(type='anti_pattern') lists them.
    `also` cross-lists it into further domain paths, `review_after` dates
    when to recheck it and `source_ref` says what it came from -- see note().

    ONE pitfall per memory: a second temptation from the same session is
    its own anti_pattern(), connected with link_memories(). See note() on
    what a body holds and when it is two memories.

    @param title

    @param domain_brief

    @param tags_brief
    """
    content = sections.render(TYPE_ANTI_PATTERN, {
        "pattern": pattern, "why_wrong": why_wrong, "instead": instead})
    with connection.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = memories.insert_memory(
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
    to get these back. ONE analysis per memory: a second hypothesis tested
    in the same session is its own reasoning(). See note() on what a body
    holds and when it is two memories.

    @param title

    @param domain_brief

    hypothesis: what you believed going in, as a claim that could be wrong.
    reasoning: how you tested it -- what you read, ran or compared.
    result: what came back. The measurement, not the interpretation.
    revised_belief: what you believe now, and where it differs from the
    hypothesis. Say plainly when the hypothesis survived unchanged.
    next_time: what someone hitting this again should do differently.

    `also`, `review_after` and `source_ref` behave as in note().

    @param tags_brief
    """
    content = sections.render(TYPE_REASONING, {
        "hypothesis": hypothesis, "reasoning": reasoning, "result": result,
        "revised_belief": revised_belief, "next_time": next_time})
    with connection.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid = memories.insert_memory(conn, type=TYPE_REASONING, content=content, title=title,
                               domain=domain, also=also, session=session or SESSION,
                               tags=tags,
                               review_after=review_after, source_ref=source_ref)
        return _write_result(conn, uid, warning, also, tags)


def _errors(errors: list[str]) -> dict:
    return {"ok": False, "errors": errors}


@tool("core")
def task(title: str, goal: str, items: str, domain: str = "", also: str = "",
         tags: str = "", session: str = "") -> dict:
    """Open a task: a goal and a checklist, kept until its items are closed.

    Stored as type='task'. Work it with task_item(), grow it with task_add()
    and discuss it with task_comment(); it archives itself once every item is
    done or dropped, and must_read() lists the open ones.

    title: one line naming what this task delivers, in the words someone
    would look for it by. At most 120 characters.

    @param domain_brief

    goal: the brief an agent with none of this session's context works from:
    what the work is and why, where it lives, the decisions and constraints
    that bind it, and what done looks like. Short, but complete enough to act
    on -- a few compact paragraphs, at most 2000 characters. Plain prose: a
    task is not a sectioned type, so no `INTENT:` / `GOAL:` / `WHY:` labels --
    those are how checkpoint, anti_pattern and reasoning bodies are read back.

    items: one checklist item per line, blank lines ignored. At most 50
    items of 300 characters each. Each gets a key (i1, i2, ...) that
    task_item() takes back.

    @param also_brief

    `tags` carries the synonyms the body never uses. The type name is not
    added for you: a word every task carries ranks no task above another.
    See note() for what belongs there.
    """
    try:
        with connection.connect() as conn:
            domain, warning = _coerce_domain(conn, domain)
            lines = tasks.split_items(items)
            uid = tasks.create_task(conn, title=title, goal=goal, items=lines,
                                    domain=domain, also=also, tags=tags,
                                    session=session or SESSION)
            result = _write_result(conn, uid, warning, also, tags)
    except ValueError as exc:
        return _errors([str(exc)])
    result["items"] = [f"i{n}" for n in range(1, len(lines) + 1)]
    return result


@tool("core")
def task_item(uid: str, item: str, state: str = "", comment: str = "",
              related: str = "") -> dict:
    """Update one item of a task: its state, a comment on it, memories linked to it.

    item: the item's number, such as 3 or i3; an item's key is its position in the checklist.

    state: todo, doing, done or dropped. The write that closes the last open
    item archives the task (`archived` in the result); one that reopens an
    item reopens the task.

    comment: a remark on this item -- what blocked it, what was decided.

    related: comma-separated uids of memories this item produced or depends
    on. An unknown uid refuses the whole call, and nothing is written.

    Give at least one of state, comment and related. They apply together, or
    not at all.
    """
    if not any(str(v).strip() for v in (state, comment, related)):
        return _errors(["give at least one of state, comment and related"])
    try:
        with connection.connect() as conn:
            key = tasks.item_key(item)
            targets = [t.strip() for t in related.split(",") if t.strip()]
            if targets:
                tasks.link_item(conn, uid, key, targets)
            if comment.strip():
                tasks.add_comment(conn, uid, comment, item=key, session=SESSION)
            if state.strip():
                tasks.set_item_state(conn, uid, key, state.strip(), session=SESSION)
            head = tasks.get_task(conn, uid)
            if head is None:
                raise ValueError(f"no task {uid}")
            current = next(i["state"] for i in head["items"] if i["key"] == key)
            result = {
                "uid": uid, "item": key, "state": current,
                "progress": tasks.progress(conn, uid), "task_state": head["state"],
                "archived": memories.memory_row(conn, uid)["status"] == "archived",
            }
    except ValueError as exc:
        return _errors([str(exc)])
    return result


@tool("core")
def task_add(uid: str, items: str) -> dict:
    """Append items to a task, one per line; a closed task reopens.

    Returns the keys the new items got, and the progress.
    """
    try:
        with connection.connect() as conn:
            added = tasks.add_items(conn, uid, tasks.split_items(items), session=SESSION)
    except ValueError as exc:
        return _errors([str(exc)])
    return {"uid": uid, "items": added["keys"], "progress": added["progress"],
            "task_state": added["task_state"], "archived": added["archived"]}


@tool("core")
def task_comment(uid: str, body: str, item: str = "") -> dict:
    """Comment on a task, or on one of its items when `item` names a key.

    A comment never edits the task's content, so it carries what the
    checklist cannot: why an item is blocked, what a review said.
    """
    try:
        with connection.connect() as conn:
            comment_id = tasks.add_comment(conn, uid, body, item=item, session=SESSION)
    except ValueError as exc:
        return _errors([str(exc)])
    return {"uid": uid, "comment_id": comment_id}


@tool("core")
def task_read(uid: str, part: str, item: str = "", offset: int = 0) -> dict:
    """Read one collection of a task, one page at a time.

    part: `items` (each with its state and counts), `notes` and `comments`
    (the task's own; with `item`, that item's) or `links` (the memories
    linked to `item`). Follow `next_offset` until it is absent.
    """
    try:
        with connection.connect() as conn:
            return tasks.read_part(conn, uid, part, item, offset)
    except ValueError as exc:
        return _errors([str(exc)])


@tool("core")
def task_note(uid: str, title: str = "", body: str = "", items: str = "", note_id: int = 0,
              delete: bool = False) -> dict:
    """Write a note owned by a task: an item's brief, a rule its items share.

    Not a memory: only task_read() returns it. note_id=0 creates; a
    note_id edits in place (empty fields keep theirs) or, with delete,
    removes it. items: keys such as "i3,i7"; empty or "-" means the whole
    task. Title up to 120 characters, body up to 4000.
    """
    keys = [k.strip() for k in items.split(",") if k.strip() and k.strip() != "-"]
    try:
        with connection.connect() as conn:
            if delete:
                tasks.delete_note(conn, uid, note_id)
                return {"uid": uid, "note_id": note_id, "deleted": True}
            if note_id:
                tasks.edit_note(conn, uid, note_id, title=title, body=body,
                                items=keys if items.strip() else None)
            else:
                note_id = tasks.add_note(conn, uid, title=title, body=body, items=keys,
                                         session=SESSION)
            on = tasks.note(conn, uid, note_id)["items"]
    except ValueError as exc:
        return _errors([str(exc)])
    return {"uid": uid, "note_id": note_id, "items": on}


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

    @param title

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

    @param tags_brief

    `review_after` and `source_ref` behave as in note(), and a flow is
    exactly the kind of memory they are for: it describes code, and the
    code moves.

    Returns {"uid": ...}, or {"ok": False, "errors": [...]} with nothing
    written at all. Node positions are computed and stored server-side,
    so the flow renders identically for every reader -- see get_diagram().
    """
    with connection.connect() as conn:
        domain, warning = _coerce_domain(conn, domain)
        uid, errors = diagram_persist.insert_diagram(
            conn, title=title, nodes=nodes, edges=edges, summary=summary,
            kind=kind, domain=domain, also=also, session=session or SESSION, tags=tags,
            review_after=review_after, source_ref=source_ref,
        )
        if errors or uid is None:
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
    with connection.connect() as conn:
        if delete:
            ok, errors = diagram_persist.delete_diagram_node(conn, uid, key)
        else:
            ok, errors = diagram_persist.upsert_diagram_node(conn, uid, key, label=label, shape=shape, note=note)
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
    with connection.connect() as conn:
        if delete:
            ok, errors = diagram_persist.delete_diagram_edge(conn, uid, from_key, to_key)
        else:
            ok, errors = diagram_persist.upsert_diagram_edge(conn, uid, from_key, to_key, label=label)
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
    with connection.connect() as conn:
        if delete:
            ok = diagram_persist.delete_node_link(conn, uid, node_key, target_uid)
            errors = [] if ok else [f"no link from node {node_key!r} to {target_uid!r}"]
        else:
            ok, errors = diagram_persist.add_node_link(conn, uid, node_key, target_uid, relation_type)
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
    with connection.connect() as conn:
        if delete:
            ok = diagram_persist.delete_diagram_jump(conn, uid, node_key, peer_uid, peer_node)
            errors = [] if ok else [f"no jump between {node_key!r} and {peer_uid!r}"]
        else:
            ok, errors = diagram_persist.add_diagram_jump(
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
    with connection.connect() as conn:
        moved = diagram_persist.relayout_diagram(conn, uid)
    return {"ok": moved > 0, "nodes": moved}


_DIAGRAM_FORMATS = ("mermaid", "text", "json", "svg", "svg-interactive")


@tool("core")
def get_diagram(uid: str, format: str = "mermaid", offset: int = 0) -> dict:
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
    point where it matters: when the user asked to SEE the flow, reading the
    file and emitting it inline is the work, not a cost to avoid.

    A long `body`, and the SVG step index, page through `offset`.
    """
    if format not in _DIAGRAM_FORMATS:
        return _errors([f"unknown format {format!r}; use "
                        f"{', '.join(repr(f) for f in _DIAGRAM_FORMATS)}"])
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        data = diagram_persist.get_diagram(conn, uid)
        if data is None:
            return _errors([f"{uid} is not a diagram"])
        memories.record_recall(conn, [uid])
        if format in ("svg", "svg-interactive"):
            return _write_render(conn, uid, data, format, offset)
        body = (
            json.dumps(data, ensure_ascii=False, indent=1) if format == "json"
            else diagram_persist.render_diagram_text(conn, uid) if format == "text"
            else diagram_persist.render_diagram_mermaid(conn, uid)
        )
    text, nxt = budget.text_chunk(body, offset)
    out = {"uid": uid, "title": data["title"], "format": format, "offset": offset,
           "chars": len(body), "body": text}
    if nxt is not None:
        out["next_offset"] = nxt
    if format == "mermaid":
        # Repeated from the docstring because the caller is reading this now: answering "render
        # the diagram" with mermaid would silently swap the user's arrangement for a fresh layout.
        out["note"] = (
            "mermaid re-lays out the flow and discards the stored positions. "
            "To show the arrangement the user actually made, call again with "
            "format='svg-interactive' and emit the returned file inline.")
    return out


def _write_render(conn, uid: str, data: dict, format: str, offset: int = 0) -> dict:
    """Draw, write, sweep, and report -- without the markup in the payload.

    The index of steps IS the payload: labels and link targets, so the
    caller can talk about the diagram it just rendered, but not the notes,
    which are already in the file and are most of its size.
    """
    interactive = format == "svg-interactive"
    inline_target = None
    if interactive:
        # Two files: opening one needs a full document (doctype, charset, a dark-safe body), while
        # inline embedding needs no doctype, no body and no styling that leaks into the host page.
        markup = diagram_svg.render_interactive(data, standalone=True)
        viewbox = diagram_svg.render_svg(data)[1]
        inline_target = renders.renders_dir() / f"diagram-{uid}.inline.html"
        inline_target.write_text(
            diagram_svg.render_interactive(data), encoding="utf-8")
    else:
        markup, viewbox = diagram_svg.render_svg(data)
    target = renders.renders_dir() / f"diagram-{uid}.{'html' if interactive else 'svg'}"
    target.write_text(markup, encoding="utf-8")
    swept = renders.prune_renders(settings.get_svg_retention(conn), keep=target)
    links: dict[str, list[str]] = {}
    for link in data.get("links") or []:
        links.setdefault(link["node_key"], []).append(link["target_uid"])
    return {
        "uid": uid,
        "title": data["title"],
        "format": format,
        "path": str(target),
        # The payload cannot be drawn from, so it names the next step instead of leaving the
        # caller to infer that reading the file is it.
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
        "edges": len(data["edges"]),
        "retention": swept["mode"],
        "pruned": swept["pruned"],
    } | _page([
        {"key": n["key"], "label": n["label"], "shape": n["shape"],
         **({"links": links[n["key"]]} if n["key"] in links else {})}
        for n in data["nodes"]
    ], offset, key="nodes")


@tool("core")
def search(query: str, domain: str = "", type: str = "", limit: int = 10, offset: int = 0) -> dict:
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

    type filters: 'note', 'reasoning', 'checkpoint', 'anti_pattern', 'diagram',
    'task' (one writer each) and 'handoff' (rows already stored; task() carries
    unfinished work to the next session); any other type is an error.
    Ask for type='diagram' to sweep the documented flows on purpose. To recall
    note()'d knowledge specifically, recall() is the sugar for search(type='note')
    -- which also means recall() never surfaces a diagram; use search() for that.

    domain scopes to a path AND everything under it: domain='acme/x100'
    searches the module and each of its routines. Give more of the path to
    narrow it. A domain naming only the deep end of a path ('p200') is
    resolved to the branches it sits in -- every result carries its real
    `domain`, which is where to read what the filter actually covered.

    @param offset_page
    """
    if error := memories.type_error(type) or _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        return _listing(conn, store_search.search_ranked(conn, query, domain=domain, type=type,
                                               limit=limit, collapse=True), offset)


@tool("core")
def recall(query: str, domain: str = "", limit: int = 10, offset: int = 0) -> dict:
    """Recall long-term knowledge saved with note() (type='note').

    The dedicated verb for "bring back what I noted": a BM25 search scoped
    to type='note', ranked by relevance -- which is what you want for
    timeless facts/rules/decisions. This (or search(type='note')) is how
    notes come back by relevance; must_read(type='note') lists the newest.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated -- call get_memory(uid) for the full record; a
    result's `est_tokens` estimates what that full record costs, and the
    top-level `est_tokens` is the sum over the results.

    domain scopes to a path and everything nested under it, and resolves a
    bare deep segment the same way search() does. Results carry the same
    `succeeded_by` / `collapsed` annotations search() explains.

    @param offset_page
    """
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        return _listing(conn, store_search.search_ranked(conn, query, domain=domain, type=TYPE_NOTE,
                                               limit=limit, collapse=True), offset)


@tool("core")
def list_by_domain(
    domain: str, type: str = "", limit: int = 50, subtree: bool = True,
    status: str = "active", offset: int = 0,
) -> dict:
    """List memories for a domain and its subdomains, most recent first.

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

    status is 'active' (the default), 'archived' or 'all'. Closing a task
    archives its memory, so list_by_domain(domain, type='task',
    status='archived') returns the completed and cancelled tasks. A task
    row carries `state` (open, completed or cancelled) and `progress`
    {done, total}.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated per result -- call get_memory(uid) for the full
    record; a result's `est_tokens` estimates what that full record costs,
    and the top-level `est_tokens` is the sum over the results.

    @param offset_page
    """
    if error := memories.type_error(type):
        return _errors([error])
    if status not in LIST_STATUSES:
        return _errors([f"{status!r} is not a status; use one of {', '.join(LIST_STATUSES)}"])
    with connection.connect() as conn:
        rows = store_search.list_by_domain(
            conn, domain, type=type, status="" if status == "all" else status,
            limit=limit, subtree=subtree)
        listing = _listing(conn, rows, offset)
        for result in listing["results"]:
            if result["type"] == memories.TASK_TYPE:
                state = pending_lists.task_state(conn, result["uid"])
                if state is not None:
                    result["state"] = state
                    result["progress"] = pending_lists.task_progress(conn, result["uid"])
    return listing


@tool("core")
def list_recent(
    type: str = "", domain: str = "", limit: int = 20, subtree: bool = True, offset: int = 0
) -> dict:
    """List the most recent active memories, optionally filtered by type/domain.

    A domain covers its subdomains, and a bare deep segment resolves to the
    branches holding it (see list_by_domain); subtree=False narrows to that
    exact path.

    Returns {"results": [...], "est_tokens": N}. Content is
    snippet-truncated per result -- call get_memory(uid) for the full
    record; a result's `est_tokens` estimates what that full record costs,
    and the top-level `est_tokens` is the sum over the results.

    @param offset_page
    """
    if error := memories.type_error(type):
        return _errors([error])
    with connection.connect() as conn:
        rows = store_search.list_recent(conn, type=type, domain=domain, limit=limit,
                                         subtree=subtree)
        return _listing(conn, rows, offset)


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
    if error := memories.type_error(type):
        return _errors([error])
    with connection.connect() as conn:
        if uid:
            anchored_by = "uid"
            anchor = memories.get_memory(conn, uid)
            if anchor is None:
                return _errors([f"no memory {uid}"])
        else:
            anchored_by = "query"
            hits = store_search.search_ranked(conn, query, domain=domain, type=type, limit=1)
            if not hits:
                return _errors([f"no memory matches query: {query}"])
            # Re-read as a record: a search hit carries retrieval annotations
            # (match_source, ranks) that are not part of the memory.
            anchor = memories.memory_row(conn, hits[0]["uid"])
        older, newer = store_search.timeline_neighbours(
            conn, anchor, before=before, after=after, domain=domain, type=type)
        # Each side keeps the records nearest the anchor that fit half a page.
        near_old, _ = budget.page([_snippet_dict(_row_to_dict(r)) for r in reversed(older)], 0,
                                  budget.PAGE_MAX_CHARS // 2)
        near_new, _ = budget.page([_snippet_dict(_row_to_dict(r)) for r in newer], 0,
                                  budget.PAGE_MAX_CHARS // 2)
        _read(conn, [anchor, *older[len(older) - len(near_old):], *newer[:len(near_new)]])
    out = {
        "anchored_by": anchored_by,
        "anchor": _snippet_dict(_row_to_dict(anchor)),
        "before": list(reversed(near_old)),
        "after": near_new,
    }
    if len(near_old) < len(older) or len(near_new) < len(newer):
        out["trimmed"] = {"before": len(older) - len(near_old), "after": len(newer) - len(near_new)}
    return out


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
        "active": paths.active_project(),
        "projects": [{"name": s["name"], "memories": s["memories"], "active": s["active"]}
                   for s in projects.list_projects(counts=True)],
    }


@tool("core")
def list_domains(offset: int = 0) -> dict:
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

    @param offset_page
    """
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        return _page(domains.list_domains(conn), offset, key="domains")


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
    with connection.connect() as conn:
        if memories.get_memory(conn, uid) is None:
            return _errors([f"no memory {uid}"])
        try:
            return {"uid": uid, "also": memories.add_domain_link(conn, uid, domain)}
        except ValueError as exc:
            return _errors([str(exc)])


@tool("core")
def unfile_domain(uid: str, domain: str) -> dict:
    """Drop one of a memory's cross-listings. Does not touch where it is filed.

    Matched on the exact path: dropping 'acme' leaves a separate membership
    in 'acme/x100' alone, because that is a different scope. Returns
    {"uid": ..., "also": [...]} with what remains.
    """
    with connection.connect() as conn:
        if memories.get_memory(conn, uid) is None:
            return _errors([f"no memory {uid}"])
        return {"uid": uid, "also": memories.remove_domain_link(conn, uid, domain)}


@tool("curation")
def get_domain_case() -> dict:
    """Report the store's domain-casing policy.

    Returns {"mode": "preserve"|"lower"|"upper"}. 'preserve' stores
    domains as written; 'lower'/'upper' coerce every domain to that case
    on write (a non-conforming domain is adjusted, not rejected, and the
    writer's result carries a `domain_adjusted` note). Read this before
    coining a new domain so its casing matches what will be stored.
    """
    with connection.connect() as conn:
        return {"mode": domains.get_domain_case(conn)}


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
    with connection.connect() as conn:
        return {"mode": domains.set_domain_case(conn, mode)}


@tool("core")
def must_read(domain: str = "", type: str = "", limit: int = 10, offset: int = 0,
              pinned: bool = False) -> dict:
    """What is still open in a scope: counts per category, or one category's headers.

    Without type: {"categories": [{"type", "count"}, ...]} for task,
    anti_pattern, handoff, note and diagram, in that order, leaving out the
    empty ones, plus "pinned" -- the same shape, counting the memories a
    person pinned as mandatory reading -- when any pin is in scope. A pinned
    memory still counts in its category. With type: {"type", "total",
    "items", "next_offset"} -- one page of headers (uid, title, domain,
    est_tokens; a task adds `progress` {done, total} and `doing`, the keys of
    its items in progress), newest first, a task by its latest item update.
    Open a header with get_memory(uid). `next_offset` is absent on the last
    page.

    pinned=true narrows the page to the pins of that type, and also accepts
    checkpoint and reasoning. Every pin listed is read with get_memory(uid)
    before acting, none skipped.

    Pending means: a task that is open; an active anti_pattern, handoff or
    note that is not contradicted; an active diagram.

    domain covers its subdomains and what is cross-listed there; empty is the
    whole project. A pin is in scope when it is global, or when the memory's
    domain or one of its also paths is the asked domain or above it; the
    whole project counts global pins only. limit is 1 to 50. Any other type
    is an error.
    """
    allowed = pending_lists.PINNED_TYPES if pinned else pending_lists.CATEGORIES
    if error := memories.type_error(type, allowed=allowed):
        return _errors([error])
    with connection.connect() as conn:
        if not type:
            result = {"categories": pending_lists.counts(conn, domain)}
            pins = pending_lists.pinned_counts(conn, domain)
            if pins:
                result["pinned"] = pins
            return result
        try:
            page = {"limit": int(limit), "offset": int(offset)}
        except (TypeError, ValueError):
            return _errors(["limit and offset must be a whole number, "
                            f"got limit={limit!r} offset={offset!r}"])
        result = pending_lists.headers(conn, domain, type, pinned=bool(pinned), **page)
        _read(conn, result["items"])
    return result


READ_NEXT_PINNED = ("Read every pinned memory: for each type in `pinned`, call "
                    "must_read({domain}type=<t>, pinned=true), then get_memory(uid) on each "
                    "one. Skip none.")
READ_NEXT_TASKS = ("Work the open tasks: call must_read({domain}type='task'), then "
                   "get_memory(uid) on each task you will touch.")
READ_NEXT_OTHERS = ("Scan the rest: for each {other}type in `must_read`, call "
                    "must_read({domain}type=<t>) and read the titles it lists.")


def _read_next(domain: str, categories: list[dict], pinned: list[dict]) -> str:
    """The reading order for what is pending and pinned, or "" when nothing is.

    One step reads as a single sentence; several are numbered in the order to run them.
    """
    has_tasks = bool(categories) and categories[0]["type"] == TYPE_TASK
    steps = [READ_NEXT_PINNED] if pinned else []
    steps += [READ_NEXT_TASKS] if has_tasks else []
    if len(categories) > has_tasks:
        steps.append(READ_NEXT_OTHERS)
    if not steps:
        return ""
    arg = f"'{domain}', " if domain else ""
    steps = [s.format(domain=arg, other="other " if has_tasks else "") for s in steps]
    if len(steps) == 1:
        return f"Before acting, {steps[0][0].lower()}{steps[0][1:]}"
    return "Before acting, do these in order: " + " ".join(
        f"{n}. {s}" for n, s in enumerate(steps, 1))


@tool("core")
def pulse(domain: str = "", offset: int = 0) -> dict:
    """Session warm-up: the latest checkpoint, what is pending, and what to read next.

    Returns {project, latest_checkpoint, must_read, pinned, read_next, scope}.

    latest_checkpoint is picked by created_at DESC, never by similarity --
    a similarity-ranked top-1 can return a stale checkpoint over a
    same-day one, which is exactly the failure mode this avoids. It is
    returned in full (that's the point of pulse), with its relations
    attached so linked memories are visible without a separate
    get_relations call, and carries `est_tokens`, what this response already
    spent on it.

    `must_read` is the `categories` list must_read(domain) returns without a
    type: a count per category (task, anti_pattern, handoff, note, diagram),
    leaving out the empty ones. `read_next` is the instruction to follow with
    it, before acting: pinned memories, then open tasks, then
    must_read(domain, type=...) for each other category, numbered when there
    is more than one step. It is "" when nothing is pending. A pulse lists
    no memories besides the checkpoint; must_read(domain, type=...) lists
    headers and get_memory(uid) opens one.

    `pinned` counts the pins in scope the same way (see must_read()); when it
    is non-empty `read_next` asks for them before anything else.

    domain warms up a path and everything under it, so pulse('acme/x100')
    is the module-wide brief and pulse('acme/x100/p200') the routine's.
    A domain that names only the deep end of a path ('p200') is resolved
    to the branches it sits in -- `scope.paths` reports which, and an
    ambiguous name resolves to ALL of them.

    `scope` is the rest of the brief: what the scope HOLDS. `scope.by_type`
    counts every memory per type and `scope.subdomains` says which level it
    is sitting in (`own` = filed there, `subtree` = with its descendants).
    Read them as the drill-down plan: search(query, domain=...) or
    list_by_domain(domain, type=..., limit=...) on the child that holds what
    you need. A pulse is the state of a scope, never its contents.

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

    A long checkpoint is cut (`next` names the rest); `offset` pages
    `scope.subdomains`.
    """
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        census = domains.domain_census(conn, domain)
        latest_checkpoint = store_search.latest_by_type(conn, TYPE_CHECKPOINT, domain=domain,
                                              exclude_contradicted=True)
        categories = pending_lists.counts(conn, domain)
        pins = pending_lists.pinned_counts(conn, domain)
        checkpoint_dict = _row_to_dict(latest_checkpoint)
        if checkpoint_dict:
            # Returned whole up to one piece of a page, so its est_tokens is what this response
            # spent on it rather than what a fetch would cost.
            _with_est_tokens(checkpoint_dict)
            text, more = budget.text_chunk(checkpoint_dict["content"], 0, PULSE_CHECKPOINT_CHARS)
            if more is not None:
                checkpoint_dict["content_chars"] = len(checkpoint_dict["content"])
                checkpoint_dict["content"] = text
                checkpoint_dict["next"] = (f"get_memory(uid='{checkpoint_dict['uid']}', "
                                           f"content_offset={more})")
            checkpoint_dict["relation_count"] = len(relations.get_relations(conn, checkpoint_dict["uid"]))
            _read(conn, [latest_checkpoint])
    return {
        "project": paths.active_project(),
        "latest_checkpoint": checkpoint_dict,
        "must_read": categories,
        "pinned": pins,
        "read_next": _read_next(domain, categories, pins),
        "scope": {
            "domain": domain,
            "paths": census["paths"],
            "total": census["total"],
            **({"also": census["also"]} if census.get("also") else {}),
            **({"stale": census["stale"]} if census.get("stale") else {}),
            "by_type": census["by_type"],
            **_subdomains(census["children"], offset),
        },
    }


PULSE_CHECKPOINT_CHARS = 12_000


def _subdomains(children: list, offset: int) -> dict:
    rows, nxt = budget.page(children, offset, budget.PAGE_MAX_CHARS // 2)
    page = {"subdomains": rows, "subdomains_total": len(children)}
    if nxt is not None:
        page["subdomains_next_offset"] = nxt
    return page


@mcp.prompt()
def warm_up(domain: str = "") -> str:
    """What memai already knows, as text, for a session about to start.

    The same brief the SessionStart hook emits (see memai.hook), offered
    here for hosts that surface prompts as commands. A prompt is invoked by
    the person, which makes it the one place in the MCP protocol where the
    store can be READ without the agent having decided to read it.
    """
    with connection.connect() as conn:
        text = brief.session_brief(conn, domain=domain, project=paths.active_project())
    return text or "The memai store is empty -- nothing to warm up from yet."


# An edit body longer than this is cut in an edits page; the memory's current body reads in full.
EDIT_BODY_SHOWN = 4_000


def _edit_record(row) -> dict:
    record = _row_to_dict(row)
    record.pop("memory_uid", None)
    cut = False
    for field in ("prev_content", "new_content"):
        body = record.get(field) or ""
        record[field.replace("content", "chars")] = len(body)
        if len(body) > EDIT_BODY_SHOWN:
            record[field] = body[:EDIT_BODY_SHOWN]
            cut = True
    record["truncated"] = cut
    return record


def _paged(uid: str, part: str, records: list, offset: int) -> dict:
    rows, nxt = budget.page(records, offset)
    out = {"uid": uid, "part": part, "total": len(records), "offset": offset, "records": rows}
    if nxt is not None:
        out["next_offset"] = nxt
    return out


@tool("core")
def get_memory(uid: str, edits_offset: int = -1, content_offset: int = -1) -> dict:
    """Fetch one memory: its fields, and counts of what is linked to it.

    `next` names the call that pages each part: get_relations() for
    relations and diagrams, get_diagram() for a diagram's graph,
    task_read() for a task's items, notes and comments, edits_offset for
    the edit history (bodies cut to 4000 characters) and content_offset
    for a body too long for one response.
    """
    try:
        with connection.connect() as conn:
            row = memories.get_memory(conn, uid)
            if row is None:
                # Not {}: an empty dict reads as an empty record, when the uid names no memory at all.
                return _errors([f"no memory {uid}"])
            if edits_offset != -1:
                edits = [_edit_record(e) for e in memories.get_edit_history(conn, uid)]
                return _paged(uid, "edits", edits, edits_offset)
            if content_offset != -1:
                text, more = budget.text_chunk(row["content"], content_offset)
                out = {"uid": uid, "part": "content", "offset": content_offset,
                       "content_chars": len(row["content"]), "text": text}
                if more is not None:
                    out["next_offset"] = more
                return out
            _read(conn, [row])
            count = memories.edit_count(conn, uid)
            relation_count = len(relations.get_relations(conn, uid))
            result = _row_to_dict(row)
            nxt: dict = {}
            if row["type"] == TYPE_DIAGRAM:
                result.pop("content", None)
                mermaid, more = budget.text_chunk(diagram_persist.render_diagram_mermaid(conn, uid), 0,
                                                  diagram_render.DIAGRAM_BODY_BUDGET)
                result["mermaid"] = mermaid
                result["node_link_count"] = len(diagram_persist.get_node_links(conn, uid))
                result["jump_count"] = len(diagram_persist.get_diagram_jumps(conn, uid))
                nxt["diagram"] = f"get_diagram(uid='{uid}', format='json')"
                if more is not None:
                    nxt["mermaid"] = f"get_diagram(uid='{uid}', format='mermaid', offset={more})"
            else:
                diagrams = len(diagram_persist.diagrams_referencing(conn, uid))
                result["referenced_by_diagrams"] = diagrams
                if diagrams:
                    nxt["diagrams"] = f"get_relations(uid='{uid}', part='diagrams')"
            if row["type"] == TYPE_TASK:
                result.pop("content", None)
                head = tasks.head(conn, uid)
                head["next"] = {p: f"task_read(uid='{uid}', part='{p}')"
                                for p in ("items", "notes", "comments")}
                result["task"] = head
    except ValueError as exc:
        return _errors([str(exc)])
    if "content" in result:
        text, more = budget.text_chunk(result["content"], 0)
        if more is not None:
            result["content"] = text
            result["content_chars"] = len(row["content"])
            nxt["content_offset"] = more
    result["edit_count"] = count
    if count:
        nxt["edits"] = f"get_memory(uid='{uid}', edits_offset=0)"
    result["relation_count"] = relation_count
    if relation_count:
        nxt["relations"] = f"get_relations(uid='{uid}')"
    if nxt:
        result["next"] = nxt
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
    stakes the existing text on it being copied faithfully. Append what
    THIS memory gained. A fact about a further subject is a new memory plus
    an edge, not a line at the bottom -- appended text is ranked as part of
    the body it lands in and comes back with it.

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
    editable here like any other memory's. A task's content is generated
    from its goal and items the same way, and is refused the same way.
    """
    if mode not in ("replace", "append"):
        return _errors([f"mode must be 'replace' or 'append'; got {mode!r}"])
    if not (new_content.strip() or source_ref.strip() or title.strip() or tags.strip()):
        return _errors(["nothing to change: pass new_content, source_ref, title or tags"])
    changed = []
    with connection.connect() as conn:
        if new_content.strip():
            if diagram_persist.is_diagram(conn, uid):
                return _errors([
                    f"{uid} is a diagram: its content is generated from the graph. "
                    "Use diagram_node/diagram_edge to change the flow."
                ])
            if tasks.is_task(conn, uid):
                return _errors([
                    f"{uid} is a task: its content is generated from the goal and "
                    "items. Change them through the task tools."
                ])
            try:
                if not memories.update_memory_content(conn, uid, new_content, note=note,
                                                append=mode == "append"):
                    return _errors([f"no memory {uid}"])
            except ValueError as exc:
                # a body the store will not hold: one that does not read as its
                # type's fields, or one carrying a tool call's own source
                return _errors([str(exc)])
            changed.append("content")
        if source_ref.strip():
            if not memories.set_source_ref(conn, uid, source_ref, note=note):
                return _errors([f"no memory {uid}"])
            changed.append("source_ref")
        if title.strip():
            if diagram_persist.is_diagram(conn, uid):
                return _errors([
                    f"{uid} is a diagram: its title is part of what generates its "
                    "body, so a rename here would be overwritten by the next "
                    "structural change. Rename it in the dashboard."
                ])
            too_long = store_sections.title_error(title)
            if too_long:
                return _errors([too_long])
            if not memories.set_title(conn, uid, title, note=note):
                return _errors([f"no memory {uid}"])
            changed.append("title")
        if tags.strip():
            if not memories.set_tags(conn, uid, tags, note=note):
                return _errors([f"no memory {uid}"])
            changed.append("tags")
    return {"ok": True, "changed": changed}


@tool("core")
def link_memories(from_uid: str, to_uid: str, relation_type: str, note: str = "") -> dict:
    """Create a queryable edge between two memories.

    relation_type is free text but keep it consistent, e.g.
    'supersedes', 'relates_to', 'contradicts', 'links_to'.

    This is what splitting a body into several memories costs: a [[uid]]
    written inside prose is a reference a reader follows, and only an edge
    created here is visible to get_relations() and to the graph.

    Refuses an unknown uid, a memory related to itself, and an edge that
    already exists with that same type -- each as
    {"ok": False, "errors": [...]}, so a typo comes back as something to
    fix instead of a dangling edge or a raw database error.
    """
    with connection.connect() as conn:
        try:
            rel_id = relations.add_relation(conn, from_uid, to_uid, relation_type, note=note)
        except ValueError as exc:
            return _errors([str(exc)])
    return {"relation_id": rel_id}


@tool("core")
def get_relations(uid: str, part: str = "relations", offset: int = 0) -> dict:
    """List a memory's links, one page at a time.

    part: 'relations' (edges, both ways) or 'diagrams' (flows pointing at
    it); follow `next_offset`.
    """
    if part not in ("relations", "diagrams"):
        return _errors([f"{part!r} is not a part; use 'relations' or 'diagrams'"])
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        rows = (relations.get_relations(conn, uid) if part == "relations"
                else diagram_persist.diagrams_referencing(conn, uid))
    return _page([_row_to_dict(r) for r in rows], offset, uid=uid, part=part)


@tool("core")
def set_confidence(uid: str, confidence: str) -> dict:
    """Set a memory's confidence: unverified | confirmed | contradicted."""
    if confidence not in optimizer.CONFIDENCE_VALUES:
        return {"ok": False, "error": f"confidence must be {'|'.join(optimizer.CONFIDENCE_VALUES)}"}
    with connection.connect() as conn:
        ok = memories.set_confidence(conn, uid, confidence)
    return {"ok": ok}


@tool("core")
def forget(uid: str, reason: str = "", superseded_by: str = "") -> dict:
    """Archive a memory (soft delete -- content is kept, just excluded from default search/list).

    A `reason` is recorded as a status-change audit entry, without touching
    the content. Archiving an open task cancels it; its items keep their states.
    """
    with connection.connect() as conn:
        ok = memories.set_status(
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
    with connection.connect() as conn:
        ok = memories.purge_memory(conn, uid)
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
    return portable.move(paths.active_project(), target, uids=wanted, domain=domain,
                         dry_run=dry_run, create=create)


@tool("curation")
def dedup_scan(domain: str = "", type: str = "", threshold: float = 0.6, limit: int = 20,
               offset: int = 0) -> dict:
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

    @param offset_page
    """
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        pairs = dedup.dedup_candidates(conn, domain=domain, type=type, threshold=threshold,
                                    limit=limit)
    records = [
        {"a": _snippet_dict(_row_to_dict(a)), "b": _snippet_dict(_row_to_dict(b)),
         "ratio": round(score, 3), "method": method}
        for a, b, score, method in pairs
    ]
    return _page(records, offset, key="pairs")


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
    rejected without it: cross-check newer memories in the corpus, verify
    code anchors against the live repo, web-check world facts.
    """
    with connection.connect() as conn:
        corpus = store_corpus.optimization_corpus(
            conn, domain=domain, type=type, since=since,
            include_archived=include_archived,
            limit=limit, offset=offset, full=full)
        pairs = dedup.dedup_candidates(conn, domain=domain, type=type, since=since, limit=20)
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
    retitle {"title"}, redomain {"domain"}, crosslist {"also": [...]}
    (replaces the whole set), set_confidence {"confidence"}, review
    {"review_after"} (a date or a span like '180d'; '' clears it), archive
    {"reason"}, link {"from_uid","to_uid","relation_type"}, merge
    {"keep_uid","drop_uid"}, distill {"source_uids","new_type","new_content",
    "title"}, unleak {"field": "content|tags|source_ref"} (the repair is
    computed at staging). link/merge derive target_uid from the payload and
    distill creates its target -- omit it for those.

    Destructive kinds (archive, set_confidence=contradicted, merge,
    distill) require a non-empty `verified` describing the live-facts
    check behind them. Invalid suggestions are skipped and reported in
    `errors`; the rest are staged. Returns {run_id, staged, errors}.

    `note` is one short summary of the pass, at most 250 characters; a
    longer one raises and stages nothing. What a single suggestion needs
    said belongs in its own `rationale` and `verified`, which are not
    capped.
    """
    with connection.connect() as conn:
        result = optimizer.stage_optimization(conn, note, suggestions)
    return result


@tool("curation")
def optimize_runs(offset: int = 0) -> dict:
    """List optimization runs with their review progress.

    Read-only companion to optimize_stage: after staging, use this to see
    whether the user has applied/rejected your suggestions in the admin
    dashboard. Each run carries total/pending/applied/rejected counts,
    its note, and the safety-backup path once the first apply happened.
    Applying/rejecting stays in the dashboard by design -- the agent
    proposes, the human disposes.

    @param offset_page
    """
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        rows = optimizer.list_optimization_runs(conn)
    return _page([dict(r) for r in rows], offset, key="runs")


@tool("curation")
def optimize_status(run_id: int, offset: int = 0) -> dict:
    """Inspect one optimization run: every suggestion and its decision.

    Read-only. Returns the run header plus each suggestion's kind,
    target_uid, payload, rationale, verified, status
    (pending/applied/rejected) and decided_at -- so you can tell which
    proposals landed, follow up on rejected ones, or build on applied
    ones in a later pass.

    @param offset_page
    """
    if error := _offset_error(offset):
        return _errors([error])
    with connection.connect() as conn:
        run = optimizer.get_optimization_run(conn, run_id)
        if run is None:
            return {"error": f"unknown run: {run_id}"}
        sugs = optimizer.get_optimization_suggestions(conn, run_id)
    return _page([{**dict(s), "payload": json.loads(s["payload"]) if s["payload"] else {}}
                  for s in sugs], offset, key="suggestions", run=dict(run))


# Every tool this module defines, by name, whether or not its group is
# published in this process.
_TOOLS = {
    "note": note,
    "checkpoint": checkpoint,
    "anti_pattern": anti_pattern,
    "reasoning": reasoning,
    "task": task,
    "task_item": task_item,
    "task_add": task_add,
    "task_comment": task_comment,
    "task_read": task_read,
    "task_note": task_note,
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
    "must_read": must_read,
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
}

# _TOOLS is written by hand and _GROUP_OF by the decorator, so they can drift.
# Cheap to check, once, at import.
assert set(_TOOLS) == set(_GROUP_OF), (
    f"_TOOLS is out of step with the decorated tools: "
    f"{set(_TOOLS) ^ set(_GROUP_OF)}")


def main() -> None:
    # Before mcp.run(): the main thread has no event loop or stdio readers yet, and a lifespan
    # hook would sit on the initialize path. A no-op unless MEMAI_ADMIN_AUTOSTART; never raises.
    autostart.ensure_admin_running()
    mcp.run()


if __name__ == "__main__":
    main()
