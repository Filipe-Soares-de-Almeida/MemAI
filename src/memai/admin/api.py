"""The wrapper that serves a synchronous handler as a JSON endpoint off the event loop."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


def api(handler):
    """Wrap a sync (request, payload) handler into an async JSON endpoint.

    The handler runs in a worker thread, so a long one leaves the event
    loop free to accept and route everything else. Its whole body --
    including the db.connect block -- runs on that one thread, which is
    what sqlite3's same-thread connections require.

    A CPU-bound handler still slows a concurrent one down through the
    GIL, but it does not stop the server. Handlers therefore reach the
    store concurrently: writes serialize on SQLite itself, and
    db.connect opens WAL with a 30s busy timeout, so a writer waits for
    a writer rather than failing.

    ValueError -> 400 with the message (validation/guardrail failures);
    anything else -> 500. Body is parsed as JSON for mutating methods.
    """
    async def endpoint(request):
        payload = {}
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
        try:
            return JSONResponse(await run_in_threadpool(handler, request, payload))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # pragma: no cover - defensive
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    endpoint.__wrapped__ = handler
    return endpoint
