"""The store's settings, as the dashboard reads and writes them."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import sections
from memai.admin.api import api
from memai.admin.shared import _section_spec
from memai.store import connection, domains, settings

# setting -> (reader, writer)
_SETTINGS = {
    "domain_case": (domains.get_domain_case, domains.set_domain_case),
    "svg_retention": (settings.get_svg_retention, settings.set_svg_retention),
    "warden_enabled": (settings.get_warden_enabled, settings.set_warden_enabled),
    "warden_minutes": (settings.get_warden_minutes, settings.set_warden_minutes),
    "task_ask_enabled": (settings.get_task_ask_enabled, settings.set_task_ask_enabled),
    "task_ask_minutes": (settings.get_task_ask_minutes, settings.set_task_ask_minutes),
    "checkpoint_block_enabled": (settings.get_checkpoint_block_enabled,
                                 settings.set_checkpoint_block_enabled),
    "checkpoint_days": (settings.get_checkpoint_days, settings.set_checkpoint_days),
    "confidence_ask_enabled": (settings.get_confidence_ask_enabled,
                               settings.set_confidence_ask_enabled),
    "confidence_ask_minutes": (settings.get_confidence_ask_minutes,
                               settings.set_confidence_ask_minutes),
    "unverified_days": (settings.get_unverified_days, settings.set_unverified_days),
}


def _saved(conn) -> schema.ConfigSaved:
    return cast(schema.ConfigSaved, {key: read(conn) for key, (read, _) in _SETTINGS.items()})


def get_config(request, payload) -> schema.Config:
    """Settings, plus the section spec the forms build their fields from.

    The spec is served rather than spelled in the UI: a label written in
    both places is a body the store would read into fields the form never
    offered.
    """
    with connection.connect() as conn:
        return cast(schema.Config, {**_saved(conn),
                "sections": {type_: [_section_spec(s) for s in spec]
                             for type_, spec in sections.SECTION_SPEC.items()}})


def set_config(request, payload) -> schema.ConfigSaved:
    """Write whichever settings the payload names.

    Partial: only the settings the payload names are written, so a caller
    that knows about one of them does not have to send a value for the
    other.
    """
    given = {k: payload[k] for k in _SETTINGS if payload.get(k) is not None}
    if not given:
        raise ValueError(f"expected one of {', '.join(_SETTINGS)}")
    with connection.connect() as conn:
        for key, value in given.items():
            _SETTINGS[key][1](conn, value)
        return _saved(conn)


ROUTES = [
    Route("/api/config", api(get_config), methods=["GET"]),
    Route("/api/config", api(set_config), methods=["POST"]),
]
