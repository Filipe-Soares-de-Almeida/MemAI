"""The hashed lock files stay in step with pyproject.toml's dependency ranges."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def _pins(lock: str) -> dict[str, str]:
    """name -> version of every package pinned in `lock`."""
    pins = {}
    for line in (ROOT / lock).read_text(encoding="utf-8").splitlines():
        if m := PIN.match(line):
            pins[canonicalize_name(m.group(1))] = m.group(2)
    return pins


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _direct(extras: bool) -> list[Requirement]:
    project = _project()
    reqs = list(project["dependencies"])
    if extras:
        reqs += project["optional-dependencies"]["dev"]
    return [Requirement(r) for r in reqs]


@pytest.mark.parametrize("lock, extras", [("requirements.txt", False),
                                          ("requirements-dev.txt", True)])
def test_every_direct_dependency_is_locked_inside_its_range(lock, extras):
    pins = _pins(lock)
    for req in _direct(extras):
        name = canonicalize_name(req.name)
        assert name in pins, f"{req.name} is missing from {lock}"
        assert req.specifier.contains(pins[name], prereleases=True), \
            f"{lock} pins {req.name}=={pins[name]} outside {req.specifier}"


def test_every_line_of_a_lock_carries_a_hash():
    for lock in ("requirements.txt", "requirements-dev.txt"):
        text = (ROOT / lock).read_text(encoding="utf-8")
        assert text.count("--hash=sha256:") >= len(_pins(lock)), lock


def test_the_dev_lock_pins_the_runtime_set_at_the_same_versions():
    runtime, dev = _pins("requirements.txt"), _pins("requirements-dev.txt")
    drift = {n: (v, dev.get(n)) for n, v in runtime.items() if dev.get(n) != v}
    assert not drift, f"runtime vs dev lock: {drift}"
