"""The few pieces of the store's layout that the hook's guard path needs.

`memai.db` is the store itself and costs a visible fraction of a second to
import; the guard runs in front of every memai tool call and has to start
without it. What sits here is what both can share: where the home directory
is, the clock's format, how a domain path is cut and normalised, and the
intervals a session's state is judged against. `memai.db` re-exports all of
it, so callers keep one spelling.

Nothing here imports another memai module.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

DOMAIN_SEP = "/"

# Minutes before the warden, and the ask about open tasks, are owed again.
WARDEN_MINUTES_DEFAULT = 20
TASK_ASK_MINUTES_DEFAULT = 30


def home() -> Path:
    """`MEMAI_HOME`, or `~/.memai`, created if needed."""
    path = Path(os.environ.get("MEMAI_HOME", Path.home() / ".memai"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def split_domain(domain: str) -> list[str]:
    """A domain path's segments, outermost first. Blank segments drop out."""
    return [s for s in (p.strip() for p in (domain or "").split(DOMAIN_SEP)) if s]


def normalize_domain(domain: str) -> str:
    """Canonical form of a domain path: trimmed segments, single separators.

    Every write path runs this, so 'acme / x100//' and 'acme/x100' are one
    domain and no caller can coin an empty segment -- a path with one
    would sit in the tree at a level nothing can name.
    """
    return DOMAIN_SEP.join(split_domain(domain))
