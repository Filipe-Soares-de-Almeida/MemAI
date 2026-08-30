---
name: memai-maintenance
description: >
  Orchestrator of the MemAI MAINTENANCE pass (long-term agent memory). Runs
  [[memai-checkpoints]], [[memai-distill]], [[memai-confidence]] and
  [[memai-link]] as one curation pass, owns what none of them covers — the
  DOMAIN TREE (nested paths, cross-listing, casing), the DIAGRAMS and the
  store's DECAY (`review_after`) — and consolidates everything into ONE
  `optimize_stage` run the human applies or rejects in the dashboard. Use when
  asked to "optimize / maintain / curate / clean up / review my MemAI
  memories", "run the memory maintenance pass", "validate and set confidence",
  "set confidence in bulk", "archive old checkpoints", "link memories up",
  "fix the domain tree", or for any broad sweep over the store. Conceptual
  base: [[memai-memory]].
---

# memai-maintenance — the MemAI curation pass

Entry point of the **maintenance pass**. It does not do the fine-grained work
— it **coordinates** the four specialized skills, owns what none of them
covers (**domain tree**, **diagrams**, **decay**), and folds every result into
a **single** `optimize_stage` run.

> **Proposer → disposer.** This pass **applies nothing on its own**: it
> collects the sub-skills' suggestions and hands them to the dashboard through
> `optimize_stage`. The human applies or rejects each one there (a backup is
> taken before the first apply; every apply can be undone). Apply directly
> (`set_confidence`/`edit_memory`/`link_memories`/`forget`/`also_domain`) only
> when the user asks for it **without** the dashboard.
>
> **`help()` is the source of truth for the tools** — signatures read live from
> the code. When unsure of a name or an argument, call `help` before trusting
> this file.
>
> **Dashboard:** `http://127.0.0.1:8888` (default port). The MCP server can
> bring it up alongside itself when `MEMAI_ADMIN_AUTOSTART` is set; otherwise
> `run-admin.bat`.

---

## 0. Pre-flight (done ONCE; the sub-skills reuse it)

The orchestrator warms up **once** and keeps the result in context for every
sub-skill. Read each source once; nothing below is read twice.

1. **`pulse(domain)`** — the state a scope inherits, plus:
   - **`scope`** — what the scope HOLDS next to what came back: `subdomains`
     (`own` = filed exactly there, `subtree` = with descendants) and
     **`not_shown`** per list (a warm-up stops at a handful; this counts what
     it left behind). Read it as the **drill-down plan**:
     `list_by_domain(domain, type=…)` / `search(query, domain=…)` on the child
     that was only counted.
   - **`scope.paths`** — which path(s) the name given actually resolved to
     (§2).
   - **`scope.stale`** — how many memories in the scope carry a `review_after`
     date that has passed. Present only when non-zero, and the pass's own
     work list (§4, step 3).
   - **`diagrams`** — the documented flows **by title only**, never inlined.
     Open one with `get_diagram(uid, format='json')`.
2. **`list_domains()`** — the **tree**, not a list: per entry `parent`,
   `depth`, `count` (filed at exactly that path), `subtree` (that plus its
   descendants), `children`, `implicit` (a level that exists only because
   something deeper is filed below it), plus **`also`/`subtree_also`** for what
   is **cross-listed** there and not filed. `count: 0` with `also > 0` is a
   pure **cross-cutting subject**.
3. **`get_domain_case()`** — the store's casing policy: `preserve` | `lower` |
   `upper`, `preserve` by default. It governs **writes**; reads fold case
   either way (§2).
4. **`optimize_runs()`** — take the `created_at` of the **latest run**. The
   scan is **incremental**: `optimize_scan(since="<that created_at>")` reviews
   **only the delta** (a full scan on the first pass, or on request). The
   fields that drive the pass:
   - `stats` — over the whole filtered corpus, independent of `limit`,
     including `due_for_review` and `never_recalled`;
   - **`due: true`** per memory, with its `review_after` and `source_ref` — the
     store saying a writer dated this claim for a recheck and the date has
     passed. This is the decay work list; `recalls` is **not** (a low count
     means unproven, never useless — judge the store by the aggregate and never
     archive a row for being unread);
   - `dedup_hints` — pairs probed from the delta against the **whole** store;
   - `domain_hints` — spelling/separator/case variants of one domain,
     **cross-window**;
   - **`domain_nesting`** — flat domains that already spell a hierarchy
     (`acme-x100-p200-cache-warmup` → `acme/x100/p200/cache-warmup`): the raw
     material for `redomain` (§2);
   - **`also`** per memory — the cross-listings that **already hold**, so a new
     one is distinguishable from one already in place;
   - **`anchors`** per memory — the verifiable references found in the **full**
     body (URLs, file paths, table/field-style identifiers, SNAKE_CASE
     constants): the list [[memai-confidence]] checks against live facts;
   - `truncated: true` → page on with `offset = offset + count`. `full=True`
     returns whole bodies (expensive); `include_archived=True` only when the
     question is about what was already archived.
