"""The store's settings, as the dashboard reads and writes them."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import db, sections
from memai.admin.api import api
from memai.admin.shared import _section_spec


def get_config(request, payload) -> schema.Config:
    """Settings, plus the section spec the forms build their fields from.

    The spec is served rather than spelled in the UI: a label written in
    both places is a body the store would read into fields the form never
    offered.
    """
    with db.connect() as conn:
        return cast(schema.Config, {"domain_case": db.get_domain_case(conn),
                "svg_retention": db.get_svg_retention(conn),
                "warden_enabled": db.get_warden_enabled(conn),
                "warden_minutes": db.get_warden_minutes(conn),
                "task_ask_enabled": db.get_task_ask_enabled(conn),
                "task_ask_minutes": db.get_task_ask_minutes(conn),
                "sections": {type_: [_section_spec(s) for s in spec]
                             for type_, spec in sections.SECTION_SPEC.items()}})


def set_config(request, payload) -> schema.ConfigSaved:
    """Write whichever settings the payload names.

    Partial: only the settings the payload names are written, so a caller
    that knows about one of them does not have to send a value for the
    other.
    """
    writers = {"domain_case": db.set_domain_case,
               "svg_retention": db.set_svg_retention,
               "warden_enabled": db.set_warden_enabled,
               "warden_minutes": db.set_warden_minutes,
               "task_ask_enabled": db.set_task_ask_enabled,
               "task_ask_minutes": db.set_task_ask_minutes}
    given = {k: payload[k] for k in writers if payload.get(k) is not None}
    if not given:
        raise ValueError(f"expected one of {', '.join(writers)}")
    with db.connect() as conn:
        for key, value in given.items():
            writers[key](conn, value)
        return {"domain_case": db.get_domain_case(conn),
                "svg_retention": db.get_svg_retention(conn),
                "warden_enabled": db.get_warden_enabled(conn),
                "warden_minutes": db.get_warden_minutes(conn),
                "task_ask_enabled": db.get_task_ask_enabled(conn),
                "task_ask_minutes": db.get_task_ask_minutes(conn)}


ROUTES = [
    Route("/api/config", api(get_config), methods=["GET"]),
    Route("/api/config", api(set_config), methods=["POST"]),
]
