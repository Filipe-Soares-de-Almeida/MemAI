"""contract.json is the one place a constant both languages need is written."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import webui_constants
from memai import admin, contract, db, diagram_svg, tasks

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "memai"
CONTRACT = json.loads((PACKAGE / "contract.json").read_text(encoding="utf-8"))

# Contract keys the code spells differently, or too generic to search for, by the names it uses.
RENAMED = {
    ("memory", "TYPES"): ["MEMORY_TYPES", "TYPE_ORDER"],
    ("memory", "CONFIDENCES"): ["CONFIDENCES", "CONFIDENCE_VALUES"],
    ("memory", "PINS"): ["PINS"],
    ("memory", "TITLE_MAX"): ["TITLE_MAX", "NOTE_TITLE_MAX"],
    ("task", "STATES"): ["TASK_STATES"],
    ("admin", "ARCHIVE_LABEL_MAX"): ["ARCHIVE_LABEL_MAX", "ZIP_NAME_MAX"],
    ("diagram", "SHAPES"): ["NODE_SHAPES"],
    ("diagram", "NODE_W"): ["NODE_W", "NODE_DEFAULT_W"],
    ("diagram", "NODE_H"): ["NODE_H", "NODE_DEFAULT_H"],
    ("diagram", "DECISION_H"): ["DECISION_H", "DECISION_DEFAULT_H"],
}

LITERAL = r"""\s*=\s*(?!=)[-+\d'"`\[(]"""


def _names() -> list[str]:
    names = []
    for group, values in CONTRACT.items():
        for key in values:
            names += RENAMED.get((group, key), [key])
    return names


def _sources() -> list[Path]:
    skip = {"dist", "public", "node_modules", "__pycache__"}
    found = [p for p in PACKAGE.rglob("*") if p.suffix in {".py", ".js", ".mjs", ".ts", ".vue"}]
    found += [p for p in (ROOT / "tools").glob("*") if p.suffix in {".py", ".mjs", ".ts"}]
    return [p for p in found if not skip & set(p.parts) and p.name != "contract.py"]


def redefinitions(source: str) -> list[str]:
    """Each contract name `source` assigns a literal to, instead of reading it from the contract."""
    return [name for name in _names()
            if re.search(rf"(?<![\w.]){name}{LITERAL}", source)]


def test_no_module_writes_a_contract_value_of_its_own():
    found = {p.relative_to(ROOT).as_posix(): hits for p in _sources()
             if (hits := redefinitions(p.read_text(encoding="utf-8")))}
    assert not found, f"read these from contract.json instead: {found}"


@pytest.mark.parametrize("source", [
    'TITLE_MAX = 120',
    'NODE_MIN_W, NODE_MAX_W = 110.0, 560.0',
    'MEMORY_TYPES = ("note", "task")',
    "export const TYPE_ORDER = ['note', 'task'];",
    'const NODE_MIN_W = 110, NODE_MAX_W = 560;',
    "  const ZIP_NAME_MAX = 60;",
])
def test_the_scan_catches_a_literal_definition(source):
    assert redefinitions(source)


@pytest.mark.parametrize("source", [
    "TITLE_MAX = contract.TITLE_MAX",
    "const { BULK_MAX } = ADMIN;",
    "if len(uids) > BULK_MAX:",
    "if TITLE_MAX == 120:",
    "self.TITLE_MAX = 3",
])
def test_the_scan_leaves_reads_and_comparisons_alone(source):
    assert not redefinitions(source)


def test_python_reads_the_values_the_file_holds():
    memory, task, diagram = CONTRACT["memory"], CONTRACT["task"], CONTRACT["diagram"]
    assert tuple(memory["TYPES"]) == db.MEMORY_TYPES
    assert db.CONFIDENCE_VALUES == admin.CONFIDENCES == tuple(memory["CONFIDENCES"])
    assert ("", *memory["PINS"]) == db.PIN_VALUES
    assert memory["TITLE_MAX"] == db.TITLE_MAX and memory["DOMAIN_SEP"] == db.DOMAIN_SEP
    assert tuple(task["STATES"]) == tasks.TASK_STATES and task["NOTE_MAX"] == tasks.NOTE_MAX
    assert CONTRACT["admin"]["BULK_MAX"] == admin.BULK_MAX
    assert diagram["NODE_W"] == db.NODE_DEFAULT_W and diagram["ORTH_SNAP"] == diagram_svg.ORTH_SNAP


def test_the_svg_export_keeps_its_metrics_as_floats():
    assert isinstance(contract.ARROW_LEN, float) and f"{-contract.ARROW_LEN}" == "-9.0"
    assert isinstance(contract.BADGE_WORDS, int)


def test_the_dashboard_reads_the_same_file():
    assert webui_constants()["types"] == CONTRACT["memory"]["TYPES"]
