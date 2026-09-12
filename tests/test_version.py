"""Where the version is allowed to live.

A release rewrites one line of source and `pyproject.toml` reads the version
from it. A second copy of the number is a second thing a release can leave
behind, and the generic updater matches an annotation, so a line without one
is skipped without failing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import memai

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
INIT = (ROOT / "src" / "memai" / "__init__.py").read_text(encoding="utf-8")


def _version_line() -> str:
    """The line of the package's `__init__.py` that sets `__version__`."""
    for line in INIT.splitlines():
        if line.startswith("__version__"):
            return line
    raise AssertionError("src/memai/__init__.py sets __version__")


def test_pyproject_declares_the_version_dynamic():
    assert "version" not in PYPROJECT["project"]
    assert "version" in PYPROJECT["project"]["dynamic"]


def test_pyproject_reads_the_version_from_the_package():
    assert PYPROJECT["tool"]["hatch"]["version"]["path"] == "src/memai/__init__.py"


def test_the_version_line_carries_the_release_annotation():
    line = _version_line()
    assert "x-release-please-version" in line
    assert f'"{memai.__version__}"' in line
