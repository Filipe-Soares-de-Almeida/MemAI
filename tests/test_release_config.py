"""What the release configuration has to say for a release to come out whole.

release-please writes the next version into the files its configuration names,
and a file it cannot find is not an error to it -- it is a version left behind.
Below 1.0.0 its default sends a breaking change to 1.0.0, which
`bump-minor-pre-major` holds back.
"""

from __future__ import annotations

import json
from pathlib import Path

import memai

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))

PACKAGE = CONFIG["packages"]["."]


def test_a_breaking_change_stays_below_one_point_zero():
    assert PACKAGE["bump-minor-pre-major"] is True


def test_the_package_version_file_is_named_explicitly():
    paths = [entry["path"] for entry in PACKAGE["extra-files"]]
    assert "src/memai/__init__.py" in paths


def test_the_manifest_matches_the_package_version():
    assert MANIFEST["."] == memai.__version__
