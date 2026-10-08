"""The memai-admin command: take the port, register the server, report on it and stop it."""

from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import socket
import sys

import uvicorn

from memai import __version__, autostart, db, webui_build
from memai.admin.app import app

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _bind(host: str, port: int) -> socket.socket | None:
    """Take the port, or report that somebody else has it.

    uvicorn.run() binds inside its own event loop and, on a taken port,
    logs an error and exits 3. That is the right behaviour for a person
    who typed a command and can read the message, and the wrong one for
    an autostarted process racing its own siblings -- so bind here first
    and hand the socket over.

    Two error numbers mean "taken", and only one of them is obvious.
    Plain bind() on Windows gives EADDRINUSE, but a socket that set
    SO_REUSEADDR gets EACCES instead -- errno 13, not errno.WSAEACCES,
    which is 10013 and would never match. On POSIX, EACCES means a
    privileged port and is a real error, so the second case is gated to
    Windows. (A Windows reserved exclusion range also lands on EACCES
    with nothing listening; the caller says "in use" either way, which is
    imprecise but points at the same fix: pick another port.)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != "win32":
        # Not on Windows, where SO_REUSEADDR steals a live socket rather than reusing a dead one.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            return None
        if sys.platform == "win32" and exc.errno == errno.EACCES:
            return None
        raise
    sock.listen(2048)
    return sock


def _write_registry(host: str, port: int) -> None:
    """Record where this dashboard is, for autostart to find later.

    Written by the server itself because only it knows its own pid: a
    venv's python.exe is a redirector that runs the real interpreter as a
    child, so whoever spawned us saw a different process.

    Advisory, never a lock. autostart re-confirms it with /api/ping
    before believing it, which is what keeps a record left behind by an
    abrupt kill from disabling the dashboard rather than merely being
    ignored.
    """
    try:
        autostart.registry_path().write_text(
            json.dumps({"host": host, "port": port, "pid": os.getpid(),
                        "version": __version__}) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"  note: could not write {autostart.registry_path()} ({exc})")


def _clear_registry() -> None:
    try:
        autostart.registry_path().unlink(missing_ok=True)
    except OSError:
        pass


def _cmd_status() -> int:
    found = autostart.find_running()
    if not found:
        print("memai admin: not running")
        return 1
    host, port = found
    info = autostart.ping(host, port) or {}
    print(f"memai admin: http://{host}:{port} · pid {info.get('pid', '?')} "
          f"· version {info.get('version', '?')}")
    return 0


def _cmd_stop() -> int:
    """Stop the running dashboard by the pid it reports over HTTP.

    The dashboard holds a console of its own, so no console control
    event reaches it and uvicorn's signal handling is out of reach: this
    terminates the process. The store is SQLite in WAL mode and survives
    a hard stop.
    """
    found = autostart.find_running()
    if not found:
        print("memai admin: not running")
        return 1
    host, port = found
    info = autostart.ping(host, port) or {}
    pid = info.get("pid")
    if not isinstance(pid, int):
        print(f"memai admin at http://{host}:{port} did not report a pid")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"could not stop pid {pid}: {exc}")
        return 1
    _clear_registry()
    print(f"memai admin: stopped pid {pid} (was http://{host}:{port})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Separate from main() so a test can ask what the defaults resolved to.

    The port default comes from autostart rather than being read here, so
    that the guard looking for a dashboard and the dashboard itself
    cannot end up with different ideas of where it is -- which is exactly
    what happened while run-admin.bat set the variable and the code
    defaulted elsewhere.
    """
    parser = argparse.ArgumentParser(description="memai admin dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=autostart.configured_port())
    parser.add_argument("--autostarted", action="store_true",
                        help="started by the MCP server: lose a port race quietly")
    parser.add_argument("--status", action="store_true",
                        help="report where a running dashboard is, and exit")
    parser.add_argument("--stop", action="store_true",
                        help="stop a running dashboard, and exit")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.status:
        raise SystemExit(_cmd_status())
    if args.stop:
        raise SystemExit(_cmd_stop())

    sock = _bind(args.host, args.port)
    if sock is None:
        # Losing this race is how a duplicate autostart normally ends; only the operator is told.
        if args.autostarted:
            raise SystemExit(0)
        print(f"memai admin: port {args.port} is already in use "
              f"(memai-admin --status says what is there)")
        raise SystemExit(1)

    print(f"memai admin · project {db.active_project()} · db {db.default_db_path()} "
          f"· http://{args.host}:{args.port}")
    if args.host not in LOOPBACK_HOSTS:
        print(f"  WARNING: {args.host} is not loopback. This API has NO authentication:"
              f"\n  anyone who can reach {args.host}:{args.port} can read, edit and"
              f"\n  permanently delete every memory in the store.")

    # After the bind, so only the process that won the port runs npm.
    webui_build.ensure_built()

    _write_registry(args.host, args.port)
    try:
        config = uvicorn.Config(app, log_level="warning")
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        _clear_registry()