5. **The state of the work a checkpoint anchors.** [[memai-checkpoints]]
   decides keep/archive against whether that work is still **live** or already
   **closed**, and the store does not know which. That state comes from
   whatever the **host project declares as its source of truth** for it — read
   it once here, so no sub-skill goes looking again. **When the project
   declares none, the pass does not guess:** keep the most recent checkpoint
   per scope, **report** the rest for the user to rule on, and stage nothing
   destructive against them.

> Bodies come **truncated** in the scan and the listings (~120 chars, with
> `content_len` alongside) — open the full record with `get_memory(uid)` before
> deciding a reword or an archive.

---

## 1. The eleven suggestion kinds (exact payload)

| `kind` | `payload` | notes |
|---|---|---|
| `compact` / `reword` | `{new_content}` | two kinds, one payload. **Rejected on a `type=diagram`** (§3) |
| `retag` | `{tags}` | csv; replaces the field (`cache,warmup,cold-start`) |
| `redomain` | `{domain}` | where the memory is **FILED** — one path, one parent chain. Path shape is normalized **at staging**, so the panel shows the path the memory will land in; the **casing** is applied through the store policy at apply |
| `crosslist` | `{also: [path, …]}` | **replaces the whole set**; `[]` clears it |
| `set_confidence` | `{confidence}` | `unverified` \| `confirmed` \| `contradicted` |
| `review` | `{review_after}` | pushes the decay date out: a date (`'2026-11-01'`) or a span from today (`'180d'`), **normalized to a date at staging** so the panel shows the day that will hold; `''` clears it. Required key — there is no default |
| `archive` | `{reason}` | soft and reversible — never deletes |
| `link` | `{from_uid, to_uid, relation_type, note?}` | `target_uid` derives from `from_uid` — omit it |
| `merge` | `{keep_uid, drop_uid, note?}` | links `supersedes` and archives `drop_uid`; `target_uid` derives from `drop_uid` — omit it |
| `distill` | `{source_uids: […], new_type, new_content, tags?, domain?}` | **n-ary**; `new_type` ∈ `note` \| `reasoning` \| `anti_pattern`; creates the target, links `supersedes` from it to every source and archives them. **Omit `target_uid`** — there is nothing to point at yet |

