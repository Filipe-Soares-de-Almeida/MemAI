"""The store's settings, as the dashboard reads and writes them."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import sections
from memai.admin.api import api
from memai.admin.shared import _section_spec
from memai.store import connection, domains, settings


def get_config(request, payload) -> schema.Config:
    """Settings, plus the section spec the forms build their fields from.

    The spec is served rather than spelled in the UI: a label written in
    both places is a body the store would read into fields the form never
    offered.
    """
    with connection.connect() as conn:
        return cast(schema.Config, {"domain_case": domains.get_domain_case(conn),
                "svg_retention": settings.get_svg_retention(conn),
                "warden_enabled": settings.get_warden_enabled(conn),
                "warden_minutes": settings.get_warden_minutes(conn),
                "task_ask_enabled": settings.get_task_ask_enabled(conn),
                "task_ask_minutes": settings.get_task_ask_minutes(conn),
                "sections": {type_: [_section_spec(s) for s in spec]
                             for type_, spec in sections.SECTION_SPEC.items()}})


def set_config(request, payload) -> schema.ConfigSaved:
    """Write whichever settings the payload names.

    Partial: only the settings the payload names are written, so a caller
    that knows about one of them does not have to send a value for the
    other.
    """
    writers = {"domain_case": domains.set_domain_case,
               "svg_retention": settings.set_svg_retention,
               "warden_enabled": settings.set_warden_enabled,
               "warden_minutes": settings.set_warden_minutes,
               "task_ask_enabled": settings.set_task_ask_enabled,
               "task_ask_minutes": settings.set_task_ask_minutes}
    given = {k: payload[k] for k in writers if payload.get(k) is not None}
    if not given:
        raise ValueError(f"expected one of {', '.join(writers)}")
    with connection.connect() as conn:
        for key, value in given.items():
            writers[key](conn, value)
        return {"domain_case": domains.get_domain_case(conn),
                "svg_retention": settings.get_svg_retention(conn),
                "warden_enabled": settings.get_warden_enabled(conn),
                "warden_minutes": settings.get_warden_minutes(conn),
                "task_ask_enabled": settings.get_task_ask_enabled(conn),
                "task_ask_minutes": settings.get_task_ask_minutes(conn)}


ROUTES = [
    Route("/api/config", api(get_config), methods=["GET"]),
    Route("/api/config", api(set_config), methods=["POST"]),
]
