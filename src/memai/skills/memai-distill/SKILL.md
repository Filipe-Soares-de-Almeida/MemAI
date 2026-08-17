---
name: memai-distill
description: >
  Distill and MOVE knowledge between MemAI types: pull what is durable out
  of one checkpoint (or several) into a note/reasoning/anti_pattern, change
  a memory's type (checkpoint→note when the content is timeless), and merge
  near-duplicates. Use when the user asks to "distill memory", "turn a
  checkpoint into a note", "move a memory to another type", "merge duplicate
  memories", or as a step of the [[memai-maintenance]] pass. Knows that
  `distill` ARCHIVES its sources — and when not to use it. Conceptual base:
  [[memai-memory]] (the checkpoint × note × reasoning boundary).
---

# memai-distill — distill / move type / merge

Saves the **durable** knowledge that is stuck in the wrong type, or held in
a checkpoint that is about to be archived, without losing any of it.

> **The type boundary** ([[memai-memory]] §1): `note` = a **timeless**
> fact; `reasoning` = an **analysis trace**; `anti_pattern` = a trap;
> `checkpoint` = transient **bearing**. When a checkpoint (or a
> `reasoning`) carries a fact that **outlives the session**, that fact
> wants to be a `note`.

---

## 1. `distill` — extract the durable (ARCHIVES the sources)

`optimize_stage` kind **`distill`**, payload `{source_uids:[...],
new_type:"note|reasoning|anti_pattern", new_content, tags?, domain?}`:
**creates** the new memory, links `supersedes` from it to every source and
**archives every source**. The dashboard's undo purges the created memory
and restores each source, so the whole thing is reversible. Two uses:

- retiring the checkpoints of **finished** work while keeping what they
  taught;
- **n-ary merge**: folding N near-duplicates into one, with
  **synthesized** content.

`verified` is **required** — the kind archives its sources. Say what the
live-facts check was and where the durable content landed.

Payload details:

- **`new_type`** accepts only `note` / `reasoning` / `anti_pattern`.
  `checkpoint`/`handoff` are not targets, and neither is **`diagram`**: a
  flow is written by `diagram()` + `diagram_node`/`diagram_edge`, never
  from prose.
- **`domain`** is a **path** (`acme/x100/p200`). The new memory inherits
  nothing from its sources, so state where it is filed — omit it and the
  memory is filed nowhere. A cross-cutting axis does not go here: after
  the apply, `crosslist` (`{"also": ["omni/x900"]}`) or
  `also_domain(uid, 'omni/x900')`.
- **The payload accepts `source_uids`, `new_type`, `new_content`, `tags`
  and `domain`, and nothing else.** Any other key is refused at staging
  and returned in `errors`, so `also`, `review_after`, `source_ref` and
  `session` do not travel with a distill — they are the follow-up in §2.
- **A `diagram` cannot be a source.** Its content is the projection of its
  graph, and archiving it trades a navigable flow for prose, so staging
  refuses it. An obsolete diagram is an explicit `archive`; a **wrong**
  one is a fix to the graph. It never arrives as a candidate from
  `dedup_scan` either, which excludes diagrams from its pairs.

## 2. The new memory inherits nothing — `review_after` and `source_ref`

A `distill` mints a **new** memory: it starts `unverified` — whether that
claim is confirmed is [[memai-confidence]]'s call, not this skill's — with
no cross-listings, no recheck date and no source reference. The archived
sources' `review_after`/`source_ref` were about the sources, and nothing
carries them over. When the distilled fact describes code, config, a
schema or a URL, the new memory is where that date and that reference
belong ([[memai-memory]] §2.4) — and neither is settable in the distill
payload:

