"""CI tests every Python that pyproject.toml's requires-python admits."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parent.parent


def _matrix_pythons() -> list[str]:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    line = re.search(r"^\s*python:\s*\[(.*)\]", ci, re.M)
    assert line, "ci.yml has no python matrix"
    return [v.strip().strip("\"'") for v in line.group(1).split(",")]


def _requires_python() -> SpecifierSet:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return SpecifierSet(project["requires-python"])


def test_every_python_in_the_matrix_is_admitted():
    spec = _requires_python()
    for version in _matrix_pythons():
        assert spec.contains(version), f"requires-python {spec} refuses {version}"


def test_the_ceiling_sits_just_above_the_newest_python_ci_tests():
    spec = _requires_python()
    ceilings = [s.version for s in spec if s.operator == "<"]
    assert ceilings, f"requires-python {spec} has no ceiling"
    major, minor = max(tuple(map(int, v.split("."))) for v in _matrix_pythons())
    assert ceilings == [f"{major}.{minor + 1}"], \
        f"ceiling {ceilings} should be {major}.{minor + 1}, one above CI's newest Python"
