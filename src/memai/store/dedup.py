"""Word-token similarity between memories, and the near-duplicate pairs it finds."""

from __future__ import annotations

import difflib
import re
import sqlite3

from memai.store.domains import domain_clause, domain_scope_clause
from memai.store.memories import GENERATED_TYPES, get_memory

# Similarity is measured over WORD tokens: difflib's quick_ratio is a character-multiset bound that
# reads high for any two texts in one language.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    """The lowercased word tokens a similarity measure runs over."""
    return _WORD_RE.findall(text.lower())


class _Prepared:
    """One memory's text, tokenized once for repeated comparison.

    A sweep is quadratic in the rows and compares each text against many
    others; tokenizing inside the loop would redo that work n times per
    row. `counts` is the token multiset the ratio's upper bound needs.
    """

    __slots__ = ("tokens", "counts")

    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.counts: dict[str, int] = {}
        for token in self.tokens:
            self.counts[token] = self.counts.get(token, 0) + 1


def _ratio_bound(a: _Prepared, b: _Prepared) -> float:
    """The highest ratio two prepared texts could have, 0..1.

    difflib matches each token at most once, so the size of the token
    MULTISET intersection caps the number of matches an alignment can
    find, whatever order the tokens appear in. Costs one dict lookup per
    distinct token against the ratio's own quadratic cost, and reads far
    lower than the same bound over characters: an alphabet is shared by
    every text in a language, a vocabulary is not.

    At least one of the two has to hold a token.
    """
    smaller, larger = (a.counts, b.counts) if len(a.counts) <= len(b.counts) else (b.counts, a.counts)
    matches = 0
    for token, count in smaller.items():
        other = larger.get(token)
        if other is not None:
            matches += count if count < other else other
    return 2.0 * matches / (len(a.tokens) + len(b.tokens))


def _pair_ratio(a: _Prepared, b: _Prepared, threshold: float) -> float:
    """How much of two prepared texts is the same word sequence, 0..1.

    Returns 0.0 for a pair ruled out by a bound instead of scored, so a
    caller compares the result against `threshold` and nothing else. No
    pair that would reach `threshold` is ruled out: both gates are upper
    bounds on the ratio -- token counts too far apart for any alignment
    to reach it, then the multiset bound of _ratio_bound.

    1.0 is the same text, and it is the same sequence being compared in
    any language, so one threshold holds across a mixed store.
    """
    la, lb = len(a.tokens), len(b.tokens)
    if not la or not lb:
        return 0.0
    if 2.0 * (la if la < lb else lb) < threshold * (la + lb):
        return 0.0
    if _ratio_bound(a, b) < threshold:
        return 0.0
    return difflib.SequenceMatcher(None, a.tokens, b.tokens).ratio()


def text_ratio(a: str, b: str) -> float:
    """The word-sequence ratio of two raw strings, with no gate in front."""
    return difflib.SequenceMatcher(None, _tokens(a), _tokens(b)).ratio()


