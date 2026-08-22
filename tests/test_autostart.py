"""The guard that starts the admin dashboard from the MCP server.

Nothing here spawns a real process. It could not be hermetic if it did:
conftest's autouse no_real_model fixture patches this interpreter only,
so a genuine child would load the embedding model for real and bind a
real port. _spawn_admin is replaced by a recorder instead, and the cases
that need something on a port use an ephemeral listener -- a real socket,
so the probe is really exercised, but one the kernel picked and the test
owns.

MEMAI_HOME is a tmp dir per test, which is where the registry file lives.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memai import admin, autostart


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    monkeypatch.delenv("MEMAI_ADMIN_AUTOSTART", raising=False)
    monkeypatch.delenv("MEMAI_ADMIN_PORT", raising=False)
    monkeypatch.delenv("MEMAI_ADMIN_HOST", raising=False)
    return tmp_path


@pytest.fixture()
def spawned(monkeypatch):
    """Record spawn attempts instead of making them."""
    calls = []
    monkeypatch.setattr(autostart, "_spawn_admin",
                        lambda host, port: calls.append((host, port)))
    return calls


@pytest.fixture()
def dead_port():
    """A port with nothing on it: bound to learn the number, then released."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def busy_port():
    """A real listener that is not MemAI -- it accepts and says nothing."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


@pytest.fixture()
def memai_port():
    """A real port answering /api/ping the way the dashboard does.

    A socket rather than uvicorn: what is under test is the probe, and it
    speaks HTTP/1.0 over a plain connection. The genuine route is
    exercised separately, through the real app, in test_ping_identifies_itself.
    """
    payload = json.dumps({"app": "memai", "version": "0.1.0", "pid": 4242})
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.recv(4096)
                    conn.sendall(
                        b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
                        + payload.encode())
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    server.close()
    thread.join(timeout=2)


def _registry(home, host="127.0.0.1", port=1, pid=999):
    (home / autostart.REGISTRY_NAME).write_text(
        json.dumps({"host": host, "port": port, "pid": pid}), encoding="utf-8")


# --- the opt-in ------------------------------------------------------

def test_does_nothing_unless_asked(spawned):
    autostart.ensure_admin_running()
    assert spawned == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_every_spelling_of_yes_starts_it(monkeypatch, spawned, dead_port, value):
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", value)
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(dead_port))
    autostart.ensure_admin_running()
    assert spawned == [("127.0.0.1", dead_port)]


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_anything_else_is_no(monkeypatch, spawned, value):
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", value)
    autostart.ensure_admin_running()
    assert spawned == []


def test_refuses_a_non_loopback_host(monkeypatch, spawned, dead_port, capsys):
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_HOST", "0.0.0.0")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(dead_port))
    autostart.ensure_admin_running()
    assert spawned == []
    # the dashboard has no authentication, and nobody reads the stderr of
    # a process the agent host started -- so this one refuses rather than
    # warning the way main() does for a human who typed it
    assert "loopback-only" in capsys.readouterr().err


# --- not starting a second one ---------------------------------------

def test_a_port_that_answers_but_is_not_memai_blocks_the_start(
        monkeypatch, spawned, busy_port, capsys):
    """The failure this whole design exists for.

    A bare TCP probe cannot tell a dashboard from anything else, and the
    admin's default port was in real life held by an unrelated MCP
    server. Starting here would fight over somebody else's port.
    """
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(busy_port))
    autostart.ensure_admin_running()
    assert spawned == []
    err = capsys.readouterr().err
    assert "is not memai" in err and str(busy_port) in err


def test_the_registry_finds_a_dashboard_on_another_port(
        monkeypatch, spawned, home, dead_port, memai_port):
    """run-admin.bat sets MEMAI_ADMIN_PORT inside its own process.

    That never reaches an MCP server started from a desktop app's
    config, so probing only the configured port would miss the operator's
    own dashboard and open a second one on the same store.
    """
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(dead_port))   # not where it is
    _registry(home, port=memai_port)                          # where it really is
    autostart.ensure_admin_running()
    assert spawned == []


def test_the_configured_port_is_believed_when_memai_answers(
        monkeypatch, spawned, memai_port):
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(memai_port))
    autostart.ensure_admin_running()
    assert spawned == []


def test_a_stale_registry_starts_one_rather_than_never_starting(
        monkeypatch, spawned, home, dead_port):
    """A record left behind by an abrupt kill must not disable autostart.

    This is why the registry is advisory and re-confirmed with a ping,
    and why there is no lock file: the host kills MCP servers without
    warning, so anything that survives a kill and is *believed* would
    turn off the dashboard permanently.
    """
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(dead_port))
    _registry(home, port=dead_port)
    autostart.ensure_admin_running()
    assert spawned == [("127.0.0.1", dead_port)]


@pytest.mark.parametrize("body", ["", "not json", "[]", '{"app": "something-else"}'])
def test_an_unreadable_registry_is_ignored(monkeypatch, spawned, home,
                                           dead_port, body):
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(dead_port))
    (home / autostart.REGISTRY_NAME).write_text(body, encoding="utf-8")
    autostart.ensure_admin_running()
    assert spawned == [("127.0.0.1", dead_port)]


# --- the identity probe ----------------------------------------------

def test_ping_identifies_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as client:
        body = client.get("/api/ping").json()
    assert body["app"] == "memai"
    assert isinstance(body["pid"], int)
    assert body["version"]


def test_ping_returns_none_for_a_port_with_nothing_on_it(dead_port):
    assert autostart.ping("127.0.0.1", dead_port) is None


def test_ping_returns_none_for_a_stranger(busy_port):
    assert autostart.ping("127.0.0.1", busy_port, timeout=0.2) is None


def test_probe_tells_the_three_cases_apart(dead_port, busy_port, memai_port):
    """"Nothing there" and "someone else's" must not collapse together.

    Treating them alike is what would either fight another service for a
    port or never start the dashboard at all.
    """
    assert autostart.probe("127.0.0.1", dead_port) == (autostart.CLOSED, None)
    assert autostart.probe("127.0.0.1", busy_port, 0.2) == (autostart.STRANGER, None)
    state, payload = autostart.probe("127.0.0.1", memai_port)
    assert state == autostart.MEMAI
    assert payload["pid"] == 4242


# --- configuration ---------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # deliberately not the default: a value equal to it would pass whether
    # the variable was read or ignored
    ("9001", 9001),
    ("garbage", autostart.DEFAULT_PORT),
    ("", autostart.DEFAULT_PORT),
    ("0", autostart.DEFAULT_PORT),
    ("70000", autostart.DEFAULT_PORT),
    ("-1", autostart.DEFAULT_PORT),
])
def test_port_config(monkeypatch, raw, expected):
    monkeypatch.setenv("MEMAI_ADMIN_PORT", raw)
    assert autostart.configured_port() == expected


def test_the_default_port_is_the_one_the_launcher_uses():
    """One default port, in one place: the number the launcher and the
    documentation both name."""
    assert autostart.DEFAULT_PORT == 8888


def test_admin_and_autostart_agree_on_the_port(monkeypatch):
    """Two defaults would mean the guard looking for the dashboard on a
    port the dashboard never binds."""
    monkeypatch.delenv("MEMAI_ADMIN_PORT", raising=False)
    args = admin.build_parser().parse_args([])
    assert args.port == autostart.configured_port() == autostart.DEFAULT_PORT

    monkeypatch.setenv("MEMAI_ADMIN_PORT", "9002")
    assert admin.build_parser().parse_args([]).port == 9002
    assert admin.build_parser().parse_args(["--port", "9003"]).port == 9003


# --- it may never cost the caller its memory tools -------------------

def test_a_failing_spawn_is_survivable(monkeypatch, dead_port, capsys):
    """An optional dashboard must never take the MCP server down with it."""
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")
    monkeypatch.setenv("MEMAI_ADMIN_PORT", str(dead_port))

    def boom(host, port):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(autostart, "_spawn_admin", boom)
    autostart.ensure_admin_running()          # must not raise
    assert "could not start the dashboard" in capsys.readouterr().err


def test_a_failure_looking_up_what_is_running_is_survivable(monkeypatch, capsys):
    """Anything unexpected on the path, not just the spawn itself."""
    monkeypatch.setenv("MEMAI_ADMIN_AUTOSTART", "1")

    def boom():
        raise OSError("no such drive")

    monkeypatch.setattr(autostart, "_from_registry", boom)
    autostart.ensure_admin_running()          # must not raise
    assert "could not start the dashboard" in capsys.readouterr().err


# --- no console window on the desktop --------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="console windows are Windows")
def test_it_starts_the_dashboard_with_a_windowless_interpreter():
    """DETACHED_PROCESS alone left a terminal flashing up every session.

    A venv's python.exe relaunches the real interpreter itself, and
    Windows hands a console-subsystem process with no inherited console a
    fresh one. The fix is not being a console program.
    """
    chosen = Path(autostart.interpreter())
    assert chosen.name == "pythonw.exe"
    assert chosen.is_file()
    assert _pe_subsystem(chosen) == 2, "pythonw.exe should be the GUI subsystem"
    # and the thing it replaced is what caused the window
    assert _pe_subsystem(Path(sys.executable)) == 3


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are Windows")
def test_it_falls_back_when_there_is_no_pythonw(monkeypatch, tmp_path):
    """Better a console nobody looks at than no dashboard at all."""
    lonely = tmp_path / "python.exe"
    lonely.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(lonely))
    assert autostart.interpreter() == str(lonely)


def _pe_subsystem(path: Path) -> int:
    """2 = GUI (never allocates a console), 3 = console."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<H", data, pe + 24 + 68)[0]


# --- the backstop that closes the race -------------------------------

def test_bind_returns_none_when_the_port_is_taken(busy_port):
    """Several MCP servers start at once; the kernel picks the winner.

    admin._bind is the arbiter, which is why there is no lock file.
    """
    assert admin._bind("127.0.0.1", busy_port) is None


def test_bind_succeeds_on_a_free_port(dead_port):
    sock = admin._bind("127.0.0.1", dead_port)
    assert sock is not None
    try:
        assert sock.getsockname()[1] == dead_port
    finally:
        sock.close()