- **`verified` is mandatory on the destructive kinds:** `archive`,
  `set_confidence=contradicted`, `merge`, `distill`. Staging refuses them
  without it — `merge` and `distill` because they archive a memory too.
  Describe the **live** check that justifies it (file:line, a schema column, a
  commit, the task's own state).
- An invalid suggestion is **skipped** and returned in `errors` — fix it and
  re-stage what was dropped. The rest of the batch still lands.
- **`purge_memory` never enters a pass.** It is irreversible, and it takes a
  confirm phrase the user has to have stated themselves.

---

## 2. The domain tree (the orchestrator's own work — no sub-skill covers it)

A domain is a **path** (`acme/x100/p200`), and the distinction between being
filed and belonging decides what to stage:

- **`redomain` = where the memory IS FILED.** One path. Every scoped read
  covers the **subdomains**, so filing deeper hides nothing from whoever asks
  about the parent.
- **`crosslist` = the subjects it also BELONGS to** — the axes that **cut
  across** the tree (several routines that are each a step of one end-to-end
  process, none of them the parent of the others, e.g. `omni/x900`).
  - it **replaces the whole set** → include the paths that must survive, and
    read the scan's `also` first;
  - a path the memory's own `domain` already covers is **dropped as
    redundant**; a non-empty set that survives as empty is **rejected** rather
    than applied as a silent clear;
  - **there is no hint for it.** Whether a subject cuts across is a judgement
    about what the memory SAYS, not the result of splitting a string. Outside a
    run, the one-off pair is `also_domain(uid, domain)` /
    `unfile_domain(uid, domain)`.
- **`domain_nesting` is a proposal, not a verdict.** Nothing in a split can
  tell a real level from a hyphen inside a name, so a human confirms each one.
  Check every proposal and stage only what holds up — `proj-1042` is **one**
  segment, not `proj/1042`.
- **`domain_hints`** (variants meaning the same thing) → a per-memory
  `redomain` when the drift is **spelling or separator** (`proj_1042` ×
  `proj-1042`). Under a `lower`/`upper` policy the case you propose is coerced
  at apply anyway, so a per-memory case fix means something only under
  `preserve`. The **store-wide** repair is the dashboard's **Domains** tab:
  `normalize` (with the **dry run** first) to bring stored domains in line with
  the policy, `rename` to move or merge a whole path with its subtree.
- **Casing folds on read.** A filter written in any case finds the stored path,
  down to a bare deep segment (`P200` reaches `acme/x100/p200`), and a resolved
  scope comes back spelled **as stored**. The residual hazard is the
  **`preserve`** policy, where `acme/Cache` and `acme/cache` are genuinely
  **two paths**: a filter then **broadens to both**, and a count read as one
  scope is really two. Reuse a path exactly as `list_domains()` spells it, and
  treat that pair as drift to merge.
- **Name resolution.** A filter that gives only the deep end (`p200`) resolves
  to the branches holding it — the **literal reading always wins**, and an
  **ambiguous name broadens** to every branch rather than picking one
  (`scope.paths` / `domain_scope` says which). Never assume the domain asked
  for is the one the query covered.

---

## 3. Diagrams (`type=diagram`)

A diagram's body is **not hand-written prose**: the graph (nodes, edges, notes,
links) is the source of truth and `content` is the **generated projection** of
it — which is what FTS indexes.

> **Never stage a `reword`/`compact` against a diagram.** `edit_memory` and the
> dashboard refuse it, and so do staging and apply — the suggestion comes back
> in `errors` instead of landing, so a batch that carries one is a batch with a
> hole in it. The body is regenerated from the graph on the next structural
> change, so it is not where the text lives: when a diagram reads wrong, the
> defect is in the **graph**.

What IS valid to stage against a diagram: `retag`, `redomain`, `crosslist`,
`link`, `set_confidence`, `review`, `archive`. The graph is edited outside the
run, with `diagram_node` / `diagram_edge` / `diagram_link` (attaches a memory
to **one step**) / `diagram_jump` (one step **continues in another flow**) /
`diagram_relayout` (rebuilds the positions — the fix for a diagram dragged into
a mess).

To **decide** anything about a diagram, read it with
**`get_diagram(uid, format='json')`** (the whole graph, the only
round-trippable format) or `'text'` (the prose projection). `format` defaults
to **`mermaid`**, which **discards the stored positions** and draws a layout
the user never arranged; `'svg-interactive'` only when the user wants to
**see** it.

Typical diagram maintenance: a step that names a memory and has no
`diagram_link`; a flow that ends where another begins and has no
`diagram_jump`; a title or domain that no longer matches the routine.

> **Database and index health** (integrity, FTS rebuild, orphaned relations,
> pruning SVG renders, vacuum, backup) is the dashboard's
> **Maintenance** tab — **not** this pass's job. If `health` flags something,
> **report it as pending** instead of reaching for a tool.

---

## 4. Order of the sub-skills

Each one **returns a list of suggestions** in `optimize_stage`'s shape. Under
the orchestrator **none of them stages its own batch** — everything goes into
the single call in §5. The order is fixed: what survives is decided first, and
the graph is stitched last, when content and type are stable.

1. **[[memai-checkpoints]]** — keep / archive / redomain, and corrects
   **out-of-date** facts inside a `checkpoint`, against the task state read in
   §0.5. First, because it decides **which memories survive**.
2. **[[memai-distill]]** — saves what is **durable** in the checkpoints about
   to die (distill → `note`/`reasoning`/`anti_pattern`), converts a type, and
   merges near-duplicates.