- **`review_after`** → an explicit `review` suggestion
  (`{"review_after": '180d'}`, one of `optimize_stage`'s **11** kinds), in
  a **later** run. Every kind validates its uids at staging time against
  memories that already exist, so nothing in the same batch can point at
  the memory a `distill` is going to create — a follow-up `review`,
  `crosslist` or `link` waits until the human has applied the distill.
- **`source_ref`** → `edit_memory(uid, source_ref=…)` once the human has
  applied the distill and the new uid exists. It takes no `new_content`,
  so setting the reference does not touch the distilled body.

When the recheck date and the reference matter more than archiving the
sources, write the memory with `note(..., review_after='90d',
source_ref=...)` instead of distilling (§3), and treat the sources
separately.

## 3. When NOT to use `distill` — create the note and KEEP the source

`distill` **archives** its sources. When a source has to **stay** — the
checkpoint on a still-paused piece of work is the bearing the next session
reads — do not distill. Instead:

1. **`note(content, domain, also, tags, session, review_after,
   source_ref)`** — writes the durable memory directly, applied on the spot
   rather than staged.
2. **`link_memories(from_uid=<the note>, to_uid=<the source>,
   relation_type='relates_to')`** — ties the new memory to where it came
   from.
3. If the source also holds an **out-of-date** fact, leave that to
   [[memai-checkpoints]] to fix by `reword`.

> A technique picked up while working `proj-1042`, which is still paused:
> the technique holds regardless of when it is read, so it becomes a
> `note` filed at `proj-1042` — and the checkpoint stays, because it is
> still the bearing for the unfinished work. `note()` + `link_memories`,
> no distill.

## 4. Moving a type (retype)

There is no in-place retype tool. Do it like this:

- **checkpoint→note / reasoning→note**: write the target with the timeless
  content (`note`) and then deal with the source — keep it and link (§3),
  or archive it once it is redundant. If the whole source existed only for
  that one fact → `distill`.
- **note→reasoning** (rare): the same, creating the `reasoning` and
  archiving the mis-typed `note`.
- **→diagram**: does not exist. A note that describes a routine step by
  step becomes a flow only through `diagram()` +
  `diagram_node`/`diagram_edge`, outside the run; the original note
  usually **stays** and is attached to the right step with `diagram_link`
  (see [[memai-link]]).

## 5. Merging duplicates (the pairs from `dedup_scan`)

A pair with a high `ratio` and **the same durable content** → **`merge`**
`{keep_uid, drop_uid}`, which links `supersedes` and archives the `drop`.
Keep the richer, better-verified side. When each side holds something the
other does not → n-ary `distill`, synthesizing a new memory from both.

- `verified` is demanded on **both** — each archives a memory. Say which
  side survives and what the live check was.
- Pairs that merely **relate** are not a merge → they are a link (hand
  them to [[memai-link]]).
- `dedup_scan` **never merges anything**: it returns candidate pairs with
  their `ratio` and `method` for you to decide, over the path **and its
  subtree**.

---

## Standalone (running alone, or from its own hook)

Runs **entirely on its own** — it does not need the orchestrator to hand
it candidates.

- **Standalone discovery**: (1) checkpoints of work that is **finished**,
  whose durable content is **not** yet in a `note` (`search`/`recall`) —
  what counts as finished comes from whatever the host project declares as
  its source of truth for the state of the work, read live rather than
  assumed (see [[memai-checkpoints]]); (2) `dedup_scan` at a high
  `threshold` → near-duplicates of a durable type; (3) a
  `reasoning`/`checkpoint` that actually holds a **timeless fact** (a
  candidate for `note`).
- **Its own warm-up**, then stage the batch yourself:
  `optimize_stage(note="distill/merge — <summary>")`. A `note()` written
  along the way **applies immediately** — report those apart from the
  staged suggestions.
- **Under [[memai-maintenance]]**: the candidates arrive already triaged
  from [[memai-checkpoints]], and this skill only **returns** its
  suggestions instead of staging them.
- **Out of order is safe**: nothing here applies anything (the dashboard
  does). Before proposing a `distill` or a `merge`, **re-read the target**
  with `get_memory` — another pass may have archived or reworded it — and
  do not re-propose what was already applied.
