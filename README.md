# MemAI

A long-term memory MCP server for AI agents. Agents call its tools to write
memories (facts, decisions, checkpoints, pitfalls) during a session and read
them back in future sessions — the thing that lets an agent "remember" across
process restarts, since an MCP server's own process does not persist state
between conversations on its own.

## Why this exists

Vector-database-backed memory stores usually couple two things that don't
like being coupled: an ANN vector index (e.g. HNSW) and a separate metadata
store, each with its own durability model. An agent host that manages MCP
servers as subprocesses typically kills them abruptly at session end, not
cleanly — and a kill that lands between the metadata write and the index
flush desyncs the two. The failure mode is silent: search still returns
results, just increasingly wrong ones (stale, or referencing entries that no
longer exist). This isn't hypothetical — e.g. Chroma 0.5.7–0.5.12 lost
not-yet-synced embeddings while keeping their documents
([chroma#2922](https://github.com/chroma-core/chroma/issues/2922)).

MemAI avoids the failure class differently: vectors live *inside* the same
transactional store as everything else. There is no second store with its
own durability model, so there is nothing to desync from.

## How it works

**Storage.** A single SQLite file, WAL mode, holding six things together in
one transactional unit:
- `memories` — the rows themselves (type, domain, session, tags, content,
  status, confidence, timestamps).
- `memories_fts` — an FTS5 (BM25, porter-stemmed) full-text index over
  content + tags + domain, kept in sync with `memories` via triggers on
  every insert/update/delete.
- `memories_vec` — a [sqlite-vec](https://github.com/asg017/sqlite-vec)
  `vec0` table holding one embedding per memory (over the same
  content + tags + domain text FTS indexes). sqlite-vec hooks SQLite's
  transaction lifecycle, so vector writes commit/roll back with the row
  they belong to.
- `edits` — full edit history; correcting a memory keeps the previous
  version instead of overwriting it.
- `relations` — a queryable graph of typed edges between memories
  (`supersedes`, `relates_to`, `contradicts`, ...).
- `meta` — which embedding model (and dimension) produced the stored
  vectors.

Because everything lives in one file under one set of ACID transactions,
there's nothing that can desync from anything else, including across a hard
kill — SQLite's WAL journal guarantees the file is either fully committed or
rolled back, never half-written. That includes the vectors: no ANN index
sitting beside the database waiting for a clean shutdown.

**Embeddings.** A local [model2vec](https://github.com/MinishLab/model2vec)
static model (`minishlab/potion-base-8M`, ~30MB, numpy-only CPU inference)
ships bundled inside the package — no Hugging Face download and no network
access required, which matters on corporate networks that block
huggingface.co. Set `MEMAI_EMBED_MODEL` to a Hugging Face repo id or a local
path to use a different model instead. Embedding versioning is handled
explicitly: the `meta` table records
the model name + dimension, and if either changes, all vectors are dropped
and re-embedded in one transaction on the next connect — vectors from one
model are meaningless in another model's space. If the model can't load
(e.g. first run offline), writes proceed without vectors and retrieval
degrades to keyword-only (relevant only if `MEMAI_EMBED_MODEL` points
somewhere unreachable); missing vectors are backfilled automatically on a
later connect.

**Retrieval.** `search` is hybrid: FTS5 BM25 across content/tags/domain,
plus brute-force KNN (cosine, no ANN index — nothing to desync, and at
memory-store scale linear scan is plenty) over the vectors, merged by
reciprocal rank fusion. Each result says which side matched
(`match_source`: `fts` | `vec` | `both`) and carries the raw scores
(`fts_rank`, `vec_distance`). Both retrievers only *widen the candidate
set* — semantic judgment (does this candidate actually answer the query)
is still left to the calling agent: it reads back the candidates and
decides relevance itself, the same way it would judge any other tool's
output. Multi-term queries are OR'd together on the keyword side, so
several paraphrases in one call still help. `list_by_domain` /
`list_recent` exist as a brute-force fallback for when a search comes back
thin.

**Domains nest.** A memory's `domain` is the subject it belongs to, written
as a path: `acme/x100/p200` is a routine inside a module inside a product.
One flat bucket per subject stops working as soon as subjects contain
subjects — the routine's notes and the module's notes are the same
material at two levels of detail. Every read that takes a domain covers its
subdomains, so `pulse('acme/x100')` is the module-wide brief and
`pulse('acme/x100/p200')` the routine's, and `list_domains()` returns the
tree (per level: what is filed on it, what its subtree holds, whether the
level exists only because something deeper is filed under it). Pass
`subtree=False` to `list_by_domain` / `list_recent` for one level alone.

A domain filter also reaches for a name that is only the *deep end* of a
path: what a caller has in hand is usually the routine's code, not the
product above it, so `list_by_domain('p200')` still finds the routine once
it lives at `acme/x100/p200`. The literal reading always wins (a `p200`
that exists as its own domain means that domain), an ambiguous name covers
every branch holding it rather than picking one, and where a response has
room to say so it reports `domain_scope` — the filter never claims a scope
it did not run. Re-homing is deliberately *not* resolved: a rename moves
exactly the path it was given.

The nesting lives in the string — no domains table, no id to resolve. A
store with no separator anywhere is a tree of depth 1 and behaves exactly as
it did, FTS tokenizes the levels into searchable words for free (a module
code finds the routines under it), and re-homing a domain in the dashboard
rewrites the subtree's paths in one audited pass.

**A warm-up says what it left out.** `pulse` is the state of a scope, not its
contents: each list stops at a handful, and on a parent domain the newest few
of one busy child can fill it alone — which used to hide both the parent's own
memories and the fact that anything was hidden. So the response carries a
`scope` block: where it read (`paths`), what the scope holds (`total`,
`by_type`), what each list left behind (`not_shown`), and the level below it
(`subdomains`, each with `own` and `subtree` counts). That is the drill-down
plan — `search(query, domain=…)` or `list_by_domain(domain, type=…)` on the
child that holds what the warm-up only counted.

**Recency vs. similarity.** `pulse` and the `list_*` tools always sort by
`created_at DESC` — never by similarity. Similarity ranking exists only
inside `search`, where it orders *candidates* for the agent to judge, not
answers. A similarity-ranked top-1 can surface an old memory that happens
to score well over a same-day one that's actually current, which is exactly
why the "what's the latest state" tools stay recency-only.

**Confirmation-gated deletion.** `forget` is a soft delete: content is kept,
the row is just excluded from default search/list output (`status:
archived`). `purge_memory` is a real, permanent delete of the row plus its
edit history and relations — gated on a `confirm_phrase` argument that must
exactly equal `"DELETE <uid>"`. The intent is that this string can only
plausibly come from a human explicitly confirming that exact id in their own
words, not from an agent inferring "the user probably wants this deleted."

## Tools

An agent can discover all of this at runtime: `help()` returns every
tool with a one-line summary, and `help(command='<name>')` returns that
tool's full signature and docstring, read live from the code.

| Tool | Purpose |
|---|---|
| `note(content, domain, tags, session)` | Save a fact/decision/finding (`type='note'`) |
| `checkpoint(intent, established, pursuing, open_questions, session, domain)` | Save work state; fields are free-length |
| `anti_pattern(pattern, why_wrong, instead, domain, session)` | Save a pitfall to avoid repeating |
| `reasoning(content, domain, session)` | Save a reasoning trace (`type='reasoning'`) |
| `handoff(content, domain, session)` | Leave a note for another agent/session |
| `search(query, domain, type, limit)` | Hybrid BM25 + vector search, source-annotated |
| `recall(query, domain, limit)` | Relevance-ranked recall of `note()`'d knowledge (search scoped to `type='note'`) |
| `list_by_domain(domain, type, limit, subtree)` | Recency-ordered list, scoped to a domain path and its subdomains |
| `list_recent(type, domain, limit, subtree)` | Recency-ordered list, global |
| `list_domains()` | The domain tree: every path with own/subtree counts + latest activity (warm-up discovery) |
| `pulse(domain)` | Session warm-up: latest checkpoint + open handoffs/anti-patterns + recent notes, plus a `scope` census of what it did not show |
| `get_memory(uid)` | Full record, including edit history and relations |
| `edit_memory(uid, new_content, note)` | Correct a memory, keeping the prior version |
| `link_memories(from_uid, to_uid, relation_type, note)` | Create a typed relation between two memories |
| `get_relations(uid)` | List relations for a memory |
| `set_confidence(uid, confidence)` | `unverified` \| `confirmed` \| `contradicted` |
| `dedup_scan(domain, type, threshold, limit)` | Surface likely-duplicate candidate pairs by lexical overlap, for the agent to review |
| `forget(uid, reason, superseded_by)` | Soft delete (archive, reversible) |
| `purge_memory(uid, confirm_phrase)` | Hard delete, requires an explicit user-stated `"DELETE <uid>"` |
| `help(command)` | Tool docs read live from the code's docstrings; no arg = one-line summary of everything |

Writer tool names match the `type` they store (`note()` → `type='note'`,
`reasoning()` → `type='reasoning'`, ...), so the verb an agent calls is
exactly the string it later filters on.

## Setup

```sh
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # or .venv/bin/pip on non-Windows
pytest
```

On Windows, `install.bat` does the venv + install steps and
`run-admin.bat` starts the admin dashboard (both activate `.venv`
themselves; extra arguments are passed through, e.g.
`run-admin.bat --port 8890`).

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

The MCP server can bring the admin dashboard up with it, so a session
begins with both. It is off unless asked, because the dashboard has no
authentication and opening a web port uninvited is not a memory server's
business:

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

Any variable MemAI reads belongs in that block, and these are spelled out
rather than left implicit so the knobs are where you would look for them. `MEMAI_ADMIN_PORT` is shown at its default, for when 8888 turns
out to be taken. `MEMAI_HOME` is a placeholder — drop the line to keep
the store at `~/.memai`, and on Windows mind that JSON wants its
backslashes doubled.

Setting them anywhere else will not do. A server the host launches sees
the environment in this block and no other — not your shell's, and not a
launcher's, which is why `run-admin.bat` now sets nothing and leans on
the same defaults.

A host starts several MCP servers per session, so "start it" has to mean
"start it once". Each one asks `/api/ping` whether a MemAI dashboard is
already answering — first at the address a running one recorded in
`$MEMAI_HOME/admin.json`, then at its own configured port — and only then
tries. Whichever wins the kernel's race for the port keeps it and the
rest exit; there is no lock file to be left behind by a session that was
killed rather than closed. If the port answers but is not MemAI, nothing
starts and the reason is logged.

The dashboard is detached on purpose: it outlives the session that opened
it, the same as one started by hand. `memai-admin --status` says where it
is, `memai-admin --stop` stops it. Autostart is loopback-only and refuses
anything else — `--host` on the command line still lets a person override
that, with the warning it prints.

## Admin dashboard

`memai-admin` (or `python -m memai.admin`) serves a local web dashboard
over the same store, at `http://127.0.0.1:8888` (loopback
only; `--host`/`--port`/`MEMAI_ADMIN_PORT` to change). It is the human
curation surface for everything the MCP tools do, plus the operations
that only make sense for a person:

- **Overview** — live counts, confidence meter, per-type distribution,
  30-day activity, vector coverage.
- **Memories** — hybrid search + filters (type/domain/status/confidence/
  session), a per-memory record drawer (edit content with history, edit
  metadata with re-embedding, confidence triage, archive/restore,
  relations, line-level diffs of past edits, guarded purge), and
  multi-select bulk actions.
- **Graph** — force-layout of the relations graph; drag, zoom, click to
  inspect, and a link mode to create relations between two nodes.
- **Domains** — the domain tree: one row per level, expandable, showing what
  is filed on it and what its subtree holds. Move/rename/merge (typing a
  path re-homes the domain and its subdomains; every affected row is
  re-embedded and audited), casing policy, and spelling-drift detection
  between siblings (`acme/Cache` vs `acme/cache`).
- **Maintenance** — integrity/FTS/vector health checks, FTS rebuild,
  vector backfill/re-embed, orphan cleanup, VACUUM, timestamped backups
  (`VACUUM INTO`), a dedup-candidate review queue, and the audit trail.

It runs on Starlette + uvicorn, which the `mcp` SDK pulls in anyway, so
nothing extra is downloaded to get a dashboard — but they are declared
here too, because this package imports them and the SDK does not bound
them. The front end is plain ES modules
with no build step (`webui/core/` for the router and shared machinery,
`webui/views/` for one module per section). Destructive-action parity with
the MCP tools is kept: archiving is the default "delete", and purging
demands the literal `DELETE <uid>` phrase typed into the UI.

**It has no authentication**, so it binds to loopback and refuses
cross-origin requests (any `Sec-Fetch-Site` or `Origin` that is not this
server, and any write that is not `application/json` — that content type
is what forces a browser preflight, which is what stops another page you
have open from POSTing to your own port). Those checks are not a login:
passing `--host` something other than loopback exposes the whole store to
anyone who can reach the port, and prints a warning saying so.

The UI asks for Roboto per the Material spec and ships it: the latin
subset of six faces lives in `webui/fonts/`, under the SIL Open Font
License 1.1 (`webui/fonts/OFL.txt`, separate from MemAI's own MIT
licence). Nothing about the dashboard needs the network.

That is not only a convenience. `src/memai/roboto_metrics.json` is a
width table extracted from those exact files, and it is what
`diagram_svg` wraps text against when it draws a diagram without a
browser — so the faces have to be the ones the canvas is using for the
two renderers to break lines in the same place. `tools/fetch-fonts.py`
refreshes them and `tools/gen-roboto-metrics.py` re-derives the table;
run the two together or not at all.

## Data location

`%MEMAI_HOME%/memai.db` if the `MEMAI_HOME` environment variable is set,
otherwise `~/.memai/memai.db`. Not tracked in git — it's user data, created
on first run.

The default embedding model ships inside the package (`src/memai/models/`)
so this is offline, CPU-only from the first run — no download, no
huggingface-hub cache. Only an explicit `MEMAI_EMBED_MODEL` override that
names a Hugging Face repo id touches the network (and then caches under
`~/.cache/huggingface`, same as before).