def _timeline_pair(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    """Checkpoint x checkpoint inside the same effort is a timeline, not a dup.

    True for two checkpoints sharing a domain or a session, whatever they
    score: consecutive checkpoints of one effort narrate different
    moments through the same skeleton, and a later one extending an
    earlier one is a bearing being kept, not a memory to merge.
    """
    if a["type"] != "checkpoint" or b["type"] != "checkpoint":
        return False
    same_domain = bool(a["domain"]) and a["domain"] == b["domain"]
    same_session = bool(a["session"]) and a["session"] == b["session"]
    return same_domain or same_session


# How similar a just-written memory must be before the writer is told. Above dedup_candidates'
# 0.6: this interrupts an agent mid-write, so only a real collision speaks.
SIMILAR_ON_WRITE = 0.75
SIMILAR_ON_WRITE_MAX = 3
SIMILAR_SNIPPET = 160


def similar_memories(
    conn: sqlite3.Connection, uid: str, *,
    threshold: float = SIMILAR_ON_WRITE, limit: int = SIMILAR_ON_WRITE_MAX,
) -> list[dict]:
    """What the store already held that closely resembles this memory.

    For the moment of writing, which is the only moment the answer is
    free to act on: the agent still has the context that produced the
    text, so it can tell a correction from a duplicate from a second
    unrelated fact. dedup_scan asks the same question later, over the
    whole store, for a human to answer.

    Never blocks a write and never merges anything -- the memory is
    already stored when this runs. Diagrams and tasks are out on both sides:
    their content is a projection of rows, so a resemblance between two of
    them is not a merge anyone could apply. Consecutive checkpoints of one
    effort are out too (see _timeline_pair) -- they share a skeleton by
    design and would fire on every write.
    """
    row = get_memory(conn, uid)
    if row is None or row["type"] in GENERATED_TYPES:
        return []

    # Scanned within the memory's own scope, so a write does not slow down as unrelated branches grow.
    prepared = _Prepared(row["content"])
    scored: list[tuple[sqlite3.Row, float, str]] = []
    clause, params = domain_clause(row["domain"], alias="") if row["domain"] else ("", [])
    for other in conn.execute(
        f"SELECT * FROM memories WHERE uid <> ? {clause}", [uid, *params]
    ).fetchall():
        ratio = _pair_ratio(prepared, _Prepared(other["content"]), threshold)
        if ratio >= threshold:
            scored.append((other, ratio, "lexical"))

    out = []
    for other, score, method in sorted(scored, key=lambda s: -s[1]):
        if other["status"] != "active" or other["type"] in GENERATED_TYPES:
            continue
        if _timeline_pair(row, other):
            continue
        out.append({
            "uid": other["uid"], "type": other["type"], "domain": other["domain"],
            "ratio": round(score, 3), "method": method,
            "content": other["content"][:SIMILAR_SNIPPET],
        })
        if len(out) == limit:
            break
    return out


def dedup_candidates(
    conn: sqlite3.Connection, *, domain: str = "", type: str = "",
    threshold: float = 0.6, limit: int = 20, since: str = "",
    subtree: bool = True,
) -> list[tuple[sqlite3.Row, sqlite3.Row, float, str]]:
    """Surface likely-duplicate/contradictory pairs for the agent to review.

    Candidate pairs come from lexical overlap of the word sequence
    (method 'lexical'), which `threshold` applies to -- see _pair_ratio.
    It matches near-identical text, not paraphrases: two takes on one
    subject in different words do not surface here. Not a merge, just a
    candidate list -- the agent judges whether pairs are actually
    duplicates, the same split search uses.

    `since` makes the hints directional for incremental runs: at least
    one side of every pair is new (created/updated at/after `since`),
    but the OTHER side may be anywhere in the store -- a new memory
    colliding with an old one outside the scan window still surfaces.
    Old x old pairs are skipped; they belong to a full pass, not this
    run's delta.

    Checkpoint handling: checkpoint x checkpoint pairs within the same
    domain or session are dropped entirely (see _timeline_pair), and
    pairs involving checkpoints rank below note/reasoning pairs of equal
    score -- real merges live in durable types. The returned score is
    never altered, only the ordering.

    Diagrams and tasks never enter the candidate pool: their content is a
    generated projection of rows, so two similar flows are not a prose merge
    anybody could apply -- proposing one would only produce a suggestion
    that cannot be carried out.
    """
    sql = ["SELECT * FROM memories WHERE status = 'active' AND type NOT IN (?, ?)"]
    params: list = [*GENERATED_TYPES]
    if domain:
        clause, values, _ = domain_scope_clause(conn, domain, alias="", subtree=subtree)
        sql.append(clause)
        params.extend(values)
    if type:
        sql.append("AND type = ?")
        params.append(type)
    rows = conn.execute(" ".join(sql), params).fetchall()
    is_new = (lambda r: r["updated_at"] >= since) if since else (lambda r: True)

    # Tokenized once per row, not once per pair: the sweep is quadratic in
    # the rows and would otherwise redo this work n times for each.
    prepared = [_Prepared(r["content"]) for r in rows]

    pairs: list[tuple[sqlite3.Row, sqlite3.Row, float, str]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if not (is_new(a) or is_new(b)):
                continue
            ratio = _pair_ratio(prepared[i], prepared[j], threshold)
            if ratio >= threshold:
                pairs.append((a, b, ratio, "lexical"))

    pairs = [p for p in pairs if not _timeline_pair(p[0], p[1])]

    def rank(p) -> float:
        penalty = 0.05 * ((p[0]["type"] == "checkpoint") + (p[1]["type"] == "checkpoint"))
        return p[2] - penalty

    pairs.sort(key=rank, reverse=True)
    return pairs[:limit]
