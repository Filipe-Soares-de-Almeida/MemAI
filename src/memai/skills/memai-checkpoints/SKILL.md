---
name: memai-checkpoints
description: >
  The lifecycle of MemAI CHECKPOINTS: deciding which bearing is still current
  and which one is superseded, archiving the checkpoint of work that is closed
  or an intra-session trail that a newer one already covers, keeping the single
  bearing a live effort still needs, correcting a fact in a kept checkpoint
  that has gone out of date, pushing out its recheck date, and re-filing its
  domain path. Use when the user asks to TRIAGE or CLEAN UP checkpoints,
  "archive the old checkpoints", "retire this checkpoint", "what is still
  active", "which of these is the current one", and as the checkpoint step of
  the [[memai-maintenance]] pass. Conceptual base: [[memai-memory]] — a
  checkpoint is a bearing, not an archive.
---

# memai-checkpoints — the lifecycle of a checkpoint

A `checkpoint` is a **bearing summary** — where the work stands — transient and
replaced as the work moves on ([[memai-memory]] §5). The durable knowledge
lives in `note`/`reasoning`/`anti_pattern`. This skill keeps **only the bearing
still needed** and retires the rest **without losing knowledge**: if the
durable part is not in a `note` yet, it goes there first ([[memai-distill]]).

> **This skill does not curate `note`.** Only `type=checkpoint` records enter
> here.

> **It proposes; it does not apply.** Everything below is staged through
> `optimize_stage` and applied or rejected by the human in the dashboard. The
> destructive kinds it can stage — `archive` and `distill` — are **rejected
> without a non-empty `verified`** describing the live-facts check behind them.

---

## 1. The state of the work is declared by the project

Every decision below turns on whether the work a checkpoint is the bearing for
is **live**, **paused** or **closed**. That state is not in the store: MemAI
records where the work stood, not whether it is still open.

So read it from whatever the host project **declares** as its source of truth
for the state of work — a section of its own `CLAUDE.md`, an issue tracker, the
branch and the git log, a status file the project keeps. This skill reads that
source; it never infers the state from a checkpoint's own wording, from its
age, or from a domain having gone quiet. Establish what the source is before
the first decision, and take what it says as the state.

The checkpoint's `domain` is what joins it to that work. A ticket or issue is
**one segment** — `proj-1042`, where the hyphen is not a level
([[memai-memory]] §2.1) — so it can be the whole path or sit inside one
(`proj-1042/p200`), and a filter on `proj-1042` covers the subtree either way.

