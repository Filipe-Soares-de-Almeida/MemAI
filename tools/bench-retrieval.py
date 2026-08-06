"""Measure retrieval against a real store, using the store's own ground truth.

Retrieval changes are easy to argue about and hard to be right about. Two
in this repo shipped on textbook reasoning and cost recall: fusing deeper
than `limit`, and giving both arms an equal vote. Both looked obviously
correct and both were measured wrong only afterwards. This is the thing
that should have existed first.

The ground truth is not invented here. A store accumulates pairs that a
PERSON asserted were related, and they make a labelled set nobody had to
sit down and write:

  node-link   a diagram step's label, and the memory linked to explain it.
              A short human-written query with a known right answer, and
              the closest thing in the store to how anyone actually
              searches.
  relates_to  two memories somebody joined with an edge. Query built from
              one, target is the other.
  self        a slice of a memory's own text, used to find it again. The
              weakest of the three -- the query is a literal substring, so
              the keyword arm cannot lose -- kept as a NON-REGRESSION
              check, not as evidence about semantics. Reading it as
              evidence is the mistake that made the vector arm look
              worthless.

Run it against a copy of a store, not the live one: it opens the database,
which applies any pending schema migration.

    python tools/bench-retrieval.py --home /path/to/store-copy
    MEMAI_EMBED_MODEL=/path/to/other-model python tools/bench-retrieval.py ...

What to read. `recall@k` is the headline. `ceiling` is what ANY fusion of
these two arms could reach -- when the headline is near it, tuning the
fusion is finished and only a better retriever moves anything. `median
rank` says how far off the arm that missed was: rank 8 of 200 is a model
that nearly knows, rank 100 is a model that does not.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _words(text: str, count: int, skip: int = 0) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]{3,}", text or "")[skip:skip + count])


def node_link_cases(conn) -> list[tuple[str, str, str]]:
    return [
        (r["q"], r["target"], r["src"])
        for r in conn.execute("""
            SELECT n.label AS q, l.target_uid AS target, l.memory_uid AS src
            FROM diagram_node_links l
            JOIN diagram_nodes n
              ON n.memory_uid = l.memory_uid AND n.node_key = l.node_key
            JOIN memories b ON b.uid = l.target_uid AND b.status = 'active'""")
        if len(r["q"] or "") >= 12
    ]


def relation_cases(conn, kind: str = "relates_to") -> list[tuple[str, str, str]]:
    out = []
    for r in conn.execute("""
        SELECT a.content AS text, a.uid AS src, b.uid AS target
        FROM relations r
        JOIN memories a ON a.uid = r.from_uid AND a.status = 'active'
        JOIN memories b ON b.uid = r.to_uid AND b.status = 'active'
        WHERE r.relation_type = ?""", (kind,)):
        q = _words(r["text"], 14, skip=3)
        if len(q) >= 20:
            out.append((q, r["target"], r["src"]))
    return out


def self_cases(conn, sample: int, seed: int) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT uid, content FROM memories WHERE status='active' AND type != 'diagram'"
    ).fetchall()
    random.Random(seed).shuffle(rows)
    out = []
    for r in rows:
        q = _words(r["content"], 8, skip=4)
        if len(q) >= 20:
            # src == target here: the memory IS the answer, so nothing is excluded
            out.append((q, r["uid"], ""))
        if len(out) >= sample:
            break
    return out


def measure(db, conn, cases, k: int, total: int) -> dict:
    """recall@k per arm and fused, plus how far the misses were."""
    hit = {"fts": 0, "vec": 0, "hybrid": 0}
    unique = {"fts": 0, "vec": 0}
    reachable = 0
    ranks = {"fts": [], "vec": []}

    for q, target, src in cases:
        def rank_in(rows):
            uids = [r["uid"] for r in rows if r["uid"] != src]
            return (uids.index(target) + 1) if target in uids else None

        # deep, unbounded pulls: where the target really sits in each ordering
        deep_f = db.search_memories(conn, q, limit=total)
        deep_v = db.search_semantic(conn, q, limit=total, max_distance=2.0)
        fr, vr = rank_in(deep_f), rank_in(deep_v)
        if fr:
            ranks["fts"].append(fr)
        if vr:
            ranks["vec"].append(vr)

        f, v = (fr is not None and fr <= k), (vr is not None and vr <= k)
        hit["fts"] += f
        hit["vec"] += v
        unique["fts"] += f and not v
        unique["vec"] += v and not f
        reachable += f or v
        hyb = [r["uid"] for r in db.search_hybrid(conn, q, limit=k + 3) if r["uid"] != src]
        hit["hybrid"] += target in hyb[:k]

    n = len(cases) or 1
    pct = lambda x: 100.0 * x / n
    return {
        "n": len(cases),
        "fts": pct(hit["fts"]), "vec": pct(hit["vec"]), "hybrid": pct(hit["hybrid"]),
        "ceiling": pct(reachable),
        "only_fts": unique["fts"], "only_vec": unique["vec"],
        "median_rank": {arm: (st.median(rs) if rs else None) for arm, rs in ranks.items()},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Measure memai retrieval against a store's own labelled pairs.")
    ap.add_argument("--home", help="MEMAI_HOME to measure (use a COPY of a real store)")
    ap.add_argument("-k", type=int, default=5, help="recall@k (default 5)")
    ap.add_argument("--self-sample", type=int, default=120,
                    help="how many self-retrieval cases (default 120)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--vec-cut", type=float, default=None,
                    help="override db.VEC_MAX_DISTANCE. Cosine scale is a property of "
                         "the MODEL, not of retrieval: comparing two models on one cut "
                         "measures the cut. Calibrate per model before believing a "
                         "fused number.")
    args = ap.parse_args(argv)

    if args.home:
        os.environ["MEMAI_HOME"] = args.home
    from memai import db, embed

    if args.vec_cut is not None:
        db.VEC_MAX_DISTANCE = args.vec_cut

    with db.connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        suites = {
            "node-link": node_link_cases(conn),
            "relates_to": relation_cases(conn),
            "self": self_cases(conn, args.self_sample, args.seed),
        }
        print(f"store   : {os.environ.get('MEMAI_HOME', '~/.memai')}  "
              f"({total} active memories)")
        print(f"model   : {embed.model_name()}  dim={embed.embedding_dim()}")
        print(f"fusion  : vec weight {db.VEC_WEIGHT}, depth {db._FUSION_FETCH}x, "
              f"distance cut {db.VEC_MAX_DISTANCE}\n")

        head = (f"{'suite':12} {'n':>4} {'FTS':>7} {'VEC':>7} {'FUSED':>7} "
                f"{'ceiling':>8} {'+fts':>5} {'+vec':>5} {'med rank f/v':>14}")
        print(head)
        print("-" * len(head))
        for name, cases in suites.items():
            if not cases:
                print(f"{name:12} {'-':>4}  (no labelled pairs of this kind in the store)")
                continue
            m = measure(db, conn, cases, args.k, total)
            mr = m["median_rank"]
            rank = f"{mr['fts'] or '-'}/{mr['vec'] or '-'}"
            print(f"{name:12} {m['n']:4} {m['fts']:6.1f}% {m['vec']:6.1f}% "
                  f"{m['hybrid']:6.1f}% {m['ceiling']:7.1f}% "
                  f"{m['only_fts']:5} {m['only_vec']:5} {rank:>14}")

    print("\n+fts / +vec: targets one arm reached in the top-k and the other did not.")
    print("ceiling    : the best ANY fusion of these two arms could do.")
    print("'self' is a non-regression check, not evidence about semantics --")
    print("its queries are literal substrings, so the keyword arm cannot lose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
