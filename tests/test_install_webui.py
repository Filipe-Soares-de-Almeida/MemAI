"""Tests for tools/install-webui.py, the dashboard step of install.bat.

npm, the network and the release are replaced by fakes; every build and zip
is a fictional one written into tmp_path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import urllib.error
import zipfile
from pathlib import Path

import pytest

from memai import update

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "install-webui.py"


@pytest.fixture()
def tool():
    spec = importlib.util.spec_from_file_location("install_webui", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip(path: Path, files: dict[str, str], *, checksum: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    if checksum:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_name(f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return path


@pytest.fixture()
def build_zip(tmp_path):
    return _zip(tmp_path / "memai-webui-1.2.3.zip",
                {"index.html": "<title>new</title>", "assets/app.js": "1"})


@pytest.fixture()
def dist(tmp_path):
    root = tmp_path / "webui" / "dist"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<title>old</title>", encoding="utf-8")
    return root


class Done:
    def __init__(self, returncode=0):
        self.returncode = returncode


def test_the_script_downloads_from_the_repository_update_checks(tool):
    assert tool.REPO == update.REPO


def test_npm_builds_when_node_and_npm_are_there(tool, tmp_path):
    calls = []
    run = lambda cmd, **kw: calls.append(cmd[1:]) or Done()
    assert tool.npm_build(tmp_path, which=lambda name: f"/bin/{name}", run=run) is None
    assert calls == [["ci"], ["run", "build"]]


def test_no_node_means_no_npm_attempt(tool, tmp_path):
    run = lambda *a, **k: pytest.fail("npm ran without node")
    why = tool.npm_build(tmp_path, which=lambda name: None, run=run)
    assert why == "node is not on PATH"


def test_a_refused_npm_ci_is_reported(tool, tmp_path):
    why = tool.npm_build(tmp_path, which=lambda n: n, run=lambda cmd, **kw: Done(1))
    assert why == "npm ci exited with 1"


def test_a_verified_zip_replaces_the_build(tool, build_zip, dist):
    tool.verify(build_zip, build_zip.with_name(build_zip.name + ".sha256"))
    tool.install_zip(build_zip, dist)
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>new</title>"
    assert (dist / "assets" / "app.js").is_file()
    assert sorted(p.name for p in dist.parent.iterdir()) == ["dist"]


def test_a_tampered_zip_is_refused(tool, build_zip):
    build_zip.write_bytes(build_zip.read_bytes() + b"x")
    with pytest.raises(ValueError, match="does not match"):
        tool.verify(build_zip, build_zip.with_name(build_zip.name + ".sha256"))


def test_a_zip_without_its_checksum_is_refused(tool, tmp_path):
    archive = _zip(tmp_path / "memai-webui-1.0.0.zip", {"index.html": "x"}, checksum=False)
    with pytest.raises(ValueError, match="missing"):
        tool.verify(archive, archive.with_name(archive.name + ".sha256"))


def test_a_zip_reaching_outside_the_build_leaves_dist_alone(tool, tmp_path, dist):
    archive = _zip(tmp_path / "evil.zip", {"index.html": "x", "../escape.txt": "x"})
    with pytest.raises(ValueError, match="outside"):
        tool.install_zip(archive, dist)
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>old</title>"
    assert not (dist.parent / "escape.txt").exists()


def test_a_zip_without_a_page_leaves_dist_alone(tool, tmp_path, dist):
    archive = _zip(tmp_path / "empty.zip", {"assets/app.js": "1"})
    with pytest.raises(ValueError, match="index.html"):
        tool.install_zip(archive, dist)
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>old</title>"


def _urlopen(files: dict[str, bytes]):
    def opener(url, timeout):
        name = url.rsplit("/", 1)[1]
        if name not in files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(files[name])
    return opener


def test_the_release_asset_is_downloaded_verified_and_installed(tool, build_zip, dist):
    files = {build_zip.name: build_zip.read_bytes(),
             build_zip.name + ".sha256": build_zip.with_name(build_zip.name + ".sha256").read_bytes()}
    assert tool.download("1.2.3", dist, urlopen=_urlopen(files)) is None
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>new</title>"


def test_a_version_without_an_asset_says_so(tool, dist):
    why = tool.download("9.9.9", dist, urlopen=_urlopen({}))
    assert "has no memai-webui-9.9.9.zip" in why
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>old</title>"


def test_an_unreachable_network_is_reported(tool, dist):
    def offline(url, timeout):
        raise urllib.error.URLError("no route to host")
    assert "no route to host" in tool.download("1.2.3", dist, urlopen=offline)


def test_main_stops_at_a_successful_npm_build(tool, dist):
    fetch = lambda *a: pytest.fail("downloaded although npm built")
    assert tool.main([], dist=dist, build=lambda: None, fetch_release=fetch) == 0


def test_main_falls_back_to_the_release_asset(tool, dist):
    seen = []
    fetch = lambda ver, d: seen.append(ver)
    assert tool.main(["--version", "1.2.3"], dist=dist, build=lambda: "node is not on PATH",
                     fetch_release=fetch) == 0
    assert seen == ["1.2.3"]


def test_main_keeps_the_build_in_place_when_both_fail(tool, dist):
    code = tool.main(["--version", "1.2.3"], dist=dist, build=lambda: "no node",
                     fetch_release=lambda ver, d: "offline")
    assert code == 0


def test_main_reports_no_dashboard_when_nothing_is_left(tool, tmp_path):
    empty = tmp_path / "nothing" / "dist"
    code = tool.main(["--version", "1.2.3"], dist=empty, build=lambda: "no node",
                     fetch_release=lambda ver, d: "offline")
    assert code == tool.NO_DASHBOARD


def test_main_installs_a_zip_fetched_by_hand(tool, build_zip, dist):
    build = lambda: pytest.fail("npm ran for a --zip install")
    assert tool.main(["--zip", str(build_zip)], dist=dist, build=build) == 0
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>new</title>"


def test_main_refuses_a_hand_fed_zip_that_fails_its_checksum(tool, build_zip, dist):
    build_zip.with_name(build_zip.name + ".sha256").write_text("0" * 64 + f"  {build_zip.name}\n")
    assert tool.main(["--zip", str(build_zip)], dist=dist) == tool.NO_DASHBOARD
    assert (dist / "index.html").read_text(encoding="utf-8") == "<title>old</title>"
