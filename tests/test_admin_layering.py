"""The dashboard's HTTP layer runs no SQL of its own: every query it needs is a store function."""

import io
import re
import tokenize
from pathlib import Path

from starlette.routing import Match, Route

import memai
from memai.admin.app import app as admin_app

PACKAGE = Path(memai.__file__).parent
SQL = re.compile(r"\b(SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|PRAGMA)\b")
EXECUTE = {"execute", "executemany", "executescript"}


def _admin_sources() -> list[Path]:
    return sorted((PACKAGE / "admin").rglob("*.py"))


def _sql_in(source: str) -> list[str]:
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.NAME and tok.string in EXECUTE:
            found.append(f"{tok.start[0]}: calls {tok.string}")
        elif tok.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE) and SQL.search(tok.string):
            found.append(f"{tok.start[0]}: SQL in a string")
    return found


def test_admin_runs_no_sql_of_its_own():
    sources = _admin_sources()
    assert sources
    found = {str(p.relative_to(PACKAGE)): _sql_in(p.read_text(encoding="utf-8")) for p in sources}
    assert {name: lines for name, lines in found.items() if lines} == {}


def test_the_check_sees_a_query_and_a_call():
    source = 'rows = conn.execute(f"SELECT uid FROM memories WHERE {clause}")\n'
    assert _sql_in(source) == ["1: calls execute", "1: SQL in a string"]
    assert _sql_in('PHRASE = "DELETE <uid>"\n') == []


def test_no_route_is_answered_by_an_earlier_one():
    """Routes are matched in order, so a pattern above a literal path can take its requests."""
    for route in admin_app.routes:
        if not isinstance(route, Route):
            continue
        path = re.sub(r"\{\w+\}", "abc", re.sub(r"\{\w+:int\}", "7", route.path))
        for method in route.methods or ():
            scope = {"type": "http", "path": path, "method": method, "root_path": ""}
            first = next(r for r in admin_app.routes if r.matches(scope)[0] == Match.FULL)
            assert first is route, f"{method} {route.path} is answered by {first.path}"
