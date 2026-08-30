---
name: memai-link
description: >
  Stitch the MemAI relations GRAPH: typed edges between memories
  (supersedes / relates_to / contradicts / links_to), turning [[wikilinks]]
  written in a body into real edges, connecting complementary memories that
  share a domain, and preserving lineage (checkpoint to note, archived to
  successor). Uses dedup_scan to find candidate pairs and get_relations
  before every edge, so a rerun creates nothing twice. Use when asked to
  "link memories", to "connect" or "relate" two memories, to "materialize
  the wikilinks", to "stitch the relations graph", or when invoked as the
  linking step of the [[memai-maintenance]] pass. Conceptual base:
  [[memai-memory]]. Siblings in the same pass: [[memai-checkpoints]],
  [[memai-distill]], [[memai-confidence]].
---

# memai-link — stitching the memory graph

Creates the edges that make the store navigable. It **only relates**: when a
pair is the **same thing**, that is a merge and goes to [[memai-distill]].

## 1. Relation vocabulary (keep it consistent)

| `relation_type` | When it applies |
|---|---|
| `supersedes` | A replaces or retires B — a new version → the old one, a kept checkpoint → the archived one. Lineage. |
| `relates_to` | Complementary, the same subject, or the same phenomenon from another angle. The default. |
| `contradicts` | A and B claim incompatible things — pair it with the `contradicted` confidence of [[memai-confidence]]. |
| `links_to` | A generic pointer when none of the above fits. |

> `link_memories` takes `relation_type` as **free text** — nothing rejects a
> fifth spelling. Write one of those four.

## 2. What is worth linking

- **Materialize `[[wikilinks]]`.** A body that cites `[[slug]]` and has no
  edge in the graph gets a `relates_to`: a note on the queue drain whose text
  points at `[[cache-warmup-cold-start]]` reads as a link and is not one
  until the edge exists.
- **Complementary memories under one domain.** Two notes filed in
  `acme/x100`, one on the batch retry and one on the queue drain it
  re-enqueues into, that nobody ever connected.
- **checkpoint → note lineage.** A kept checkpoint → the canonical note for
  the mechanism (`relates_to`); an **archived** checkpoint from a finished
  `proj-1042` → the note that inherited its durable content (`relates_to`).
- **Succession.** A superseded memory → the one that stayed (`supersedes`).
  `distill` already links its new memory `supersedes` to every source it
  archives — do not stage those again.

> **An edge is not the only stitch.** When N memories are **steps of one
> end-to-end process** and none of them is the parent of the others, a mesh
> of `relates_to` is the wrong repair: cross-list the N into the process path
> (`omni/x900`), and a single read scoped there returns them all, subtree
> included. That is the `crosslist` kind and it belongs to the orchestrator —
> hand it to [[memai-maintenance]] instead of weaving the mesh.

### 2.1 Diagram stitches are not `link`

A `type=diagram` has stitches of its own, and they **do not go through
`optimize_stage`** — they apply on the spot, so **report them apart** from
the staged run.

| What | Tool | Not |
|---|---|---|
| A memory explains **one step** of the flow | `diagram_link(uid, node_key, target_uid)` | `link_memories`, which ties the **whole diagram** |
| A step **continues in another flow** | `diagram_jump(uid, node_key, peer_uid, peer_node?)` | `relates_to` between the two diagrams |

`link_memories` on the diagram as a whole is still right for "this diagram
and this note are about the same subject".

To find the `node_key`, read the graph with **`get_diagram(uid,
format='json')`** — the whole flow, with positions, node notes, step links
and jumps. `format` **defaults to `mermaid`**, and mermaid re-lays out the
flow and **discards the stored node positions**, so a bare
`get_diagram(uid)` is the one format that throws away the arrangement
somebody made. The two SVG formats do not answer this question either:
`svg-interactive` writes **two** files — `path`, a standalone document, and
`inline_path`, the fragment to emit inline — and the payload carries only a
thin index of the steps. `json` is the format to reason over.

`get_memory` on the target memory lists **`referenced_by_diagrams`**, the
flows that already point a step at it: read that before adding a step link
so a second pass adds nothing. A diagram's `kind` is `flowchart` and nothing
else — the writer rejects any other value.

## 3. Tools and hygiene

- **`dedup_scan(domain, type, threshold=0.6, limit=20)`** — candidate pairs,
  each carrying its `method` (`lexical`, overlap over near-identical text)
  and its `ratio`. It **never
  merges**; it is the starting point. A very high ratio over the same durable
  content is not a link, it is a **merge** for [[memai-distill]]. `domain`
  scans the path **and its subtree**, which is where near-duplicates collect
  — between a module and its own routines. Checkpoint pairs from the same
  domain and session are excluded, and the checkpoint pairs that remain rank
  below durable-type pairs.
- **`get_relations(uid)` before creating** — it lists **incoming and
  outgoing** edges, so an edge someone made in the other direction still
  shows. `link_memories` itself refuses an unknown uid, a memory linked to
  itself, and an edge that already exists with that same type, each as
  `{"ok": false, "errors": [...]}`.
- Stage it as the `optimize_stage` kind **`link`**, one of its 11 kinds, with
  payload `{from_uid, to_uid, relation_type, note}`. It takes **no
  `target_uid`**: the kind derives that end from `payload.from_uid`, and a
  `target_uid` that names anything else is rejected. The payload's `note` is
  stored **on the edge**; the suggestion's `rationale` is what the human
  reads in the dashboard.

## 4. Do not over-connect

Prefer **few high-value edges** — a complementary same-domain pair, lineage,
a materialized wikilink — to a fully connected mesh. If the relation is
already obvious from the tags and the domain, it does not need an edge.

---

## Running on its own

Runs **entirely alone**, with no orchestrator around it.

- **Standalone discovery:** (1) `dedup_scan(domain, type, threshold)` for
  candidate pairs; (2) sweep bodies for `[[wikilinks]]` (`search` /
  `list_recent`, then `get_relations` on the pair to see whether the edge is
  already there); (3) notes filed under the same domain with nothing between
  them.
- Warm up on its own, and call `optimize_stage(note="links — <summary>")`
  yourself.
- **Under [[memai-maintenance]]:** runs last, with content and types already
  settled, and only **returns** its suggestions.
- **Out of order is safe.** `get_relations` before each edge keeps a rerun
  idempotent. An edge into a memory that is archived afterwards survives —
  `forget` keeps the content and the relations, and the edge reads as
  lineage. Nothing here is applied: the human applies in the dashboard.
