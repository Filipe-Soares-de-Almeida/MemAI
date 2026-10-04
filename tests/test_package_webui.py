"""Tests for tools/package-webui.py, which packs the dashboard release asset.

The script lives outside the package, so it is loaded from its path. Every
build it packs here is a fictional one written into tmp_path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "package-webui.py"


@pytest.fixture()
def tool():
    spec = importlib.util.spec_from_file_location("package_webui", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>demo</title>", encoding="utf-8")
    (root / "THIRD-PARTY-NOTICES.txt").write_text("example-lib 1.0.0 -- MIT\n", encoding="utf-8")
    (root / "build.json").write_text('{"version": "9.8.7", "sources": "abc"}', encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return root


def test_the_zip_holds_the_build_at_its_root(tool, dist, tmp_path):
    archive, _ = tool.pack(dist, tmp_path / "out", "9.8.7")
    assert archive.name == "memai-webui-9.8.7.zip"
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == [
            "THIRD-PARTY-NOTICES.txt", "assets/index-abc123.js", "build.json", "index.html"]
        assert zf.read("index.html").startswith(b"<!doctype html>")


def test_the_checksum_file_verifies_the_zip(tool, dist, tmp_path):
    archive, checksum = tool.pack(dist, tmp_path / "out", "9.8.7")
    assert checksum.name == "memai-webui-9.8.7.zip.sha256"
    digest, name = checksum.read_text(encoding="ascii").split()
    assert name == archive.name
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_the_same_build_packs_to_the_same_bytes(tool, dist, tmp_path):
    first, _ = tool.pack(dist, tmp_path / "a", "1.0.0")
    second, _ = tool.pack(dist, tmp_path / "b", "1.0.0")
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("missing", ["index.html", "THIRD-PARTY-NOTICES.txt", "build.json"])
def test_a_build_without_its_page_or_notices_is_refused(tool, dist, tmp_path, missing):
    (dist / missing).unlink()
    with pytest.raises(SystemExit, match=missing):
        tool.pack(dist, tmp_path / "out", "1.0.0")


def test_the_version_comes_from_the_package(tool, tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text('__version__ = "4.5.6"  # x-release-please-version\n', encoding="utf-8")
    assert tool.version(init) == "4.5.6"
