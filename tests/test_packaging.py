"""What the package declares about itself, held to what it can install.

`requires-python` is a promise pip enforces on someone else's machine. A
floor no leg of CI installs on is a promise nobody checks, and a dependency
whose own floor is higher makes it unsatisfiable without one test going red.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _floor() -> tuple[int, ...]:
    """The `requires-python` lower bound, as a version tuple."""
    spec = PYPROJECT["project"]["requires-python"]
    found = re.search(r">=\s*(\d+)\.(\d+)", spec)
    assert found, f"requires-python has no lower bound: {spec!r}"
    return tuple(int(g) for g in found.groups())


def _ci_versions() -> list[tuple[int, ...]]:
    """The Python versions the test matrix runs, as version tuples."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    row = re.search(r"^\s*python:\s*\[(.+)\]\s*$", workflow, re.M)
    assert row, "ci.yml no longer declares a python matrix"
    return [tuple(int(p) for p in v.split(".")) for v in re.findall(r"[\"'](\d+\.\d+)[\"']", row.group(1))]


def test_the_ci_matrix_runs_the_declared_python_floor():
    """The oldest leg is the floor itself.

    A floor below the matrix is a version the package claims and nothing
    installs on -- which is how a dependency requiring a newer Python than
    `requires-python` allows goes unnoticed until someone else's install
    fails.
    """
    versions = _ci_versions()
    assert versions, "the python matrix is empty"
    assert min(versions) == _floor(), (
        f"matrix starts at {min(versions)}, requires-python floor is {_floor()}")


def test_the_installer_names_the_declared_python_floor():
    """install.bat tells a person which Python to have on PATH, so it says
    the same number `requires-python` does."""
    major, minor = _floor()
    text = (ROOT / "install.bat").read_text(encoding="utf-8")
    assert f"{major}.{minor}+" in text, f"install.bat does not name {major}.{minor}+"
