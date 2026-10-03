"""Tests for tools/install-guard.py, the check install.bat and update.bat run first.

The script is stdlib-only and lives outside the package, so it is loaded from
its path. Processes, pings and kills are replaced by recorders; every path is
fictional.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "install-guard.py"
ROOT = Path("C:/path/to/MemAI")
OTHER = Path("C:/path/to/other/MemAI")


@pytest.fixture()
def guard(monkeypatch):
    spec = importlib.util.spec_from_file_location("install_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module through sys.modules while the class is built
    monkeypatch.setitem(sys.modules, "install_guard", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    monkeypatch.delenv("MEMAI_ADMIN_PORT", raising=False)
    return tmp_path


def _mcp(pid, root):
    return (pid, str(root / ".venv" / "Scripts" / "memai-mcp.exe"))


def _pinger(answers):
    """answers: {port: ping body}. Any other port answers nothing."""
    asked = []

    def ping(host, port):
        asked.append((host, port))
        return answers.get(port)

    ping.asked = asked
    return ping


def _ping_body(pid, root):
    return {"app": "memai", "pid": pid, "root": str(root)}


# --- what counts as running from this checkout --------------------------------

def test_a_server_from_this_checkout_blocks(guard, home):
    found = guard.find_running(ROOT, processes=lambda: [_mcp(41, ROOT)],
                               ping=_pinger({}))
    assert [item.pid for item in found] == [41]
    assert found[0].kind == "mcp"


def test_a_server_from_another_checkout_does_not_block(guard, home):
    found = guard.find_running(ROOT, processes=lambda: [_mcp(41, OTHER)],
                               ping=_pinger({}))
    assert found == []


def test_path_casing_and_slashes_do_not_matter(guard, home):
    upper = (41, str(ROOT / ".venv" / "Scripts" / "memai-mcp.exe").upper().replace("/", "\\"))
    found = guard.find_running(ROOT, processes=lambda: [upper], ping=_pinger({}))
    assert [item.pid for item in found] == [41]


def test_a_server_whose_path_is_hidden_blocks(guard, home):
    found = guard.find_running(ROOT, processes=lambda: [(41, "")], ping=_pinger({}))
    assert [item.pid for item in found] == [41]


def test_a_dashboard_from_this_checkout_on_the_default_port_blocks(guard, home):
    ping = _pinger({8888: _ping_body(52, ROOT)})
    found = guard.find_running(ROOT, processes=lambda: [], ping=ping)
    assert [(item.kind, item.pid) for item in found] == [("dashboard", 52)]
    assert ("127.0.0.1", 8888) in ping.asked


def test_a_dashboard_from_another_checkout_does_not_block(guard, home):
    ping = _pinger({8888: _ping_body(52, OTHER)})
    assert guard.find_running(ROOT, processes=lambda: [], ping=ping) == []


def test_a_dashboard_that_does_not_name_its_checkout_blocks(guard, home):
    ping = _pinger({8888: {"app": "memai", "pid": 52}})
    found = guard.find_running(ROOT, processes=lambda: [], ping=ping)
    assert [item.pid for item in found] == [52]


def test_the_configured_port_is_probed(guard, home, monkeypatch):
    monkeypatch.setenv("MEMAI_ADMIN_PORT", "9123")
    ping = _pinger({9123: _ping_body(52, ROOT)})
    found = guard.find_running(ROOT, processes=lambda: [], ping=ping)
    assert [item.port for item in found] == [9123]


def test_the_registered_dashboard_is_probed(guard, home):
    (home / "admin.json").write_text(json.dumps({"host": "127.0.0.1", "port": 9200}))
    ping = _pinger({9200: _ping_body(52, ROOT)})
    found = guard.find_running(ROOT, processes=lambda: [], ping=ping)
    assert [item.port for item in found] == [9200]


def test_one_dashboard_on_two_probed_ports_is_listed_once(guard, home):
    (home / "admin.json").write_text(json.dumps({"host": "127.0.0.1", "port": 8888}))
    ping = _pinger({8888: _ping_body(52, ROOT)})
    found = guard.find_running(ROOT, processes=lambda: [], ping=ping)
    assert len(found) == 1


def test_a_broken_registry_falls_back_to_the_port(guard, home):
    (home / "admin.json").write_text("{not json")
    ping = _pinger({})
    assert guard.find_running(ROOT, processes=lambda: [], ping=ping) == []
    assert ("127.0.0.1", 8888) in ping.asked


# --- what main() does with it -----------------------------------------------

def _main(guard, rounds, *, answer="", interactive=True):
    """rounds: what find_running returns on each call, in order."""
    calls = iter(rounds)
    killed: list[int] = []
    out: list[str] = []
    code = guard.main(
        ROOT,
        find=lambda root: next(calls),
        ask=lambda prompt: answer,
        interactive=interactive,
        kill=killed.append,
        say=out.append,
        wait=lambda: None,
    )
    return code, killed, "\n".join(out)


def _item(guard, kind="mcp", pid=41):
    return guard.Running(kind=kind, pid=pid, where="C:/path/to/MemAI/.venv/Scripts/memai-mcp.exe")


def test_nothing_running_lets_the_install_go_on(guard):
    code, killed, out = _main(guard, [[]])
    assert code == 0 and killed == []


def test_a_refusal_lists_what_is_open_and_how_to_close_it(guard):
    code, killed, out = _main(guard, [[_item(guard), _item(guard, "dashboard", 52)]],
                              answer="n")
    assert code == 1 and killed == []
    assert "41" in out and "52" in out
    assert "Claude" in out and "stop-admin.bat" in out


def test_without_a_console_it_refuses_without_asking(guard):
    asked = []
    code = guard.main(ROOT, find=lambda root: [_item(guard)], ask=asked.append,
                      interactive=False, kill=lambda pid: None, say=lambda m: None,
                      wait=lambda: None)
    assert code == 1 and asked == []


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "s", "sim"])
def test_a_yes_closes_them_and_goes_on(guard, answer):
    code, killed, out = _main(guard, [[_item(guard), _item(guard, "dashboard", 52)], []],
                              answer=answer)
    assert code == 0 and killed == [41, 52]


def test_something_that_survives_the_close_still_refuses(guard):
    code, killed, out = _main(guard, [[_item(guard)], [_item(guard)]], answer="y")
    assert code == 1 and killed == [41]
