---
name: memai-confidence
description: >
  Verify a MemAI memory's claims against live facts and set its `confidence`
  (unverified | confirmed | contradicted). Checks the `anchors` a scan already
  extracted — file:line, symbol, table/column, domain, URL — against today's
  source, promotes or demotes each memory, stages a `reword` for a detail that
  drifted, and pushes `review_after` out with a `review` once a claim has
  actually been rechecked. Use when asked to "validate the memories", "set
  confidence", "check whether the facts still hold", "recheck what has gone
  stale", or when invoked as a step of the [[memai-maintenance]] pass. The
  store's own model is [[memai-memory]].
---

# memai-confidence — verifying facts, setting confidence

`confidence` is only worth setting when the claim was **checked live**. This
skill reads a memory's anchors against today's source and sets the field —
and it is where a memory the store handed back as `due` gets its recheck
([§5](#5-review_after--closing-the-decay-loop)).

The pass **proposes**: everything below is staged through `optimize_stage` and
applied by a human in the dashboard.

---

## 1. The three values

| Value | When |
|---|---|
| **`confirmed`** | A `note`/`reasoning`/`anti_pattern`/`diagram` whose **structural anchors** were checked **live**: the file and line exist, the symbol is there, the table/column exists, the domain resolves, the URL answers. |
| **`contradicted`** | The claim is **false today** — the fact no longer holds. Requires a non-empty `verified`; normally staged next to a `reword` or an `archive`. |
| **`unverified`** | Nothing to check it against: **ephemeral** or point-in-time (a count, a value, "open this month"), external, or a **`checkpoint` still bearing live work**. |

> **A checkpoint stays `unverified`.** Its content is where the work stood, not
> a verifiable fact, so there is nothing for a live check to agree with.
> A checkpoint for an effort that has **closed** is not a confidence question
> either — it is retirement, in [[memai-checkpoints]].

> **Do not let an ephemeral tail contaminate a durable core.** A note carrying a
> durable mechanism plus one dated example (the counts from one run, one queue's
> depth) is `confirmed` **on the mechanism**. The dated example neither blocks
> the confirmation nor earns a demotion.

---

## 2. How a check is made

- **Start from the `anchors` the scan already extracted.** Per memory,
  `optimize_scan` pulls the verifiable references out of the **full** body —
  URLs, file paths, table/field-style identifiers, `SNAKE_CASE` constants — and
  returns them space-joined. That is the work list, without re-reading every
  body. It is **capped at the top 5**: when the verdict needs more than those,
  open the record with `get_memory(uid)`.
- **A check is read-only, against the live source the claim points at.** Code
  and config: read the file, grep the symbol. Schema: describe the table. A
  URL: fetch it. **Which tool is allowed to read a database, and which agent a
  fan-out may be delegated to, are the host project's rules** — read them and
  use what they approve. This skill names no server and no model tier; it only
  requires that the check touches the real thing and changes nothing.
- **Fan the checks out into separate agents, one per cluster
  ([§4](#4-clustering-the-work)).** Verifying an anchor costs the reading it
  takes — files opened, tables described, dead ends — and that reading belongs
  in an agent's context, not in the pass's own. Each agent returns a verdict
  **per claim** plus the evidence for it; the pass keeps the verdicts.
- **Ask for a verdict, not a repair.** The agent reports what it found
  (`file:line`, the column, the resolved domain). Fixing the code is not this
  pass.
- **Hand over exact paths.** Resolve them (`Glob`) before dispatching, or every
  agent spends its first turns finding what the pass already knew.

---

## 3. What each verdict stages

| Verdict | Stage |
|---|---|
| **Confirmed** | `set_confidence` `{"confidence": "confirmed"}`, with `verified` = the evidence (`file:line`, the column, the response). |
| **Partial** — the mechanism holds, a detail drifted | `reword` fixing the detail, **then** `set_confidence` `{"confidence": "confirmed"}`; say in the rationale that the confirmation is against the reworded text. |
| **Contradicted** | `set_confidence` `{"confidence": "contradicted"}` plus `reword` or `archive`, both carrying `verified`. |

**Partial is the common verdict, and it is not a contradiction.** The claim's
mechanism checks out and one detail has moved: a note anchored at
`warmup.py:214` whose function now starts at `:260`; a note that names the flag
`USE_NEW_PARSER` where the code declares `F100_TOTAL`; a rule that holds on the
write path only. Reword the detail and confirm the mechanism — demoting the
whole memory throws away a fact that is still true.

**A corrupted body is a cleanup `reword`, whatever the verdict.** A body that
holds its own text twice around a stray literal markup tag is an artifact of
the write, not something an author typed: stage the single clean body even when
the fact in it checks out.

> `set_confidence(uid, confidence)` — the one-record MCP tool — takes **no**
> evidence argument. `verified` is a field on the **staged suggestion**, which
> is where the store enforces it: `set_confidence=contradicted`, `archive` and
> `distill` are rejected without one.

If the corrected fact deserves a memory of its own rather than a rewrite of
this one, that is [[memai-distill]]; recording the `contradicts` edge between
the old claim and the one that replaced it is [[memai-link]].

> **`type=diagram`: verify it, never reword it.** The body is the projection
> **generated** from the graph. `set_confidence` applies normally — the flow
> either matches today's code or it does not — and a wrong step is fixed with
> `diagram_node`/`diagram_edge`, outside the pass. A `reword` or `compact`
> staged against a diagram is **rejected at staging**, with the same refusal
> `edit_memory` gives, and refused again on apply. Read the flow with
> `get_diagram(uid, format='json')`: the default format re-lays out the graph
> and discards the arrangement somebody made.

---

## 4. Clustering the work

Group memories by subject so one agent covers neighbours in a single reading:
the cache-warmup notes filed under `acme/x100/p200` (they all point at the same
warmup path), the queue-drain notes and the retry pitfall beside them
(`queue,drain,retry`), the report-export memories cross-listed into
`omni/x900`, the memories filed under `proj-1042`, the index-rebuild and
batch-retry notes wherever they live.

Fewer agents, each denser: an agent that has already opened a file answers the
second claim about that file for free.

---

## 5. `review_after` — closing the decay loop

`review_after` is the writer's own date for when a claim stops being safe to
trust unchecked; `source_ref` says what to check it against. The store can flag
the decay but not resolve it: `pulse` counts the overdue rows in a scope as
`scope.stale`, and `optimize_scan` marks each one **`due: true`** and lists its
`source_ref`. **This is the pass that closes the loop.**

- **A `due` memory is a recheck, and it goes first.** Its `source_ref` is the
  anchor its own writer chose, so the target is already named.
- **After the recheck, move the date** — `review` with
  `{"review_after": "180d"}` (or a date, or `''` to clear it for a claim that
  no longer tracks anything that moves). A memory confirmed and left on a
  passed date comes back `due` on every later scan and reads as never checked.
- **A confirmation is not a reason to invent a date.** Confirming says the
  claim holds today, not that it needs looking at again. **Most memories should
  leave `review_after` empty** — a date nobody meant is worse than none. Set
  one only where the recheck showed the claim is pinned to something that
  moves: a file that gets edited, a schema, an external URL.

The `review` suggestion, exactly:

```json
{"kind": "review", "target_uid": "<uid>",
 "payload": {"review_after": "180d"},
 "rationale": "rechecked against the live path; pushed out",
 "verified": "warmup.py:260 — the warmup still runs before the first read"}
```

`payload.review_after` is required (`''` clears it) and is **normalized to a
date when it is staged**, so a span resolves against the day the pass ran, not
the day the human applies it. `review` is not a destructive kind, so `verified`
is not enforced on it — record the check anyway: that field is what lets a
later pass tell a real recheck from a date that was simply pushed out.

---

## Independence (standalone, or under the maintenance pass)

Runs **fully on its own** — its natural target is self-discovered.

- **Standalone discovery**: `optimize_scan` for the memories marked `due`, plus
  `list_recent`/`search` filtered to `unverified` (and any `confirmed` old
  enough to be worth a re-check). Fan out, then stage the batch yourself with
  `optimize_stage(note="confidence — <summary>")`.
- **Under [[memai-maintenance]]**: verify the memories that **survived** the
  earlier steps and **return** the `set_confidence`/`reword`/`review`
  suggestions to the orchestrator instead of staging them.
- **Out of order is safe.** It acts on `unverified` and on `due`, so it is
  idempotent over what is already `confirmed` and current. **Skip archived
  rows** — they are absent from the default `list`/`search` output anyway. And
  it only ever proposes: the dashboard applies.

> `optimize_stage` accepts **11** kinds. This skill stages `set_confidence`,
> `reword` and `review`, plus `archive` when a contradicted claim has nothing
> left worth keeping. The full list is in [[memai-memory]], and
> `help(command='optimize_stage')` is read live from the code.
