"""The dashboard's HTTP layer runs no SQL of its own: every query it needs is a store function."""

import io
import re
import tokenize
from pathlib import Path

import memai

PACKAGE = Path(memai.__file__).parent
SQL = re.compile(r"\b(SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|PRAGMA)\b")
EXECUTE = {"execute", "executemany", "executescript"}


def _admin_sources() -> list[Path]:
    package = PACKAGE / "admin"
    return sorted(package.rglob("*.py")) if package.is_dir() else [PACKAGE / "admin.py"]


def _sql_in(source: str) -> list[str]:
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.NAME and tok.string in EXECUTE:
            found.append(f"{tok.start[0]}: calls {tok.string}")
        elif tok.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE) and SQL.search(tok.string):
            found.append(f"{tok.start[0]}: SQL in a string")
    return found


def test_admin_runs_no_sql_of_its_own():
    found = {p.name: _sql_in(p.read_text(encoding="utf-8")) for p in _admin_sources()}
    assert {name: lines for name, lines in found.items() if lines} == {}


def test_the_check_sees_a_query_and_a_call():
    source = 'rows = conn.execute(f"SELECT uid FROM memories WHERE {clause}")\n'
    assert _sql_in(source) == ["1: calls execute", "1: SQL in a string"]
    assert _sql_in('PHRASE = "DELETE <uid>"\n') == []
