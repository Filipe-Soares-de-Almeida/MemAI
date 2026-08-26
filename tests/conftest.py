"""Keep tests hermetic: never load the real model2vec model (network
download on first use). Default = embedder unavailable, so existing
tests exercise the FTS-only degradation path. Vector tests opt into a
deterministic fake embedder via the fake_embedder fixture.
"""

from __future__ import annotations

import math
import re
import struct

import pytest

from memai import db, embed, sections

# Controlled vocabulary -> vector index. No hashing, no collisions:
# each known word gets its own dimension, unknown words are ignored.
VOCAB = ["car", "maintenance", "schedule", "database", "tuning", "note", "alpha", "beta"]
SYNONYMS = {"automobile": "car", "vehicle": "car"}
FAKE_DIM = len(VOCAB)


def make_fake_embed(dim: int = FAKE_DIM):
    def fake_embed_texts(texts: list[str]) -> list[bytes]:
        out = []
        for t in texts:
            v = [0.0] * dim
            for w in re.findall(r"[a-z]+", t.lower()):
                w = SYNONYMS.get(w, w)
                if w in VOCAB and VOCAB.index(w) < dim:
                    v[VOCAB.index(w)] += 1.0
            n = math.sqrt(sum(x * x for x in v))
            if n == 0:
                v[dim - 1] = 1.0
                n = 1.0
            out.append(struct.pack(f"{dim}f", *[x / n for x in v]))
        return out

    return fake_embed_texts


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    monkeypatch.setattr(embed, "_model", None)
    monkeypatch.setattr(embed, "_dim", None)
    monkeypatch.setattr(embed, "_load_failed", True)


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(embed, "embedding_dim", lambda: FAKE_DIM)
    monkeypatch.setattr(embed, "embed_texts", make_fake_embed(FAKE_DIM))
    monkeypatch.setattr(embed, "model_name", lambda: f"fake-model-{FAKE_DIM}d")
    return FAKE_DIM


def shaped(type_: str, text: str) -> str:
    """A body of `type_` that reads back into its fields, carrying `text`.

    For a test that needs a memory of some type and does not care what it
    says. `text` goes under the first field; the rest carry the same filler
    everywhere, so it is present in every document and distinguishes none of
    them -- no lexical weight, and none of its words are in the fake
    embedder's vocabulary either.
    """
    spec = sections.spec_for(type_)
    if not spec:
        return text
    return sections.render(
        type_, {s.key: text if i == 0 else "nothing to add" for i, s in enumerate(spec)})


def unmigrated(conn) -> None:
    """Put the store in the state it is in before it has been read.

    Writes are queued rather than refused while this holds, which is how a
    body that predates the spec gets into a store at all.

    Readiness is derived from the rows, so this plants what an unread store
    has in it: one body of a sectioned type that nothing has read. A store
    holding none at all is vacuously read, which is right for a new one and
    useless for a test that needs the other state.
    """
    type_ = next(iter(sections.SECTION_SPEC))
    uid = db.insert_memory(conn, type=type_, domain="acme",
                           content=shaped(type_, "a body from before the spec"))
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))
