# MemAI

A long-term memory MCP server for AI agents. Agents call its tools to write
memories — facts, decisions, checkpoints, pitfalls, documented flows — during a
session and read them back in later ones, which is the state an MCP server's
own process does not keep between conversations.

One SQLite file holds all of it: rows, keyword index, vectors, edit history,
relations, diagrams. A local admin dashboard serves the same store as the human
curation surface.

## Storage

A single SQLite file in WAL mode:

| table | holds |
|---|---|
| `memories` | the rows: type, domain, session, tags, content, status, confidence, timestamps |
| `memory_domains` | the extra domains a memory belongs to, one row per path — every domain filter reads it |
| `memories_fts` | FTS5 (BM25, `porter unicode61`) over content + tags + domain + cross-listed domains, synced by triggers |
| `memories_vec` | [sqlite-vec](https://github.com/asg017/sqlite-vec) `vec0`, one cosine embedding per memory over that same text |
| `edits` | full history; correcting a memory keeps the previous version |
| `relations` | typed edges between memories (`supersedes`, `relates_to`, `contradicts`, ...) |
| `diagrams`, `diagram_nodes`, `diagram_edges`, `diagram_node_links`, `diagram_jumps` | documented flows and what they point at |
| `optimization_runs`, `optimization_suggestions` | curation staged for human review |
| `meta` | store settings, plus the embedding model and dimension behind the vectors |

sqlite-vec hooks SQLite's transaction lifecycle, so a vector commits or rolls
back with the row it belongs to.

## Embeddings

A [model2vec](https://github.com/MinishLab/model2vec) static model
(`minishlab/potion-base-8M`) ships bundled in the package; `MEMAI_EMBED_MODEL`
selects another one by Hugging Face repo id or local path.

`meta` records the model name and dimension; if either changes, all vectors are
dropped and re-embedded in one transaction on the next connect, since vectors
from one model are meaningless in another's space. If the model cannot load,
writes proceed without vectors, retrieval degrades to keyword-only, and the
gaps are backfilled on a later connect.

## Retrieval

`search` is hybrid: FTS5 BM25 plus brute-force cosine KNN over the vectors (no
ANN index — a linear scan is plenty at this scale), merged by reciprocal rank
fusion. Each result says which side matched (`match_source`: `fts` | `vec` |
`both`) and carries the raw `fts_rank` / `vec_distance`. Multi-term queries are
OR'd on the keyword side, so several paraphrases in one call all help.

Both retrievers only *widen the candidate set*; whether a candidate answers the
query is the calling agent's judgement. `list_by_domain` / `list_recent` are
the fallback for a search that comes back thin.

`pulse` and the `list_*` tools sort by `created_at DESC`, never by similarity —
a similarity top-1 can surface an old memory over a current one, so the
"what is the latest state" tools stay recency-only.

`pulse` is the state of a scope, not its contents: each list stops at a handful
and its `scope` block is the rest — where it read (`paths`), what the scope
holds (`total`, `by_type`), what each list left behind (`not_shown`), and the
level below it (`subdomains`, with `own` and `subtree` counts). That is the
drill-down plan for `search(query, domain=…)` or `list_by_domain(domain,
type=…)`.

## Domains

A memory's `domain` is a path: `acme/x100/p200` is a routine inside a module
inside a product. Every read that takes a domain covers its subdomains, so
`pulse('acme/x100')` is the module-wide brief and `pulse('acme/x100/p200')` the
routine's; `subtree=False` narrows a list to one level. `list_domains()`
returns the tree, per level: what is filed on it, what its subtree holds, and
whether it exists only because something deeper is filed under it.

A filter also resolves a name that is only the *deep end* of a path, since a
caller usually has the routine's code and not the product above it:
`list_by_domain('p200')` finds the routine at `acme/x100/p200`. The literal
reading wins, an ambiguous name covers every branch holding it, and a response
with room for it reports `domain_scope`. A rename moves exactly the path it was
given.

The nesting lives in the string — no domains table, no id to resolve. Casing is
a store-wide policy (`preserve` | `lower` | `upper`); a non-conforming domain is
adjusted on write, not rejected.

**A memory can belong to more than one.** The path says where it *lives* — one
parent chain, the thing a re-home renames. `also` says which subjects it is
*part of*, and those cut across the tree: several routines can each be a step
of one end-to-end process without any being the parent of the others.
`note(..., domain='acme/x100/p200', also='omni/x900')` files on the routine and
makes every read scoped to `omni/x900` return it too, subtree included.

A cross-listing is a membership, not a move: `domain` is untouched, re-homing
`omni/x900` retargets the memberships pointing into it, and a path the memory's
own domain already sits under is dropped as redundant. A path can exist *only*
as a cross-listing — an end-to-end flow whose every step lives under some other
branch — and it is a scope like any other, counted apart from the filed rows
(`also`/`subtree_also` in `list_domains()`, `scope.also` in `pulse`) so the
tree never totals more than the store.

## Diagrams

`diagram()` documents a routine start to end as a graph, stored as
`type='diagram'`. A node has a stable `key`, an objective `label`, a shape
(`start` | `step` | `decision` | `io` | `end`) and an optional `note` carrying
the reasoning; an edge carries the condition on a branch. Exactly one `start`
is required and every node must be reachable from it; cycles are allowed.

Steps are addressable, which is what makes a diagram the index of its domain
rather than prose: `diagram_link()` attaches a note/anti_pattern/reasoning to
one step (visible from both ends), `diagram_jump()` continues a step into
another flow (stored once, reported on both), `diagram_node`/`diagram_edge`
patch one piece at a time.

Node positions are stored server-side, so every reader sees the same picture
and an arrangement made in the dashboard persists; `diagram_relayout()`
rebuilds them. `get_diagram(uid, format=…)` reads it back:

| format | what it gives |
|---|---|
| `svg-interactive` | the canvas drawing in a pan/zoom shell — the one to show |
| `svg` | the same drawing as a plain file, to attach or link |
| `mermaid` | portable, but re-lays out and discards the stored positions |
| `text` | the prose projection kept as the memory's content |
| `json` | the full graph with positions, notes and links; the only round-trippable one |

Both SVG formats write the markup to `$MEMAI_HOME/renders/` and return the path
plus a thin index of the steps. A render is a cache of a record that lives in
the database, so it is swept per the retention setting in the dashboard.

## Curation

`dedup_scan` surfaces likely-duplicate pairs by lexical overlap. The full pass
is two tools: `optimize_scan` dumps the corpus compactly — each memory's
curation fields, the relation edges, dedup hints, domain-variant clusters, flat
domains that already spell a hierarchy, and per-memory `anchors` (URLs, paths,
identifiers) to check against live facts — with paging and `since` for
incremental passes. `optimize_stage` writes a batch of suggestions to a run:
`compact`, `reword`, `retag`, `redomain`, `crosslist`, `set_confidence`,
`archive`, `link`, `merge`, `distill`.

Nothing is applied there: the human reviews each one in the dashboard, which
backs up before the first apply and can undo any of them. Destructive kinds
require a non-empty `verified` describing the live-facts check behind them.

`forget` is a soft delete — content kept, row out of default search/list output
(`status: archived`). `purge_memory` permanently deletes the row plus its edit
history and relations, gated on a `confirm_phrase` equal to `"DELETE <uid>"`,
a string that can only plausibly come from a human confirming that id.

## Tools

`help()` returns every tool with a one-line summary; `help(command='<name>')`
returns that tool's signature and docstring, read live from the code.

| Writing | |
|---|---|
| `note(content, domain, also, tags, session)` | Save a fact/decision/finding (`type='note'`) |
| `checkpoint(intent, established, pursuing, open_questions, session, domain, also)` | Save work state; fields are free-length |
| `anti_pattern(pattern, why_wrong, instead, domain, also, session)` | Save a pitfall to avoid repeating |
| `reasoning(content, domain, also, session)` | Save a reasoning trace |
| `handoff(content, domain, also, session)` | Leave a note for another agent/session |

| Reading | |
|---|---|
| `pulse(domain)` | Warm-up: latest checkpoint, open handoffs/anti-patterns, recent notes, flow titles, `scope` census |
| `search(query, domain, type, limit)` | Hybrid BM25 + vector search, source-annotated |
| `recall(query, domain, limit)` | Relevance-ranked recall of `note()`'d knowledge |
| `list_by_domain(domain, type, limit, subtree)` | Recency-ordered, scoped to a path and its subdomains |
| `list_recent(type, domain, limit, subtree)` | Recency-ordered, global |
| `list_domains()` | The domain tree: own/subtree/cross-listed counts and latest activity |
| `get_memory(uid)` | Full record: edit history, relations, referencing diagrams |
| `get_relations(uid)` | Relations for a memory |

| Diagrams | |
|---|---|
| `diagram(title, nodes, edges, summary, domain, also, session, tags, kind)` | Document a routine as a graph |
| `diagram_node(uid, key, label, shape, note, delete)` | Add, patch or remove one step |
| `diagram_edge(uid, from_key, to_key, label, delete)` | Wire two steps, relabel or remove the wire |
| `diagram_link(uid, node_key, target_uid, relation_type, delete)` | Attach a memory to one step |
| `diagram_jump(uid, node_key, peer_uid, peer_node, label, delete)` | Continue a step into another flow |
| `diagram_relayout(uid)` | Recompute the stored node positions |
| `get_diagram(uid, format)` | Read a diagram back (formats above) |

| Editing and domains | |
|---|---|
| `edit_memory(uid, new_content, note)` | Correct a memory, keeping the prior version |
| `link_memories(from_uid, to_uid, relation_type, note)` | Create a typed relation |
| `set_confidence(uid, confidence)` | `unverified` \| `confirmed` \| `contradicted` |
| `also_domain(uid, domain)` | Cross-list a memory into one more path |
| `unfile_domain(uid, domain)` | Drop one cross-listing; where it is filed is untouched |
| `get_domain_case()` / `set_domain_case(mode)` | Read/set the domain-casing policy |

| Curation and deletion | |
|---|---|
| `dedup_scan(domain, type, threshold, limit)` | Likely-duplicate pairs, for review |
| `optimize_scan(domain, type, since, include_archived, limit, offset, full)` | Dump the corpus compactly to plan a curation pass |
| `optimize_stage(suggestions, note)` | Stage a batch of suggestions for human review |
| `optimize_runs()` / `optimize_status(run_id)` | What was staged, and what the human applied or rejected |
| `forget(uid, reason, superseded_by)` | Soft delete (archive, reversible) |
| `purge_memory(uid, confirm_phrase)` | Hard delete, requires `"DELETE <uid>"` |
| `help(command)` | Tool docs read live from the code's docstrings |

Writer tool names match the `type` they store (`note()` → `type='note'`,
`reasoning()` → `type='reasoning'`, ...), so the verb an agent calls is exactly
the string it later filters on.

## Admin dashboard

`memai-admin` (or `python -m memai.admin`) serves a local web dashboard over
the same store at `http://127.0.0.1:8888` (loopback only;
`--host`/`--port`/`MEMAI_ADMIN_PORT` to change):

- **Overview** — counts, confidence meter, per-type distribution, 30-day
  activity, vector coverage, recent memories, active domains.
- **Memories** — hybrid search + filters (type/domain/status/confidence/
  session), bulk actions, and a record drawer: edit content with history and
  line-level diffs, edit metadata with re-embedding (cross-listings included),
  confidence triage, archive/restore, relations, guarded purge. A row that
  matches a domain filter only by cross-listing says so.
- **Graph** — force layout of the relations graph; drag, zoom, click to
  inspect, link mode to create relations.
- **Diagrams** — the documented flows, and a canvas editor for one: add and
  patch steps and edges, drag the arrangement, attach memories to a step, jump
  to another flow, relayout, export mermaid.
- **Domains** — the tree, one expandable row per level with filed, subtree and
  cross-listed counts. Move/rename/merge (re-homes the subdomains too; every
  affected row is re-embedded and audited), archive/delete, casing policy and
  normalization, spelling-drift detection between siblings (`acme/Cache` vs
  `acme/cache`).
- **Optimization** — the staged runs: each suggestion as the set before and
  after, applied or rejected one at a time or in a batch behind a safety
  backup, and undone individually.
- **Maintenance** — integrity/FTS/vector health checks, FTS rebuild, vector
  backfill/re-embed, orphan cleanup, VACUUM, timestamped backups
  (`VACUUM INTO`), render retention, a dedup review queue, the audit trail.

Destructive-action parity with the MCP tools is kept: archiving is the default
"delete", purging demands the literal `DELETE <uid>` typed into the UI.

It is meant to run on the machine that holds the store, so it has no login: it
binds to loopback and refuses cross-origin requests — any `Sec-Fetch-Site` or
`Origin` that is not this server, and any write that is not
`application/json`, so another page you have open cannot POST to your port.
`--host` on something other than loopback serves the whole store to anyone who
can reach it, and prints a warning saying so.

It runs on Starlette + uvicorn, which the `mcp` SDK pulls in anyway (declared
here too, since this package imports them and the SDK does not bound them). The
front end is plain ES modules, no build step: `webui/core/` for the router and
shared machinery, `webui/views/` one module per section,
`webui/diagram-engine.js` for the canvas. UI text lives in
`webui/i18n/<code>.json`, one file per language — English and pt-BR ship, and
only the fallback and the active locale are fetched. Roboto is bundled in
`webui/fonts/`, under the SIL Open Font License 1.1 (`webui/fonts/OFL.txt`,
separate from MemAI's MIT licence).

## Setup

```sh
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # or .venv/bin/pip on non-Windows
pytest
```

On Windows, `install.bat` does the venv + install steps and `run-admin.bat`
starts the dashboard (both activate `.venv` themselves; extra arguments pass
through, e.g. `run-admin.bat --port 8890`).

Register it as an MCP server (e.g. in a Claude Desktop / Claude Code MCP
config) pointing at the installed console script:

```json
{
  "mcpServers": {
    "memai": {
      "command": "memai-mcp"
    }
  }
}
```

### Starting the dashboard with it

The MCP server can bring the dashboard up with it, so a session begins with
both. Off unless asked — a memory server has no business opening a web port
uninvited:

```json
{
  "mcpServers": {
    "memai": {
      "command": "memai-mcp",
      "env": {
        "MEMAI_HOME": "/path/to/your/memai-store",
        "MEMAI_ADMIN_AUTOSTART": "1",
        "MEMAI_ADMIN_PORT": "8888"
      }
    }
  }
}
```

Every variable MemAI reads belongs in that block: a server the host launches
sees this environment and no other, not your shell's. `MEMAI_ADMIN_PORT` is
shown at its default, for when 8888 turns out to be taken; `MEMAI_HOME` is a
placeholder — drop the line to keep the store at `~/.memai`, and on Windows
mind that JSON wants its backslashes doubled.

A host starts several MCP servers per session, so "start it" has to mean "start
it once". Each one asks `/api/ping` whether a MemAI dashboard already answers —
first at the address a running one recorded in `$MEMAI_HOME/admin.json`, then
at its own configured port — and only then tries to bind. Whichever wins the
kernel's race keeps the port and the rest exit, with no lock file to survive a
killed session. A port that answers but is not MemAI stops the attempt, logged.

The dashboard is detached on purpose: it outlives the session that opened it.
`memai-admin --status` says where it is, `memai-admin --stop` stops it.
Autostart is loopback-only; `--host` on the command line still lets a person
override that, with the warning it prints.

## Data location

`$MEMAI_HOME/memai.db` if `MEMAI_HOME` is set, otherwise `~/.memai/memai.db`,
with `backups/` and `renders/` beside it. Not tracked in git — user data,
created on first run.