| State of the work | Decision on its checkpoints |
|---|---|
| **Closed** | **archive** — but only after confirming with `search`/`recall` that the durable part is already in a `note`. **If it is not, do not stage the archive**: running standalone, apply [[memai-distill]]'s logic (`distill`, or `note()` directly) to save the lesson FIRST, or hold the archive and **report** the checkpoint as "durable pending". Never archive knowledge away because a distill pass has not run. |
| **Live / paused** | **Keep the most recent** (`created_at DESC`) as the bearing. **Archive** the intra-session trail behind it, each with a `supersedes` link → the one kept ([§4](#4-recognising-a-superseded-trail)). |
| **Not declared** — the project names no source of truth, or its source does not mention this work | Keep the most recent and **report the rest as a question for the human**: is this still open, or delivered and waiting to be closed? **Stage nothing destructive.** Do not invent a state. |

> **Never** archive the **only** or the **most recent** checkpoint of live or
> paused work — that is the bearing `pulse` hands the next cold session.

---

## 2. What this skill proposes

- **archive** (`{reason}`) — work that is closed with its durable part
  preserved, or a superseded trail. `verified` is the check itself: which
  declared source says the work is closed, or which newer uid covers this one.
- **reword** (`{new_content}`) — a fact in a **kept** checkpoint that has gone
  **out of date**: the bearing for the report export says the change sits in the
  working tree uncommitted, and the git log now shows it merged → rewrite it to
  what holds; `edit_history` keeps the original. Use it also to name the durable
  note distilled out of the checkpoint, by that note's `uid`, alongside a
  **link** suggestion (`relates_to`) — prose alone is not an edge.
- **review** (`{review_after}`) — the recheck date on a **kept** bearing, as a
  date (`'2026-11-01'`) or a span from today (`'90d'`, `'180d'`); `''` clears
  it. `checkpoint()` itself takes no `review_after` and the `review` kind is not
  restricted by type, so a staged `review` is how a date reaches a checkpoint. A
  passed date shows as `due: true` in `optimize_scan` and counts in `pulse`'s
  `scope.stale` — push it out **after** actually rechecking, never to silence
  the count.
- **redomain** (`{domain}`) — re-file a **kept** checkpoint: a wrong or empty
  domain, or nesting a flat path that `optimize_scan`'s `domain_nesting` hint
  proposed. Read every proposal: a hyphen inside a name is not a level. Case is
  not worth a redomain under `lower`/`upper`, where the policy coerces on apply
  and the change lands as a no-op ([§3](#3-casing-and-the-preserve-store)).
  Kept checkpoints only — an archived one does not need a home.
- **link** `supersedes` — from the archived checkpoint → the one kept as the
  bearing, so the lineage survives the archive. That one edge is this skill's;
  relation curation across the store is [[memai-link]]'s.

> **`crosslist` is not this skill's business.** A checkpoint is the bearing of
> ONE effort; a cross-cutting axis (`omni/x900`) is a judgement about durable
> knowledge and stays with the orchestrator ([[memai-maintenance]]).

> **Confidence is not either.** [[memai-confidence]] decides it, and a
> checkpoint of live work stays `unverified` — a bearing is not a verifiable
> fact. Here the work is archiving and correcting, not setting confidence.

---

## 3. Casing and the `preserve` store

**Reads fold case** (`db._fold`, `db.resolve_domain_scopes`): a filter written
in any case finds the path, and a resolved scope comes back spelled **as
stored**. **Writes are coerced** to the store's policy and the writer echoes
`domain_adjusted` ([[memai-memory]] §2.3). So a checkpoint filed at
`acme/cache` is found by a filter on `acme/Cache`, and a wrong-case query is
not the reason a triage came back empty.

What is left is drift under **`preserve`**, where each write keeps its own
spelling: `acme/Cache` and `acme/cache` are then **two paths**, and a filter
**broadens to both** — the same widening an ambiguous segment gets. One
effort's checkpoints arrive split across the two spellings, so the trail
decision is made over the union, and merging them is a real `redomain`.
`get_domain_case()` reports the policy; `list_domains()` spells every path as
stored.

---

## 4. Recognising a superseded trail

Several checkpoints on the same domain in one session (`20260810T1423-1f4c`),
narrating the same progression: the **last** by `created_at DESC` usually holds
everything the earlier ones held and more. Confirm by reading the bodies with
`get_memory` — where the newest **covers** the older, the older is trail →
archive + `supersedes`. Exception: an earlier checkpoint that records a
concrete confirmed instance and is already linked to a note can be worth
keeping. Judge by reuse, not by date alone.

> `dedup_scan` **drops checkpoint × checkpoint pairs of the same domain or
> session** deliberately — consecutive checkpoints of one effort share a
> skeleton and score high while narrating different moments, so they are a
> timeline, not a duplicate (`db._timeline_pair`). The trail decision is
> therefore **the reader's**, from the bodies, and never the scan's. It scans
> the path **and its subtree** (`acme/x100` brings in `acme/x100/p200`), so
> nested work has its children's checkpoints in the same scan.

---

## 5. Running standalone

This skill runs on its own, which is what a dedicated hook needs (a throttled
reminder on `stop` — [[memai-memory]] §6).

- **Its own warm-up**: `list_recent(type='checkpoint')`, scoped with `domain`
  when the pass is focused on one subject, plus the project's declared source
  for the state of the work ([§1](#1-the-state-of-the-work-is-declared-by-the-project)).
  Then stage the batch: `optimize_stage(note='checkpoints — <summary>')`.
- **Under [[memai-maintenance]]**: reuse the `optimize_scan` and the state
  already in context, and just **return** the suggestions.
- **Out of order is safe**: nothing is applied here, and the declared source is
  re-read on every pass, so the state is current rather than remembered.
- **Idempotent**: an archived checkpoint is out of the default
  `list_recent`/`list_by_domain` (`status='active'`), so it is not proposed a
  second time.
- **Self-sufficient on the durable guard**: when the durable part of a closed
  effort is not in a `note`, resolve it **here** (distill, or `note()`) or
  **hold and report** — never assume another skill ran first
  ([§1](#1-the-state-of-the-work-is-declared-by-the-project)).
