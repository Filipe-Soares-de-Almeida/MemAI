---
name: memai-memory
description: >
  How to use the MemAI MCP server (long-term agent memory: one ACID SQLite
  store, BM25 keyword search) — which tool
  family to call when: pulse/pending/search/recall/list_by_domain/list_recent/
  list_domains/get_memory/help to read; note, reasoning, anti_pattern,
  checkpoint, task, diagram to write; task_item/task_add/task_comment to work a
  task; diagram_*/get_diagram for flows;
  edit_memory/link_memories/set_confidence/also_domain/forget/purge_memory to
  curate — plus the session/domain/tags convention, with domains as nested
  PATHS and cross-listing (also), and review_after/source_ref for a memory
  that will go stale. Use at the START of a session or when RESUMING work
  (warming a cold session), whenever SAVING or LOOKING UP durable knowledge
  (a fact, a decision, a rule, a pitfall), when DOCUMENTING a routine as a
  flow, and before PAUSING or ENDING work (checkpoint, open tasks). Also on
  "MemAI", "memory", "long-term memory", "warm up the session", "checkpoint",
  "what did the last session leave", "what is pending", "task", "checklist",
  "save this lesson", "anti-pattern", "diagram of the flow".
---

# memai-memory — using the MemAI MCP server

**MemAI** is a long-term memory store with **BM25 keyword** retrieval,
exposed over MCP under the name the host registered the server with —
`mcp__memai__*` for the documented one. It matches on the **words that were
written** — an exact identifier (`F100_TOTAL`, `USE_NEW_PARSER`, `proj-1042`)
as readily as a phrase — which is why `tags` carry the synonyms a body never
happened to use.

> **`help()` is the source of truth for the tools.** `help()` lists every tool
> with a one-line summary; `help(command='<name>')` returns that tool's
> **signature and full documentation**, read live from the code, so it cannot
> drift from behaviour. When unsure of a name or an argument, call `help`
> instead of trusting this file.

> **Not every tool is loaded.** `MEMAI_TOOLS` names the tool groups a process
> publishes (`core`, `diagrams`, `curation`; `full` is the default), because
> every published schema is paid for on every request. A tool this file names
> may therefore be absent from the session. `help()` documents all of them
> regardless and reports the ones this process did not load, under
> `not_loaded` — so an unavailable tool is a setting to change, not a missing
> feature.

