"""Shared fixtures and body-shaping helpers for the test suite."""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from memai import sections, update
from memai.store import memories


@pytest.fixture(autouse=True)
def no_release_request(monkeypatch):
    """Nothing in the suite asks GitHub for the latest release.

    `memai.update._fetch` is the one call that leaves the machine, and the
    stop hook makes it. A test that wants an answer replaces this stub with
    its own.
    """
    monkeypatch.setattr(update, "_fetch", lambda timeout=update.TIMEOUT: {})


def shaped(type_: str, text: str) -> str:
    """A body of `type_` that reads back into its fields, carrying `text`.

    For a test that needs a memory of some type and does not care what it
    says. `text` goes under the first field; the rest carry the same filler
    everywhere, so it is present in every document, distinguishes none of
    them, and carries no lexical weight.
    """
    spec = sections.spec_for(type_)
    if not spec:
        return text
    return sections.render(
        type_, {s.key: text if i == 0 else "nothing to add" for i, s in enumerate(spec)})


def brief(goal: str = "drain the queue", **fields: str) -> str:
    """A task note body that reads as a brief: `goal` and any field given, filler in the rest."""
    values = {s.key: "nothing to add" for s in sections.BRIEF_SPEC if not s.optional}
    return sections.render_spec(sections.BRIEF_SPEC, {**values, "goal": goal, **fields})


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
    uid = memories.insert_memory(conn, type=type_, domain="acme",
                           content=shaped(type_, "a body from before the spec"))
    conn.execute("DELETE FROM memory_sections WHERE memory_uid = ?", (uid,))
    conn.execute("DELETE FROM section_migration WHERE memory_uid = ?", (uid,))


@functools.cache
def webui_constants() -> dict:
    """The dashboard's vocabularies as its modules export them, printed by
    tools/webui-constants.mjs under node. Skips the caller where node is absent."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(["node", str(root / "tools" / "webui-constants.mjs")], cwd=root,
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)