3. **[[memai-confidence]]** — **checks facts against live ones** (start from
   the scan's `anchors`) and sets `confidence` on what survived; rewords to fix
   drift or imprecision. It also owns **decay**: every memory the scan marked
   **`due`** gets its `source_ref` rechecked, then a **`review`** suggestion
   pushing the date out — or `set_confidence=contradicted` and a reword when
   the claim no longer holds. A `due` memory nobody rechecks stays due.
4. **[[memai-link]]** — stitches the **graph** (`dedup_scan`, which scans the
   path **and its subtree**; materializes the wikilinks written inside memory
   bodies; checkpoint→note lineage; same-domain clusters). Last, with content
   and type already stable.

Domains (§2) and diagrams (§3) stay **with the orchestrator**: after 1 (what
survives is settled) and before 4 (link and dedup depend on the final scope).

> The sub-skills **share** the pre-flight already in context — none of them
> redoes the warm-up.

**Verification is read-only checks against the live thing a claim points at** —
the file at that path, the URL, the schema holding that identifier, the
constant, the commit. Which tools reach those is the **host project's** to
declare: follow its own rules for database access and for delegating fan-out to
other agents. Whatever they are, **run the exploration in separate agents and
bring back the verdicts, not the files** — a fan-out read into the
orchestrator's own context leaves no room for the pass it is running.

---

## 5. Consolidate and report

- Send EVERY suggestion in one call:
  **`optimize_stage(suggestions=[...], note="RUN N — <summary>")`**. One run
  means one review in the dashboard.
- The run's `note` **is the record of the pass** (the next one reads it through
  `optimize_runs`): the window used, the sources of truth consulted, what was
  checked live, the findings, and what was left out **on purpose**.
- Present the **consolidated report** (format below) and tell the user to
  **apply or reject in the dashboard**.

### Final report format

```
## MemAI maintenance — RUN N (delta since <date of the previous run>)

Reviewed: <N> memories (<X notes / Y checkpoints / Z diagrams / …>).  Staged: <M> suggestions, <E> errors.

### Checkpoints         [memai-checkpoints]
- archive <uid> — <work closed / superseded by <uid>>
- reword  <uid> — out-of-date fact: <what>

### Distill / type      [memai-distill]
- distill <uids> → note "<slug>"  (sources archived)

### Confidence          [memai-confidence]
- confirmed <uid> — checked: <file:line / schema column>
- contradicted <uid> — <claim that is false today>

### Decay               [memai-confidence]
- review <uid> — was due <2026-11-01>, source_ref rechecked → pushed out <180d>
- (a due memory whose claim no longer holds goes under Confidence, not here)

### Domains             [orchestrator]
- redomain <uid> — acme-x100-p200-cache-warmup → acme/x100/p200/cache-warmup  (domain_nesting checked)
- crosslist <uid> — also: [omni/x900] — <the cross-cutting axis>
- (store-wide casing / renaming a whole domain → dashboard, Domains tab, dry run first)

### Diagrams            [orchestrator]
- diagram_link <uid>#<node> → <uid> — <the step names that memory>
- (graph edited outside the run; never a reword on a diagram)

### Links               [memai-link]
- relates_to <a>→<b> — <why>

### Pending for the user
- <checkpoint whose task state the project declares nowhere / a genuine archive doubt / a health warning>
```

Omit empty sections. A store already clean → `✓ Nothing to curate in the
delta.`

---

## 6. Guard-rails for the pass

- **Incremental by default** (`since`); a full scan only on the first pass or
  on request.
- MemAI is not the host's own memory files. A pass reaches memories through the
  MemAI tools and touches nothing else on disk ([[memai-memory]]).
- **Never** `reword`/`compact` a `type=diagram` (§3), and **never**
  `purge_memory` (§1).
- **Reuse the path `list_domains()` spells.** Reads fold case, so a filter in
  the wrong case still finds the rows; what a coined variant costs is a second
  path in the tree under a `preserve` policy (§2).
- **Ask when genuinely unsure.** Destructive kinds are the user's call. Where
  the project's source of truth settles the state of the work, there is no
  doubt — stage it (the dashboard is still the gate). Ask in the chat only when
  the state is really ambiguous.
- **Curation over volume** — propose what has reuse and what is a real
  correction, not churn.

---

## Selective invocation

| Command | Scope |
|---------|-------|
| `/memai-checkpoints` | Checkpoint lifecycle only (live/paused/closed → keep/archive/reword/redomain) |
| `/memai-distill` | Distilling and moving type only (checkpoint→note), plus merges |
| `/memai-confidence` | Live fact-checking, `set_confidence` and decay `review` only |
| `/memai-link` | Graph stitching only (links, dedup) |
| `/memai-maintenance` | The whole pass (the four above + domains + diagrams) |

> Standalone runs (`/memai-link` and friends): the sub-skill does its own
> minimal warm-up and calls `optimize_stage` itself. Under the orchestrator it
> only **returns** the suggestions.