> **Transport:** a local **stdio** process (`memai-mcp`), no HTTP server. Only
> the agent talks to it over MCP. The `memai-hook` CLI opens the same SQLite
> file directly, without MCP, which is how the store reaches a session before
> any tool has loaded (see [§6](#6-surfaces-outside-the-tool-calls)).

> **Architecture:** **one SQLite file** (WAL, ACID) at `$MEMAI_HOME/memai.db`,
> or `~/.memai/memai.db` when `MEMAI_HOME` is unset. Rows, FTS5 index (BM25),
> edit history, relations and the **diagram graph** (nodes, edges, step links,
> jumps) commit in the same transaction. There is **no `snapshot`/`reindex`**
> — a write is durable when it returns. Nothing here reaches the network.

---

## 1. What goes in the store, and as which type

Six types, one writer each. Choosing the type is choosing how the memory
comes back later ([§3.1](#31-getting-back-what-you-wrote-which-tool-to-call)).
A seventh type, `handoff`, is carried by memories already in the store: they are
read, searched and counted as pending like any other, and nothing writes a new
one — a task carries unfinished work to the next session.

| Knowledge | Writer | Why that one |
|---|---|---|
| **Timeless fact** — a rule, a long-lived finding, a decision | **`note`** | Worth recovering in **any** future session; not tied to a state of work |
| **Reasoning/analysis** worth keeping (what was tested → what is known now → what to do next) | **`reasoning`** | A thought process for the next agent, not a fact |
| **Pitfall** that looks right and is not | **`anti_pattern`** | Comes back when the temptation reappears; counted as pending, listed by `pending(type='anti_pattern')` |
| **Where the work stands** at a pause | **`checkpoint`** | The one body `pulse` returns whole to warm a cold session — read for bearing, not as an archive |
| **Work for whoever picks this up** | **`task`** | A goal and a checklist, worked with `task_item` and closed once every item is done or dropped; open ones come first in every session start ([§4.1](#41-tasks-a-goal-and-a-checklist)) |
| **What a routine does, end to end** (steps, decisions, outputs) | **`diagram`** | A flow as a **graph**, not prose: each step carries its own note and links, which makes it the index of its domain ([§4.2](#42-diagrams-a-flow-as-a-graph)) |

> **checkpoint × note × reasoning** — the boundary most often crossed by
> mistake:
> - **`checkpoint`** = *where the work stands* right now, so the next session
>   picks up the right bearing. Fields are **free-length**, but a checkpoint
>   is read **for bearing, not as an archive** — keep it a readable summary
>   and push timeless detail into `note`.
> - **`note`** = **timeless** knowledge (a rule, a long-lived finding, a
>   decision) — it holds regardless of when it is read. If the fact outlives
>   the session, it is a `note`, not a checkpoint.
> - **`reasoning`** = a **reasoning trace** worth preserving — not a fact, the
>   process (hypothesis → what was proved → what to do). The fact distilled
>   out of the analysis is a `note`; the analysis itself is `reasoning`.
>
> **`diagram` is for a PROCESS, not a fact.** `note` records what is true,
> `checkpoint` where the work stands, `diagram` how a routine runs.

> **One memory, ONE fact.** A body that answers four questions is still one
> ranked row: it comes back for all four subjects and is read for one of
> them. When a second subject turns up while writing, it is a second
> memory — filed on its own domain, cited from the first by `[[uid]]` and
> joined with `link_memories()`. Past a couple of thousand characters, a
> body is usually several memories written as one. A `[[uid]]` in prose is a
> reference a reader follows, **not** an edge: `get_relations()` and the
> graph see only what `link_memories()` created
> ([§4](#4-during-the-work)).

> **Curate, do not index.** Write MemAI **record by record**, for what has
> reuse. It is not a document index — do not load a corpus of files into it.
> Every stored row is a candidate the retrieval side has to rank against every
> query, for every subject, in every later session.

---

## 2. Labelling — title, session, domain, tags

| Field | Convention | Examples |
|---|---|---|
| `title` | **required** on every writer: one line naming what the memory is about, in the words someone would look for it by. It is what a list shows instead of the body's opening line, and it outweighs every other field in search | `When the nightly export runs` · `Why the drain step retries twice` |
| `session` | **optional** free text; a per-process stamp is applied when it is omitted, so one conversation's memories group together without an agent tracking an id | `20260810T1423-1f4c` |
| `domain` | where the memory is **FILED**: a **path**, outermost scope first | `acme/x100/p200`, `acme/x100`, `proj-1042` |
| `also` | csv of other paths the memory **BELONGS** to — the subjects that cut across the tree ([§2.2](#22-cross-listing--the-subject-that-cuts-across-the-tree)) | `omni/x900` |
| `tags` | csv of keywords/synonyms. Retrieval is **BM25 over content, tags and domain**, and tags weigh second only to the body — a synonym here is the only way a memory answers a query that words the subject differently. Write the identifier, the symbol, the error string and the plain-language phrasing someone will type | `cache,warmup,cold-start` · `queue,drain,retry` |

> **`title` is required; a write without one is refused before it reaches the
> store.** Name the memory by its subject — a title that repeats the type
> ("note about the loader") names nothing.

> **`tags` and `also` are on every writer.** A memory written without tags is
> reachable only by quoting its own body, and the write says so: the result
> carries `tags_indexed`, and `tags_hint` in its place when the count is zero.
> `review_after`/`source_ref` are on the durable writers —
> `note`, `reasoning`, `anti_pattern`, `diagram` — and not on `checkpoint` or
> `task` ([§2.4](#24-decay--review_after-and-source_ref)). Signatures are
> in [§7](#7-tool-reference).

### 2.1 A domain is a path (a subject inside a subject)

`acme/x100/p200` is a routine inside a module inside a product. **Every read
that takes a domain covers its subdomains**, so the same verb asks about the
module (`pulse('acme/x100')`) or one routine (`pulse('acme/x100/p200')`) —
filing deeper hides nothing from whoever asks about the parent.

- **`list_domains()`** returns the **tree**: `parent`, `depth`, `count`
  (filed at exactly that path), `subtree` (that plus everything under it),
  `children`, `implicit` (a level that exists only because something deeper is
  filed below it), plus `also`/`subtree_also` for what is cross-listed there.
- **A partial name resolves.** What a caller has in hand is usually the **deep
  end** (`p200`), which is the front of no path once the routine lives at
  `acme/x100/p200`. When nothing in the store starts with the string, it is
  retried as a **run of segments anywhere inside** a path, and every branch it
  names becomes a scope. The **literal reading always wins**, and an
  **ambiguous name broadens** to every branch holding it rather than picking
  one — `pulse` reports the result in `scope.paths`, so never assume the scope
  read was only the one asked for.
- **Do not invent levels.** A hyphen inside a name is not a level:
  `proj-1042` is **one** segment.
- `subtree=False` on `list_by_domain`/`list_recent` narrows to exactly that
  path and nothing deeper.

### 2.2 Cross-listing — the subject that CUTS ACROSS the tree

A memory is **filed** at one path (`domain`) and can **belong** to others
(`also`): the axes that cross the hierarchy — several routines that are each a
step of one end-to-end process without any being the parent of the others. A
read scoped to that path returns all of them.

`note(..., domain='acme/x100/p200', also='omni/x900')` files on the routine
and makes every read scoped to `omni/x900` return it too, subtree included.

- **`also_domain(uid, domain)`** cross-lists after the fact;
  **`unfile_domain(uid, domain)`** drops **one** cross-listing, matched on the
  exact path, and never moves where the memory is filed.
- A path the memory's own `domain` already sits under is **dropped as
  redundant** — the writer echoes the set that actually holds, so
  cross-listing three subjects and getting two back says which reading was
  already covered. A path **below** the `domain` is kept: a narrower scope is
  a real thing to say.
- A path can exist **only** as a cross-listing (`count: 0`, `also` above it):
  an end-to-end process whose every step lives under some other branch. It is
  a scope like any other.

### 2.3 Casing is a store-wide policy, applied on write and folded on read

- **`get_domain_case()`** reports the policy: `preserve` | `lower` | `upper`.
  **Read it before coining a new domain.**
- Under `lower`/`upper` a domain is **coerced on write** (adjusted, never
  rejected — the writer's result carries `domain_adjusted` saying `from`,
  `to` and `policy`), and a **filter written in any case is folded to it**, so
  `list_by_domain('ACME/X100')` finds what was stored as `acme/x100`. A
  resolved scope comes back spelled **as stored**, which is what `scope.paths`
  reports.
- Under `preserve` the casing of each write survives, which means
  `acme/Cache` and `acme/cache` are **two paths**. A filter then broadens to
  both, the same way an ambiguous segment does. Reuse an existing path exactly
  as `list_domains()` spells it.

### 2.4 Decay — `review_after` and `source_ref`

A memory about code is true until the code changes, and nothing in a store
notices. The durable writers (`note`, `reasoning`, `anti_pattern`, `diagram`)
take two optional fields for that:

- **`review_after`** — when the claim stops being safe to trust unchecked, as
  a date (`'2026-11-01'`) or a span from today (`'90d'`).
- **`source_ref`** — what to check it against: a path, a URL, an identifier.

What they buy: `pulse` reports **`scope.stale`** when a scope holds memories
whose date has passed, `optimize_scan` marks each one `due` and lists its
`source_ref`, and a curation pass pushes the date out with a `review`
suggestion **after** actually rechecking. Verifying a `source_ref` against a
live tree is not something the store does.

Neither field has to be right at write time: `edit_memory(uid,
source_ref=…)` sets or repoints one afterwards, without touching the body,
which is how a memory written before anyone knew where the code lived — or
one whose file has since moved — gets its reference.

**When to set them:** a memory that describes code, config, a schema or an
external URL — a diagram is the clearest case, since it describes code and
code moves. **When to leave them empty:** everything that does not go stale,
which is most memories. A date nobody meant is worse than no date.

---

## 3. Session START — warming a cold session

When the session-start hook fires, or when resuming work:

1. **`pulse(domain)`** — the state inherited by a scope, as
   `{project, latest_checkpoint, pending, pinned, read_next, scope}`:
   - **`latest_checkpoint`** — by `created_at DESC`, never by similarity,
     returned **in full** with its **relations** attached. It is the only body
     a pulse carries.
   - **`pending`** — the `categories` list that `pending()` without a `type`
     returns, `[{"type", "count"}, ...]`: `task` (open), `anti_pattern`,
     `handoff` and `note` (active and not contradicted), `diagram` (active), in
     that order, empty ones left out. No list of memories comes with it.
   - **`pinned`** — the pins in scope, `[{"type", "count"}, ...]`, in the same
     shape as `pending`. A person pinned these as mandatory reading: when it is
     non-empty `read_next` asks for them first. A pinned memory still counts
     in its category.
   - **`read_next`** — one instruction naming what to call next: pinned
     memories first, then open tasks, then `pending(domain, type=…)` for each
     other category, before acting. Empty when nothing is pending.
   - **`scope`** — what the scope holds: `paths` (which path(s) the name
     resolved to), `total`, `by_type`, `subdomains` (`own` = filed there,
     `subtree` = with descendants), plus `also` (how much of the scope arrived
     by cross-listing) and `stale` — those two present only when non-zero, so a
     store that never cross-lists never sees the field. It is the drill-down
     plan: `search(query, domain=…)` or `list_by_domain(domain, type=…,
     limit=…)` on the child that was only counted.
2. **`pending(domain, type, limit, offset, pinned)`** — the read that lists what a
   pulse only counts. Without `type` it returns
   `{"categories": [{"type", "count"}, ...]}`; with `type` (`task`, `anti_pattern`, `handoff`, `note` or `diagram`) it
   returns `{type, total, items, next_offset}`: one page of **headers** — `uid`,
   `title`, `domain`, `est_tokens` — newest first, a task by its latest item
   update. A task header adds `progress` (`{done, total}`) and `doing`, the keys
   of its items in progress. Open a header with `get_memory(uid)`;
   `next_offset` is absent on the last page and `limit` is at most 50.
   Without `type` the response adds `pinned` when a pin is in scope.
   `pending(type, pinned=true)` lists only the pins of that type (it also
   accepts `checkpoint` and `reasoning`); open **every** one with
   `get_memory(uid)` before acting. A global pin is in every scope; a domain
   pin is in scope for its domain, its cross-listed paths and everything
   under them. Pins are set by a person in the dashboard; no tool writes them.
   **Work the open tasks first**: `pending(type='task')`, then `get_memory(uid)`
   for each one the session will touch.
3. **`search(query, domain, type, limit)`** — BM25 over the subject at hand.
   **Spend terms freely:** every space-separated term is asked for separately
   and a row matching more of them ranks higher, so piling the identifier, the
   routine name and the plain-language phrasing into one query costs one call
   and finds strictly more. Each hit carries `match_source` (`fts`, or `uid`
   for the row a pasted identifier names) and `fts_rank` (lower = better).
   Content comes back **truncated** — open the full record with
   `get_memory(uid)`.
4. **`recall(query, domain)`** — the dedicated verb for long-term knowledge
   written with `note` ([§3.1](#31-getting-back-what-you-wrote-which-tool-to-call)).
5. **`timeline(uid | query, before, after, domain, type)`** — what else was
   being written **around** one memory, in creation order, whether or not it
   shares a word with it. The neighbours of a checkpoint are the notes and
   pitfalls of the same stretch of work. One of `uid` or `query` is required
   (`uid` wins if both are given; `query` takes the top hit), and the
   response reports `anchored_by` plus the whole `anchor`, so the record it
   was built around is never a guess. `domain`/`type` narrow the
   **neighbourhood**, not the anchor.
6. **`list_domains()`** / `list_by_domain(domain)` / `list_recent()` — the
   real domain **tree** ([§2.1](#21-a-domain-is-a-path-a-subject-inside-a-subject)),
   and the recency fallback when search comes back thin.
7. **`help()`** / `help(command)` — to confirm an exact name or signature, and
   to see which tools this process did not load.

> **The listing tools return an envelope, not a bare list.** `search`,
> `recall`, `list_by_domain` and `list_recent` return
> `{"results": [...], "est_tokens": N}` — index into `results`. Every record
> anywhere (a listing, a `pending` page, a `pulse`, a `timeline`) carries its
> own `est_tokens`, the estimated cost of its **full** content: on a truncated
> one that prices the `get_memory(uid)` before making the call, and the
> top-level number is the sum over the results.

> Two annotations in search results are worth acting on. **`succeeded_by`**
> means something in the store supersedes this memory — read that one instead.
> **`collapsed`** lists near-identical results folded into this one, so a fact
> written five times spends one slot; raise `limit` to see the copies. A
> memory with `confidence: contradicted` sorts behind everything that still
> holds but does come back, because knowing a claim was ruled out is what
> stops it being written again.

> Empty means the store has no context for that scope yet. Carry on, and
> populate it as you go.

---

## 3.1 Getting back what you wrote (which tool to call)

Every writer stores a **fixed `type`**, and the writer is **named after the
type**. To bring it back, filter on that `type`.

| Written with… | Stored as `type=` | How to get it back |
|---|---|---|
| `note` | **`note`** | **`recall(query, domain)`** (a `search` scoped to `type='note'`) · or `search(type='note')` · fallback `list_*` with `type='note'` · full body via `get_memory(uid)` |
| `reasoning` | `reasoning` | `search(…, type='reasoning')` / `list_*` with `type='reasoning'` |
| `anti_pattern` | `anti_pattern` | counted in **`pulse`**, listed by **`pending(type='anti_pattern')`** · or `list_*`/`search` with `type='anti_pattern'` |
| `checkpoint` | `checkpoint` | **`pulse`** returns the latest by `created_at`, in full |
| `task` | `task` | open ones are listed by **`pending(type='task')`** · or `list_*`/`search` with `type='task'` · the checklist itself via `get_memory(uid)`; closed ones via `list_by_domain(domain, type='task', status='archived')`; worked with `task_item` |
| `handoff` | — (a type existing memories carry) | active ones are listed by **`pending(type='handoff')`** · or `list_*`/`search` with `type='handoff'` |
| `diagram` | `diagram` | counted in `pulse`, **titles** from `pending(type='diagram')` · or `list_*`/`search` with `type='diagram'` (search matches the prose the graph generates) · the graph itself via **`get_diagram(uid)`** |

> **`recall(query, domain)`** is the dedicated recall verb: a search scoped
> to `type='note'` and ranked by **relevance**, which is what timeless
> knowledge wants. It therefore never surfaces a diagram — use `search` for
> that. `pending(type='note')` complements it with the scope's notes by
> **recency**, as headers.
>
> **Get the `type` string exactly right:** a read given a type outside the
> vocabulary (`search`, `list_by_domain`, `list_recent`, `timeline`,
> `pending`) is an **error** naming what it was given and listing the valid
> ones, so a typo never reads as an empty scope. The valid types are exactly
> `note`, `reasoning`, `anti_pattern`, `checkpoint`, `handoff`, `diagram`,
> `task`; `pending` serves `task`, `anti_pattern`, `handoff`, `note` and
> `diagram`.
>
> A diagram ranks like any other memory in `search` — nothing lifts a type to
> the top. When one does come back, open it first: it states a whole routine
> the surrounding notes only annotate.

---

## 4. DURING the work

- **`note(title, content, domain, also, tags, session, review_after, source_ref)`** —
  timeless knowledge (a rule, a long-lived finding, a decision) worth
  recovering in **any** future session. File it as deep as the fact is
  specific; it still comes back when someone asks about the module above it.
- **`reasoning(title, hypothesis, reasoning, result, revised_belief, next_time,
  domain, also, tags, session, review_after, source_ref)`** — an analysis worth
  keeping, field by field: what you believed, how you tested it, what came
  back, what you believe now, and what to do differently next time. The FACT
  it produced is a `note`; this is the process that produced it.
- **`anti_pattern(title, pattern, why_wrong, instead, domain, also, tags,
  session, review_after, source_ref)`** — an approach that **looks** right and is a
  trap (restarting the worker to clear a stuck queue instead of draining it).
  Counted by `pulse` and listed by `pending(type='anti_pattern')`.
- **`task(title, goal, items, domain, also, tags, session)`** — work for the next
  agent or session: a goal and a checklist
  ([§4.1](#41-tasks-a-goal-and-a-checklist)).
- **`diagram(...)`** — a routine as a **flow/graph**
  ([§4.2](#42-diagrams-a-flow-as-a-graph)).

**A writer answers back.** When something already in the store closely
resembles what was just written, the result carries **`similar`** plus one
line on what to do about it. The write is never blocked. Act on it while the
context that produced the text is still in hand: `edit_memory(uid, …)` or
`forget(uid, superseded_by=<new uid>)` if this one corrects an existing
memory, `forget()` the copy if it restates one, `link_memories()` and keep
both if they are genuinely different facts.

**Curation, one record at a time:**

- `edit_memory(uid, new_content, note, mode, source_ref, title)` corrects while keeping
  the previous version; `mode='append'` adds a line instead of replacing the body,
  for a memory that **gains** a fact rather than turning out wrong. It
  **refuses a diagram's or a task's body** — that content is generated from
  the graph or from the goal and the items.
  `source_ref` repoints the memory at its source and takes no `new_content`,
  so a missing or moved reference is a one-argument fix
  ([§2.4](#24-decay--review_after-and-source_ref)); a diagram accepts that one.
  `title` renames the memory, also on its own, and is **refused for a
  diagram** — its title generates part of its body, so the dashboard renames
  that one.
- `set_confidence(uid, unverified|confirmed|contradicted)`.
- `link_memories(from_uid, to_uid, relation_type, note)` creates a typed edge
  (`supersedes`, `relates_to`, `contradicts`, `links_to`); `get_relations(uid)`
  lists them.
- `also_domain` / `unfile_domain` cross-list and un-cross-list without moving
  the `domain` ([§2.2](#22-cross-listing--the-subject-that-cuts-across-the-tree)).
- `dedup_scan(domain, type, threshold, limit)` returns likely duplicate or
  contradictory **pairs** to review — it never merges, and it scans the path
  **plus its subtree**.
- `forget(uid, reason, superseded_by)` archives: reversible, content kept, out
  of default search and list output. On an open task it cancels the task.
- `purge_memory(uid, "DELETE <uid>")` deletes permanently, with the edit
  history and relations. Only when the user explicitly asks and **states the
  uid in their own message** — do not build that phrase from an inferred
  "yes".

## 4.1 Tasks (a goal and a checklist)

```
task(title, goal, items, domain, also, tags, session)
  items: one checklist item per line; blank lines are ignored
```

A task is a `goal` (what done looks like) and up to 50 items of up to 300
characters. Each item gets a key in the order it is added — `i1`, `i2`, … —
and a key is never reused: an item that stops applying is `dropped`, never
deleted. A tool takes `i3` or `3` for the third item. The task's content is
generated from the goal and the items (`[ ]` todo, `[~]` doing, `[x]` done,
`[-]` dropped), and every change to it is one entry in the edit history.

Work it with:

- **`task_item(uid, item, state, comment, related)`** — set an item's `state`
  (`todo`, `doing`, `done`, `dropped`), comment on it, and link the memories it
  produced or depends on (`related`: comma-separated uids; an unknown uid
  refuses the whole call). Give at least one of the three; they apply together
  or not at all. The result carries `progress` (`{done, dropped, total}`),
  `task_state` and `archived`.
- **`task_add(uid, items)`** — append items, one per line.
- **`task_comment(uid, body, item)`** — a comment on the task, or on one item
  when `item` names a key. A comment never edits the content, so it carries
  what a checklist cannot: why an item is blocked, what a review said.
- **`get_memory(uid)`** returns a `task` block beside the usual record: the
  goal, the state, every item with its linked memories, and the comments,
  oldest first.

**Lifecycle.** A task is `open` while any item is `todo` or `doing`. The
write that leaves every item `done` or `dropped` closes it itself: at least one
`done` makes it `completed`, all `dropped` makes it `cancelled`, and either
way it is archived — no `forget()` needed (`archived: true` in the result says
so). Adding an item to a closed task, or moving one of its items back to
`todo` or `doing`, reopens it. `forget()` on an open task cancels it.
A closed task stays in the store as an archived record: `get_memory(uid)`
opens it, the dashboard's archived filter lists it, and
`list_by_domain(domain, type='task', status='archived')` lists the completed
and cancelled ones, each with its `state` and `progress`. `search`,
`list_recent` and `pending` read active memories only.

Tasks are left out of duplicate detection and distillation, and a memory is
never retyped to or from `task`: two tasks with similar items are still two
pieces of work.

## 4.2 Diagrams (a flow as a graph)

```
diagram(title, nodes, edges, summary, domain, also, session, tags,
        kind="flowchart", review_after, source_ref)
  nodes: [{"key": "load", "label": "Read the export window",
           "shape": "step", "note": "..."}]
  edges: [{"from": "load", "to": "check", "label": "optional branch"}]
```

- `key` is the stable id the edges refer to: letters, digits, `_` or `-`.
- `shape` ∈ `start` | `step` | `decision` | `io` | `end`. **Exactly one
  `start`**, and every node reachable from it; **cycles are allowed** — a
  retry loop is a real flow. Validation is all-or-nothing: on error nothing is
  written and the result is `{"ok": false, "errors": [...]}`.
- Keep every `label` **objective** — what happens at that step. The reasoning
  and the caveats go in that node's **`note`**.
- Node positions are computed and stored **server-side**, so every reader sees
  the same picture and an arrangement made in the dashboard persists.

Edit it afterwards (never through `edit_memory`): **`diagram_node`** (add,
patch or remove one step — only the arguments passed are touched),
**`diagram_edge`** (wire, relabel or remove one arrow), **`diagram_link`**
(attach a memory to **one step**, visible from both ends),
**`diagram_jump`** (one step **continues in another flow**),
**`diagram_relayout`** (rebuild the positions — the fix for a diagram dragged
into a mess).

Read it back with **`get_diagram(uid, format=…)`**:

| `format` | For |
|---|---|
| `json` | **Reasoning** over the flow — the whole graph (positions, notes, links); the only round-trippable one |
| `text` | The prose projection kept as the memory's content — readable anywhere |
| `svg-interactive` | **Showing** it: writes the markup to a file; read `inline_path` and emit its contents inline (pan/zoom, self-contained fragment) |
| `svg` | The same drawing as a standalone document, to attach or open in a browser |
| `mermaid` | Only when the client can render nothing else — it **re-lays out and discards the stored positions**, showing a flow the user never arranged |

> `format` **defaults to `mermaid`**, so a bare `get_diagram(uid)` silently
> swaps the user's arrangement for a fresh layout. Pass a format on purpose.
> Attaching or linking a file is not showing a diagram — that hands the user
> something to open later.

> **Curation over volume.** Write what has reuse, with the right `domain` and
> `tags`.
>
> **A curation pass over the whole store** — validating or setting
> `confidence`, retiring checkpoints, distilling, moving a type, linking — is
> the sibling-skill family: [[memai-maintenance]] (the orchestrator) plus
> [[memai-checkpoints]] / [[memai-distill]] / [[memai-confidence]] /
> [[memai-link]]. They propose through `optimize_scan`/`optimize_stage` and
> the human applies in the dashboard. This section covers the **one-record**
> case.

---

## 5. Session END / PAUSE

Nothing writes a checkpoint but the call, so **a checkpoint exists only if it
is written while the session is still alive** — the stop hook reminds, it does
not write.

Open tasks are settled the same way: before pausing, `task_item` each task the
session worked on — `done` for what finished, `doing` for what is under way,
`dropped` for what does not apply, a `comment` for what the next session
needs to know. Work that stays unfinished and has no task yet is a `task`, not
a line in a checkpoint.

- **`checkpoint(title, intent, established, pursuing, open_questions, session,
  domain, also, tags)`** — where the work stands, so the next session picks up the
  right bearing via `pulse`. Fields are **free-length**, but a checkpoint is
  read **for bearing, not as an archive**: keep it a readable summary and do
  not dump detail into it.

If something large has to be remembered, do not force it into the checkpoint:

- **`note`** → the content is **timeless** (a rule, a long-lived finding, a
  decision). It holds in any future session, independent of this state of
  work.
- **`reasoning`** → it is a long **analysis trace** worth preserving
  (hypotheses tested, the path to the conclusion). Leave the summary and the
  pointer in the checkpoint; the body goes in `reasoning`.

> Rule of thumb: **checkpoint = where I stopped** (bearing). **note = what I
> learned that always holds** (timeless). **reasoning = the thinking that got
> there.**

> There is **no `snapshot`/`reindex`** — SQLite is durable at the moment of
> the write. Nothing has to be made durable afterwards.

---

## 6. Surfaces outside the tool calls

An MCP server cannot make an agent call it, and a store nobody reads is a
store with no memory. Three things close that gap, plus one for moving the
content around.

**`memai-hook`** — a console script that opens the SQLite store **directly**:
no MCP, so no server to wait for and no tool that has to have been loaded. It
reads the host's hook payload on stdin, writes one JSON object on stdout, and
never fails loudly (no store, an unreadable one, a payload that is not JSON —
all exit 0 with no output). **Four events:**

| Event | What it emits |
|---|---|
| `session-start` | the store's state as context — the memory count, active domains, the latest checkpoint and one line of what is pending (`Pending in …: 2 open tasks, 21 pitfalls, …`, left out when nothing is) — ending in the instruction to call `pending()` and `pulse(domain)` for the subject **before the session's first tool call**, and what the store's casing policy means for that path. When tasks are open the line tells the agent to work through them first |
| `pre-compact` | a reminder that whatever should outlive the transcript belongs in the store: `checkpoint()` where the work stands, `note()` what was established, `anti_pattern()` what turned out to be a trap |
| `stop` | a nudge to checkpoint, and **only when nothing was written recently** (`--quiet-minutes`, 45 by default) — a nudge that fires regardless of whether there is anything to record teaches the agent to skip it. When the domains the session named have **open tasks** it **blocks** the stop once per interval (30 minutes by default, per session) with the count, those domains, and the request to call `pending(type='task', domain=...)` for each of them, to check any similar domain from `list_domains()` for a task filed under another path, and to update the ones the session touched with `task_item` — or to say in one line that it touched none; a session that named no domain is not asked; the checkpoint nudge and the warden ask, when due, follow it in the same reason. The switch and the interval are the dashboard's Maintenance → Reminders tab, and `memai-hook stop --task-minutes N` overrides the stored interval |
| `guard` | fires for every memai tool: it records the `domain` a call names, or the domain of the memory a task tool or `get_memory` names by `uid`, in the session's state (the domains the stop ask covers), and refuses a memai write whose **required** text never arrived — a parameter tag opened without the `antml:` prefix is dropped before the call leaves the client, so the text it held is gone. It exits 2 with the cause on stderr, the one event that stops the call it reads rather than emitting context. The refusal spells each tool as a signature: those fields are positional, so the retry is the WHOLE call retyped in that order, not the one field the message named. A parameter the tool does not require cannot break the write, so that is a `systemMessage` and the call goes through |

Register all four with `memai-hook install`, which writes them into the user's
`~/.claude/settings.json` — the only scope memai maintains and reads back. The
first three are registered on the host events of the same name, `guard` on
`PreToolUse` with a matcher over every memai tool: the hook records the domains a
call names, and the guard checks only the writers.
`--settings <file>` writes the same block into a named file instead, and what it
registers there is nobody's to keep current but yours. `--check`
reports what is registered and exits non-zero if anything is missing;
`--print` shows what it would write without writing it. `--domain` narrows the
session brief and `--budget` caps the characters it emits. Hooks on the same
event that memai did not write are left alone, memai's own entries are
replaced rather than appended, and an existing settings file is copied to
`<name>.bak-<stamp>` first.

Two more subcommands read the same store without being one of those events.
**`memai-hook statusline`** writes the store as one plain line — how much is
stored, the busiest domain, how old the latest checkpoint is — for a host that
renders a status line; an empty store emits nothing. **`memai-hook install
--skills`** copies the skill directories memai ships into the `skills/`
directory beside the settings file, so this skill and its siblings install
with the tool; a file already holding the bundled bytes is left alone and any
other in the way is backed up first. Each run leaves a receipt beside them, so
a later `--check` can tell an untouched copy the bundle has moved past from one
somebody edited; only the former is reported as an update waiting.

**The `warm_up` prompt.** An MCP prompt is invoked by the **person**, which
makes it the one place in the protocol where the store is read without the
agent having decided to read it. Hosts that surface prompts show it as a
command; it returns the same brief the session-start hook emits.

**Server instructions.** Sent in the MCP handshake and injected by the hosts
that support it — a paragraph naming the read tools, the write ones, and
`pending()` with the open tasks it puts first. A host that runs no hooks learns
from it alone that tasks exist.

**`memai-store`** moves the content as text, which a binary copy of the store
cannot: `export --format jsonl` is one round-trippable record per line (every
column, cross-listings, relations, whole diagram graphs including hand-made
positions), `export --format md` groups documents by domain to read and grep,
and `import <file>` writes what is not already there and skips what is — so
running it twice changes nothing and the local row always wins. `--domain`
limits an export to a path and its subdomains; `--dry-run` reports what an
import would do.

---

## 7. Tool reference

The **group** column is the `MEMAI_TOOLS` group a tool belongs to: `core` is
always published, `diagrams` and `curation` only when named (or under the
`full` default). `help()` reports what this process actually loaded.

| Reading | | group |
|---|---|---|
| `pulse(domain)` | Warm-up: `{project, latest_checkpoint (+ relations), pending (the `categories` list), read_next, scope}` — the `scope` census includes `stale` | core |
| `pending(domain, type, limit, offset)` | Without `type`: `{"categories": [{"type", "count"}, ...]}`, one per category with something waiting. With `type`: one page of headers (`uid`, `title`, `domain`, `est_tokens`; a task adds `progress` and `doing`) and `next_offset`. An unknown `type` is an error | core |
| `search(query, domain, type, limit)` | BM25, annotated with `match_source`/`fts_rank` | core |
| `recall(query, domain, limit)` | Relevance-ranked recall of `note()`d knowledge (`search` scoped to `type='note'`) | core |
| `list_by_domain(domain, type, limit, subtree, status)` | Recency-ordered, scoped to a path and its subdomains; `status` is `active` (default), `archived` or `all`, and a task row carries `state` and `progress` | core |
| `list_recent(type, domain, limit, subtree)` | Recency-ordered, global unless a `type`/`domain` narrows it | core |
| | The four above return `{"results": [...], "est_tokens": N}` — index into `results` | |
| `timeline(uid, query, before, after, domain, type)` | The records created immediately before and after one anchor, oldest first: `{"anchored_by", "anchor", "before", "after"}` | core |
| `list_domains()` | The domain **tree**: `parent`/`depth`/`count`/`subtree`/`children`/`implicit` + `also`/`subtree_also` and latest activity — how to find the exact string | core |
| `get_memory(uid)` | Full record + edit history + relations (+ the diagrams whose steps point at it; a task's goal, items, linked memories and comments) | core |
| `get_relations(uid)` | A memory's relations, incoming and outgoing | core |
| `get_diagram(uid, format)` | Read a flow back: `json` · `text` · `svg-interactive` · `svg` · `mermaid` | core |
| `help(command)` | Every tool with a one-line summary, or one tool's signature + full docs, read live from the code; names what this process did not load | core |

| Writing | | group |
|---|---|---|
| `note(title, content, domain, also, tags, session, review_after, source_ref)` | Timeless knowledge → `type='note'` | core |
| `reasoning(title, hypothesis, reasoning, result, revised_belief, next_time, domain, also, tags, session, review_after, source_ref)` | A reasoning trace → `type='reasoning'` | core |
| `anti_pattern(title, pattern, why_wrong, instead, domain, also, tags, session, review_after, source_ref)` | A pitfall → `type='anti_pattern'` (counted by `pulse`, listed by `pending`) | core |
| `checkpoint(title, intent, established, pursuing, open_questions, session, domain, also, tags)` | Where the work stands → `type='checkpoint'` (summary, not an archive) | core |
| `task(title, goal, items, domain, also, tags, session)` | A goal and a checklist, one item per line → `type='task'` | core |
| `task_item(uid, item, state, comment, related)` | One item's state (`todo` \| `doing` \| `done` \| `dropped`), a comment on it, memories linked to it; the last close archives the task | core |
| `task_add(uid, items)` | Append items to a task, one per line; a closed task reopens | core |
| `task_comment(uid, body, item)` | A comment on a task, or on one item | core |
| `diagram(title, nodes, edges, summary, domain, also, session, tags, kind, review_after, source_ref)` | A routine as a flow/graph → `type='diagram'` | diagrams |
| `diagram_node` / `diagram_edge` / `diagram_link` / `diagram_jump` / `diagram_relayout` | One step / one arrow / a memory on a step / a jump into another flow / rebuild positions | diagrams |

| Editing and domains | | group |
|---|---|---|
| `edit_memory(uid, new_content, note, mode, source_ref, title)` | Correct (or `mode='append'` add to) a memory, keeping the previous version; **refuses a diagram's or a task's body**. `source_ref` repoints it at its source and `title` renames it, either alone or with the edit | core |
| `link_memories(from_uid, to_uid, relation_type, note)` | A typed edge between two memories | core |
| `set_confidence(uid, confidence)` | `unverified` \| `confirmed` \| `contradicted` | core |
| `also_domain(uid, domain)` / `unfile_domain(uid, domain)` | Cross-list / drop one cross-listing — **never** moves the `domain` | core |
| `get_domain_case()` / `set_domain_case(mode)` | The store's casing policy: `preserve` \| `lower` \| `upper` | curation |
| `forget(uid, reason, superseded_by)` | Archive: reversible, content kept | core |
| `purge_memory(uid, "DELETE <uid>")` | Permanent delete; only on an explicit request with the uid stated by the user | curation |

| Curation pass | | group |
|---|---|---|
| `dedup_scan(domain, type, threshold, limit)` | Likely duplicate/contradictory **pairs** to review; no merge | curation |
| `optimize_scan(domain, type, since, include_archived, limit, offset, full)` | Dump the corpus compactly to plan a pass: curation fields, relation edges, dedup and domain hints, recall counts, and per-memory `anchors` (URLs, paths, identifiers) to check against live facts. `due: true` is the store saying a `review_after` has passed | curation |
| | A low `recalls` count means **unproven, not useless** — a memory about a rare subject looks exactly like one nobody wants. Judge the store by the aggregate; never archive a row for being unread | |
| `optimize_stage(suggestions, note)` | Stage a batch for human review; **nothing is applied here** | curation |
| `optimize_runs()` / `optimize_status(run_id)` | What was staged, and what the human applied or rejected | curation |

**`optimize_stage` accepts 11 suggestion kinds:** `compact` and `reword`
(`{"new_content"}`), `retag` (`{"tags"}`), `redomain` (`{"domain"}`),
`crosslist` (`{"also": [...]}` — replaces the whole set), `set_confidence`
(`{"confidence"}`), `review` (`{"review_after"}` — a date or a span like
`'180d'`; `''` clears it), `archive` (`{"reason"}`), `link`
(`{"from_uid","to_uid","relation_type"}`), `merge`
(`{"keep_uid","drop_uid"}`), `distill`
(`{"source_uids","new_type","new_content"}`, where `new_type` is one of `note`,
`reasoning`, `anti_pattern`). `link`/`merge` derive
`target_uid` from the payload and `distill` creates its target, so those omit
it. The destructive kinds — `archive`, `set_confidence=contradicted`,
`distill` — are rejected without a non-empty `verified` describing the
live-facts check behind them. **Check every proposal against live facts before
staging it.**

The human applies or rejects each suggestion in the local admin dashboard
(`memai-admin`), which backs up before the first apply and can undo any of
them. The agent proposes, the human disposes.
