"""memai admin dashboard -- local web UI over the memory store.

A Starlette + uvicorn app (both already shipped as dependencies of the
`mcp` SDK, so this adds no new requirements) exposing a JSON API over
the store plus a single-page UI, built from webui/ by Vite into
webui/dist/ and served from there. It is a *maintenance*
surface: everything the MCP tools can do, plus operations that only
make sense for a human curator -- bulk confidence triage, domain
renames/merges, relation pruning, dedup review, FTS rebuilds,
VACUUM/backup, and an audit trail over the edits table.

Handlers are written synchronously against the store, and `api` runs
each one in a worker thread. The whole handler -- connect, work, commit
-- stays on one thread, which preserves both the store's
one-transaction-per-connect model and sqlite3's same-thread rule. It
also keeps a slow handler off the event loop, so a scan does not stop
the server from answering anything else (see maintenance/dedup).

Destructive parity with the MCP tools is kept: archive (forget) is the
default "delete", and purge demands the literal confirmation phrase
"DELETE <uid>" typed by the operator, same guardrail as server.py. A
whole domain reads the same way one memory does -- archiving it is the
reversible option, and deleting it asks for "DELETE <domain>".

Run with `memai-admin` (default http://127.0.0.1:8888); binds to
loopback unless --host says otherwise. Honors MEMAI_HOME.
"""

from memai.admin.api import api
from memai.admin.app import app
from memai.admin.cli import build_parser, main
from memai.admin.pages import WEBFONTS
from memai.admin.routes.memories import CONFIDENCES
from memai.admin.routes.overview import HEALTH_DELTA_DAYS
from memai.admin.shared import BULK_MAX

__all__ = ["BULK_MAX", "CONFIDENCES", "HEALTH_DELTA_DAYS", "WEBFONTS", "api", "app",
           "build_parser", "main"]
