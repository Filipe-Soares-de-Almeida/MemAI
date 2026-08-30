"""What the package declares about itself, held to what it can install.

`requires-python` is a promise pip enforces on someone else's machine. A
floor no leg of CI installs on is a promise nobody checks, and a dependency
whose own floor is higher makes it unsatisfiable without one test going red.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PACKAGE_JSON = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))


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


# ------------------------------------------------------- the node toolchain

def _node_floor() -> tuple[int, ...]:
    """The `engines.node` lower bound, as a version tuple."""
    spec = PACKAGE_JSON["engines"]["node"]
    found = re.search(r">=\s*(\d+)\.(\d+)", spec)
    assert found, f"engines.node has no lower bound: {spec!r}"
    return tuple(int(g) for g in found.groups())


def test_npm_enforces_the_declared_node_floor():
    """`engines` is advice to npm until engine-strict turns it into a rule.

    Without it a fresh install on an older Node proceeds and fails somewhere
    inside the build, where the message names anything but the Node version.
    """
    assert "engine-strict=true" in (ROOT / ".npmrc").read_text(encoding="utf-8")


def test_the_installer_names_the_declared_node_floor():
    """install.bat tells a person which Node to have on PATH, so it says the
    same number `engines.node` does."""
    major, minor = _node_floor()
    text = (ROOT / "install.bat").read_text(encoding="utf-8")
    assert f"{major}.{minor}+" in text, f"install.bat does not name {major}.{minor}+"


def test_ci_runs_a_node_the_package_allows():
    """A workflow pinned below the floor is a red build engine-strict causes
    and nobody expects."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pinned = [int(v) for v in re.findall(r"""node-version:\s*["'](\d+)""", workflow)]
    assert pinned, "ci.yml no longer pins a node version"
    assert min(pinned) >= _node_floor()[0]


def test_the_lock_can_install_without_the_registry_deciding_anything():
    """`npm ci` installs from the lock alone, so every package it names is
    pinned to one version and carries a hash to check the download against."""
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] >= 3
    packages = lock["packages"]
    assert packages, "the lock names no packages"

    unpinned = [name for name, meta in packages.items()
                if name and not meta.get("version")]
    assert not unpinned, f"no version for {unpinned}"
    unverifiable = [name for name, meta in packages.items()
                    if name and not meta.get("integrity")]
    assert not unverifiable, f"no integrity for {unverifiable}"


def test_what_the_browser_gets_is_a_runtime_dependency():
    """A bundled package is a dependency, not a devDependency: the licence
    plugin in vite.config.js reads `dependencies` to know whose notices the
    build has to carry."""
    assert "highlight.js" in PACKAGE_JSON["dependencies"]
    assert "highlight.js" not in PACKAGE_JSON.get("devDependencies", {})
