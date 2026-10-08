"""Middleware that keeps other browser pages from driving the dashboard, and caching off."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Admin UI iterates often and is tiny; never let a browser cache it."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


def _same_origin(origin: str, request) -> bool:
    """Does `origin` name this very server, as the request reached it?"""
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc == request.headers.get("host", "")


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Keep another page in the browser from driving this server.

    There is no login here -- it is a single-user loopback tool -- so the
    browser is the only thing between a random web page you happen to
    visit and POST /api/maintenance/vacuum on your own machine. Two
    checks do that job:

    * Fetch metadata, then Origin. A browser labels every request with
      Sec-Fetch-Site, and any cross-origin one with Origin. A non-browser
      client (curl, the test suite) sends neither and is let through --
      it is not the threat, and it cannot be tricked by a web page.

    * application/json on a written body. A cross-origin POST escapes the
      CORS preflight only while it looks like a form: text/plain,
      multipart/form-data, application/x-www-form-urlencoded. Starlette's
      request.json() does not care about the content type, which is what
      made that a working attack -- so care here instead. Requiring JSON
      forces a preflight, and this server answers none.

    Neither check is a substitute for authentication. Do not put this on
    a network interface; see the warning in main().
    """

    WRITE_METHODS = ("POST", "PUT", "PATCH")

    async def dispatch(self, request, call_next):
        site = request.headers.get("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            return JSONResponse({"error": f"cross-origin request refused ({site})"},
                                status_code=403)
        origin = request.headers.get("origin")
        if origin and not _same_origin(origin, request):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        if request.method in self.WRITE_METHODS:
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse(
                    {"error": f"{request.method} requires Content-Type: application/json"},
                    status_code=415)
        return await call_next(request)
