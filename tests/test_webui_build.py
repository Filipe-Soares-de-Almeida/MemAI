"""Tests for the dashboard rebuild check (memai/webui_build.py).

Every test builds a fictional checkout under tmp_path and replaces npm with a
recorder, so nothing here runs node or touches the real dist/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from memai import webui_build

NPM = "C:/path/to/npm.cmd"
VERSION = "1.2.3"


def _write(path: Path, text: str = "x", mtime: float = 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _stamp(root: Path, version: str = VERSION, sources: str | None = None) -> None:
    stamp = {"version": version, "sources": sources or webui_build.source_hash(root)}
    _write(root / "src/memai/webui/dist/build.json", json.dumps(stamp), 2000)


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    """A checkout whose build is stamped with its current sources."""
    monkeypatch.delenv("MEMAI_ADMIN_BUILD", raising=False)
    root = tmp_path / "repo"
    for name in webui_build.BUILD_CONFIG:
        _write(root / name, f"config {name}")
    _write(root / "src/memai/webui/index.html", "<title>demo</title>")
    _write(root / "src/memai/webui/views/list.js", "export const list = 1;")
    _write(root / "node_modules/.package-lock.json", "{}", 2000)
    _write(root / "src/memai/webui/dist/index.html", "built", 2000)
    _stamp(root)
    return root


class Npm:
    """Stands in for subprocess.run and records each npm command."""

    def __init__(self, fail: str = ""):
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, argv, **kw):
        self.calls.append(argv[1:])
        code = 1 if self.fail and argv[-1] == self.fail else 0
        return subprocess.CompletedProcess(argv, code)


def _no_fetch(root, version):
    pytest.fail("the prebuilt asset was fetched")


def _ensure(root, npm, which=NPM, log=None, fetch=_no_fetch):
    return webui_build.ensure_built(
        root, run=npm, which=lambda name: which, log=(log or (lambda msg: None)),
        fetch=fetch, version=VERSION)


def test_an_up_to_date_build_runs_nothing(checkout):
    npm = Npm()
    assert _ensure(checkout, npm) is True
    assert npm.calls == []


def test_a_changed_source_rebuilds(checkout):
    _write(checkout / "src/memai/webui/views/list.js", "export const list = 2;")
    npm = Npm()
    assert _ensure(checkout, npm) is True
    assert npm.calls == [["run", "build"]]


def test_a_touched_but_unchanged_source_does_not_rebuild(checkout):
    path = checkout / "src/memai/webui/views/list.js"
    os.utime(path, (9000, 9000))
    npm = Npm()
    assert _ensure(checkout, npm) is True
    assert npm.calls == []


def test_a_rewound_source_rebuilds_even_when_older_than_the_build(checkout):
    _write(checkout / "src/memai/webui/views/list.js", "export const list = 0;", mtime=10)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["run", "build"]]


def test_line_endings_do_not_change_the_hash(checkout):
    path = checkout / "src/memai/webui/views/list.js"
    path.write_bytes(b"export const a = 1;\nexport const b = 2;\n")
    before = webui_build.source_hash(checkout)
    path.write_bytes(b"export const a = 1;\r\nexport const b = 2;\r\n")
    assert webui_build.source_hash(checkout) == before


@pytest.mark.parametrize("name", ["package.json", "vite.config.js", "tsconfig.json"])
def test_a_changed_build_config_rebuilds(checkout, name):
    _write(checkout / name, "changed")
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["run", "build"]]


def test_files_inside_dist_never_count_as_sources(checkout):
    _write(checkout / "src/memai/webui/dist/assets/app.js", "anything", 9000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == []


def test_a_build_without_a_stamp_is_built(checkout):
    (checkout / "src/memai/webui/dist/build.json").unlink()
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["run", "build"]]


def test_an_unreadable_stamp_is_built(checkout):
    _write(checkout / "src/memai/webui/dist/build.json", "{not json", 2000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["run", "build"]]


def test_a_newer_lockfile_reinstalls_before_building(checkout):
    _write(checkout / "package-lock.json", '{"lockfileVersion": 3}', 3000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["ci"], ["run", "build"]]


def test_missing_node_modules_installs_before_building(checkout):
    (checkout / "node_modules/.package-lock.json").unlink()
    _write(checkout / "src/memai/webui/index.html", "<title>changed</title>")
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["ci"], ["run", "build"]]


def test_an_install_without_sources_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMAI_ADMIN_BUILD", raising=False)
    npm = Npm()
    assert _ensure(tmp_path / "site-packages", npm) is False
    assert npm.calls == []


def test_without_npm_a_build_of_this_version_stays_and_it_says_so(checkout):
    _write(checkout / "src/memai/webui/index.html", "<title>changed</title>")
    lines: list[str] = []
    npm = Npm()
    assert _ensure(checkout, npm, which=None, log=lines.append) is False
    assert npm.calls == []
    assert any("npm is not on PATH" in line for line in lines)


def test_without_npm_a_build_of_another_version_is_replaced_by_the_release_asset(checkout):
    _stamp(checkout, version="0.0.1", sources="0" * 64)
    asked = []
    fetch = lambda root, version: asked.append((root, version))
    assert _ensure(checkout, Npm(), which=None, fetch=fetch) is True
    assert asked == [(checkout, VERSION)]


def test_without_npm_a_failed_fetch_keeps_the_build_and_says_why(checkout):
    _stamp(checkout, version="0.0.1", sources="0" * 64)
    lines: list[str] = []
    fetch = lambda root, version: "offline"
    assert _ensure(checkout, Npm(), which=None, log=lines.append, fetch=fetch) is False
    assert any("offline" in line for line in lines)


def test_the_hash_matches_the_one_the_vite_build_writes(checkout):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    script = Path(__file__).resolve().parents[1] / "tools" / "build-stamp.mjs"
    code = ("const [script, root] = process.argv.slice(1);"
            "const { pathToFileURL } = await import('node:url');"
            "const m = await import(pathToFileURL(script).href);"
            "console.log(await m.sourceHash(root));")
    (checkout / "src/memai/webui/i18n").mkdir()
    (checkout / "src/memai/webui/i18n/pt.json").write_bytes(b'{"a": "b"}\r\n')
    out = subprocess.run([node, "--input-type=module", "-e", code, str(script), str(checkout)],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == webui_build.source_hash(checkout)


def test_a_failed_install_skips_the_build(checkout):
    _write(checkout / "package-lock.json", '{"lockfileVersion": 3}', 3000)
    lines: list[str] = []
    npm = Npm(fail="ci")
    assert _ensure(checkout, npm, log=lines.append) is False
    assert npm.calls == [["ci"]]
    assert lines


def test_a_failed_build_is_reported(checkout):
    _write(checkout / "src/memai/webui/index.html", "<title>changed</title>")
    lines: list[str] = []
    assert _ensure(checkout, Npm(fail="build"), log=lines.append) is False
    assert any("failed" in line for line in lines)


def test_npm_that_cannot_start_is_reported(checkout):
    _write(checkout / "src/memai/webui/index.html", "<title>changed</title>")

    def broken(argv, **kw):
        raise OSError("cannot start")

    lines: list[str] = []
    assert _ensure(checkout, broken, log=lines.append) is False
    assert lines


def test_npm_runs_in_the_checkout_without_a_window(checkout):
    _write(checkout / "src/memai/webui/index.html", "<title>changed</title>")
    seen = {}

    def record(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0)

    _ensure(checkout, record)
    assert Path(seen["cwd"]) == checkout
    assert seen["stdin"] is subprocess.DEVNULL
    if os.name == "nt":
        assert seen["creationflags"] & webui_build.CREATE_NO_WINDOW


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_the_check_can_be_switched_off(checkout, monkeypatch, value):
    monkeypatch.setenv("MEMAI_ADMIN_BUILD", value)
    _write(checkout / "src/memai/webui/index.html", "<title>changed</title>")
    npm = Npm()
    assert _ensure(checkout, npm) is False
    assert npm.calls == []


class _Stop(Exception):
    pass


def _run_admin_main(monkeypatch, tmp_path, bound):
    from memai.admin import cli

    events: list[str] = []
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["memai-admin", "--autostarted"])
    monkeypatch.setattr(cli, "_bind", lambda host, port: events.append("bind") or bound)
    monkeypatch.setattr(webui_build, "ensure_built", lambda: events.append("build"))

    def registry(host, port):
        events.append("registry")
        raise _Stop

    monkeypatch.setattr(cli, "_write_registry", registry)
    with pytest.raises((_Stop, SystemExit)):
        cli.main()
    return events


def test_admin_builds_after_winning_the_port(monkeypatch, tmp_path):
    events = _run_admin_main(monkeypatch, tmp_path, bound=object())
    assert events == ["bind", "build", "registry"]


def test_admin_that_loses_the_port_builds_nothing(monkeypatch, tmp_path):
    events = _run_admin_main(monkeypatch, tmp_path, bound=None)
    assert events == ["bind"]


def test_the_repository_root_is_three_levels_above_the_module():
    root = webui_build.repo_root()
    assert (root / "src" / "memai" / "webui_build.py").is_file()
