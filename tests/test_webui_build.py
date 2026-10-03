"""Tests for the dashboard rebuild check (memai/webui_build.py).

Every test builds a fictional checkout under tmp_path and replaces npm with a
recorder, so nothing here runs node or touches the real dist/.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from memai import webui_build

NPM = "C:/path/to/npm.cmd"


def _touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    """A checkout whose build and node_modules are newer than every source."""
    monkeypatch.delenv("MEMAI_ADMIN_BUILD", raising=False)
    root = tmp_path / "repo"
    for name in ("package.json", "package-lock.json", "vite.config.js"):
        _touch(root / name, 1000)
    _touch(root / "src/memai/webui/index.html", 1000)
    _touch(root / "src/memai/webui/views/list.js", 1000)
    _touch(root / "node_modules/.package-lock.json", 2000)
    _touch(root / "src/memai/webui/dist/index.html", 2000)
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


def _ensure(root, npm, which=NPM, log=None):
    return webui_build.ensure_built(
        root, run=npm, which=lambda name: which, log=(log or (lambda msg: None)))


def test_an_up_to_date_build_runs_nothing(checkout):
    npm = Npm()
    assert _ensure(checkout, npm) is True
    assert npm.calls == []


def test_a_source_newer_than_the_build_rebuilds(checkout):
    _touch(checkout / "src/memai/webui/views/list.js", 3000)
    npm = Npm()
    assert _ensure(checkout, npm) is True
    assert npm.calls == [["run", "build"]]


@pytest.mark.parametrize("name", ["package.json", "vite.config.js"])
def test_a_newer_build_config_rebuilds(checkout, name):
    _touch(checkout / name, 3000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["run", "build"]]


def test_files_inside_dist_never_count_as_sources(checkout):
    _touch(checkout / "src/memai/webui/dist/assets/app.js", 9000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == []


def test_a_missing_build_is_built(checkout):
    (checkout / "src/memai/webui/dist/index.html").unlink()
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["run", "build"]]


def test_a_newer_lockfile_reinstalls_before_building(checkout):
    _touch(checkout / "package-lock.json", 3000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["ci"], ["run", "build"]]


def test_missing_node_modules_installs_before_building(checkout):
    (checkout / "node_modules/.package-lock.json").unlink()
    _touch(checkout / "src/memai/webui/index.html", 3000)
    npm = Npm()
    _ensure(checkout, npm)
    assert npm.calls == [["ci"], ["run", "build"]]


def test_an_install_without_sources_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMAI_ADMIN_BUILD", raising=False)
    npm = Npm()
    assert _ensure(tmp_path / "site-packages", npm) is False
    assert npm.calls == []


def test_without_npm_the_old_build_stays_and_it_says_so(checkout):
    _touch(checkout / "src/memai/webui/index.html", 3000)
    lines: list[str] = []
    npm = Npm()
    assert _ensure(checkout, npm, which=None, log=lines.append) is False
    assert npm.calls == []
    assert any("npm" in line for line in lines)


def test_a_failed_install_skips_the_build(checkout):
    _touch(checkout / "package-lock.json", 3000)
    lines: list[str] = []
    npm = Npm(fail="ci")
    assert _ensure(checkout, npm, log=lines.append) is False
    assert npm.calls == [["ci"]]
    assert lines


def test_a_failed_build_is_reported(checkout):
    _touch(checkout / "src/memai/webui/index.html", 3000)
    lines: list[str] = []
    assert _ensure(checkout, Npm(fail="build"), log=lines.append) is False
    assert any("failed" in line for line in lines)


def test_npm_that_cannot_start_is_reported(checkout):
    _touch(checkout / "src/memai/webui/index.html", 3000)

    def broken(argv, **kw):
        raise OSError("cannot start")

    lines: list[str] = []
    assert _ensure(checkout, broken, log=lines.append) is False
    assert lines


def test_npm_runs_in_the_checkout_without_a_window(checkout):
    _touch(checkout / "src/memai/webui/index.html", 3000)
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
    _touch(checkout / "src/memai/webui/index.html", 3000)
    npm = Npm()
    assert _ensure(checkout, npm) is False
    assert npm.calls == []


class _Stop(Exception):
    pass


def _run_admin_main(monkeypatch, tmp_path, bound):
    from memai import admin

    events: list[str] = []
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["memai-admin", "--autostarted"])
    monkeypatch.setattr(admin, "_bind", lambda host, port: events.append("bind") or bound)
    monkeypatch.setattr(webui_build, "ensure_built", lambda: events.append("build"))

    def registry(host, port):
        events.append("registry")
        raise _Stop

    monkeypatch.setattr(admin, "_write_registry", registry)
    with pytest.raises((_Stop, SystemExit)):
        admin.main()
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
