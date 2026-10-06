"""The dashboard's Vitest suite, run from pytest so one command runs every test."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_dashboard_behaviour_tests_pass():
    npm = shutil.which("npm")
    if npm is None or not (ROOT / "node_modules").is_dir():
        pytest.skip("npm or node_modules is missing: run `npm ci` to include the Vitest suite")
    out = subprocess.run([npm, "test"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600)
    assert out.returncode == 0, (out.stdout + out.stderr)[-6000:]
