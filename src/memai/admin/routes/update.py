"""Releases: whether this install is current, checking for a newer one, the changelog."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route

from memai import __version__, changelog, update
from memai import admin_schemas as schema
from memai.admin.api import api

# Where a version sits relative to the one running.
INSTALLED = "installed"

AHEAD = "ahead"        # published, and not what this process is running

PAST = "past"


def _state_of(version: str, current: str) -> str:
    if update.parse_version(version) == update.parse_version(current):
        return INSTALLED
    return AHEAD if update.is_newer(version, current) else PAST


def update_state(request=None, payload=None) -> schema.UpdateState:
    """What the release check knows, without asking it anything.

    The cache is filled by a hook process or by `check_update`; this only
    reads it, so it answers the same whether the machine is online or not.
    `failed` is whether the last request came back empty, and
    `interval_hours` is how long an answer is used.
    """
    record = update.cached()
    latest = update.newest(record)
    behind = update.ahead(record)
    return {"current": __version__,
            "latest": latest if update.is_newer(latest, __version__) else "",
            "behind": len(behind),
            "url": str(record.get("url") or update.RELEASES_PAGE),
            "checked_at": str(record.get("checked_at", "")),
            "failed": bool(record.get("failed")),
            "enabled": update.enabled(),
            "interval_hours": update.interval(),
            # Shown, never run: they rewrite the environment the servers around it run from.
            "commands": update.commands()}


def check_update(request, payload) -> schema.UpdateState:
    """Ask GitHub for the releases now, whatever the window says.

    The one place the dashboard reaches the network. A request that does not
    come back leaves the release already known in place and reports
    `failed`; a check that is switched off is a 400, not a silent no-op.
    """
    if not update.enabled():
        raise ValueError("the release check is off (MEMAI_UPDATE_CHECK)")
    update.refresh(force=True, timeout=update.MANUAL_TIMEOUT)
    return update_state()


def set_update_interval(request, payload) -> schema.UpdateState:
    """Choose how many hours an answer is used before another request."""
    update.set_interval(payload.get("hours"))
    return update_state()


def changelog_page(request, payload) -> schema.Changelog:
    """Every release this installation can name, newest first.

    Two sources, one shape. `CHANGELOG.md` ships with the package and is the
    history; the release check's cache carries what was published after this
    version, which no local file can know about. Each release says whether it
    is the one installed, one published since, or one already passed.
    """
    current = __version__
    rows: dict[tuple, dict] = {}
    for release in update.ahead():
        version = str(release.get("version", ""))
        rows[update.parse_version(version)] = {
            "version": version.lstrip("v"),
            "date": str(release.get("published_at", "")),
            "url": str(release.get("url", "")),
            "sections": changelog.sections_of(release.get("notes")),
            "state": AHEAD,
        }
    # Second, and deliberately overwriting: for a version that appears in both,
    # the shipped file is the copy this installation can be held to.
    for entry in changelog.releases():
        rows[update.parse_version(entry["version"])] = {
            **entry, "state": _state_of(entry["version"], current)}
    releases = [rows[key] for key in sorted(rows, reverse=True)]
    return {"current": current, "releases": cast(list[schema.Release], releases),
            "update": update_state(), "source": changelog.source() is not None}


ROUTES = [
    Route("/api/changelog", api(changelog_page), methods=["GET"]),
    Route("/api/update", api(update_state), methods=["GET"]),
    Route("/api/update/check", api(check_update), methods=["POST"]),
    Route("/api/update/interval", api(set_update_interval), methods=["POST"]),
]
