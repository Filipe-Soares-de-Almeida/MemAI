"""The dashboard API's declared shapes: every route names a response TypedDict, every GET answers
with exactly its keys, api/types.ts is generated from them, and only api/ spells a path."""

from __future__ import annotations

import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

from memai import admin, db, tasks

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "src" / "memai" / "webui"

API_ROUTES = [r for r in admin.app.routes
              if isinstance(r, Route) and r.path.startswith("/api/") and hasattr(r.endpoint, "__wrapped__")]


def _response_type(route: Route) -> Any:
    return get_type_hints(route.endpoint.__wrapped__)["return"]


def mismatches(value: Any, hint: Any, where: str = "$") -> list[str]:
    """Where `value` departs from `hint`: a missing or unexpected key, or a value of another kind."""
    if hint is Any:
        return []
    if is_typeddict(hint):
        if not isinstance(value, dict):
            return [f"{where}: expected an object, got {type(value).__name__}"]
        hints = get_type_hints(hint)
        out = [f"{where}: missing '{k}'" for k in hint.__required_keys__ if k not in value]
        out += [f"{where}: unexpected '{k}'" for k in value if k not in hints]
        for key, item in value.items():
            if key in hints:
                out += mismatches(item, hints[key], f"{where}.{key}")
        return out
    origin, args = get_origin(hint), get_args(hint)
    if origin in (Union, types.UnionType):
        found = [mismatches(value, arm, where) for arm in args]
        return [] if any(not f for f in found) else min(found, key=len)
    if origin is Literal:
        return [] if value in args else [f"{where}: {value!r} is not one of {args}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{where}: expected a list, got {type(value).__name__}"]
        return [m for i, item in enumerate(value) for m in mismatches(item, args[0], f"{where}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: expected an object, got {type(value).__name__}"]
        return [m for k, item in value.items() for m in mismatches(item, args[1], f"{where}.{k}")]
    kinds = {float: (int, float), int: (int,), str: (str,), bool: (bool,), type(None): (type(None),)}
    allowed = kinds.get(hint, (hint,))
    if isinstance(value, bool) and hint in (int, float):
        return [f"{where}: expected a number, got a boolean"]
    return [] if isinstance(value, allowed) else [
        f"{where}: expected {getattr(hint, '__name__', hint)}, got {type(value).__name__}"]


def test_every_api_route_declares_a_response_typeddict():
    undeclared = []
    for route in API_ROUTES:
        hint = _response_type(route)
        arms = get_args(hint) if get_origin(hint) in (Union, types.UnionType) else (hint,)
        if not all(is_typeddict(a) for a in arms):
            undeclared.append(f"{sorted(route.methods or [])} {route.path}")
    assert not undeclared


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """An invented store holding one of each thing a GET route reads."""
    home = tmp_path_factory.mktemp("home")
    patch = pytest.MonkeyPatch()
    patch.setenv("MEMAI_HOME", str(home))
    with db.connect() as conn:
        note = db.insert_memory(conn, type="note", domain="acme/x100", title="Lantern oil grade",
                                content="The lantern takes grade B oil.", tags="lantern, oil",
                                also="acme/lighting")
        other = db.insert_memory(conn, type="note", domain="acme/x200", title="Wick length",
                                 content="Trim the wick to a centimetre.", tags="wick")
        db.add_relation(conn, note, other, "relates_to")
        task = tasks.create_task(conn, title="Refill the lanterns", goal="Every lantern burns tonight.",
                                 items=["Buy oil", "Trim wicks"], domain="acme/x100")
        tasks.add_note(conn, task, title="Where the oil is", body="In the shed.", items=[])
        tasks.add_comment(conn, task, "Oil is on order.", item="", author="person")
        diagram, _ = db.insert_diagram(
            conn, title="Refill routine", domain="acme/x100",
            nodes=[{"key": "a", "shape": "start", "label": "Start"},
                   {"key": "b", "shape": "end", "label": "End"}],
            edges=[{"from": "a", "to": "b", "label": "done"}])
        db.add_node_link(conn, diagram, "a", note, "explains")
        run = db.stage_optimization(conn, "tidy the tags", [
            {"kind": "retag", "target_uid": note, "payload": {"tags": "lantern, fuel"},
             "rationale": "a synonym", "verified": "checked"}])
    with TestClient(admin.app) as client:
        yield {"client": client, "note": note, "task": task, "diagram": diagram,
               "run": run["run_id"]}
    patch.undo()


# Each GET route and the requests that exercise it; {note}, {task}, {diagram} and {run} are
# filled from the invented store.
GETS = {
    "/api/overview": ["/api/overview"],
    "/api/memories": ["/api/memories", "/api/memories?q=lantern"],
    "/api/memories/{uid}": ["/api/memories/{note}", "/api/memories/{task}", "/api/memories/{diagram}"],
    "/api/graph": ["/api/graph"],
    "/api/diagrams": ["/api/diagrams"],
    "/api/diagrams/{uid}": ["/api/diagrams/{diagram}"],
    "/api/diagrams/{uid}/mermaid": ["/api/diagrams/{diagram}/mermaid"],
    "/api/changelog": ["/api/changelog"],
    "/api/update": ["/api/update"],
    "/api/config": ["/api/config"],
    "/api/domains": ["/api/domains"],
    "/api/domains/detail": ["/api/domains/detail?domain=acme/x100"],
    "/api/maintenance/health": ["/api/maintenance/health"],
    "/api/maintenance/backups": ["/api/maintenance/backups"],
    "/api/projects": ["/api/projects"],
    "/api/maintenance/dedup": ["/api/maintenance/dedup?threshold=0.1"],
    "/api/maintenance/sections-queue": ["/api/maintenance/sections-queue"],
    "/api/optimization/runs": ["/api/optimization/runs"],
    "/api/optimization/suggestions": ["/api/optimization/suggestions?run={run}"],
    "/api/optimization/summary": ["/api/optimization/summary?run={run}"],
    "/api/audit": ["/api/audit"],
    "/api/lookup": ["/api/lookup?q=lantern"],
}


def test_every_get_route_is_exercised_here():
    gets = {r.path for r in API_ROUTES if "GET" in (r.methods or set())}
    assert gets == set(GETS)


@pytest.mark.parametrize("path", sorted(GETS))
def test_a_get_route_answers_with_exactly_its_declared_keys(store, path):
    route = next(r for r in API_ROUTES if r.path == path and "GET" in (r.methods or set()))
    hint = _response_type(route)
    for url in GETS[path]:
        res = store["client"].get(url.format(**store))
        assert res.status_code == 200, (url, res.text)
        assert mismatches(res.json(), hint) == [], url


@pytest.mark.parametrize(("value", "found"), [
    ({"ok": True}, []),
    ({}, ["$: missing 'ok'"]),
    ({"ok": True, "extra": 1}, ["$: unexpected 'extra'"]),
    ({"ok": False}, ["$.ok: False is not one of (True,)"]),
])
def test_the_key_check_reports_what_departs(value, found):
    from memai.admin_schemas import Ok
    assert mismatches(value, Ok) == found


def test_the_generated_types_are_current():
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "gen-api-types.py"), "--check"],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr


def test_only_the_api_client_spells_an_api_path():
    literal = re.compile(r"""['"`]/api/""")
    found = [f"{p.relative_to(WEBUI).as_posix()}:{n}"
             for p in WEBUI.rglob("*") if p.suffix in {".js", ".ts", ".vue"}
             and not {"dist", "public", "api"} & set(p.relative_to(WEBUI).parts[:-1])
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
             if literal.search(line)]
    assert not found
