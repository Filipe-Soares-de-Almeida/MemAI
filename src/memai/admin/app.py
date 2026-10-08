"""The dashboard's Starlette app: every route, the static build, and the middleware."""

from __future__ import annotations

import mimetypes

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from memai.admin import pages
from memai.admin.routes import (
    backups,
    config,
    diagrams,
    domains,
    graph,
    maintenance,
    memories,
    optimization,
    overview,
    projects,
    relations,
    sections,
    tasks,
    update,
)
from memai.admin.security import NoCacheMiddleware, SameOriginMiddleware

# Windows' registry-derived mimetypes map serves .js as text/plain, which
# browsers refuse to execute as an ES module. Force the correct types.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
# The registry map has no woff2 entry, so the bundled faces would go out as octet-stream.
mimetypes.add_type("font/woff2", ".woff2")

routes = [
    *pages.ROUTES,
    *overview.ROUTES,
    *memories.ROUTES,
    *tasks.ROUTES,
    *relations.ROUTES,
    *graph.ROUTES,
    *diagrams.ROUTES,
    *update.ROUTES,
    *config.ROUTES,
    *domains.ROUTES,
    *maintenance.ROUTES,
    *backups.ROUTES,
    *projects.ROUTES,
    *sections.ROUTES,
    *optimization.ROUTES,
    # check_dir=False: importing this module must not depend on the build
    # having run, or every test that imports it fails at import time.
    Mount("/static", StaticFiles(directory=str(pages.WEBUI_DIR), check_dir=False),
          name="static"),
]
app = Starlette(routes=routes, middleware=[
    Middleware(SameOriginMiddleware),
    Middleware(NoCacheMiddleware),
])
